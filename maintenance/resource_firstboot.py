"""Fixed first-boot resource installation; catalog data never selects executable code."""
from pathlib import Path
import copy
import contextlib
import hashlib
import json
import os
import re
import subprocess
from .engine import Engine, _json_write
from .adoption_blobs import _file, _directory
from .resource_runtime import ResourcePool, ResourceSession, ResourceError, resolve
from protocol.resource_pool import validate_pool

INTENT = 'var/lib/boiler/resource-firstboot/intent.json'
STATE = 'var/lib/boiler/resource-firstboot/state.json'
KIND = 'BoilerResourceFirstbootIntent'

class ResourceBusy(ResourceError):
    """Another owned maintenance operation holds the shared lock."""


def read_private(path):
    with _file(Path(path), private=True) as (stream, info):
        if info.st_size > 1024 * 1024: raise ResourceError('First-boot state exceeds limit')
        return json.loads(stream.read(1024 * 1024 + 1))


def checked_intent(root, *, identity=None):
    root = Path(root).absolute()
    value = read_private(root / INTENT)
    if (value.get('kind') != KIND or value.get('schema_version') != 1
            or value.get('mode') != 'firstboot-install-v1'
            or not re.fullmatch('[0-9a-f]{32}', value.get('generation', ''))
            or value.get('slot') not in ('A', 'B')
            or not isinstance(value.get('selection'), dict)
            or not isinstance(value.get('root_partuuid'), str)):
        raise ResourceError('Invalid first-boot installation intent')
    if identity is not None and identity.lower() != value['root_partuuid'].lower():
        raise ResourceError('Booted root differs from the installer-bound target slot')
    selection = value['selection']
    if selection.get('trust') != 'local-test':
        raise ResourceError('This test build requires explicit local resource consent')
    return value


@contextlib.contextmanager
def open_pool(root, intent):
    pool_root = Path(root) / 'usr/local/lib/boiler/cache/resource-pool'
    digest = intent['selection']['catalog_sha256']
    catalog = validate_pool(pool_root, expected_catalog_sha256=digest)
    pool = ResourcePool(pool_root, catalog, local_test=True)
    models = [m for m in catalog['models'] if m['id'] == intent['selection']['model_id']]
    if len(models) != 1: raise ResourceError('Installed first-boot model is ambiguous')
    from .hdr_resources import bind
    effective, references = bind(pool, models[0]['resources'], intent['selection']['components'])
    if {r['id'] for r in resolve(effective, references, intent['selection']['components'])} != set(intent['selection']['components']):
        raise ResourceError('First-boot selection is not dependency complete')
    with contextlib.ExitStack() as stack:
        if intent.get('hdr_panel_binding'):
            from .hdr_resources import bind_live_intent
            effective,references=stack.enter_context(bind_live_intent(effective,references,intent['selection']['components'],intent['hdr_panel_binding']))
        if intent.get('desktop_hdr_panel_binding'):
            from .desktop_hdr_panel import bind_live_intent
            effective,references=stack.enter_context(bind_live_intent(effective,references,intent['selection']['components'],intent['desktop_hdr_panel_binding']))
        yield effective,references


def environment(root):
    root = Path(root)
    release = {}
    # The conventional /etc/os-release symlink is handled by normal read only;
    # resource installation paths still pass Engine's independent confinement.
    for line in (root/'etc/os-release').read_text().splitlines():
        key, sep, value = line.partition('=')
        if sep: release[key] = value.strip().strip('"\'')
    kernels = sorted(p.name for p in (root/'usr/lib/modules').iterdir() if p.is_dir())
    return dict(architecture='x86_64', os_id=release.get('ID'), os_version=release.get('VERSION_ID'), kernels=kernels)


def _provider(identity):
    # Fixed trusted dispatch, never import a module path from selection data.
    if identity == 'resource:gamma22-hdr-desktop':
        from . import desktop_hdr_activation
        return desktop_hdr_activation
    if identity in ('resource:onexplayer-input', 'resource:bosch-bmi260-gyro'):
        from . import input_activation
        return input_activation
    if identity == 'resource:common-kde-keyboard':
        from . import keyboard_activation
        return keyboard_activation
    if identity == 'resource:common-telemetry-overlay':
        from . import overlay_activation
        return overlay_activation
    if identity == 'resource:common-flathub-mirror':
        from . import flathub_activation
        return flathub_activation
    if identity == 'resource:intel-g3e-public-cpu-controls':
        from . import cpu_live_activation
        return cpu_live_activation
    if identity == 'resource:onexplayer-ec-fan-driver':
        from . import ec_profile_activation
        return ec_profile_activation
    if identity == 'resource:onexplayer-fan':
        from . import fan_live_activation
        return fan_live_activation
    if identity == 'resource:common-waydroid':
        from . import waydroid_activation
        return waydroid_activation
    from . import resource_activation
    return resource_activation


def _new_state(intent):
    return dict(schema_version=1, kind='BoilerResourceFirstbootState', generation=intent['generation'],
                slot=intent['slot'], root_partuuid=intent['root_partuuid'], status='pending', resources={})


import contextlib

@contextlib.contextmanager
def operation_lock(root):
    import fcntl,stat
    directory=Path(root)/STATE
    with _directory(directory.parent,create=True):pass
    descriptor=os.open(directory.parent/'lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'a+b') as lock:
        info=os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=0 or info.st_mode&0o077:raise ResourceError('Unsafe resource lifecycle lock')
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:raise ResourceBusy('Resource maintenance is already running') from error
        from .resource_access import operation
        token=operation.set(str(Path(root).absolute()))
        try: yield
        finally: operation.reset(token)

def run(root=Path('/'), *, retry_failed=False):
    root = Path(root).absolute()
    if root != Path('/') or os.name != 'posix' or os.geteuid() != 0:
        raise ResourceError('Resource first-boot activation requires the actual Linux root')
    from .live_root import RootCommands
    actual = RootCommands().identity()
    intent = checked_intent(root, identity=actual['partuuid'])
    state_path = root / STATE
    with operation_lock(root):
        return _run_locked(root,intent,state_path,retry_failed=retry_failed)


def _ensure_hdr_bindings(root, intent):
    from . import resource_lifecycle as lifecycle, retry_documents
    from .hdr_firstboot import ensure as game_ensure
    from .desktop_hdr_firstboot import ensure as desktop_ensure
    modes = {'initial-x2-hdr-binding-v1': game_ensure, 'initial-desktop-hdr-binding-v1': desktop_ensure}
    pending = set()
    with lifecycle.TargetMounts(intent, readonly=True) as mounts:
        marker = mounts.home/'.boiler/resource-maintenance/lifecycle-action.json'
        paths = [(marker,None), *((slot/STATE,'maintenance_retry') for slot in mounts.roots.values())]
        for path,key in paths:
            if not path.exists(): continue
            value = read_private(path)
            if key: value = value.get(key)
            if not value or value.get('status') == 'closed': continue
            if value.get('kind') != 'BoilerResourceRetryAction':
                raise ResourceError('Another shared action requires explicit recovery')
            value = retry_documents.hydrate(value,path)
            if value.get('mode') not in modes: raise ResourceError('Another resource retry requires explicit recovery')
            pending.add(value['mode'])
    if len(pending) > 1: raise ResourceError('Conflicting HDR binding actions require explicit recovery')
    if pending: intent = modes[pending.pop()](root,intent)
    intent = game_ensure(root,intent)
    return desktop_ensure(root,intent)

def _run_locked(root, intent, state_path, *, retry_failed=False):
    """Separated only for deterministic orchestration regression fixtures."""
    if root==Path('/') and any(x in intent['selection']['components'] for x in ('resource:gamma22-hdr-game','resource:gamma22-hdr-desktop')):
        intent=_ensure_hdr_bindings(root,intent)
    state = read_private(state_path) if state_path.exists() else _new_state(intent)
    if any(state.get(k) != intent.get(k) for k in ('generation', 'slot', 'root_partuuid')):
        raise ResourceError('Existing first-boot state belongs to a different installation')
    if state.get('kind') != 'BoilerResourceFirstbootState': raise ResourceError('Invalid first-boot state kind')
    if state.get('maintenance_retry',{}).get('status') not in (None,'closed'):
        raise ResourceError('An interrupted resource reinstall requires explicit retry')
    barrier=root/'home/.boiler/resource-maintenance/lifecycle-action.json'
    if barrier.exists() and read_private(barrier).get('status')!='closed':raise ResourceError('An interrupted shared resource maintenance action requires explicit recovery')
    with open_pool(root,intent) as (pool,references):
        return _run_pool(root,intent,state_path,state,pool,references,retry_failed=retry_failed)


def _run_pool(root,intent,state_path,state,pool,references,*,retry_failed=False):
    from . import resource_activation
    facts = resource_activation.collect_facts(root=root)
    state.setdefault('initial_probe',copy.deepcopy(facts))
    slot = intent['slot']
    if not Engine(root, read_only=True).verify_readonly('core:maintenance')['ok']:
        raise ResourceError('Retained maintenance runtime failed verification')
    targets = {slot: dict(root=root, environment=environment(root), hardware=facts,
                          opt_offload=(root/'opt').is_mount())}
    boot_id=(root/'proc/sys/kernel/random/boot_id').read_text().strip()
    if not re.fullmatch('[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}',boot_id):raise ResourceError('The current kernel boot identity is unavailable')
    state['boot_id']=boot_id
    if not Engine(root,read_only=True).verify_readonly('core:resource-firstboot')['ok']:raise ResourceError('First-boot intent, service and gate ownership changed')
    selected = [r['id'] for r in resolve(pool,references,intent['selection']['components'])]
    state['status'] = 'running'; _json_write(state_path, state)
    for identity in selected:
        row = state['resources'].get(identity, {})
        if row.get('status') in ('restored','activation_restored'):continue
        verify_only=row.get('status') in ('active','configured') and row.get('runtime')!='pending-user-session' and row.get('boot_id')==boot_id
        if row.get('status') in ('installing', 'activating') and identity!='resource:common-waydroid':
            row.update(status='recovery_required', error='Interrupted resource operation requires explicit maintenance recovery')
            state['resources'][identity] = row; _json_write(state_path, state); continue
        if row.get('status') in ('failed', 'recovery_required') and not retry_failed: continue
        if row.get('status') == 'recovery_required': continue
        if row.get('status')=='failed' and row.get('activation_recovery') and identity!='resource:common-waydroid':
            row.update(status='recovery_required',error='Owned activation state must be explicitly reconciled or restored before retry')
            state['resources'][identity]=row;_json_write(state_path,state);continue
        previous=copy.deepcopy(row)
        row=copy.deepcopy(row)
        if previous:row['attempt_history']=(row.get('attempt_history',[])+[{k:copy.deepcopy(previous[k]) for k in ('status','runtime','error') if k in previous}])[-16:]
        row.update(status='verifying' if verify_only else 'installing',runtime='not_validated',attempts=row.get('attempts',0)+1,boot_id=boot_id)
        state['resources'][identity] = row; _json_write(state_path, state)
        try:
            facts=resource_activation.collect_facts(root=root)
            targets[slot]['hardware']=copy.deepcopy(facts)
            # The same resource handler and journal engine perform the actual
            # resource file writes. The explicit live session validates core
            # retention without claiming the inactive slot has booted.
            session = ResourceSession(pool, targets, root/'home', read_only=True)
            closure = [r['id'] for r in resolve(pool, references, [identity])]
            active_input=pool.resource(identity)['install']['kind']=='boiler-input-runtime-v1'
            active_keyboard=pool.resource(identity)['install']['kind']=='boiler-kde-keyboard-v1'
            proof = [dict(component=identity,scope='fixed-provider',ok=True,installation='pending-fixed-provider')] if active_input or active_keyboard else session.verify(closure)
            receipt = intent.get('resource_receipts',{}).get(identity,intent['resource_receipt'])
            from .resource_runtime import check_hardware
            resource=copy.deepcopy(pool.resource(identity))
            requirements=resource.get('hardware_requires',{})
            # These describe offline deployment or a future graphical session,
            # and are not positive live physical-device predicates.
            ignored={'cpu_staging_target','cpu_service_state','cpu_service_enablement','hdr_session','gamescope_user','hdr_existing_override',
                'input_staging_target','inputplumber_service_state','inputplumber_service_enablement','gyro_service_state','gyro_service_enablement','hid_oxp_loaded','oxp3_bmi260_loaded'}
            resource['hardware_requires']={k:v for k,v in requirements.items() if k not in ignored}
            if identity not in ('resource:onexplayer-input','resource:bosch-bmi260-gyro'):check_hardware(resource,facts)
            if any(not r['ok'] for r in proof): raise ResourceError('Installed resource file verification failed')
            row.update(status='verifying' if verify_only else 'activating',files='fixed-provider' if active_input or active_keyboard else 'verified',receipt=receipt,verification=proof)
            _json_write(state_path,state)
            if verify_only:
                recovery=row.get('activation_recovery',{})
                if recovery.get('services'):
                    system=resource_activation.LiveSystem(root)
                    for owned in recovery['services']:
                        if identity not in ('resource:intel-g3e-public-cpu-controls','resource:onexplayer-fan') and owned.get('started') and not owned.get('start_pending') and system.service(owned['unit'],user=owned['user'])=='active' and system.invocation(owned['unit'],user=owned['user'])!=owned.get('invocation'):
                            raise ResourceError('A managed resource service was restarted outside this activation generation')
                result=_provider(identity).verify(identity,root=root,pool=pool,facts=facts,selection=selected,state=row.get('activation_recovery'))
                if not isinstance(result,dict) or result.get('status') not in ('active','configured'):raise ResourceError('Fixed provider verification failed')
                row.update(result);_json_write(state_path,state);continue
            def before_write(recovery):
                if 'activation_recovery' not in row:row['activation_recovery']=copy.deepcopy(recovery)
                elif row['activation_recovery']!=recovery and recovery.get('kind')=='BoilerFanRecovery':
                    original=row['activation_recovery']
                    if original.get('generation')!=recovery.get('generation'):raise ResourceError('Fan generation changed without reinstall')
                    row['activation_recovery_updates']=(row.get('activation_recovery_updates',[])+[copy.deepcopy(recovery)])[-16:]
                    updated=copy.deepcopy(recovery)
                    if original.get('initial_ec_before') is not None:updated['initial_ec_before']=original['initial_ec_before']
                    for item in updated.get('services',[]):
                        old=next((v for v in original.get('services',[]) if (v.get('user'),v.get('unit'))==(item.get('user'),item.get('unit'))),None)
                        if old is not None:item['before']=old.get('before')
                    row['activation_recovery']=updated
                elif row['activation_recovery']!=recovery and recovery.get('kind')=='BoilerEcProfileRecovery':
                    original=row['activation_recovery']
                    if original.get('generation')!=recovery.get('generation'):raise ResourceError('EC generation changed without reinstall')
                    row['activation_recovery_updates']=(row.get('activation_recovery_updates',[])+[copy.deepcopy(recovery)])[-16:]
                    row['activation_recovery']=copy.deepcopy(recovery)
                    row['activation_recovery']['preexisting']=original['preexisting']
                    row['activation_recovery']['services'][0]['before']=original['services'][0]['before']
                elif row['activation_recovery']!=recovery and recovery.get('kind')=='BoilerInputRecovery':
                    original=row['activation_recovery']
                    if original.get('generation')!=recovery.get('generation'):raise ResourceError('Input runtime generation changed without an explicit reinstall')
                    row['activation_recovery_updates']=(row.get('activation_recovery_updates',[])+[copy.deepcopy(recovery)])[-16:]
                    row['activation_recovery']=copy.deepcopy(recovery)
                    row['activation_recovery']['services']=copy.deepcopy(original['services'])
                    row['activation_recovery']['config']['before']=copy.deepcopy(original['config']['before'])
                elif row['activation_recovery']!=recovery and recovery.get('kind')=='BoilerOverlayRecovery':
                    original=row['activation_recovery']
                    if any(original.get(k)!=recovery.get(k) for k in ('generation','slot','holders')):raise ResourceError('Overlay original ownership changed')
                    row['activation_recovery_updates']=(row.get('activation_recovery_updates',[])+[copy.deepcopy(recovery)])[-16:]
                    row['activation_recovery']=copy.deepcopy(recovery)
                elif row['activation_recovery']!=recovery and recovery.get('kind')=='BoilerFlathubRecovery':
                    original=row['activation_recovery']
                    if any(original.get(k)!=recovery.get(k) for k in ('generation','slot','before')):
                        raise ResourceError('Flathub original ownership changed')
                    row['activation_recovery_updates']=(row.get('activation_recovery_updates',[])+[copy.deepcopy(recovery)])[-16:]
                    row['activation_recovery']=copy.deepcopy(recovery)
                elif row['activation_recovery']!=recovery and recovery.get('kind')=='BoilerWaydroidRecovery':
                    original=row['activation_recovery']
                    if any(original.get(k)!=recovery.get(k) for k in ('identity','generation','slot')):raise ResourceError('Waydroid original ownership changed')
                    if original.get('host',{}).get('before')!=recovery.get('host',{}).get('before'):raise ResourceError('Waydroid package ownership changed')
                    row['activation_recovery']=copy.deepcopy(recovery)
                elif row['activation_recovery']!=recovery:
                    # Preserve the original before-state and every later durable
                    # provider callback; a resume must not erase ownership.
                    row['activation_recovery_updates']=(row.get('activation_recovery_updates',[])+[copy.deepcopy(recovery)])[-16:]
                    original=row['activation_recovery']
                    if recovery.get('boot_id'):
                        original.setdefault('initial_boot_id',original.get('boot_id'));original['boot_id']=recovery['boot_id']
                    if 'services' in recovery and 'services' in original:
                        for update in recovery['services']:
                            old=next((v for v in original['services'] if (v.get('user'),v.get('unit'))==(update.get('user'),update.get('unit'))),None)
                            if old is None:original['services'].append(copy.deepcopy(update))
                            else:
                                before=old.get('before');old.update(copy.deepcopy(update));old['before']=before
                _json_write(state_path, state)
            def approve(approved):
                if approved != identity:raise ResourceError('Activation approval identity changed')
                directory=root/'var/lib/boiler-resource-approved'
                with _directory(directory,create=True,create_mode=0o755) as descriptor:
                    if descriptor is not None:os.fchmod(descriptor,0o755)
                _json_write(directory/(identity.split(':',1)[1]+'.json'),dict(generation=intent['generation'],identity=identity))
            provider=_provider(identity)
            if (active_input or identity in ('resource:intel-g3e-public-cpu-controls','resource:onexplayer-ec-fan-driver','resource:onexplayer-fan')) and previous.get('activation_recovery') and previous.get('boot_id')!=boot_id:
                result=provider.resume_boot(identity,root=root,pool=pool,facts=facts,selection=selected,state=row['activation_recovery'],record_before_write=before_write,approve=approve)
            elif identity in ('resource:common-flathub-mirror','resource:common-telemetry-overlay','resource:common-waydroid') and previous.get('activation_recovery'):
                result=provider.resume(identity,root=root,pool=pool,facts=facts,selection=selected,state=row['activation_recovery'],record_before_write=before_write,approve=approve)
            else:result=provider.activate(identity,root=root,pool=pool,facts=facts,selection=selected,record_before_write=before_write,approve=approve)
            if not isinstance(result, dict) or result.get('status') not in ('active', 'configured'):
                raise ResourceError('Fixed activation provider did not report success')
            row.update(result)
            if 'activation_recovery' not in row and isinstance(result.get('recovery'),dict):row['activation_recovery']=copy.deepcopy(result['recovery'])
        except Exception as error:
            row.update(status='failed', runtime='not_validated', error=str(error)[:2048])
        _json_write(state_path, state)
    # One shared loader lifecycle follows all selected plugin checks. No plugin
    # provider owns stopping the loader during a per-plugin restore.
    plugin_ids=set(selected)&set(resource_activation.PLUGIN_IDS)
    if plugin_ids and all(state['resources'].get(i,{}).get('status') in ('active','configured') for i in plugin_ids):
        loader=state.setdefault('decky',{})
        try:
            system=resource_activation.LiveSystem(root)
            current=system.service('plugin_loader.service')
            if loader.get('boot_id') and loader['boot_id']!=boot_id and not loader.get('start_pending'):
                loader.update(status='pending',started=False)
            loader['boot_id']=boot_id
            if loader.get('start_pending') or loader.get('status')=='recovery_required':
                raise ResourceError('Interrupted shared Loader activation requires explicit reconciliation')
            if loader.get('status')=='active':
                if current!='active':raise ResourceError('Shared Loader is no longer active')
                if loader.get('started') and system.invocation('plugin_loader.service')!=loader.get('invocation'):
                    raise ResourceError('Shared Loader was restarted outside this activation generation')
            else:
                if 'before' not in loader:
                    loader.update(before=current,status='starting',started=False,start_pending=False)
                    _json_write(state_path,state)
                directory=root/'var/lib/boiler-resource-approved'
                with _directory(directory,create=True,create_mode=0o755) as descriptor:
                    if descriptor is not None:os.fchmod(descriptor,0o755)
                _json_write(directory/'decky.json',dict(generation=intent['generation'],resources=sorted(plugin_ids)))
                if current!='active':
                    loader['start_pending']=True;_json_write(state_path,state)
                    system.run('systemctl','daemon-reload')
                    system.run('systemctl','start','plugin_loader.service')
                    if system.service('plugin_loader.service')!='active':raise ResourceError('Shared Decky loader failed to become active')
                    invocation=system.invocation('plugin_loader.service')
                    if not invocation:raise ResourceError('Shared Loader invocation identity is unavailable')
                    loader.update(started=True,start_pending=False,invocation=invocation)
                loader['status']='active'
        except Exception as error:
            loader.update(status='recovery_required' if loader.get('start_pending') else 'failed',error=str(error)[:2048])
        _json_write(state_path,state)
    state['status'] = 'complete' if all(state['resources'].get(i, {}).get('status') in ('active', 'configured') for i in selected) and (not plugin_ids or state.get('decky',{}).get('status')=='active') else 'attention_required'
    _json_write(state_path, state)
    from .firstboot_notice import publish
    publish(state,root=root)
    return state


def status(root=Path('/')):
    root=Path(root); intent=checked_intent(root)
    return read_private(root/STATE) if (root/STATE).exists() else _new_state(intent)


def restore_activation(identity, root=Path('/')):
    root=Path(root)
    if root!=Path('/') or os.name!='posix' or os.geteuid()!=0:raise ResourceError('Activation restore requires the actual Linux root')
    from .live_root import RootCommands
    intent=checked_intent(root,identity=RootCommands().identity()['partuuid'])
    with operation_lock(root):
        return _restore_locked(identity,root,intent)


def validate_restore_binding(identity,root,intent,state,*,verify_runtime=True):
    if state.get('kind')!='BoilerResourceFirstbootState' or any(state.get(k)!=intent.get(k) for k in ('generation','slot','root_partuuid')):raise ResourceError('Activation state belongs to another installation')
    if identity not in intent['selection']['components']:raise ResourceError('Resource is not in the installed selection')
    for component in ('core:maintenance','core:resource-firstboot') if verify_runtime else ():
        if not Engine(root,read_only=True).verify_readonly(component)['ok']:raise ResourceError('Retained activation runtime or intent changed')
    gate=Path(root)/'var/lib/boiler-resource-approved'/(identity.split(':',1)[1]+'.json')
    if gate.exists() and read_private(gate)!={'generation':intent['generation'],'identity':identity}:raise ResourceError('Approval gate belongs to another installation')
    shared=Path(root)/'var/lib/boiler-resource-approved/decky.json'
    if shared.exists() and read_private(shared).get('generation')!=intent['generation']:raise ResourceError('Shared Loader gate belongs to another installation')


def _restore_locked(identity,root,intent,*,verify_runtime=True):
    state_path=root/STATE;state=read_private(state_path)
    validate_restore_binding(identity,root,intent,state,verify_runtime=verify_runtime)
    row=state.get('resources',{}).get(identity)
    if (identity not in intent['selection']['components'] or not row or
            ('activation_recovery' not in row and identity!='resource:intel-g3e-public-cpu-controls')):
        raise ResourceError('No owned activation recovery state for this resource')
    row['status']='restoring';_json_write(state_path,state)
    try:
        def record_restore(recovery):
            row['restore_recovery']=copy.deepcopy(recovery);_json_write(state_path,state)
        if identity in ('resource:common-flathub-mirror','resource:common-telemetry-overlay','resource:common-waydroid'):
            result=_provider(identity).restore(identity,root=root,state=row.get('restore_recovery',row['activation_recovery']),record_before_write=record_restore)
        elif identity=='resource:intel-g3e-public-cpu-controls':
            result=_provider(identity).restore(identity,root=root,state=row.get('activation_recovery'),record_before_write=record_restore,prior_restore=row.get('restore_recovery'))
        elif identity=='resource:onexplayer-fan':
            result=_provider(identity).restore(identity,root=root,state=row['activation_recovery'],record_before_write=record_restore,prior_restore=row.get('restore_recovery'))
        elif identity in ('resource:onexplayer-input','resource:bosch-bmi260-gyro','resource:common-kde-keyboard','resource:onexplayer-ec-fan-driver','resource:gamma22-hdr-desktop'):
            result=_provider(identity).restore(identity,root=root,state=row['activation_recovery'],record_before_write=record_restore)
        else:result=_provider(identity).restore(identity,root=root,state=row['activation_recovery'])
        gate=root/'var/lib/boiler-resource-approved'/(identity.split(':',1)[1]+'.json')
        if gate.exists():
            value=read_private(gate)
            if value!={'generation':intent['generation'],'identity':identity}:raise ResourceError('Approval gate belongs to another generation')
            gate.unlink()
        row.update(status='activation_restored',runtime='not_validated',restore_result=result)
        from . import resource_activation
        plugins=set(intent['selection']['components'])&set(resource_activation.PLUGIN_IDS)
        if plugins and all(state['resources'].get(i,{}).get('status') in ('activation_restored','restored') for i in plugins):
            loader=state.get('decky',{})
            if loader.get('start_pending'):raise ResourceError('Interrupted shared Loader start requires explicit reconciliation')
            if loader.get('started'):
                system=resource_activation.LiveSystem(root)
                if system.service('plugin_loader.service')=='active':
                    if system.invocation('plugin_loader.service')!=loader.get('invocation'):raise ResourceError('Shared Loader changed outside this activation generation')
                    loader['status']='restoring';_json_write(state_path,state)
                    system.run('systemctl','stop','plugin_loader.service')
                loader['status']='restored'
            decky_gate=root/'var/lib/boiler-resource-approved/decky.json'
            if decky_gate.exists():
                approval=read_private(decky_gate)
                if approval.get('generation')!=intent['generation']:raise ResourceError('Shared Loader gate belongs to another generation')
                decky_gate.unlink()
    except Exception as error:
        row.update(status='recovery_required',error=str(error)[:2048]);_json_write(state_path,state);raise
    state['status']='attention_required';_json_write(state_path,state);return state


def main():
    import argparse
    import sys
    # This selector remains usable after the optional resource payload is restored.
    if len(sys.argv) == 3 and sys.argv[1] == 'overlay-show':
        from .overlay_show import main as show
        show(sys.argv[2]); return
    if len(sys.argv) == 3 and sys.argv[1] == 'overlay-permissions':
        from .overlay_permissions import service_action
        service_action(sys.argv[2]); return
    parser=argparse.ArgumentParser(description='Fixed Boiler first-boot resource lifecycle')
    parser.add_argument('action', choices=('run', 'retry', 'status','restore-activation','cpu-prestart','cpu-poststart','cpu-poststop','ec-profile-load','fan-prestart','fan-poststart','fan-prestop','fan-stop-auto','fan-poststop'))
    parser.add_argument('identity',nargs='?')
    args=parser.parse_args()
    if args.action in ('fan-prestart','fan-poststart','fan-prestop','fan-stop-auto','fan-poststop'):
        if args.identity is not None: raise ResourceError('Fan guard accepts no arguments')
        from . import fan_service_guard
        {'fan-prestart':fan_service_guard.prestart,'fan-poststart':fan_service_guard.poststart,
         'fan-prestop':fan_service_guard.prestop,'fan-stop-auto':fan_service_guard.stop_auto,
         'fan-poststop':fan_service_guard.poststop}[args.action]()
        return
    if args.action=='ec-profile-load':
        if args.identity is not None: raise ResourceError('EC loader accepts no arguments')
        from . import ec_profile
        ec_profile.load()
        return
    if args.action in ('cpu-prestart','cpu-poststart','cpu-poststop'):
        if args.identity is not None: raise ResourceError('CPU sidecar accepts no arguments')
        from . import cpu_sidecar
        {'cpu-prestart':cpu_sidecar.prestart,'cpu-poststart':cpu_sidecar.poststart,'cpu-poststop':cpu_sidecar.poststop}[args.action]()
        return
    try:
        result=restore_activation(args.identity) if args.action=='restore-activation' else status() if args.action=='status' else run(retry_failed=args.action=='retry')
    except ResourceBusy:
        # Periodic checks may overlap explicit maintenance. Busy is not failure.
        print(json.dumps(dict(status='busy')))
        return
    except Exception:
        if args.action in ('run','retry'):
            from .firstboot_notice import publish
            try:
                try:failed=status()
                except Exception:failed={}
                publish(dict(failed,status='attention_required'),fatal=True)
            except Exception:pass
        raise
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['status']=='attention_required': raise SystemExit(1)

if __name__ == '__main__': main()
