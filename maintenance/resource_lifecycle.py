"""Reviewed production maintenance over installer-bound A/B resource targets."""
from pathlib import Path
import contextlib
import copy
import hashlib
import json
import os
import tempfile
import uuid
from .engine import Engine,_json_write
from .resource_runtime import ResourceSession,ResourceError,resolve
from .resource_targets import TargetMounts,probe
from . import resource_firstboot as firstboot
from . import retry_documents


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _keyboard_marker():return Path('/home/.boiler/resource-maintenance/lifecycle-action.json')


def _intent():
    from .live_root import RootCommands
    if os.name!='posix' or os.geteuid()!=0:raise ResourceError('Live resource maintenance requires Linux root')
    return firstboot.checked_intent(Path('/'),identity=RootCommands().identity()['partuuid'])


def _session(mounts,intent,*,readonly):
    pool,refs=mounts.runtime.enter_context(firstboot.open_pool(Path('/'),intent))
    slots={s:dict(root=r,environment=firstboot.environment(r),hardware={'target_disk':copy.deepcopy(mounts.bound['rootfs-'+s]['target_disk'])},opt_offload=mounts.opt_offload[s]) for s,r in mounts.roots.items()}
    return ResourceSession(pool,slots,mounts.home,read_only=readonly,offline_image=True,cpu_live_binding=mounts),refs


def _receipt(intent,identity):
    return intent.get('resource_receipts',{}).get(identity,intent['resource_receipt'])


def _snapshot(intent,identity,mounts):
    if identity=='resource:common-opti2xmx':
        from .opti_runtime_guard import require_clear
        home=mounts.home/'deck'
        require_clear(home,home.stat().st_uid)
    session,refs=_session(mounts,intent,readonly=True)
    if identity not in intent['selection']['components']:raise ResourceError('Resource was not installed by this selection')
    if session.pool.resource(identity)['install']['kind']=='boiler-input-runtime-v1':return _snapshot_input(intent,identity,mounts,session)
    if session.pool.resource(identity)['install']['kind']=='boiler-kde-keyboard-v1':return _snapshot_keyboard(intent,identity,mounts,session)
    receipt=session._bound_receipt(_receipt(intent,identity)['id'])
    if receipt!=_receipt(intent,identity):raise ResourceError('Retained resource receipt changed')
    if not any(row['component']==identity for row in receipt['transactions']):raise ResourceError('Receipt has no transaction for this resource')
    if identity=='resource:gamma22-hdr-desktop':
        from .desktop_hdr_resources import check_restore
        check_restore(session,receipt)
    slots={};transactions=[];pending_scopes=set();pending_restore=[]
    from .engine import _same
    from .adoption_blobs import validate_backups
    from .cpu_resources import matches
    for scope,engine in [*session.engines.items(),('shared-home',session.shared)]:
        allowed={t['transaction'] for t in receipt['transactions'] if t['scope']==scope and t['component']==identity}
        for path in engine.journals.glob('*.json'):
            journal=firstboot.read_private(path)
            if journal.get('status') in ('committed','restored','rolled_back'):continue
            if path.stem not in allowed or journal.get('status')!='restoring':raise ResourceError('Unrelated pending transaction requires its own explicit recovery')
            observations=[]
            for operation in journal['operations']:
                observed=engine._observed(engine._target(operation['target']),operation['before'],operation['after'])
                if not (_same(observed,operation['before']) or _same(observed,operation['after'])):raise ResourceError('Interrupted restore target changed outside its known snapshots')
                observations.append(dict(target=operation['target'],observed=observed,before_sha256=digest(operation['before']),after_sha256=digest(operation['after'])))
            validate_backups(engine,journal['operations']);pending_scopes.add(scope)
            pending_restore.append(dict(scope=scope,transaction=path.stem,status='restoring',journal_sha256=digest(journal),operations=observations,backups_sha256=digest(journal['operations'])))
    # A known interrupted resource restore may coexist with untouched committed
    # core/bootstrap journals. Read their snapshots directly without recovery.
    for engine in session.engines.values():
        state=engine._state()
        for cid in ('core:maintenance','core:resource-firstboot'):
            current=state['components'].get(cid)
            if not current:raise ResourceError('Retained maintenance/bootstrap is missing')
            journal=firstboot.read_private(engine._journal(current['transaction']))
            if journal.get('status')!='committed' or journal.get('root')!=engine.identity or journal['component']['id']!=cid or any(not matches(engine,journal['component'],op) for op in journal['operations']):raise ResourceError('Retained runtime or bootstrap changed')
    for slot,root in mounts.roots.items():
        current_intent=firstboot.checked_intent(root)
        if any(current_intent.get(k)!=intent.get(k) for k in ('generation','selection','target_roles')) or current_intent['slot']!=slot:raise ResourceError('A/B resource intents do not belong to the same installation')
        state=firstboot.read_private(root/firstboot.STATE) if (root/firstboot.STATE).exists() else firstboot._new_state(current_intent)
        firstboot.validate_restore_binding(identity,root,current_intent,state,verify_runtime=not pending_scopes)
        slots[slot]=dict(activation=copy.deepcopy(state['resources'].get(identity)),state_sha256=digest(state))
        if identity=='resource:common-waydroid':
            from . import waydroid_activation
            row=state['resources'].get(identity) or {}
            recovery=row.get('restore_recovery',row.get('activation_recovery'))
            slots[slot]['waydroid_packages']=waydroid_activation.preflight_slot(root,current_intent,recovery)
        if identity=='resource:common-flathub-mirror':
            gate=root/'var/lib/boiler-resource-approved/common-flathub-mirror.json'
            gate_value=firstboot.read_private(gate) if gate.exists() else None
            if gate_value is not None and gate_value!={'generation':intent['generation'],'identity':identity}:
                raise ResourceError('Flathub approval gate belongs to another installation')
            slots[slot]['gate_sha256']=digest(gate_value)
    proof=session.verify([identity]) if not pending_scopes else [dict(component=identity,scope=scope,ok=False,reason='explicit-interrupted-restore') for scope in pending_scopes]
    restored_scopes=set(pending_scopes)
    for scope,engine in [*session.engines.items(),('shared-home',session.shared)]:
        state=engine._state()
        # Engine restore enforces this too. Surface it in the readonly review,
        # before any runtime service is stopped.
        for cid,installed in state['components'].items():
            if cid==identity:continue
            journal=firstboot.read_private(engine._journal(installed['transaction']))
            if identity in journal['component'].get('requires',[]):raise ResourceError('Restore dependent resource first: '+cid)
        for row in receipt['transactions']:
            if row['component']!=identity or row['scope']!=scope:continue
            current=state['components'].get(identity)
            journal=firstboot.read_private(engine._journal(row['transaction']))
            if journal.get('status')=='restoring' and scope in pending_scopes:
                pass
            elif journal.get('status')=='restored':
                prior=journal['before_state']['components'].get(identity)
                if current!=prior:raise ResourceError('Restored scope changed after the interrupted maintenance action')
                restored_scopes.add(scope)
            elif journal.get('status')!='committed' or not current or current['transaction']!=row['transaction']:raise ResourceError('Resource transaction was superseded')
            if journal.get('status')=='committed' and pending_scopes:
                if any(not matches(engine,journal['component'],op) for op in journal['operations']):raise ResourceError('A committed resource scope changed while another restore is pending')
                resource=session.pool.resource(identity)
                from .resource_runtime import component
                from .core_retention import is_resource,decorate
                expected=component(resource,'shared-home' if scope=='shared-home' else 'root-slots',shared_opt=session.shared_opt)
                if is_resource(journal['component']):expected=decorate(expected,shared=(scope=='shared-home'))
                for dependency in resource.get('dependencies',[]):expected['requires'].append(dependency)
                actual=copy.deepcopy(journal['component']);actual.pop('resource_group',None)
                from protocol.firmware_contract import KIND
                if resource['install']['kind']==KIND:
                    from .firmware_resources import verification
                    if not verification(resource,expected,journal,engine.root)['ok']:raise ResourceError('Firmware preserved/written decisions changed')
                elif actual!=expected:raise ResourceError('Committed resource manifest differs from the reviewed pool')
                proof.append(dict(component=identity,scope=scope,ok=True))
            transactions.append(dict(scope=scope,transaction=row['transaction'],status=journal['status'],manifest_sha256=digest(journal['component'])))
    if any(not row['ok'] and row['scope'] not in restored_scopes for row in proof):raise ResourceError('Installed resource files changed; inspect before restore')
    from .resource_activation import LiveSystem,_services,PLUGIN_IDS
    system=LiveSystem();services=[]
    for user,unit in _services(identity):
        services.append(dict(user=user,unit=unit,active=system.service(unit,user=user),invocation=system.invocation(unit,user=user)))
    plan=dict(kind='BoilerResourceRestorePlan',schema_version=1,generation=intent['generation'],identity=identity,
        roles=mounts.bound,receipt_sha256=digest(receipt),transactions=transactions,slots=slots,
        services=services,boot_id=system.boot_id(),shared_loader=identity in PLUGIN_IDS,
        scope='both-root-slots-and-single-shared-home',files=proof,pending_restore=pending_restore,pending_restore_scopes=sorted(pending_scopes),target_window=mounts.pending_window(),activation_before_files=True,
        summary=identity+'：停止并恢复受管运行状态，随后恢复 A/B 两槽和共享目录的已记录文件；将按已记录的原服务状态恢复运行。 / Restore owned runtime state and journaled files in both system slots and shared home.\n'+', '.join(v['unit']+': '+str(v['active']) for v in services))
    if identity=='resource:common-flathub-mirror':
        from . import flathub_restore
        return flathub_restore.attach(plan,intent,mounts,session)
    return plan


def _snapshot_keyboard(intent,identity,mounts,session):
    from .keyboard_runtime.service import Keyboard,TARGET
    from .keyboard_target import binding
    slots={}
    for slot,root in mounts.roots.items():
        other=firstboot.checked_intent(root)
        if (any(other.get(k)!=intent.get(k) for k in ('generation','selection','target_roles')) or other['slot']!=slot
                or other.get('root_partuuid','').lower()!=intent['target_roles']['rootfs-'+slot]['partuuid'].lower()):
            raise ResourceError('Keyboard slot intent differs')
        state=firstboot.read_private(root/firstboot.STATE) if (root/firstboot.STATE).exists() else firstboot._new_state(other)
        firstboot.validate_restore_binding(identity,root,other,state)
        gate=root/'var/lib/boiler-resource-approved/common-kde-keyboard.json'
        gate_value=firstboot.read_private(gate) if gate.exists() else None
        if gate_value is not None and gate_value!={'generation':intent['generation'],'identity':identity}:
            raise ResourceError('Keyboard approval gate belongs to another installation')
        slots[slot]=dict(binding=binding(root,expected_label=slot),state_sha256=digest(state),
                         gate_sha256=digest(gate_value))
    service=Keyboard(Path('/'));store,_,_=service._preflight()
    with store.lock():
        index,pending=service._state(store)
        if pending:raise ResourceError('Interrupted keyboard transaction requires explicit retry from its original slot')
        ledger=digest(index);target=digest(store._read(TARGET,user=True))
        managed=sorted(slot for slot,value in index['slots'].items() if TARGET in (value.get('current') or {}).get('paths',{}))
    return dict(kind='BoilerResourceRestorePlan',schema_version=1,generation=intent['generation'],identity=identity,
        handler='boiler-kde-keyboard-v1',roles=mounts.bound,slots=slots,ledger_sha256=ledger,target_sha256=target,
        managed_slots=managed,target_window=mounts.pending_window(),scope='both-slots-single-keyboard-ledger',
        summary='Restore the shared keyboard setting in both slots; preserve any user-edited kwinrc.')


def _snapshot_input(intent,identity,mounts,session):
    from . import input_activation,resource_activation
    slots={};transactions=[]
    # Preflight every scope before a current live consumer is stopped.
    for engine in [*session.engines.values(),session.shared]:
        for path in engine.journals.glob('*.json'):
            if firstboot.read_private(path).get('status') not in ('committed','restored','rolled_back'):raise ResourceError('Pending input/other transaction requires its fixed recovery before restore')
    for slot,root in mounts.roots.items():
        installed_intent=firstboot.checked_intent(root)
        if any(installed_intent.get(k)!=intent.get(k) for k in ('generation','selection','target_roles')) or installed_intent['slot']!=slot:raise ResourceError('Inactive input intent belongs to another installation')
        state=firstboot.read_private(root/firstboot.STATE) if (root/firstboot.STATE).exists() else firstboot._new_state(installed_intent)
        firstboot.validate_restore_binding(identity,root,installed_intent,state)
        row=state['resources'].get(identity,{})
        recovery=row.get('activation_recovery')
        component=input_activation.COMPONENTS[identity];engine=session.engines[slot]
        current=engine._state()['components'].get(component)
        if current:
            if not recovery:raise ResourceError('Installed input runtime has no bound recovery descriptor')
            input_activation.preflight_restore(identity,root=root,state=recovery)
            journal=firstboot.read_private(engine._journal(current['transaction']))
            if journal.get('component',{}).get('input_live_generation')!=recovery.get('generation'):raise ResourceError('Installed input generation changed')
            transactions.append(dict(scope=slot,transaction=current['transaction'],component=component,manifest_sha256=digest(journal['component'])))
        elif recovery and recovery.get('transaction') and row.get('status') not in ('restored','activation_restored'):
            journal=firstboot.read_private(engine._journal(recovery['transaction']))
            if journal.get('status')!='restored':raise ResourceError('Input runtime transaction disappeared without a committed restore')
        slots[slot]=dict(activation=copy.deepcopy(row),state_sha256=digest(state),config=input_activation._regular_or_absent(root/input_activation.CONFIGS[identity]))
    system=resource_activation.LiveSystem();services=[]
    for unit in dict.fromkeys(['inputplumber.service',*input_activation.UNITS[identity]]):
        services.append(dict(user=False,unit=unit,active=system.service(unit),invocation=system.invocation(unit)))
    return dict(kind='BoilerResourceRestorePlan',schema_version=1,generation=intent['generation'],identity=identity,
        handler='boiler-input-runtime-v1',roles=mounts.bound,slots=slots,transactions=transactions,services=services,boot_id=system.boot_id(),
        pending_restore=[],pending_restore_scopes=[],target_window=mounts.pending_window(),activation_before_files=True,
        scope='current-live-and-inactive-fixed-input-runtime',summary=identity+'：停止当前手柄/陀螺仪服务，恢复两槽固定运行时文件，保留用户按键配置和校准。 / Stop current input services and restore both slot runtime transactions, preserving user mappings and calibration.')


def _preflight_live_input_restore(identity,intent,current):
    from . import input_activation
    firstboot.validate_restore_binding(identity,Path('/'),intent,current)
    existing=current['resources'].get(identity,{})
    recovery=existing.get('activation_recovery')
    if recovery:
        input_activation.preflight_restore(identity,root=Path('/'),state=recovery)
    owned=input_activation._preflight_preloaded_custom_ownership(identity,root=Path('/'),generation=recovery['generation'] if recovery else None)
    if owned and not recovery:raise input_activation.InputActivationError('Owned input binding has no retained recovery descriptor')

def _restore_input(identity,intent,plan,reviewed_sha256):
    from . import input_activation
    current=firstboot.status();_preflight_live_input_restore(identity,intent,current)
    row=current['resources'].setdefault(identity,{})
    current['maintenance_restore']=dict(identity=identity,status='restoring-input',reviewed_sha256=reviewed_sha256)
    _json_write(Path('/')/firstboot.STATE,current)
    results=[]
    if row.get('activation_recovery'):
        def record_live(value):
            row['restore_recovery']=copy.deepcopy(value);_json_write(Path('/')/firstboot.STATE,current)
        results.append(dict(scope=intent['slot'],**input_activation.restore(identity,root=Path('/'),state=row['activation_recovery'],record_before_write=record_live)))
    from .live_root import write_scope
    with write_scope(Engine(Path('/'))):
        with TargetMounts(intent,readonly=False,recover_window=bool(plan.get('target_window'))) as mounts:
            action=mounts.home/'.boiler/resource-maintenance/lifecycle-action.json'
            _json_write(action,dict(kind='BoilerResourceMaintenanceAction',generation=intent['generation'],identity=identity,status='restoring-input',reviewed_sha256=reviewed_sha256))
            inactive='B' if intent['slot']=='A' else 'A';root=mounts.roots[inactive]
            inactive_intent=firstboot.checked_intent(root)
            state=firstboot.read_private(root/firstboot.STATE) if (root/firstboot.STATE).exists() else firstboot._new_state(inactive_intent)
            recovery=state['resources'].get(identity,{}).get('activation_recovery')
            if recovery:
                def record_offline(value):
                    state['resources'][identity]['restore_recovery']=copy.deepcopy(value);_json_write(root/firstboot.STATE,state)
                results.append(dict(scope=inactive,**input_activation.restore_offline(identity,root=root,state=recovery,record_before_write=record_offline)))
            for slot,root in mounts.roots.items():
                existing=firstboot.checked_intent(root)
                state=firstboot.read_private(root/firstboot.STATE) if (root/firstboot.STATE).exists() else firstboot._new_state(existing)
                state['resources'].setdefault(identity,{}).update(status='restored',runtime='not_validated',restore_transactions=copy.deepcopy(results))
                state['maintenance_restore']=dict(identity=identity,status='restored',reviewed_sha256=reviewed_sha256);state['status']='attention_required'
                _json_write(root/firstboot.STATE,state)
            _json_write(action,dict(kind='BoilerResourceMaintenanceAction',generation=intent['generation'],identity=identity,status='closed'))
    return dict(status='restored',identity=identity,transactions=results)


def plan_restore(identity):
    intent=_intent()
    with firstboot.operation_lock(Path('/')):
        with TargetMounts(intent,readonly=True) as mounts:plan=_snapshot(intent,identity,mounts)
    return dict(plan=plan,sha256=digest(plan))


def verify():
    intent=_intent();result={}
    from . import resource_activation
    with firstboot.operation_lock(Path('/')):
        with TargetMounts(intent,readonly=True) as mounts:
            session,refs=_session(mounts,intent,readonly=True)
            for identity in intent['selection']['components']:
                try:
                    active_input=session.pool.resource(identity)['install']['kind'] in ('boiler-input-runtime-v1','boiler-kde-keyboard-v1')
                    proof=[dict(component=identity,scope='fixed-provider',ok=True)] if active_input else session.verify([identity])
                    if any(not p['ok'] for p in proof):raise ResourceError('Resource file verification failed')
                    facts=resource_activation.collect_facts()
                    state=firstboot.status()['resources'].get(identity,{})
                    live=firstboot._provider(identity).verify(identity,root=Path('/'),pool=session.pool,facts=facts,
                        selection=intent['selection']['components'],state=state.get('activation_recovery'))
                    result[identity]=dict(ok=live.get('keyboard_verify',{}).get('ok',True),files=proof,activation=live)
                except Exception as error:result[identity]=dict(ok=False,error=str(error)[:2048])
    return dict(kind='BoilerResourceVerification',ok=all(v['ok'] for v in result.values()),resources=result)


def _reconcile_reviewed(identity,state,plan):
    """Only an explicit unchanged restore review may adopt a pending/current invoke."""
    row=state['resources'].get(identity,{})
    recovery=row.get('activation_recovery')
    if not recovery:return
    original=copy.deepcopy(recovery)
    current={(v['user'],v['unit']):v for v in plan['services']}
    for owned in recovery.get('services',[]):
        live=current.get((owned['user'],owned['unit']))
        if live is None:raise ResourceError('Restore review does not cover an owned service')
        cross_boot=recovery.get('boot_id')!=plan['boot_id']
        if owned.get('start_pending') or cross_boot:
            if live['active']=='active':
                if not live.get('invocation'):raise ResourceError('Cannot reconcile an active service without invocation identity')
                owned.update(started=True,start_pending=False,invocation=live['invocation'])
            elif live['active'] in (None,'inactive','failed','unknown'):owned.update(started=False,start_pending=False,invocation=None)
            else:raise ResourceError('Cannot reconcile unexpected service state')
    recovery['boot_id']=plan['boot_id']
    row['reviewed_restore_reconciliation']=dict(review_sha256=digest(plan),before=original,after=copy.deepcopy(recovery))


def _recover_reviewed(engine,scope,identity,session,pending):
    expected={row['transaction']:row['journal_sha256'] for row in pending if row['scope']==scope}
    if identity=='resource:intel-g3e-public-cpu-controls':
        markers={bool(firstboot.read_private(engine._journal(tx)).get('component',{}).get('cpu_live_contract')) for tx in expected}
        if len(markers)>1:raise ResourceError('Mixed CPU journal contracts require separate recovery')
        permit=(engine._cpu_live_action(session.pool,session.slots,scope,operation='restore/recover',binding=session.cpu_live_binding)
                if markers=={True} else engine._cpu_action(session.pool,session.slots,scope,recovery=True))
    else:permit=contextlib.nullcontext()
    with permit,engine._lock(live_recover=True):
        actual={}
        for path in engine.journals.glob('*.json'):
            journal=firstboot.read_private(path)
            if journal.get('status') not in ('committed','restored','rolled_back'):
                if journal.get('status')!='restoring':raise ResourceError('A new pending transaction appeared after restore review')
                actual[path.stem]=digest(journal)
        if actual!=expected:raise ResourceError('Pending recovery changed after restore review')
        return engine._recover()


def _keyboard_restore_preflight(intent,action,document,mounts):
    from .keyboard_target import binding
    if (document.get('generation')!=intent['generation'] or document.get('reviewed_sha256')!=action['reviewed_sha256']
            or mounts.bound!=action['plan']['roles']):raise ResourceError('Keyboard restore document binding changed')
    publication=action['phase'] in ('publishing','closing')
    for slot,root in mounts.roots.items():
        other=firstboot.checked_intent(root)
        if (any(other.get(k)!=intent.get(k) for k in ('generation','selection','target_roles')) or other['slot']!=slot
                or other.get('root_partuuid','').lower()!=intent['target_roles']['rootfs-'+slot]['partuuid'].lower()):
            raise ResourceError('Keyboard restore slot intent changed')
        if binding(root,expected_label=slot)!=action['plan']['slots'][slot]['binding']:
            raise ResourceError('Keyboard slot binding changed during restore')
        entry=document['slots'][slot];path=root/firstboot.STATE;exists=path.exists()
        state=firstboot.read_private(path) if exists else firstboot._new_state(other)
        before=(exists==entry['before_exists'] and state==entry['before'])
        after=(exists and state==entry['after'])
        if not before and not (publication and after):raise ResourceError('Keyboard restore slot state changed outside this action')
        gate=root/'var/lib/boiler-resource-approved/common-kde-keyboard.json'
        current=firstboot.read_private(gate) if gate.exists() else None
        if current!=entry['gate_before'] and not (publication and current is None):
            raise ResourceError('Keyboard restore approval gate changed outside this action')
    return True


def _keyboard_edited_paths(root,slot):
    from .keyboard_runtime.service import Keyboard
    from .keyboard_target import binding
    service=Keyboard(root);bound=binding(root,expected_label=slot);store,_,_=service._preflight()
    with store.lock():
        index,pending=service._state(store)
        if pending:raise ResourceError('Keyboard transaction requires recovery first')
        current=index['slots'].get(bound['slot'],{}).get('current') or {'paths':{}}
        _,edited=store._live_checked(current)
        return sorted(edited)


def _resume_keyboard_restore(intent,action):
    from . import keyboard_resource as bridge
    from . import keyboard_restore_docs
    from .keyboard_target import binding
    marker=_keyboard_marker()
    if (action.get('kind')!='BoilerKeyboardRestoreAction' or action.get('generation')!=intent['generation']
            or action.get('identity')!=bridge.IDENTITY or action.get('status')!='restoring'
            or digest(action.get('plan'))!=action.get('reviewed_sha256')
            or action.get('phase') not in ('prepared','recovering','restoring-A','restoring-B','publishing')
            or not isinstance(action.get('completed'),dict) or set(action['completed']) - {'A','B'}
            or (action['phase']=='publishing' and set(action['completed'])!={'A','B'})):
        raise ResourceError('Invalid keyboard restore action')
    document=keyboard_restore_docs.load(action['document'],marker)
    with TargetMounts(intent,readonly=False,home_only=True) as mounts:
            _keyboard_restore_preflight(intent,action,document,mounts)
            live=bridge.Keyboard(Path('/'));options=live.options()
            if options.get('lifecycle_reason'):raise ResourceError(options['lifecycle_reason'])
            if options.get('pending'):
                if options.get('pending_slot')!=binding(Path('/'),expected_label=intent['slot'])['slot']:
                    raise ResourceError('Interrupted keyboard transaction belongs to another slot; boot that slot before retry')
                with firstboot.open_pool(Path('/'),intent) as (pool,refs):
                    reviewed=bridge.prepare(pool,intent,'recover',root=Path('/'))
                    _keyboard_restore_preflight(intent,action,document,mounts)
                    action['phase']='recovering';_json_write(marker,action)
                    _keyboard_restore_preflight(intent,action,document,mounts)
                    bridge.retry(pool,intent,reviewed,reviewed_sha256=reviewed['review_sha256'],root=Path('/'))
            if action['phase']=='prepared':
                service=bridge.Keyboard(Path('/'));store,_,_=service._preflight()
                with store.lock():
                    index,pending=service._state(store)
                    if pending or digest(index)!=action['plan']['ledger_sha256'] or digest(store._read(bridge.TARGET,user=True))!=action['plan']['target_sha256']:
                        raise ResourceError('Keyboard ledger or target changed after review')
            with firstboot.open_pool(Path('/'),intent) as (pool,refs):
                for slot,root in mounts.roots.items():
                    if slot in action['completed']:continue
                    other=firstboot.checked_intent(root)
                    if any(other.get(k)!=intent.get(k) for k in ('generation','selection','target_roles')) or other['slot']!=slot:
                        raise ResourceError('Keyboard slot intent changed during restore')
                    reviewed=bridge.prepare(pool,other,'restore',root=root)
                    _keyboard_restore_preflight(intent,action,document,mounts)
                    preserved=action.setdefault('preserved',{}).setdefault(slot,[])
                    action['preserved'][slot]=sorted(set(preserved)|set(_keyboard_edited_paths(root,slot)))
                    action.update(phase='restoring-'+slot,slot=slot);_json_write(marker,action)
                    _keyboard_restore_preflight(intent,action,document,mounts)
                    result=bridge.restore(pool,other,reviewed,reviewed_sha256=reviewed['review_sha256'],root=root)
                    if action['preserved'][slot]:
                        result['user_edits_preserved']=True;result['configuration_restored']=False
                        result['preserved_paths']=copy.deepcopy(action['preserved'][slot])
                    _keyboard_restore_preflight(intent,action,document,mounts)
                    action['completed'][slot]=result;_json_write(marker,action)
            _keyboard_restore_preflight(intent,action,document,mounts)
            action['phase']='publishing';_json_write(marker,action)
            for slot,root in mounts.roots.items():
                _keyboard_restore_preflight(intent,action,document,mounts)
                after=document['slots'][slot]['after'];path=root/firstboot.STATE
                if not path.exists() or firstboot.read_private(path)!=after:_json_write(path,after)
                gate=root/'var/lib/boiler-resource-approved/common-kde-keyboard.json'
                if gate.exists():
                    _keyboard_restore_preflight(intent,action,document,mounts)
                    gate.unlink()
            _keyboard_restore_preflight(intent,action,document,mounts)
            action.update(status='closed',phase='closed');_json_write(marker,action)
    return dict(status='restored',identity=bridge.IDENTITY,slots=action['completed'])


def _restore_keyboard(intent,plan,reviewed_sha256):
    from . import keyboard_restore_docs
    marker=_keyboard_marker()
    if marker.exists() and firstboot.read_private(marker).get('status')!='closed':
        raise ResourceError('Another shared resource action requires retry')
    with TargetMounts(intent,readonly=True) as mounts:
        ref=keyboard_restore_docs.create(intent,plan,reviewed_sha256,mounts,marker)
    action=dict(kind='BoilerKeyboardRestoreAction',generation=intent['generation'],identity='resource:common-kde-keyboard',document=ref,
                status='restoring',phase='prepared',reviewed_sha256=reviewed_sha256,plan=copy.deepcopy(plan),completed={})
    with TargetMounts(intent,readonly=True) as mounts:
        _keyboard_restore_preflight(intent,action,keyboard_restore_docs.load(ref,marker),mounts)
    _json_write(marker,action)
    return _resume_keyboard_restore(intent,action)


def _restore_marker_preflight(identity,intent,*,root=Path('/')):
    marker=Path(root)/'home/.boiler/resource-maintenance/lifecycle-action.json'
    if not os.path.lexists(marker):return
    action=firstboot.read_private(marker)
    if action.get('status')=='closed':return
    if (action.get('kind')!='BoilerResourceMaintenanceAction' or action.get('status')!='restoring'
            or action.get('generation')!=intent['generation'] or action.get('identity')!=identity):
        raise ResourceError('Another interrupted shared action requires its explicit retry')
    current=firstboot.read_private(Path(root)/firstboot.STATE)
    restore=current.get('maintenance_restore',{})
    if (current.get('generation')!=intent['generation'] or restore.get('identity')!=identity
            or restore.get('reviewed_sha256')!=action.get('reviewed_sha256')):
        raise ResourceError('Interrupted restore action binding changed')


def restore(identity,reviewed_sha256):
    intent=_intent()
    if identity in ('resource:onexplayer-input','resource:bosch-bmi260-gyro'):
        _preflight_live_input_restore(identity,intent,firstboot.status())
    with firstboot.operation_lock(Path('/')):
        with TargetMounts(intent,readonly=True) as mounts:plan=_snapshot(intent,identity,mounts)
        if not isinstance(reviewed_sha256,str) or reviewed_sha256!=digest(plan):raise ResourceError('Resource restore plan changed; review the current plan again')
        _restore_marker_preflight(identity,intent)
        from . import resource_postrestore
        if identity in resource_postrestore.IDENTITIES and plan['transactions'] and all(t['status']=='restored' for t in plan['transactions']):
            # The prior attempt may have died after every file was restored.
            # Re-enter only retained core code, never a now-removed launcher.
            live=firstboot.status();provider=firstboot._provider(identity)
            marker=Path('/home/.boiler/resource-maintenance/lifecycle-action.json')
            action=None
            if marker.exists():
                action=firstboot.read_private(marker)
                if (action.get('kind')!='BoilerResourceMaintenanceAction' or action.get('generation')!=intent['generation']
                        or action.get('identity')!=identity or action.get('reviewed_sha256')!=live['maintenance_restore']['reviewed_sha256']):
                    raise ResourceError('Post-restore action ownership changed')
            result=resource_postrestore.run(identity,provider,intent,_receipt(intent,identity),live)
            if action is not None and action.get('status')!='closed':_json_write(marker,dict(action,status='closed'))
            return dict(status='restored',identity=identity,post_restore=result,reboot_required=False)
        if plan.get('handler')=='boiler-input-runtime-v1':return _restore_input(identity,intent,plan,reviewed_sha256)
        if plan.get('handler')=='boiler-kde-keyboard-v1':return _restore_keyboard(intent,plan,reviewed_sha256)
        if plan.get('handler')=='boiler-flathub-mirror-v1':
            from . import flathub_restore
            return flathub_restore.start(intent,plan,reviewed_sha256)
        state=firstboot.status();firstboot.validate_restore_binding(identity,Path('/'),intent,state,verify_runtime=not plan.get('pending_restore_scopes'))
        _reconcile_reviewed(identity,state,plan)
        state['maintenance_restore']=dict(identity=identity,reviewed_sha256=reviewed_sha256,status='stopping-activation')
        _json_write(Path('/')/firstboot.STATE,state)
        row=state['resources'].setdefault(identity,{})
        completed_cpu=(identity=='resource:intel-g3e-public-cpu-controls' and
                       row.get('status') in ('activation_restored','restored'))
        cpu_historical=False
        if completed_cpu:
            from .cpu_live_resources import completed_stop
            cpu_historical=completed_stop(intent,state)['historical']
        elif (row.get('activation_recovery') and not (identity=='resource:gamma22-hdr-desktop' and row.get('status') in ('activation_restored','restored'))) or identity=='resource:intel-g3e-public-cpu-controls':
            # _restore_locked rereads only this now-reviewed, durably reconciled
            # activation chain, and applies the provider's invocation safeguards.
            firstboot._restore_locked(identity,Path('/'),intent,verify_runtime=not plan.get('pending_restore_scopes'))
        from .live_root import write_scope
        with write_scope(Engine(Path('/'))):
            with TargetMounts(intent,readonly=False,recover_window=bool(plan.get("target_window"))) as mounts:
                session,refs=_session(mounts,intent,readonly=False)
                if identity=='resource:common-opti2xmx':
                    from .opti_runtime_guard import require_clear
                    home=mounts.home/'deck';require_clear(home,home.stat().st_uid)
                    for engine in [*session.engines.values(),session.shared]:engine._opti_restore_reviewed=True
                if identity=='resource:common-waydroid':
                    from . import waydroid_activation
                    for slot,slot_root in mounts.roots.items():
                        if slot==intent['slot']:continue
                        other=firstboot.checked_intent(slot_root)
                        slot_state=firstboot.read_private(slot_root/firstboot.STATE) if (slot_root/firstboot.STATE).exists() else firstboot._new_state(other)
                        row=slot_state['resources'].get(identity) or {}
                        recovery=row.get('restore_recovery',row.get('activation_recovery'))
                        if recovery is None:continue
                        def record_waydroid(value):
                            row['restore_recovery']=copy.deepcopy(value);_json_write(slot_root/firstboot.STATE,slot_state)
                        waydroid_activation.restore_slot(slot_root,other,recovery,record_waydroid)
                        row.update(status='activation_restored',runtime='not_validated');_json_write(slot_root/firstboot.STATE,slot_state)
                    for engine in [*session.engines.values(),session.shared]:engine._waydroid_restore_reviewed=True
                receipt=session._bound_receipt(_receipt(intent,identity)['id'])
                action_path=mounts.home/'.boiler/resource-maintenance/lifecycle-action.json'
                _json_write(action_path,dict(kind='BoilerResourceMaintenanceAction',generation=intent['generation'],identity=identity,status='restoring',reviewed_sha256=reviewed_sha256))
                from .hdr_resources import check_restore
                check_restore(session,receipt)
                if identity=='resource:gamma22-hdr-desktop':
                    from .desktop_hdr_resources import check_restore as check_desktop_restore
                    check_desktop_restore(session,receipt)
                # Restore root copies first. The shared generation remains until
                # both roots match their explicitly recorded before generations.
                results=[]
                for scope,engine in [*session.engines.items(),('shared-home',session.shared)]:
                    if scope in plan.get('pending_restore_scopes',[]):
                        recovered=_recover_reviewed(engine,scope,identity,session,plan['pending_restore'])
                        results.append(dict(scope=scope,recovered=recovered))
                for scope,engine in [*session.engines.items(),('shared-home',session.shared)]:
                    if scope=='shared-home':
                        for slot,current in session.engines.items():
                            before=receipt['before_slots'][slot].get(identity)
                            if current._state()['components'].get(identity)!=before:raise ResourceError('Both root copies must be restored before shared files')
                    for transaction in reversed(receipt['transactions']):
                        if transaction['component']!=identity or transaction['scope']!=scope:continue
                        if firstboot.read_private(engine._journal(transaction['transaction'])).get('status')=='restored':continue
                        if identity=='resource:intel-g3e-public-cpu-controls':
                            journal=firstboot.read_private(engine._journal(transaction['transaction']))
                            if journal.get('component',{}).get('cpu_live_contract'):
                                result=engine.restore_cpu_live(transaction['transaction'],pool=session.pool,slots=session.slots,slot=scope,binding=session.cpu_live_binding)
                            else:result=engine.restore_cpu(transaction['transaction'],pool=session.pool,slots=session.slots,slot=scope)
                        else:result=engine.restore(transaction['transaction'])
                        results.append(dict(result,scope=scope))
                for slot,root in mounts.roots.items():
                    current_intent=firstboot.checked_intent(root)
                    current=firstboot.read_private(root/firstboot.STATE) if (root/firstboot.STATE).exists() else firstboot._new_state(current_intent)
                    current['resources'].setdefault(identity,{}).update(status='restored',runtime='not_validated',restore_transactions=copy.deepcopy(results))
                    current['status']='attention_required';current['maintenance_restore']=dict(identity=identity,status='restored',reviewed_sha256=reviewed_sha256)
                    _json_write(root/firstboot.STATE,current)
                live=firstboot.status();live_row=live['resources'].get(identity,{})
                recovery=live_row.get('activation_recovery')
                provider=firstboot._provider(identity)
                if recovery and hasattr(provider,'resume_original') and not cpu_historical:
                    if identity in resource_postrestore.IDENTITIES:
                        resource_postrestore.run(identity,provider,intent,_receipt(intent,identity),live)
                    else:
                        def record_resume(value):
                            live_row['resume_original_recovery']=copy.deepcopy(value);_json_write(Path('/')/firstboot.STATE,live)
                        live_row['resume_original_result']=provider.resume_original(identity,root=Path('/'),state=recovery,record_before_write=record_resume)
                        _json_write(Path('/')/firstboot.STATE,live)
                _json_write(action_path,dict(kind='BoilerResourceMaintenanceAction',generation=intent['generation'],identity=identity,status='closed',reviewed_sha256=reviewed_sha256))
        return dict(status='restored',identity=identity,transactions=results,reboot_required=bool(firstboot.status()['resources'].get(identity,{}).get('restore_result',{}).get('reboot_required')))


def _replace_intent(root,intent):
    """Re-journal only the fixed bootstrap's existing target set and new intent."""
    engine=Engine(root);state=engine._state()['components'].get('core:resource-firstboot')
    if not state:raise ResourceError('Bootstrap transaction is missing')
    journal=firstboot.read_private(engine._journal(state['transaction']));manifest=copy.deepcopy(journal['component'])
    if not Engine(root,read_only=True).verify_readonly('core:resource-firstboot')['ok']:raise ResourceError('Bootstrap files changed')
    with tempfile.TemporaryDirectory(prefix='boiler-bootstrap-refresh-') as directory:
        temporary=Path(directory)
        for index,entry in enumerate(manifest['files']):
            data=(json.dumps(intent,sort_keys=True,separators=(',',':'))+'\n').encode() if entry['target']==firstboot.INTENT else (root/entry['target']).read_bytes()
            source=str(index)+'.bin';(temporary/source).write_bytes(data)
            entry.update(source=source,size=len(data),sha256=hashlib.sha256(data).hexdigest())
        engine.apply(manifest,temporary)


def _prepare_retry(intent,current,mounts):
    """Build the fixed action during the initial RO target window."""
    from .resource_reinstall import pending_preflight,engines
    session,refs=_session(mounts,intent,readonly=True)
    for engine in engines(session).values():pending_preflight(engine,lambda j:False)
    restored=[i for i,r in current['resources'].items() if r.get('status')=='restored']
    inputs=[i for i in restored if session.pool.resource(i)['install']['kind'] in ('boiler-input-runtime-v1','boiler-kde-keyboard-v1')]
    ordinary=[i for i in restored if i not in inputs]
    closure=[r['id'] for r in resolve(session.pool,refs,ordinary)] if ordinary else []
    if any(session.pool.resource(i)['install']['kind'] in ('boiler-input-runtime-v1','boiler-kde-keyboard-v1') for i in closure):
        raise ResourceError('File retry closure cannot contain a live input handler')
    original={};states={}
    for slot,root in mounts.roots.items():
        existing=firstboot.checked_intent(root)
        if any(existing.get(k)!=intent.get(k) for k in ('generation','selection','target_roles')) or existing['slot']!=slot:
            raise ResourceError('Retry A/B intent ownership changed')
        state=firstboot.read_private(root/firstboot.STATE) if (root/firstboot.STATE).exists() else firstboot._new_state(existing)
        if any(state.get(k)!=existing.get(k) for k in ('generation','slot','root_partuuid')):raise ResourceError('Retry state ownership changed')
        original[slot]=existing;states[slot]={cid:copy.deepcopy(state['resources'].get(cid,{})) for cid in closure+inputs}
    token=uuid.uuid4().hex;group=uuid.uuid4().hex
    return dict(kind='BoilerResourceRetryAction',schema_version=1,token=token,status='reinstalling',phase='prepared',
        generation=intent['generation'],target_roles=copy.deepcopy(intent['target_roles']),identities=restored,
        closure=closure,inputs=inputs,group=group,original_intents=original,original_resources=states,
        original_receipts={cid:copy.deepcopy(_receipt(intent,cid)) for cid in closure},
        jobs={cid:dict(group=group,closure=closure,original_receipt=copy.deepcopy(_receipt(intent,cid))) for cid in ordinary})


def _retry_preflight(intent,action,mounts):
    from . import resource_reinstall as reinstall
    intent=_retry_intent(intent,action)
    if (action.get('kind')!='BoilerResourceRetryAction' or action.get('generation')!=intent['generation']
            or action.get('target_roles')!=intent['target_roles'] or not isinstance(action.get('token'),str)
            or not __import__('re').fullmatch('[0-9a-f]{32}',action['token'])):
        raise ResourceError('Interrupted retry belongs to another installation')
    session,refs=_session(mounts,intent,readonly=True)
    marker=mounts.home/'.boiler/resource-maintenance/lifecycle-action.json'
    if marker.exists():
        prior=firstboot.read_private(marker)
        if prior.get('status')!='closed' and (prior.get('token')!=action['token'] or prior.get('generation')!=action['generation']):
            raise ResourceError('Another shared maintenance action is pending')
    if action['phase'] in ('prepared','installing'):
        path=session.shared.state_dir/'resource-actions'/(action['group']+'.json')
        if path.exists():reinstall.inspect(session,refs,action['closure'],action['group'])
        else:
            for engine in reinstall.engines(session).values():reinstall.pending_preflight(engine,lambda j:False)
        for slot,root in mounts.roots.items():
            if firstboot.checked_intent(root)!=action['original_intents'][slot]:raise ResourceError('Retry original intent changed before file commit')
    elif action['phase'] in ('files_committed','publishing','states','closing'):
        reinstall.pending_preflight(session.shared,lambda j:False)
        # Bootstrap plans are persisted for BOTH slots before either is changed.
        if action.get('bootstrap'):
            for plan in action['bootstrap'].values():reinstall.inspect_bootstrap(plan)
        else:
            for engine in reinstall.engines(session).values():reinstall.pending_preflight(engine,lambda j:False)
            for slot,root in mounts.roots.items():
                if firstboot.checked_intent(root)!=action['original_intents'][slot]:raise ResourceError('Retry intent changed before bootstrap preparation')
        if action['closure']:
            receipt=session._bound_receipt(action['group'])
            if receipt!=action['receipt'] or receipt.get('status')!='committed':raise ResourceError('Retry committed receipt changed')
            # Only these fixed bootstrap journals may be pending. Ordinary
            # Engine.verify_readonly's global barrier must remain unchanged.
            from .cpu_resources import matches
            for row in receipt['transactions']:
                engine=reinstall.engines(session)[row['scope']]
                installed=engine._state()['components'].get(row['component'])
                journal=firstboot.read_private(engine._journal(row['transaction']))
                if (not installed or installed['transaction']!=row['transaction'] or journal.get('status')!='committed'
                        or journal.get('component',{}).get('resource_group')!=action['group']
                        or any(not matches(engine,journal['component'],op) for op in journal['operations'])):
                    raise ResourceError('Retry committed files changed')
    elif action['phase']!='closed':raise ResourceError('Unknown retry continuation phase')
    return session,refs


def _retry_intent(intent,action):
    if action.get('mode')=='initial-x2-hdr-binding-v1':
        identity='resource:gamma22-hdr-game'
        if action.get('closure')!=[identity] or action.get('identities')!=[identity] or action.get('inputs')!=[] or identity not in intent['selection']['components']:
            raise ResourceError('HDR workflow contains an unrelated resource')
        from .hdr_live_panel import validate_binding
        intent=copy.deepcopy(intent)
        intent['hdr_panel_binding']=validate_binding(action['hdr_panel_binding'])
    elif action.get('mode')=='initial-desktop-hdr-binding-v1':
        identity='resource:gamma22-hdr-desktop'
        if action.get('closure')!=[identity] or action.get('identities')!=[identity] or action.get('inputs')!=[] or identity not in intent['selection']['components']:
            raise ResourceError('Desktop HDR workflow contains an unrelated resource')
        from .desktop_hdr_panel import validate_binding
        intent=copy.deepcopy(intent)
        intent['desktop_hdr_panel_binding']=validate_binding(action['desktop_hdr_panel_binding'])
    return intent


def _continue_retry(intent,action,mounts,record):
    """Resume only the durable action, never infer work from pending rows."""
    from . import resource_reinstall as reinstall
    intent=_retry_intent(intent,action)
    _retry_preflight(intent,action,mounts)
    session,refs=_session(mounts,intent,readonly=False)
    action_path=mounts.home/'.boiler/resource-maintenance/lifecycle-action.json'
    def save():
        record(action)
        _json_write(action_path,retry_documents.compact(action,action_path))
    save() # barrier is durable before the first resource/intent scope write
    if action['phase'] in ('prepared','installing'):
        action['phase']='installing';save()
        if action['closure']:
            receipt=session.install(refs,action['closure'],group=action['group'])
            if any(not v['ok'] for v in session.verify(action['closure'])):raise ResourceError('Reinstalled resource files failed verification')
            action['receipt']=receipt
        action['phase']='files_committed';save()
    if action['phase']=='files_committed':
        bootstrap={}
        for slot,root in mounts.roots.items():
            updated=copy.deepcopy(action['original_intents'][slot])
            if action.get('mode')=='initial-x2-hdr-binding-v1':updated['hdr_panel_binding']=copy.deepcopy(action['hdr_panel_binding'])
            if action.get('mode')=='initial-desktop-hdr-binding-v1':updated['desktop_hdr_panel_binding']=copy.deepcopy(action['desktop_hdr_panel_binding'])
            for cid in action['closure']:updated.setdefault('resource_receipts',{})[cid]=copy.deepcopy(action['receipt'])
            folder=session.shared.state_dir/'resource-actions'/(action['token']+'.bootstrap')/slot
            bootstrap[slot]=reinstall.prepare_bootstrap(root,updated,action['token'],folder)
        action['bootstrap']=bootstrap;action['phase']='publishing';save()
    if action['phase']=='publishing':
        # Recheck all scope ownership before recovering the first partial slot.
        for plan in action['bootstrap'].values():reinstall.inspect_bootstrap(plan)
        for slot,plan in action['bootstrap'].items():reinstall.apply_bootstrap(plan)
        action['phase']='states';save()
    if action['phase']=='states':
        for slot,root in mounts.roots.items():
            updated=firstboot.checked_intent(root)
            state=firstboot.read_private(root/firstboot.STATE) if (root/firstboot.STATE).exists() else firstboot._new_state(updated)
            if any(state.get(k)!=updated.get(k) for k in ('generation','slot','root_partuuid')):raise ResourceError('Retry state generation changed')
            reference=retry_documents.compact(action,root/firstboot.STATE)
            for cid in action['closure']+action['inputs']:
                state['resources'][cid]=dict(status='pending',runtime='pending_firstboot' if cid in action['inputs'] else 'not_validated',
                    previous_restore=dict(document=reference,slot=slot,identity=cid),retry_token=action['token'])
            state['maintenance_retry']=reference
            _json_write(root/firstboot.STATE,state)
        action['phase']='closing';save()
    if action['phase']=='closing':
        closed=copy.deepcopy(action);closed['phase']='closed';closed['status']='closed'
        # Shared barrier stays open while both private state copies are closed.
        for root in mounts.roots.values():
            state=firstboot.read_private(root/firstboot.STATE)
            if any(state['resources'].get(cid,{}).get('retry_token')!=action['token'] for cid in action['closure']+action['inputs']):
                raise ResourceError('Retry state publication changed before close')
            state['maintenance_retry']=retry_documents.compact(closed,root/firstboot.STATE);_json_write(root/firstboot.STATE,state)
        _json_write(action_path,retry_documents.compact(closed,action_path));record(closed);action=closed
    return action


def retry():
    intent=_intent()
    with firstboot.operation_lock(Path('/')):
        marker=_keyboard_marker()
        if marker.exists():
            pending=firstboot.read_private(marker)
            if pending.get('kind')=='BoilerKeyboardRestoreAction' and pending.get('status')=='restoring':
                return _resume_keyboard_restore(intent,pending)
            if pending.get('kind')=='BoilerFlathubRestoreAction' and pending.get('status')=='restoring':
                from . import flathub_restore
                return flathub_restore.resume(intent,pending)
        current=firstboot.status();action=retry_documents.hydrate(current.get('maintenance_retry'),Path('/')/firstboot.STATE)
        keyboard=current.get('resources',{}).get('resource:common-kde-keyboard',{})
        if (keyboard.get('status') in ('activating','recovery_required') or keyboard.get('keyboard_reconcile_pending')
                or (keyboard.get('status')=='failed' and keyboard.get('activation_recovery'))):
            for component in ('core:maintenance','core:resource-firstboot'):
                if not Engine(Path('/'),read_only=True).verify_readonly(component)['ok']:
                    raise ResourceError('Retained keyboard activation runtime changed')
            keyboard['keyboard_reconcile_pending']=True;_json_write(Path('/')/firstboot.STATE,current)
            from . import keyboard_activation
            with firstboot.open_pool(Path('/'),intent) as (pool,refs):
                reconciliation=keyboard_activation.reconcile(pool,intent,root=Path('/'))
            keyboard['keyboard_reconciliation']=reconciliation
            keyboard.pop('activation_recovery',None)
            keyboard['keyboard_reconcile_pending']=False;keyboard['status']='pending'
            _json_write(Path('/')/firstboot.STATE,current)
        flat=current.get('resources',{}).get('resource:common-flathub-mirror',{})
        if flat.get('activation_recovery') and flat.get('status') in ('activating','failed','recovery_required'):
            for component in ('core:maintenance','core:resource-firstboot'):
                if not Engine(Path('/'),read_only=True).verify_readonly(component)['ok']:
                    raise ResourceError('Retained Flathub activation runtime changed')
            from . import flathub_activation,resource_activation
            original=copy.deepcopy(flat['activation_recovery'])
            def record_flat(value):
                if any(value.get(k)!=original.get(k) for k in ('generation','slot','before')):
                    raise ResourceError('Flathub original ownership changed during retry')
                flat['activation_recovery']=copy.deepcopy(value);_json_write(Path('/')/firstboot.STATE,current)
            def approve_flat(identity):
                if identity!='resource:common-flathub-mirror':raise ResourceError('Flathub approval identity changed')
                gate=Path('/var/lib/boiler-resource-approved')
                from .adoption_blobs import _directory
                with _directory(gate,create=True,create_mode=0o755):pass
                path=gate/'common-flathub-mirror.json';expected=dict(generation=intent['generation'],identity=identity)
                if path.exists() and firstboot.read_private(path)!=expected:raise ResourceError('Flathub approval gate changed')
                if not path.exists():_json_write(path,expected)
            flat['status']='activating';_json_write(Path('/')/firstboot.STATE,current)
            try:
                with firstboot.open_pool(Path('/'),intent) as (pool,refs):
                    result=flathub_activation.resume('resource:common-flathub-mirror',root=Path('/'),pool=pool,
                        facts=resource_activation.collect_facts(),selection=intent['selection']['components'],
                        state=flat['activation_recovery'],record_before_write=record_flat,approve=approve_flat)
                flat.update(result);flat['boot_id']=(Path('/proc/sys/kernel/random/boot_id')).read_text().strip()
            except Exception as error:
                flat.update(status='failed',runtime='not_validated',error=str(error)[:2048]);_json_write(Path('/')/firstboot.STATE,current);raise
            _json_write(Path('/')/firstboot.STATE,current)
        overlay=current.get('resources',{}).get('resource:common-telemetry-overlay',{})
        if overlay.get('restore_recovery') and overlay.get('status')=='recovery_required':
            firstboot._restore_locked('resource:common-telemetry-overlay',Path('/'),intent)
            current=firstboot.status();overlay=current.get('resources',{}).get('resource:common-telemetry-overlay',{})
        if overlay.get('activation_recovery') and overlay.get('status') in ('activating','failed','recovery_required'):
            for component in ('core:maintenance','core:resource-firstboot'):
                if not Engine(Path('/'),read_only=True).verify_readonly(component)['ok']:raise ResourceError('Retained overlay runtime changed')
            from . import overlay_activation,resource_activation
            original=copy.deepcopy(overlay['activation_recovery'])
            def record_overlay(value):
                if any(value.get(k)!=original.get(k) for k in ('generation','slot','holders')):raise ResourceError('Overlay ownership changed during retry')
                overlay['activation_recovery']=copy.deepcopy(value);_json_write(Path('/')/firstboot.STATE,current)
            def approve_overlay(identity):
                if identity!='resource:common-telemetry-overlay':raise ResourceError('Overlay approval identity changed')
                gate=Path('/var/lib/boiler-resource-approved')
                from .adoption_blobs import _directory
                with _directory(gate,create=True,create_mode=0o755):pass
                path=gate/'common-telemetry-overlay.json';expected=dict(generation=intent['generation'],identity=identity)
                if path.exists() and firstboot.read_private(path)!=expected:raise ResourceError('Overlay approval gate changed')
                if not path.exists():_json_write(path,expected)
            overlay['status']='activating';current['boot_id']=Path('/proc/sys/kernel/random/boot_id').read_text().strip();_json_write(Path('/')/firstboot.STATE,current)
            try:
                with firstboot.open_pool(Path('/'),intent) as (pool,refs):
                    result=overlay_activation.resume('resource:common-telemetry-overlay',root=Path('/'),pool=pool,facts=resource_activation.collect_facts(),selection=intent['selection']['components'],state=overlay['activation_recovery'],record_before_write=record_overlay,approve=approve_overlay)
                overlay.update(result);overlay['boot_id']=current['boot_id']
            except Exception as error:
                overlay.update(status='failed',runtime='not_validated',error=str(error)[:2048]);_json_write(Path('/')/firstboot.STATE,current);raise
            _json_write(Path('/')/firstboot.STATE,current)
        if action and action.get('status')=='closed':action=None
        with TargetMounts(intent,readonly=True) as mounts:
            # Root /var and shared writeahead are published separately. Pick
            # the furthest durable phase of this *same* action after a kill.
            candidates=[action] if action else []
            marker=mounts.home/'.boiler/resource-maintenance/lifecycle-action.json'
            if marker.exists():
                value=firstboot.read_private(marker)
                if value.get('kind')=='BoilerResourceRetryAction' and value.get('status')!='closed':candidates.append(retry_documents.hydrate(value,marker))
            for root in mounts.roots.values():
                if (root/firstboot.STATE).exists():
                    value=firstboot.read_private(root/firstboot.STATE).get('maintenance_retry')
                    if value and value.get('status')!='closed':candidates.append(retry_documents.hydrate(value,root/firstboot.STATE))
            if candidates:
                phases=['prepared','installing','files_committed','publishing','states','closing','closed']
                first=candidates[0]
                for value in candidates:
                    if any(value.get(k)!=first.get(k) for k in ('token','group','generation','target_roles','original_intents','original_resources','original_receipts','closure','inputs','identities','mode','hdr_panel_binding','desktop_hdr_panel_binding')):
                        raise ResourceError('Retry writeahead copies belong to different actions')
                    if value.get('phase') not in phases:raise ResourceError('Invalid retry writeahead phase')
                action=max(candidates,key=lambda value:phases.index(value['phase']))
            if action is None:
                marker=mounts.home/'.boiler/resource-maintenance/lifecycle-action.json'
                if marker.exists() and firstboot.read_private(marker).get('status')!='closed':
                    candidate=firstboot.read_private(marker)
                    if candidate.get('kind')!='BoilerResourceRetryAction':raise ResourceError('Another shared maintenance action requires its own recovery')
                    action=retry_documents.hydrate(candidate,marker)
                elif any(r.get('status')=='restored' for r in current['resources'].values()):action=_prepare_retry(intent,current,mounts)
            if action:
                _retry_preflight(intent,action,mounts)
                recover_window=bool(mounts.pending_window())
        if action:
            # Save in live /var before opening the writable target window.
            def record(value):
                state=firstboot.status();state['maintenance_retry']=retry_documents.compact(value,Path('/')/firstboot.STATE);_json_write(Path('/')/firstboot.STATE,state)
            record(action)
            from .live_root import write_scope
            with write_scope(Engine(Path('/'))):
                with TargetMounts(intent,readonly=False,recover_window=recover_window) as mounts:
                    _continue_retry(intent,action,mounts,record)
            intent=_intent()
        return firstboot._run_locked(Path('/'),intent,Path('/')/firstboot.STATE,retry_failed=True)
