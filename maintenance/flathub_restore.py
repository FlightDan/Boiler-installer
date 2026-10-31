"""Fixed two-slot Flathub restore planning; no catalog-selected executables."""
import contextlib,copy
from pathlib import Path
from .resource_runtime import ResourceError
from . import flathub_activation,flathub_transaction,flathub_user_store
from .engine import Engine,_json_write,_same
from .adoption_blobs import validate_backups
from protocol.flathub_contract import IDENTITY


@contextlib.contextmanager
def _backend_for(slot,intent,mounts,pool):
    if slot==intent['slot']:
        yield flathub_activation._backend(Path('/'),pool)
    else:
        with flathub_activation.offline_backend(mounts.roots[slot],pool) as backend:yield backend


def attach(plan,intent,mounts,session):
    from .resource_lifecycle import digest
    systems={}
    for slot in ('A','B'):
        with _backend_for(slot,intent,mounts,session.pool) as backend:
            current=backend.observe('system')
            activation=plan['slots'][slot].get('activation') or {}
            recovery=activation.get('activation_recovery')
            if recovery and activation.get('status') not in ('restored','activation_restored'):
                if recovery.get('generation')!=intent['generation'] or recovery.get('slot')!=slot:
                    raise ResourceError('Flathub slot recovery differs from installed generation')
                if not all(flathub_transaction.verify(backend,recovery['rows']).values()):
                    raise ResourceError('Flathub system remote changed after installation')
            systems[slot]=digest(current)
    live=flathub_activation._backend(Path('/'),session.pool)
    shared=flathub_user_store.Store().snapshot(live)
    ledger=shared['ledger']
    if ledger and ledger.get('generation')!=intent['generation']:
        raise ResourceError('Shared Flathub ledger belongs to another installation')
    plan['handler']='boiler-flathub-mirror-v1'
    plan['flathub']=dict(system_observations=systems,shared_observation_sha256=digest(shared),
                          shared_user_changed=bool(ledger and ledger.get('status')=='active'
                            and not all(flathub_transaction.verify(live,ledger['rows']).values())))
    plan['summary']='Restore both slot Flathub remotes and the shared user remote; preserve external edits and installed app data.'
    return plan


def _marker():return Path('/home/.boiler/resource-maintenance/lifecycle-action.json')


def _system_known(recovery,current):
    row=recovery.get('rows',{}).get('system')
    if not row:return False
    phase=row.get('phase');before=row.get('before');after=row.get('after')
    if phase in ('applied','unchanged'):return current==after
    if phase=='restored':return current==before
    if phase=='restoring':
        return current==after or current==before or (before.get('exists') and flathub_transaction._owned_url_change(before,current,before['url']))
    return False


def _preflight(intent,action,document,mounts,session,*,shared_locked=False):
    from . import resource_firstboot as firstboot
    from .resource_lifecycle import digest,_receipt
    if (document.get('generation')!=intent['generation'] or document.get('reviewed_sha256')!=action['reviewed_sha256']
            or mounts.bound!=action['plan']['roles']):raise ResourceError('Flathub restore binding changed')
    publishing=action['phase']=='publishing'
    if action['phase'] in ('files','publishing') and set(action['remote_done'])!={'A','B'}:
        raise ResourceError('Flathub remote restore is incomplete')
    for slot,root in mounts.roots.items():
        other=firstboot.checked_intent(root)
        if (any(other.get(k)!=intent.get(k) for k in ('generation','selection','target_roles')) or other['slot']!=slot
                or other.get('root_partuuid','').lower()!=intent['target_roles']['rootfs-'+slot]['partuuid'].lower()):
            raise ResourceError('Flathub restore slot identity changed')
        entry=document['slots'][slot];path=root/firstboot.STATE;exists=path.exists()
        state=firstboot.read_private(path) if exists else firstboot._new_state(other)
        if not (exists==entry['before_exists'] and state==entry['before']) and not (publishing and exists and state==entry['after']):
            raise ResourceError('Flathub restore slot state changed outside this action')
        gate=root/'var/lib/boiler-resource-approved/common-flathub-mirror.json';current=firstboot.read_private(gate) if gate.exists() else None
        if current!=entry['gate_before'] and not (publishing and current is None):raise ResourceError('Flathub approval gate changed')
        recovery=action['recovery'].get(slot)
        with _backend_for(slot,intent,mounts,session.pool) as backend:
            system=backend.observe('system')
            if recovery and not _system_known(recovery,system):raise ResourceError('Flathub system remote changed outside this action')
            if action['phase']=='prepared' and digest(system)!=action['plan']['flathub']['system_observations'][slot]:
                raise ResourceError('Flathub system remote changed after review')
    if not shared_locked:
        live=flathub_activation._backend(Path('/'),session.pool);shared=flathub_user_store.Store().snapshot(live)
        ledger=shared['ledger']
        if ledger and ledger.get('generation')!=intent['generation']:raise ResourceError('Shared Flathub ownership changed')
        if action['phase']=='prepared' and digest(shared)!=action['plan']['flathub']['shared_observation_sha256']:
            raise ResourceError('Shared Flathub remote changed after review')
    receipt=session._bound_receipt(_receipt(intent,IDENTITY)['id'])
    if receipt!=_receipt(intent,IDENTITY) or digest(receipt)!=action['plan']['receipt_sha256']:
        raise ResourceError('Flathub file receipt changed')
    for scope,engine in [*session.engines.items(),('shared-home',session.shared)]:
        allowed={r['transaction'] for r in receipt['transactions'] if r['scope']==scope and r['component']==IDENTITY}
        for path in engine.journals.glob('*.json'):
            journal=firstboot.read_private(path)
            if journal.get('status') not in ('committed','restored','rolled_back') and (path.stem not in allowed or journal.get('status')!='restoring'):
                raise ResourceError('Unrelated pending transaction blocks Flathub restore')
        for transaction_id in allowed:
            journal=firstboot.read_private(engine._journal(transaction_id));status=journal.get('status')
            if status not in ('committed','restoring','restored') or journal.get('component',{}).get('resource_group')!=receipt['id']:
                raise ResourceError('Flathub file journal changed')
            if publishing and status!='restored':raise ResourceError('Flathub file restore is incomplete')
            validate_backups(engine,journal['operations'])
            for operation in journal['operations']:
                actual=engine._observed(engine._target(operation['target']),operation['before'],operation['after'])
                if status=='committed' and not _same(actual,operation['after']):raise ResourceError('Flathub installed file changed')
                if status=='restored' and not _same(actual,operation['before']):raise ResourceError('Flathub restored file changed')
                if status=='restoring' and not (_same(actual,operation['before']) or _same(actual,operation['after'])):
                    raise ResourceError('Interrupted Flathub file restore conflicts')
    return receipt


def start(intent,plan,reviewed_sha256):
    from . import resource_firstboot as firstboot,flathub_restore_docs
    from .resource_lifecycle import _session,digest
    from .resource_targets import TargetMounts
    marker=_marker()
    if marker.exists() and firstboot.read_private(marker).get('status')!='closed':raise ResourceError('Another shared resource action requires retry')
    with TargetMounts(intent,readonly=True) as mounts:
        session,refs=_session(mounts,intent,readonly=True)
        ref=flathub_restore_docs.create(intent,plan,reviewed_sha256,mounts,marker)
        document=flathub_restore_docs.load(ref,marker)
        recovery={slot:copy.deepcopy((document['slots'][slot]['before'].get('resources',{}).get(IDENTITY) or {}).get('activation_recovery')) for slot in ('A','B')}
        action=dict(kind='BoilerFlathubRestoreAction',generation=intent['generation'],identity=IDENTITY,status='restoring',
                    phase='prepared',reviewed_sha256=reviewed_sha256,plan=copy.deepcopy(plan),document=ref,recovery=recovery,remote_done={})
        _preflight(intent,action,document,mounts,session)
    _json_write(marker,action)
    return resume(intent,action)


def resume(intent,action):
    from . import resource_firstboot as firstboot,flathub_restore_docs
    from .resource_lifecycle import _session,_recover_reviewed,digest
    from .resource_targets import TargetMounts
    from .live_root import write_scope
    marker=_marker()
    if (action.get('kind')!='BoilerFlathubRestoreAction' or action.get('generation')!=intent['generation']
            or action.get('identity')!=IDENTITY or action.get('status')!='restoring'
            or action.get('phase') not in ('prepared','remote','files','publishing')
            or digest(action.get('plan'))!=action.get('reviewed_sha256') or set(action.get('recovery',{}))!={'A','B'}
            or set(action.get('remote_done',{})) - {'A','B'}):raise ResourceError('Invalid Flathub restore action')
    document=flathub_restore_docs.load(action['document'],marker)
    with TargetMounts(intent,readonly=True) as mounts:
        session,refs=_session(mounts,intent,readonly=True);_preflight(intent,action,document,mounts,session)
        pending_window=bool(mounts.pending_window())
    with write_scope(Engine(Path('/'))):
        with TargetMounts(intent,readonly=False,recover_window=pending_window) as mounts:
            session,refs=_session(mounts,intent,readonly=False)
            receipt=_preflight(intent,action,document,mounts,session)
            if action['phase'] in ('prepared','remote'):
                for slot in ('B' if intent['slot']=='A' else 'A',intent['slot']):
                    if slot in action['remote_done']:continue
                    recovery=action['recovery'][slot]
                    if recovery is None:
                        action['remote_done'][slot]={'status':'not-activated'};_preflight(intent,action,document,mounts,session);_json_write(marker,action);continue
                    action['phase']='remote';_preflight(intent,action,document,mounts,session);_json_write(marker,action)
                    with _backend_for(slot,intent,mounts,session.pool) as backend:
                        def record(value):
                            action['recovery'][slot]=copy.deepcopy(value);_preflight(intent,action,document,mounts,session);_json_write(marker,action)
                        def record_user(value):
                            # Store.release holds its exclusive ledger lock during this callback.
                            action['recovery'][slot]=copy.deepcopy(value);_preflight(intent,action,document,mounts,session,shared_locked=True);_json_write(marker,action)
                        value=flathub_activation.restore_bound(backend,recovery,record_before_write=record,
                            partuuid=intent['target_roles']['rootfs-'+slot]['partuuid'],record_user_before_write=record_user)
                    action['recovery'][slot]=value;action['remote_done'][slot]={'status':'restored','user':value.get('user_restore')}
                    _preflight(intent,action,document,mounts,session);_json_write(marker,action)
                action['phase']='files';_preflight(intent,action,document,mounts,session);_json_write(marker,action)
            if action['phase']=='files':
                for scope,engine in [*session.engines.items(),('shared-home',session.shared)]:
                    for row in reversed(receipt['transactions']):
                        if row['component']!=IDENTITY or row['scope']!=scope:continue
                        journal=firstboot.read_private(engine._journal(row['transaction']))
                        if journal['status']=='restoring':
                            _preflight(intent,action,document,mounts,session)
                            _recover_reviewed(engine,scope,IDENTITY,session,[dict(scope=scope,transaction=row['transaction'],journal_sha256=digest(journal))])
                            journal=firstboot.read_private(engine._journal(row['transaction']))
                        if journal['status']=='restored':continue
                        _preflight(intent,action,document,mounts,session);engine.restore(row['transaction'])
                action['phase']='publishing';_preflight(intent,action,document,mounts,session);_json_write(marker,action)
            for slot,root in mounts.roots.items():
                _preflight(intent,action,document,mounts,session)
                path=root/firstboot.STATE;after=document['slots'][slot]['after']
                if not path.exists() or firstboot.read_private(path)!=after:_json_write(path,after)
                gate=root/'var/lib/boiler-resource-approved/common-flathub-mirror.json'
                if gate.exists():_preflight(intent,action,document,mounts,session);gate.unlink()
            _preflight(intent,action,document,mounts,session)
            action.update(status='closed',phase='closed');_json_write(marker,action)
    return dict(status='restored',identity=IDENTITY,slots=action['remote_done'],network_validated=False)
