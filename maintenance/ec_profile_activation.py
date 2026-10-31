"""Fixed EC profile service lifecycle; never unload an existing driver."""
import copy,re
from pathlib import Path
from maintenance import ec_profile
from maintenance.resource_activation import LiveSystem

UNIT='oxp3-fan-module.service'
IDENTITY=ec_profile.IDENTITY


def _check(identity,root,selection):
    if identity!=IDENTITY or Path(root)!=Path('/') or identity not in set(selection):
        raise ec_profile.ECProfileError('EC provider requires the reviewed live resource')


def activate(identity,*,root=Path('/'),pool,facts,selection,record_before_write,approve,system=None):
    if pool.resource(identity).get('ec_profile_contract') is None:
        from maintenance import resource_activation
        return resource_activation.activate(identity,root=root,pool=pool,facts=facts,selection=selection,record_before_write=record_before_write,approve=approve,system=system)
    _check(identity,root,selection)
    if not callable(record_before_write) or not callable(approve):raise ec_profile.ECProfileError('EC durable recorder and approval required')
    system=system or LiveSystem(root);intent,transaction=ec_profile._context(gate=False)
    preloaded=ec_profile._loaded();before=system.service(UNIT)
    if before not in ('active','inactive','failed','unknown'):raise ec_profile.ECProfileError('Unexpected EC service state')
    if before=='active' and not preloaded:raise ec_profile.ECProfileError('Active EC loader has no verified module')
    recovery={'kind':'BoilerEcProfileRecovery','schema_version':1,'identity':IDENTITY,'generation':intent['generation'],
        'transaction':transaction,'boot_id':system.boot_id(),'preexisting':preloaded,'current_boot_preexisting':preloaded,'load_pending':True,
        'services':[{'user':False,'unit':UNIT,'before':before,'started':False,'start_pending':False,'invocation':None}]}
    record_before_write(copy.deepcopy(recovery));approve(identity)
    if before!='active':
        system.run('systemctl','daemon-reload');recovery['services'][0]['start_pending']=True
        record_before_write(copy.deepcopy(recovery));system.run('systemctl','start',UNIT)
        if system.service(UNIT)!='active':raise ec_profile.ECProfileError('EC loader service failed')
        invocation=system.invocation(UNIT)
        if not isinstance(invocation,str) or not re.fullmatch('[0-9a-f]{32}',invocation):raise ec_profile.ECProfileError('EC loader invocation missing')
        recovery['services'][0].update(started=True,start_pending=False,invocation=invocation)
    if not ec_profile._loaded():raise ec_profile.ECProfileError('EC interfaces unavailable after load')
    recovery['load_pending']=False;record_before_write(copy.deepcopy(recovery))
    return {'status':'active','runtime':'validated','profile':ec_profile.PROFILE,
            'ownership':'compatible-existing' if preloaded else 'loaded-by-this-generation','recovery':recovery}


def verify(identity,*,root=Path('/'),pool,facts,selection,state=None,system=None):
    if pool.resource(identity).get('ec_profile_contract') is None:
        from maintenance import resource_activation
        return resource_activation.verify(identity,root=root,pool=pool,facts=facts,selection=selection,state=state,system=system)
    _check(identity,root,selection);intent,transaction=ec_profile._context(gate=True)
    if not ec_profile._loaded():raise ec_profile.ECProfileError('EC profile not loaded')
    if not isinstance(state,dict) or state.get('kind')!='BoilerEcProfileRecovery' or state.get('generation')!=intent['generation'] or state.get('transaction')!=transaction or state.get('load_pending'):
        raise ec_profile.ECProfileError('EC ownership record differs')
    return {'status':'active','runtime':'validated','profile':ec_profile.PROFILE,
            'ownership':'compatible-existing' if state['current_boot_preexisting'] else 'loaded-by-this-generation'}


def resume_boot(identity,*,root=Path('/'),pool,facts,selection,state,record_before_write,approve,system=None):
    if not isinstance(state,dict) or state.get('kind')!='BoilerEcProfileRecovery':raise ec_profile.ECProfileError('EC original recovery missing')
    return activate(identity,root=root,pool=pool,facts=facts,selection=selection,
                    record_before_write=record_before_write,approve=approve,system=system)


def restore(identity,*,root=Path('/'),state,record_before_write,system=None):
    if isinstance(state,dict) and state.get('kind')!='BoilerEcProfileRecovery':
        from maintenance import resource_activation
        return resource_activation.restore(identity,root=root,state=state,system=system)
    if identity!=IDENTITY or Path(root)!=Path('/') or not isinstance(state,dict) or state.get('kind')!='BoilerEcProfileRecovery' or not callable(record_before_write):
        raise ec_profile.ECProfileError('Invalid EC recovery')
    system=system or LiveSystem(root);intent,transaction=ec_profile._context(gate=True)
    if state.get('generation')!=intent['generation'] or state.get('transaction')!=transaction or state.get('load_pending'):
        raise ec_profile.ECProfileError('EC recovery generation differs')
    rows=state.get('services',[])
    if len(rows)!=1 or rows[0].get('unit')!=UNIT:raise ec_profile.ECProfileError('Invalid EC service record')
    item=rows[0]
    if item.get('started'):
        if system.invocation(UNIT)!=item.get('invocation'):raise ec_profile.ECProfileError('EC loader ownership changed')
        record_before_write({'kind':'BoilerEcProfileRestore','phase':'stopping','generation':intent['generation'],'transaction':transaction})
        system.run('systemctl','stop',UNIT)
    result={'kind':'BoilerEcProfileRestore','phase':'stopped','generation':intent['generation'],
            'transaction':transaction,'reboot_required':not state['current_boot_preexisting'],'driver_preserved':True}
    record_before_write(copy.deepcopy(result));return result


def resume_original(identity,*,root=Path('/'),state,record_before_write,system=None):
    if isinstance(state,dict) and state.get('kind')!='BoilerEcProfileRecovery':
        from maintenance import resource_activation
        return resource_activation.resume_original(identity,root=root,state=state,record_before_write=record_before_write,system=system)
    if identity!=IDENTITY or not isinstance(state,dict) or state.get('kind')!='BoilerEcProfileRecovery':raise ec_profile.ECProfileError('Invalid EC original state')
    if state['services'][0]['before']=='active':
        record_before_write({'kind':'BoilerEcOriginalResume','phase':'starting'})
        system=system or LiveSystem(root);system.run('systemctl','daemon-reload');system.run('systemctl','start',UNIT)
        if system.service(UNIT)!='active':raise ec_profile.ECProfileError('Original EC service did not resume')
        return {'status':'resumed'}
    return {'status':'unchanged'}
