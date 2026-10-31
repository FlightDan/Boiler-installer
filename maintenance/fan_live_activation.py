"""Fixed fan config and service lifecycle guarded by the reviewed EC profile."""
import copy
import re
from pathlib import Path
from maintenance import ec_profile, ec_readonly_probe, fan_live_config, resource_activation
from maintenance.resource_firstboot import checked_intent,read_private,STATE
from maintenance.live_root import RootCommands

IDENTITY='resource:onexplayer-fan'
EC=ec_profile.IDENTITY
class FanActivationError(RuntimeError):pass


def _check(identity,root,pool,selection):
    if identity!=IDENTITY or Path(root)!=Path('/') or identity not in set(selection) or EC not in set(selection):
        raise FanActivationError('Fan requires the fixed selected EC dependency')
    resource=pool.resource(identity)
    expected={'config_path':'var/lib/oxp3-fan/config.json','mode':0o600,'default':{'mode':'auto'},'preserve_user_edits':True}
    if resource.get('runtime_state')!=expected or EC not in resource.get('dependencies',[]):
        raise FanActivationError('Fan private configuration contract differs')
    intent=checked_intent(Path('/'),identity=RootCommands().identity()['partuuid'])
    ec_profile._context(gate=True)
    if not ec_profile._loaded():raise FanActivationError('Reviewed EC profile unavailable')
    state=read_private(Path('/')/STATE);ec=state.get('resources',{}).get(EC,{})
    recovery=ec.get('activation_recovery',{})
    if ec.get('status')!='active' or recovery.get('kind')!='BoilerEcProfileRecovery' or recovery.get('load_pending'):
        raise FanActivationError('EC profile activation has not completed')
    return intent


def activate(identity,*,root=Path('/'),pool,facts,selection,record_before_write,approve,system=None):
    intent=_check(identity,root,pool,selection)
    if not callable(record_before_write) or not callable(approve):raise FanActivationError('Fan durable recorder and approval required')
    controller=system or resource_activation.LiveSystem(root)
    original_invocation=controller.invocation('oxp3-fan.service') if controller.service('oxp3-fan.service')=='active' else None
    if controller.service('oxp3-fan.service')=='active' and (not isinstance(original_invocation,str) or not re.fullmatch('[0-9a-f]{32}',original_invocation)):
        raise FanActivationError('Original fan service invocation is unavailable')
    recovery={'kind':'BoilerFanRecovery','schema_version':1,'identity':IDENTITY,'generation':intent['generation'],
              'boot_id':(system or resource_activation.LiveSystem(root)).boot_id(),'config':None,'services':[],
              'initial_ec_before':ec_readonly_probe.probe(),'original_invocation':original_invocation}
    def record_config(state):
        recovery['config']=copy.deepcopy(state);record_before_write(copy.deepcopy(recovery))
    recovery['config']=fan_live_config.ensure(root,generation=intent['generation'],state=None,record_before_write=record_config)
    def record_service(state):
        recovery['services']=copy.deepcopy(state['services']);record_before_write(copy.deepcopy(recovery))
    result=resource_activation.activate(identity,root=root,pool=pool,facts=facts,selection=selection,
        record_before_write=record_service,approve=approve,system=system)
    return dict(result,recovery=copy.deepcopy(recovery))


def verify(identity,*,root=Path('/'),pool,facts,selection,state=None,system=None):
    intent=_check(identity,root,pool,selection)
    if not isinstance(state,dict) or state.get('kind')!='BoilerFanRecovery' or state.get('generation')!=intent['generation']:
        raise FanActivationError('Fan configuration generation differs')
    fan_live_config.verify(root,generation=intent['generation'],state=state['config'])
    system=system or resource_activation.LiveSystem(root)
    from maintenance import fan_service_guard
    fan_service_guard.owned(intent['generation'],system.boot_id(),system.invocation('oxp3-fan.service'))
    return resource_activation.verify(identity,root=root,pool=pool,facts=facts,selection=selection,state=state,system=system)


def resume_boot(identity,*,root=Path('/'),pool,facts,selection,state,record_before_write,approve,system=None):
    intent=_check(identity,root,pool,selection)
    if not isinstance(state,dict) or state.get('kind')!='BoilerFanRecovery' or state.get('generation')!=intent['generation']:
        raise FanActivationError('Fan original configuration generation missing')
    recovery=copy.deepcopy(state);recovery['boot_id']=(system or resource_activation.LiveSystem(root)).boot_id()
    recovery['current_boot_ec_before']=ec_readonly_probe.probe()
    def record_config(value):recovery['config']=copy.deepcopy(value);record_before_write(copy.deepcopy(recovery))
    recovery['config']=fan_live_config.ensure(root,generation=intent['generation'],state=recovery['config'],record_before_write=record_config)
    def record_service(value):
        recovery['services']=copy.deepcopy(value['services']);record_before_write(copy.deepcopy(recovery))
    result=resource_activation.activate(identity,root=root,pool=pool,facts=facts,selection=selection,
        record_before_write=record_service,approve=approve,system=system)
    return dict(result,recovery=copy.deepcopy(recovery))


def restore(identity,*,root=Path('/'),state,record_before_write,system=None,prior_restore=None):
    if identity!=IDENTITY or Path(root)!=Path('/') or not isinstance(state,dict) or state.get('kind')!='BoilerFanRecovery' or not callable(record_before_write):
        raise FanActivationError('Invalid fan restoration')
    intent=checked_intent(Path('/'),identity=RootCommands().identity()['partuuid'])
    if state.get('generation')!=intent['generation']:raise FanActivationError('Fan restore generation differs')
    ec_profile._context(gate=True)
    if not ec_profile._loaded():raise FanActivationError('EC profile is not available for controlled fan stop')
    system=system or resource_activation.LiveSystem(root)
    from maintenance import fan_service_guard
    boot=system.boot_id()
    resuming=(isinstance(prior_restore,dict) and prior_restore.get('kind')=='BoilerFanRestore' and
              prior_restore.get('phase') in ('stopping','config','stopped') and system.service('oxp3-fan.service') in ('inactive','failed'))
    invocation=prior_restore.get('invocation') if resuming else system.invocation('oxp3-fan.service')
    if resuming and (prior_restore.get('generation'),prior_restore.get('boot_id'))!=(intent['generation'],boot):
        raise FanActivationError('Interrupted fan stop belongs to another boot or generation')
    fan_service_guard.owned(intent['generation'],boot,invocation,phase='stopped' if resuming else 'started')
    owned_state=copy.deepcopy(state)
    if len(owned_state.get('services',[]))!=1:raise FanActivationError('Invalid fan service ownership')
    row=owned_state['services'][0]
    if (not isinstance(row,dict) or (row.get('user'),row.get('unit')) not in set(resource_activation._services(identity)) or
        row.get('before') not in (None,'active','inactive','failed','unknown')):
        raise FanActivationError('Fan original service record differs')
    owned_state['services'][0].update(started=True,start_pending=False,invocation=invocation)
    proof={'kind':'BoilerFanRestore','generation':intent['generation'],'boot_id':boot,'invocation':invocation}
    if not resuming:
        record_before_write(dict(proof,phase='stopping',ec_before=ec_readonly_probe.probe()))
        service=resource_activation.restore(identity,root=root,state=owned_state,system=system)
    else:
        service={'status':'restored','runtime':'validated','reboot_required':False,
                 'resume_services':[{'user':r['user'],'unit':r['unit']} for r in owned_state['services'] if r['before']=='active']}
    def record_config(value):record_before_write(dict(proof,phase='config',config=value))
    config=fan_live_config.restore(root,generation=intent['generation'],state=state['config'],record_before_write=record_config)
    result=dict(proof,phase='stopped',service=service,config=config)
    record_before_write(copy.deepcopy(result));return result


def resume_original(identity,*,root=Path('/'),state,record_before_write,system=None):
    return resource_activation.resume_original(identity,root=root,state=state,record_before_write=record_before_write,system=system)

