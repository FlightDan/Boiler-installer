"""Fixed CPU live component identity and a separate file-permit type.

The permit is issued only after an owned target binding revalidation. Runtime
hardware writes are never authorized by this file deployment capability.
"""
from pathlib import Path
import copy, json, os, subprocess
from protocol.cpu_contract import IDENTITY, COMMIT, CONFIG, POLICY, TARGETS
from protocol.cpu_live_contract import KIND

MARKER = {'kind': KIND, 'public_commit': COMMIT}
_KEY = object()


def fixed_component(component):
    if (not isinstance(component, dict) or component.get('id') != IDENTITY or
        component.get('cpu_live_contract') != MARKER or component.get('cpu_mutable_config') != POLICY or
        component.get('hooks') or component.get('dynamic_handler')):
        raise ValueError('Invalid fixed live CPU component marker or policy')
    rows=component.get('files')
    if not isinstance(rows,list) or len(rows)!=8 or any(not isinstance(r,dict) for r in rows):
        raise ValueError('Invalid fixed live CPU component inventory')
    if {r.get('target') for r in rows} != set(TARGETS):
        raise ValueError('CPU live component target set differs')
    for row in rows:
        if (row.get('sha256') != TARGETS[row['target']] or type(row.get('mode')) is not int or
            row['mode'] != (0o600 if row['target'] == CONFIG else 0o644)):
            raise ValueError('CPU live component bytes or mode differ')
    return component


class _CpuLiveFilePermit:
    def __init__(self, key, engine, pool, slots, slot, operation, binding):
        if key is not _KEY: raise ValueError('Fixed CPU live authorization is required')
        self.root, self.pool, self.slots, self.slot = engine.root, pool, slots, slot
        self.operation, self.binding = operation, binding


class _OfflineBinding:
    def __init__(self, key, authorization, mounts, reviewed):
        if key is not _KEY: raise ValueError('Guest target authorization required')
        self.authorization, self.mounts = authorization, mounts
        self.reviewed = copy.deepcopy(reviewed)

    def revalidate(self, roots, *, write):
        from resource_coordinator import PreparedMounts, checked_parts
        from resource_local_session import LocalSessionAuthorization
        if (type(self.authorization) is not LocalSessionAuthorization or type(self.mounts) is not PreparedMounts or
            (write and (self.mounts.readonly or self.authorization.action != 'apply')) or not self.mounts.mounts or
            self.authorization.binding.get('execution') != 'firstboot-install-v1' or
            self.authorization.hardware or self.authorization.reviewed != self.reviewed or
            IDENTITY not in self.reviewed.get('components', []) or
            os.name != 'posix' or os.geteuid() != 0):
            raise ValueError('Offline CPU binding is not the owned writable guest session')
        if Path('/sys/class/dmi/id/sys_vendor').read_text().strip() != 'QEMU' or Path('/sys/class/block/vdc/ro').read_text().strip() != ('0' if self.authorization.action == 'apply' else '1'):
            raise ValueError('Offline CPU deployment requires the writable owned preparation VM')
        table=json.loads(subprocess.check_output(['sfdisk','--json','/dev/vdc']))['partitiontable']
        if checked_parts(table) != self.mounts.parts:
            raise ValueError('Mounted CPU target differs from the authenticated image')
        self.authorization.revalidate('/dev/vdc', copy.deepcopy(self.reviewed), table)
        if any(Path(self.mounts.roots[s]).resolve() != roots[s] or not Path(self.mounts.roots[s]).is_mount()
               for s in ('A','B')):
            raise ValueError('Offline CPU target mounts changed')
        if any(os.path.samefile(roots[s], '/') for s in ('A','B')):
            raise ValueError('Offline CPU deployment cannot write live root')


def offline_binding(authorization, mounts, reviewed):
    from resource_coordinator import PreparedMounts
    from resource_local_session import LocalSessionAuthorization
    if type(authorization) is not LocalSessionAuthorization or type(mounts) is not PreparedMounts:
        raise ValueError('Guest CPU binding requires fixed authenticated owner objects')
    return _OfflineBinding(_KEY, authorization, mounts, reviewed)


def completed_stop(intent, state, *, binding=None, operation='restore/recover'):
    """Authenticate the retained stop; this grants files-only authority."""
    from maintenance.resource_access import operation as held_operation
    from maintenance.resource_firstboot import read_private, STATE, open_pool
    from maintenance.resource_activation import LiveSystem
    from maintenance.engine import Engine
    from maintenance.cpu_resources import matches
    from maintenance import cpu_sidecar_store
    if held_operation.get()!='/':raise ValueError('CPU stop proof requires operation lock')
    if IDENTITY not in intent.get('selection',{}).get('components',[]):raise ValueError('CPU is not selected')
    engine=Engine(Path('/'),read_only=True);row=state.get('resources',{}).get(IDENTITY,{})
    proof=row.get('restore_result',{});review=state.get('maintenance_restore',{})
    if (any(state.get(k)!=intent.get(k) for k in ('generation','slot','root_partuuid')) or
        row.get('status') not in ('activation_restored','restored') or
        proof.get('kind')!='BoilerCpuLiveRestore' or proof.get('phase')!='stopped' or
        proof.get('generation')!=intent['generation'] or review.get('identity')!=IDENTITY or
        not isinstance(review.get('reviewed_sha256'),str) or not __import__('re').fullmatch('[0-9a-f]{64}',review['reviewed_sha256'])):
        raise ValueError('CPU completed stop proof differs')
    receipt=intent.get('resource_receipts',{}).get(IDENTITY,intent.get('resource_receipt',{}))
    rows=[r for r in receipt.get('transactions',[]) if r.get('component')==IDENTITY and r.get('scope')==intent['slot']]
    if len(rows)!=1 or proof.get('transaction')!=rows[0].get('transaction'):
        raise ValueError('CPU stop proof receipt differs')
    session=refs=None
    if binding is not None:
        from maintenance import resource_lifecycle as lifecycle
        session,refs=lifecycle._session(binding,intent,readonly=True)
        if session._bound_receipt(receipt['id'])!=receipt:
            raise ValueError('CPU retained receipt differs')
    tx=proof['transaction'];journal=read_private(engine._journal(tx));fixed_component(journal.get('component'))
    if (journal.get('id'),journal.get('root'))!=(tx,engine.identity) or journal.get('status') not in ('committed','restoring','restored'):
        raise ValueError('CPU retained journal differs')
    # Verify retained core directly: ordinary verify_readonly rejects the known
    # interrupted restoring journal globally, even when these snapshots match.
    for cid in ('core:maintenance','core:resource-firstboot'):
        installed=engine._state()['components'].get(cid)
        if not installed:raise ValueError('CPU retained core is missing')
        core=read_private(engine._journal(installed['transaction']))
        if (core.get('id')!=installed['transaction'] or core.get('root')!=engine.identity or core.get('status')!='committed' or
            core.get('component',{}).get('id')!=cid or any(not matches(engine,core['component'],op) for op in core['operations'])):
            raise ValueError('CPU retained core differs')
    with open_pool(Path('/'),intent) as (pool,refs):
        from protocol.cpu_live_contract import validate_contract
        validate_contract(pool.resource(IDENTITY))
    gate=Path('/var/lib/boiler-resource-approved/intel-g3e-public-cpu-controls.json')
    public=Path('/run/oxp3-cpu/state.json')
    if gate.exists() or gate.is_symlink() or public.exists() or public.is_symlink():raise ValueError('CPU gate or public state remains')
    system=LiveSystem();boot=system.boot_id();historical=proof.get('boot_id')!=boot
    from maintenance.cpu_sidecar_ledger import BOOT
    if not isinstance(proof.get('boot_id'),str) or not BOOT.fullmatch(proof['boot_id']):
        raise ValueError('CPU stopped boot identity differs')
    try:ledger=cpu_sidecar_store._snapshot_with_history(Path('/'))
    except FileNotFoundError:ledger=None
    if proof.get('metadata_only') is True:
        if ledger is not None or proof.get('invocation') is not None:raise ValueError('CPU metadata-only stop differs')
    else:
        attempts=ledger.get('attempts',[]) if isinstance(ledger,dict) else []
        if ((ledger or {}).get('generation')!=intent['generation'] or (ledger or {}).get('transaction')!=tx or not attempts or
            (attempts[-1].get('boot'),attempts[-1].get('invocation'),attempts[-1].get('phase'))!=
            (proof.get('boot_id'),proof.get('invocation'),'stopped') or
            (historical and any(a.get('boot')==boot for a in attempts))):
            raise ValueError('CPU stopped ledger differs')
    current=engine._state()['components'].get(IDENTITY);replacement=None
    if operation=='reviewed-reinstall' or (current and current.get('transaction')!=tx and state.get('maintenance_retry')):
        if binding is None:raise ValueError('CPU retry lease is missing')
        from maintenance import resource_lifecycle as lifecycle, resource_reinstall as reinstall, retry_documents
        action=retry_documents.hydrate(state.get('maintenance_retry'),Path('/')/STATE)
        if (not isinstance(action,dict) or action.get('phase')!='installing' or action.get('generation')!=intent['generation'] or
            action.get('target_roles')!=intent.get('target_roles') or action.get('original_receipts',{}).get(IDENTITY)!=receipt or
            IDENTITY not in action.get('closure',[])):
            raise ValueError('CPU historical retry action differs')
        retry_documents._identity(action)
        session,refs=lifecycle._retry_preflight(intent,action,binding)
        record,plan,journals,pending=reinstall.inspect(session,refs,action['closure'],action['group'])
        if record.get('id')!=action['group']:raise ValueError('CPU retry group differs')
        for slot,owner in session.engines.items():
            old=[r for r in receipt.get('transactions',[]) if r.get('scope')==slot and r.get('component')==IDENTITY]
            if len(old)!=1:raise ValueError('CPU retry old receipt differs')
            oldjournal=read_private(owner._journal(old[0]['transaction']));fixed_component(oldjournal.get('component'))
            if (oldjournal.get('id'),oldjournal.get('root'),oldjournal.get('status'))!=(old[0]['transaction'],owner.identity,'restored'):
                raise ValueError('CPU retry old journal differs')
            new=journals.get((slot,IDENTITY));installed=owner._state()['components'].get(IDENTITY)
            if installed and (not new or new.get('status') not in ('applying','committed') or installed.get('transaction')!=new.get('id')):raise ValueError('CPU retry replacement differs')
            if new:fixed_component(new['component'])
        replacement=journals.get((intent['slot'],IDENTITY))
        if replacement and replacement.get('status') not in ('applying','committed'):replacement=None
    if current and current.get('transaction')!=tx and (not replacement or current.get('transaction')!=replacement.get('id')):
        raise ValueError('CPU installed transaction differs')
    unit=system.service('oxp3-cpu.service')
    if unit not in ('inactive','failed'):
        path=Path('/')/'etc/systemd/system/oxp3-cpu.service'
        # A stopped unit may be unloaded after its file is restored away, even
        # on the same boot. The exact restoring/restored old journal and absent
        # unit authorize only the remaining file recovery, never CPU policy.
        if (unit!='unknown' or journal.get('status') not in ('restoring','restored') or
            ((path.exists() or path.is_symlink()) and not replacement)):
            raise ValueError('CPU service is not quiescent')
    return {'files_only':True,'historical':historical}


def authorize_files(engine, pool, slots, slot, *, operation, binding):
    """Binding is a fixed target-owner capability, not resource-provided facts."""
    from protocol.cpu_live_contract import validate_contract
    from maintenance.resource_targets import TargetMounts
    if operation not in ('offline-install', 'reviewed-reinstall', 'restore/recover'):
        raise ValueError('Unsupported CPU file operation')
    if engine.target_domain != 'root' or set(slots) != {'A','B'} or slot not in slots:
        raise ValueError('CPU live file authorization requires both target slots')
    roots = {s: Path(slots[s]['root']).resolve() for s in ('A','B')}
    if roots['A'] == roots['B'] or roots[slot] != engine.root:
        raise ValueError('CPU live target identity differs')
    validate_contract(pool.resource(IDENTITY))
    if type(binding) is _OfflineBinding:
        if operation not in ('offline-install', 'restore/recover'):
            raise ValueError('Offline CPU binding cannot authorize live reinstall')
        if binding.authorization.pool.resource(IDENTITY) != pool.resource(IDENTITY):
            raise ValueError('CPU resource differs from authenticated source')
        binding.revalidate(roots,write=True)
        return _CpuLiveFilePermit(_KEY, engine, pool, slots, slot, operation, binding)
    if operation == 'offline-install': raise ValueError('Offline CPU target binding required')
    # Live maintenance stays closed until its durable controlled-stop proof is
    # bound by the CPU provider and reviewed lifecycle.
    if type(binding) is not TargetMounts or binding.readonly or not binding.mounts:
        raise ValueError('A live target-owner lease is required')
    if any(Path(binding.roots[s]).resolve() != roots[s] for s in roots):
        raise ValueError('CPU target lease roots changed')
    binding.revalidate()
    from maintenance.resource_access import operation as held_operation, write_scope as held_write_scope
    if held_operation.get() != '/' or held_write_scope.get() != '/':
        raise ValueError('CPU live maintenance requires its held operation lock and live write scope')
    from maintenance.resource_firstboot import checked_intent, read_private, STATE
    from maintenance.live_root import RootCommands
    intent=checked_intent(Path('/'),identity=RootCommands().identity()['partuuid'])
    if any(intent.get(k)!=binding.intent.get(k) for k in ('generation','slot','root_partuuid','target_roles')):
        raise ValueError('CPU live target lease belongs to a different generation')
    state=read_private(Path('/')/STATE)
    completed_stop(intent,state,binding=binding,operation=operation)
    return _CpuLiveFilePermit(_KEY,engine,pool,slots,slot,operation,binding)


def permitted(engine, permit, component=None, *, recovery):
    if type(permit) is not _CpuLiveFilePermit or permit.root != engine.root:
        return False
    if not recovery and permit.operation == 'restore/recover': return False
    authorize_files(engine, permit.pool, permit.slots, permit.slot,
                    operation=permit.operation, binding=permit.binding)
    if component is not None: fixed_component(component)
    return True






