"""Fixed public CPU service lifecycle bound to the per-invocation sidecar."""
import copy
import re
from pathlib import Path
from maintenance import cpu_live_probe, cpu_sidecar, cpu_sidecar_store
from maintenance.resource_activation import LiveSystem
from protocol.cpu_contract import IDENTITY
from protocol.cpu_live_contract import validate_contract

UNIT='oxp3-cpu.service'
class CpuActivationError(RuntimeError): pass


def _validate(identity,root,pool,selection):
    if identity!=IDENTITY or Path(root)!=Path('/') or identity not in set(selection):
        raise CpuActivationError('CPU activation requires the fixed selected live resource and actual root')
    validate_contract(pool.resource(identity))


def _completed(intent,transaction,system,probe):
    state=cpu_sidecar_store._snapshot(Path('/'))
    invocation=system.invocation(UNIT);boot=system.boot_id()
    if (state.get('generation')!=intent['generation'] or state.get('transaction')!=transaction or
        not state.get('attempts') or system.service(UNIT)!='active'):
        raise CpuActivationError('CPU service or sidecar generation differs')
    entry=state['attempts'][-1]
    if (entry.get('boot'),entry.get('invocation'),entry.get('phase'))!=(boot,invocation,'completed'):
        raise CpuActivationError('CPU service invocation has no completed sidecar record')
    if entry.get('expected_config_sha256')!=probe['config_sha256'] or entry.get('after')!=cpu_sidecar._observations(probe):
        raise CpuActivationError('CPU saved configuration or actual policy differs from its managed invocation')
    rc,detail=system.run('systemctl','show',UNIT,'--property=Result','--property=ExecMainStatus',check=False)
    if rc or 'Result=success' not in detail.splitlines() or 'ExecMainStatus=0' not in detail.splitlines():
        raise CpuActivationError('CPU oneshot result is not successful')
    return invocation


def activate(identity, *, root=Path('/'),pool,facts,selection,record_before_write,approve,system=None):
    _validate(identity,root,pool,selection)
    if not callable(record_before_write) or not callable(approve):raise CpuActivationError('CPU durable recorder and approval are required')
    system=system or LiveSystem(root)
    intent,transaction=cpu_sidecar._current_root(require_gate=False)
    cpu_sidecar._dependencies();probe=cpu_live_probe.probe()
    before=system.service(UNIT)
    if before not in ('active','inactive','failed','unknown'):raise CpuActivationError('Unexpected original CPU service state')
    original=system.invocation(UNIT) if before=='active' else None
    if before=='active' and (not isinstance(original,str) or not re.fullmatch('[0-9a-f]{32}',original)):
        raise CpuActivationError('Original CPU service invocation is unavailable')
    prior_public=cpu_sidecar._public_state() if before=='active' else None
    # A new installation must not adopt a pre-existing CPU controller or its
    # transient state. Reuse an active service only when this generation's
    # same-boot sidecar explicitly owns the exact completed invocation.
    public_any=prior_public if before=='active' else cpu_sidecar._public_state()
    if before=='active' or public_any is not None:
        try: existing=cpu_sidecar_store._snapshot(root)
        except (FileNotFoundError, cpu_sidecar_store.ledger.LedgerError): existing=None
        last=existing.get('attempts',[])[-1] if existing and existing.get('attempts') else None
        if (before!='active' or not last or (existing.get('generation'),existing.get('transaction'))!=(intent['generation'],transaction) or
            (last.get('boot'),last.get('invocation'),last.get('phase'))!=(system.boot_id(),original,'completed')):
            raise CpuActivationError('An external CPU controller or public state is present; stop and clear it before activation')
    if before=='active':
        from maintenance import cpu_sidecar_ledger
        cpu_sidecar_ledger.expected_after(cpu_sidecar._observations(probe),{},prior_public)
    recovery={'kind':'BoilerCpuLiveRecovery','schema_version':1,'identity':IDENTITY,
              'generation':intent['generation'],'transaction':transaction,'boot_id':system.boot_id(),
              'initial_boot_id':system.boot_id(),'services':[{'user':False,'unit':UNIT,'before':before,
              'started':False,'start_pending':False,'invocation':None}], 'original_invocation':original,
              'initial_observations':cpu_sidecar._observations(probe),'initial_public_state':prior_public}
    record_before_write(copy.deepcopy(recovery));approve(identity)
    system.run('systemctl','daemon-reload')
    recovery['services'][0]['start_pending']=True;record_before_write(copy.deepcopy(recovery))
    system.run('systemctl','restart' if before=='active' else 'start',UNIT)
    probe=cpu_live_probe.probe();invocation=_completed(intent,transaction,system,probe)
    recovery['services'][0].update(started=True,start_pending=False,invocation=invocation)
    record_before_write(copy.deepcopy(recovery))
    return {'status':'active','runtime':'validated','policy':'enabled' if probe['enabled'] else 'disabled',
            'recovery':recovery,'evidence':{'invocation':invocation,'boot_id':system.boot_id()}}


def verify(identity, *, root=Path('/'),pool,facts,selection,state=None,system=None):
    _validate(identity,root,pool,selection)
    system=system or LiveSystem(root)
    intent,transaction=cpu_sidecar._current_root();cpu_sidecar._dependencies();probe=cpu_live_probe.probe()
    try:
        invocation=_completed(intent,transaction,system,probe)
    except CpuActivationError:
        # Public plugin unload directly calls restore while leaving the unit
        # active. It removes the public runtime state and disables the config.
        # Report this observed disabled state without claiming it was a unit
        # restart or writing any value back over an external change.
        if probe['enabled'] or cpu_sidecar._public_state() is not None:raise
        ledger=cpu_sidecar_store._snapshot(root);boot=system.boot_id();invocation=system.invocation(UNIT)
        if (ledger.get('generation')!=intent['generation'] or ledger.get('transaction')!=transaction or
            not ledger.get('attempts') or system.service(UNIT)!='active'):
            raise CpuActivationError('CPU disabled state lacks its retained invocation')
        attempt=ledger['attempts'][-1]
        if (attempt.get('boot'),attempt.get('invocation'),attempt.get('phase'))!=(boot,invocation,'completed'):
            raise CpuActivationError('CPU disabled state has no completed sidecar record')
        rc,detail=system.run('systemctl','show',UNIT,'--property=Result','--property=ExecMainStatus',check=False)
        if rc or 'Result=success' not in detail.splitlines() or 'ExecMainStatus=0' not in detail.splitlines():
            raise CpuActivationError('CPU service result differs')
        current=cpu_sidecar._observations(probe);first=ledger['boots'][boot]
        if set(current)!=set(attempt['before']) or set(current)!=set(first):raise CpuActivationError('CPU policy identity changed')
        external=sorted(p for p in current if current[p] not in {first[p],attempt['before'][p],attempt['after'][p]})
        return {'status':'configured','runtime':'public-state-absent','policy':'disabled',
                'evidence':{'invocation':invocation,'boot_id':boot,'external_preserved':external}}
    return {'status':'active','runtime':'validated','policy':'enabled' if probe['enabled'] else 'disabled',
            'evidence':{'invocation':invocation,'boot_id':system.boot_id()}}


def resume_boot(identity, *, root=Path('/'),pool,facts,selection,state,record_before_write,approve,system=None):
    if not isinstance(state,dict) or state.get('kind')!='BoilerCpuLiveRecovery':raise CpuActivationError('Missing CPU original recovery')
    return activate(identity,root=root,pool=pool,facts=facts,selection=selection,
                    record_before_write=record_before_write,approve=approve,system=system)


def preflight_restore(identity, *, root=Path('/'),state,system=None):
    if identity!=IDENTITY or Path(root)!=Path('/') or not isinstance(state,dict) or state.get('kind')!='BoilerCpuLiveRecovery':
        raise CpuActivationError('Invalid CPU restore state')
    system=system or LiveSystem(root);intent,transaction=cpu_sidecar._current_root(verify_dependencies=False);probe=cpu_live_probe.probe_observe()
    if (state.get('generation'),state.get('transaction'))!=(intent['generation'],transaction):
        raise CpuActivationError('CPU restoration generation differs')
    ledger=cpu_sidecar_store._snapshot(root)
    invocation=system.invocation(UNIT);boot=system.boot_id()
    if (ledger.get('generation')!=intent['generation'] or ledger.get('transaction')!=transaction or
        not ledger.get('attempts') or system.service(UNIT)!='active'):
        raise CpuActivationError('CPU service or ownership generation differs')
    attempt=ledger['attempts'][-1]
    if (attempt.get('boot'),attempt.get('invocation'),attempt.get('phase'))!=(boot,invocation,'completed'):
        raise CpuActivationError('CPU restore invocation is not sidecar-owned')
    current=cpu_sidecar._observations(probe)
    if set(current)!=set(attempt['after']): raise CpuActivationError('CPU policy identity changed')
    external=sorted(p for p in current if current[p]!=attempt['after'][p])
    return {'generation':intent['generation'],'transaction':transaction,'boot_id':boot,'invocation':invocation,'external_before_stop':external}


def restore(identity, *, root=Path('/'),state,record_before_write,system=None,prior_restore=None):
    if identity!=IDENTITY or Path(root)!=Path('/') or not callable(record_before_write):raise CpuActivationError('CPU restore recorder and fixed root required')
    system=system or LiveSystem(root)
    # A failed public apply may leave a prepared sidecar attempt. Recover it
    # through the fixed public transaction under its own lock and gate.
    try: pending=cpu_sidecar_store._snapshot(root)
    except FileNotFoundError: pending=None
    if pending is None and system.service(UNIT) in ('inactive','failed') and cpu_sidecar._public_state() is None:
        # A dependency/probe/ExecStartPre failure can precede the first sidecar
        # intent. No CPU invocation could then have applied policy. Permit
        # journaled file removal without demanding the failed dependency or
        # inventing any hardware before-state.
        intent,transaction=cpu_sidecar._current_root(require_gate=False,verify_dependencies=False)
        if state is not None and (state.get('kind')!='BoilerCpuLiveRecovery' or
            (state.get('generation'),state.get('transaction'))!=(intent['generation'],transaction)):
            raise CpuActivationError('CPU unstarted restoration generation differs')
        proof=dict(kind='BoilerCpuLiveRestore',phase='stopped',generation=intent['generation'],transaction=transaction,
            boot_id=system.boot_id(),invocation=None,metadata_only=True)
        record_before_write(copy.deepcopy(proof));return proof
    if pending and pending.get('attempts') and pending['attempts'][-1].get('phase')=='prepared':
        from maintenance import cpu_sidecar_recovery
        return cpu_sidecar_recovery.recover(state=state,record_before_write=record_before_write,system=system,prior_restore=prior_restore)
    # A process can exit after the synchronous stop completed but before the
    # outer resource receipt was published.  Resume only the exact prior stop,
    # confirmed by the sidecar's durable stopped record and this boot.
    if isinstance(prior_restore,dict) and prior_restore.get('phase') in ('stopping','stopped','reconciling') and system.service(UNIT) in ('inactive','failed'):
        if identity!=IDENTITY or Path(root)!=Path('/') or not isinstance(state,dict) or state.get('kind')!='BoilerCpuLiveRecovery':
            raise CpuActivationError('Invalid CPU restore state')
        intent,transaction=cpu_sidecar._current_root(require_gate=False,verify_dependencies=False)
        ledger=cpu_sidecar_store._snapshot(root);attempt=ledger['attempts'][-1];boot=system.boot_id()
        if (prior_restore.get('kind')!='BoilerCpuLiveRestore' or
            (state.get('generation'),state.get('transaction'))!=(intent['generation'],transaction) or
            (prior_restore.get('generation'),prior_restore.get('transaction'),prior_restore.get('boot_id'))!=(intent['generation'],transaction,boot) or
            (ledger.get('generation'),ledger.get('transaction'))!=(intent['generation'],transaction) or
            (attempt.get('boot'),attempt.get('invocation'),attempt.get('phase'))!=(boot,prior_restore.get('invocation'),'stopped')):
            raise CpuActivationError('Interrupted CPU stop proof differs')
        result=dict(prior_restore,phase='stopped',external_preserved=attempt.get('external',[]))
        record_before_write(copy.deepcopy(result));return result
    before=preflight_restore(identity,root=root,state=state,system=system)
    record_before_write(dict(kind='BoilerCpuLiveRestore',phase='stopping',**before))
    system.run('systemctl','stop',UNIT)
    if system.service(UNIT) not in ('inactive','failed'):raise CpuActivationError('CPU service did not stop')
    ledger=cpu_sidecar_store._snapshot(root);attempt=ledger['attempts'][-1]
    if (attempt.get('boot'),attempt.get('invocation'),attempt.get('phase'))!=(before['boot_id'],before['invocation'],'stopped'):
        raise CpuActivationError('CPU poststop sidecar is missing')
    result={'kind':'BoilerCpuLiveRestore','phase':'stopped',**before,'external_preserved':attempt.get('external',[])}
    record_before_write(copy.deepcopy(result))
    return result


def resume_original(identity, *, root=Path('/'),state,record_before_write,system=None):
    if identity!=IDENTITY or not isinstance(state,dict) or state.get('kind')!='BoilerCpuLiveRecovery':
        raise CpuActivationError('Invalid original CPU service state')
    rows=state.get('services',[])
    if len(rows)!=1 or rows[0].get('unit')!=UNIT:raise CpuActivationError('Invalid original CPU service record')
    if rows[0].get('before')=='active':
        if not callable(record_before_write):raise CpuActivationError('CPU resume recorder required')
        record_before_write({'kind':'BoilerCpuOriginalResume','phase':'starting','unit':UNIT})
        system=system or LiveSystem(root);system.run('systemctl','daemon-reload');system.run('systemctl','start',UNIT)
        if system.service(UNIT)!='active':raise CpuActivationError('Original CPU service did not resume')
    return {'status':'resumed' if rows[0].get('before')=='active' else 'unchanged'}




