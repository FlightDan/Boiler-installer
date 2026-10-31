"""Fixed read-only overlay activation and post-file shared-home release."""
import copy,os,pwd,re,time
from pathlib import Path
from . import overlay_home,resource_activation
from .resource_runtime import ResourceError
from protocol.overlay_contract import IDENTITY,validate_contract

UNITS=((False,'boiler-overlay-read.service'),(True,'oxp3-overlay-telemetry.service'))

def intent_for(identity,root,selection):
    from .resource_firstboot import checked_intent
    intent=checked_intent(root)
    if identity!=IDENTITY or identity not in selection or set(selection)!=set(intent['selection']['components']):raise ResourceError('Overlay selection differs from intent')
    return intent

def account():
    user=pwd.getpwnam('deck')
    if user.pw_uid<1000 or user.pw_gid<=0 or user.pw_dir!='/home/deck':raise ResourceError('Unexpected deck account')
    return user

def holders(intent):
    values=[intent['target_roles']['rootfs-'+s]['partuuid'].lower() for s in ('A','B')]
    if len(set(values))!=2 or any(not re.fullmatch('[0-9a-f-]{36}',x) for x in values):raise ResourceError('Invalid A/B overlay holders')
    return values

def ctl(system,user,*args,check=True):
    return system.userctl(*args,check=check) if user else system.run('systemctl',*args,check=check)

def _fresh_as_deck(system):
    code="import sys;sys.path.insert(0,'/usr/local/lib/boiler');from maintenance.overlay_show import cache_values;raise SystemExit(0 if cache_values() is not None else 1)"
    rc,_=system.run('runuser','-u','deck','--','/usr/bin/python3','-I','-B','-c',code,check=False)
    return rc==0

def _apply(identity,root,pool,selection,record,approve,previous=None):
    intent=intent_for(identity,root,selection);validate_contract(pool.resource(identity));user=account();system=resource_activation.LiveSystem(root)
    boot_changed=False
    if previous is None:
        recovery=dict(kind='BoilerOverlayRecovery',generation=intent['generation'],slot=intent['slot'],holders=holders(intent),boot_id=system.boot_id(),services=[])
        record(copy.deepcopy(recovery))
    else:
        recovery=copy.deepcopy(previous)
        if any(recovery.get(k)!=v for k,v in (('kind','BoilerOverlayRecovery'),('generation',intent['generation']),('slot',intent['slot']),('holders',holders(intent)))):raise ResourceError('Overlay recovery belongs to another installation')
        if recovery.get('boot_id')!=system.boot_id():
            boot_changed=True
            recovery['boot_id']=system.boot_id()
            for row in recovery['services']:row.update(started=False,start_pending=False,invocation=None)
            record(copy.deepcopy(recovery))
    approve(identity)
    def save_home(value):recovery['home_epoch']=value['epoch'];record(copy.deepcopy(recovery))
    store=overlay_home.Store(root)
    for holder in recovery['holders']:store.acquire(intent['generation'],holder,save_home,user)
    system.run('systemctl','daemon-reload')
    pending=False
    for is_user,unit in UNITS:
        current=system.service(unit,user=is_user)
        if is_user and current is None:pending=True;continue
        row=next((r for r in recovery['services'] if (r['user'],r['unit'])==(is_user,unit)),None)
        if row is None:
            enabled=ctl(system,is_user,'is-enabled',unit,check=False)
            if enabled is None:pending=True;continue
            row=dict(user=is_user,unit=unit,before_active=current,before_enabled=enabled[1],enabled=False,enable_pending=False,started=False,start_pending=False,invocation=None)
            recovery['services'].append(row);record(copy.deepcopy(recovery))
        if boot_changed and current=='active' and row.get('enabled') and row.get('before_active')!='active':
            invocation=system.invocation(unit,user=is_user)
            if not invocation or not re.fullmatch('[0-9a-f]{32}',invocation):raise ResourceError('Overlay service invocation unavailable')
            row.update(started=True,invocation=invocation);record(copy.deepcopy(recovery))
        if row['enable_pending']:
            current_enable=ctl(system,is_user,'is-enabled',unit,check=False)
            if current_enable is None:pending=True;continue
            if current_enable[1]!='enabled':ctl(system,is_user,'enable',unit)
            row.update(enabled=True,enable_pending=False);record(copy.deepcopy(recovery))
        if row['before_enabled']=='disabled' and not row['enabled']:
            row['enable_pending']=True;record(copy.deepcopy(recovery));ctl(system,is_user,'enable',unit);row.update(enabled=True,enable_pending=False);record(copy.deepcopy(recovery))
        if row['start_pending']:
            if current=='active':
                row.update(started=True,start_pending=False,invocation=system.invocation(unit,user=is_user));record(copy.deepcopy(recovery))
            else:row['start_pending']=False;record(copy.deepcopy(recovery))
        if current!='active':
            row['start_pending']=True;record(copy.deepcopy(recovery));ctl(system,is_user,'start',unit)
            if system.service(unit,user=is_user)!='active':raise ResourceError('Overlay service failed: '+unit)
            invocation=system.invocation(unit,user=is_user)
            if not invocation or not re.fullmatch('[0-9a-f]{32}',invocation):raise ResourceError('Overlay service invocation unavailable')
            row.update(started=True,start_pending=False,invocation=invocation);record(copy.deepcopy(recovery))
    if not pending:
        for _ in range(30):
            if _fresh_as_deck(system):break
            time.sleep(.1)
        else:pending=True
    preserved=bool(store.verify(intent['generation'],recovery['holders'])['preserved'])
    return dict(status='configured' if pending else 'active',runtime='pending-user-session' if pending else 'collector-active',user_edits_preserved=preserved,hardware_accepted=False,recovery=recovery)

def activate(identity,*,root=Path('/'),pool,facts,selection,record_before_write,approve):return _apply(identity,root,pool,selection,record_before_write,approve)
def resume(identity,*,root=Path('/'),pool,facts,selection,state,record_before_write,approve):return _apply(identity,root,pool,selection,record_before_write,approve,previous=state)
def verify(identity,*,root=Path('/'),pool,facts,selection,state=None):
    intent=intent_for(identity,root,selection);validate_contract(pool.resource(identity))
    if not state or state.get('generation')!=intent['generation'] or state.get('holders')!=holders(intent):raise ResourceError('Overlay recovery differs')
    shared=overlay_home.Store(root).verify(intent['generation'],holders(intent))
    system=resource_activation.LiveSystem(root)
    if system.service('boiler-overlay-read.service')!='active':raise ResourceError('Overlay permission service inactive')
    if system.service('oxp3-overlay-telemetry.service',user=True) is None:return dict(status='configured',runtime='pending-user-session',hardware_accepted=False)
    if system.service('oxp3-overlay-telemetry.service',user=True)!='active' or not _fresh_as_deck(system):return dict(status='configured',runtime='pending-user-session',hardware_accepted=False)
    return dict(status='active',runtime='collector-active',user_edits_preserved=shared['preserved'],hardware_accepted=False)

def restore(identity,*,root=Path('/'),state,record_before_write):
    from .resource_firstboot import checked_intent,read_private
    intent=checked_intent(root)
    if identity!=IDENTITY or state.get('generation')!=intent['generation'] or state.get('holders')!=holders(intent):raise ResourceError('Overlay recovery belongs to another generation')
    gate=Path(root)/'var/lib/boiler-resource-approved/common-telemetry-overlay.json'
    if gate.exists():
        if read_private(gate)!={'generation':intent['generation'],'identity':identity}:raise ResourceError('Overlay approval gate differs')
        gate.unlink()
        from .adoption_blobs import _sync_dir
        _sync_dir(gate.parent)
    system=resource_activation.LiveSystem(root);recovery=copy.deepcopy(state)
    # Stop the read grant first even when the user manager is absent.
    for row in recovery['services']:
        if row['start_pending'] or row['enable_pending']:raise ResourceError('Interrupted overlay service operation requires retry')
        active=system.service(row['unit'],user=row['user'])
        if row.get('restore_pending') and active!='active':row.update(started=False,restore_pending=False);record_before_write(copy.deepcopy(recovery))
        if row['started'] and active=='active':
            if system.invocation(row['unit'],user=row['user'])!=row['invocation']:raise ResourceError('Overlay service changed externally')
            row['restore_pending']=True;record_before_write(copy.deepcopy(recovery));ctl(system,row['user'],'stop',row['unit']);row.update(started=False,restore_pending=False);record_before_write(copy.deepcopy(recovery))
        if row['enabled']:
            current=ctl(system,row['user'],'is-enabled',row['unit'],check=False)
            if current is None:raise ResourceError('User session required to restore overlay service enablement')
            if row.get('disable_pending') and current[1]!='enabled':row.update(enabled=False,disable_pending=False);record_before_write(copy.deepcopy(recovery))
            if row['enabled'] and current[1]=='enabled':
                row['disable_pending']=True;record_before_write(copy.deepcopy(recovery));ctl(system,row['user'],'disable',row['unit']);row.update(enabled=False,disable_pending=False);record_before_write(copy.deepcopy(recovery))
            elif row['enabled']:
                recovery.setdefault('preserved_service_state',[]).append(dict(unit=row['unit'],user=row['user'],observed=current[1]))
                row['enabled']=False;record_before_write(copy.deepcopy(recovery))
    from . import overlay_permissions
    with overlay_permissions.private_lock():
        conflicts=overlay_permissions.restore()
    if conflicts:raise ResourceError('Overlay sensor permissions changed externally: '+', '.join(conflicts))
    return dict(status='restored',recovery=recovery,service_edits_preserved=bool(recovery.get('preserved_service_state')),hardware_accepted=False)

def resume_original(identity,*,root=Path('/'),state,prior_resume=None,record_before_write):
    from .resource_firstboot import checked_intent
    intent=checked_intent(root)
    if identity!=IDENTITY or state.get('generation')!=intent['generation'] or state.get('holders')!=holders(intent):raise ResourceError('Overlay post-file release binding differs')
    progress=copy.deepcopy(prior_resume if prior_resume is not None else state)
    if (not isinstance(progress,dict) or progress.get('kind')!='BoilerOverlayRecovery' or progress.get('generation')!=intent['generation'] or progress.get('holders')!=holders(intent) or not isinstance(progress.get('released',[]),list) or any(x not in holders(intent) for x in progress.get('released',[]))):raise ResourceError('Invalid overlay release progress')
    progress.setdefault('released',[]);store=overlay_home.Store(root);user=account()
    for holder in holders(intent):
        if holder in progress['released']:continue
        def record_ledger(value):progress['home_epoch']=value['epoch'];record_before_write(copy.deepcopy(progress))
        result=store.release(intent['generation'],holder,record_ledger,user,allow_unacquired=progress.get('home_epoch') is None)
        progress.setdefault('release_results',{})[holder]=result;progress['released'].append(holder);record_before_write(copy.deepcopy(progress))
    return dict(status='released',results=progress.get('release_results',{}),hardware_accepted=False)
