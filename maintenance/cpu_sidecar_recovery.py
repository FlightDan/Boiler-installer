"""Recover one interrupted fixed public CPU invocation using its own restore."""
import fcntl,hashlib,os,stat
from pathlib import Path
from maintenance import cpu_live_probe,cpu_sidecar,cpu_sidecar_ledger as ledger,cpu_sidecar_store
from maintenance.adoption_blobs import _directory,_file
from protocol.cpu_contract import IDENTITY,TARGETS

MODULE=Path('/var/lib/oxp3-cpu/cpu_control.py')
LOCK=Path('/run/lock/oxp3-cpu.lock')
UNIT='oxp3-cpu.service'
class RecoveryError(RuntimeError):pass

def validate_public(attempt,current):
    """Accept only public states that this exact prepared apply can produce."""
    before=attempt['before'];desired=attempt['desired'];previous=attempt['public_state_before']
    if current is None:return
    ledger.expected_after(before,{},current)
    if previous is not None:ledger.expected_after(before,{},previous)
    if current==previous:return
    if not desired:raise RecoveryError('Interrupted disabled restore state differs')
    original=previous['before'] if previous else before
    if current['before']!=original or set(current['owned'])!=set(before):raise RecoveryError('Public CPU before-state differs')
    for path in before:
        actual=set(current['owned'][path]);old=set(previous['owned'][path]) if previous else set()
        if actual not in (old|{before[path],desired[path]}, {desired[path]}):
            raise RecoveryError('Public CPU owned values differ')

def _module():
    with _file(MODULE) as (stream,info):
        if info.st_uid!=0 or info.st_gid!=0 or stat.S_IMODE(info.st_mode)!=0o644 or info.st_size>1024*1024:
            raise RecoveryError('Unsafe fixed public CPU module')
        raw=stream.read(1024*1024+1)
    if hashlib.sha256(raw).hexdigest()!=TARGETS['var/lib/oxp3-cpu/cpu_control.py']:
        raise RecoveryError('Fixed public CPU module differs')
    namespace={'__name__':'boiler_verified_public_cpu','__file__':str(MODULE)}
    exec(compile(raw,str(MODULE),'exec'),namespace)
    return namespace

def _owned_history(snapshot,boot,paths):
    first=snapshot.get('boots',{}).get(boot);ledger._values(first)
    if set(first)!=set(paths):raise RecoveryError('CPU first-before policy identity differs')
    owned={path:set() for path in paths}
    for entry in snapshot.get('attempts',[]):
        if entry.get('boot')!=boot:continue
        desired=entry.get('desired')
        if not desired:continue
        ledger._values(desired)
        if set(desired)!=set(paths):raise RecoveryError('CPU ownership history policy identity differs')
        for path,value in desired.items():owned[path].add(value)
    return first,owned

def recover(*,state,record_before_write,system,prior_restore=None):
    if not callable(record_before_write):raise RecoveryError('Durable CPU recovery recorder required')
    intent,transaction=cpu_sidecar._current_root(require_gate=False,verify_dependencies=False)
    if not isinstance(state,dict) or state.get('kind')!='BoilerCpuLiveRecovery' or (state.get('generation'),state.get('transaction'))!=(intent['generation'],transaction):
        raise RecoveryError('CPU recovery generation differs')
    snapshot=cpu_sidecar_store._snapshot_with_history(Path('/'));attempt=snapshot['attempts'][-1];boot=system.boot_id()
    if (snapshot.get('generation'),snapshot.get('transaction'))!=(intent['generation'],transaction) or attempt.get('boot')!=boot or attempt.get('phase')!='prepared' or 'public_state_before' not in attempt:
        raise RecoveryError('No same-boot prepared CPU invocation')
    invocation=attempt['invocation']
    proof={'kind':'BoilerCpuLiveRestore','phase':'reconciling','generation':intent['generation'],'transaction':transaction,'boot_id':boot,'invocation':invocation}
    if prior_restore is not None and (not isinstance(prior_restore,dict) or prior_restore.get('phase')!='reconciling' or any(prior_restore.get(k)!=v for k,v in proof.items() if k!='phase')):
        raise RecoveryError('Prior CPU recovery differs')
    if system.service(UNIT) not in ('inactive','failed'):raise RecoveryError('CPU service must finish stopping before recovery')
    record_before_write(dict(proof))
    gate=cpu_sidecar.GATE
    try:
        if cpu_sidecar.read_private(gate)!={'generation':intent['generation'],'identity':IDENTITY}:raise RecoveryError('CPU approval gate differs')
        with _directory(gate.parent) as directory:
            os.unlink(gate.name,dir_fd=directory);os.fsync(directory)
    except FileNotFoundError:
        if prior_restore is None:raise RecoveryError('CPU gate missing before recovery')
    with _directory(LOCK.parent) as directory:
        fd=os.open(LOCK.name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1:raise RecoveryError('Unsafe public CPU lock')
            fcntl.flock(fd,fcntl.LOCK_EX)
            observed=cpu_sidecar._observations(cpu_live_probe.probe_observe());public=cpu_sidecar._public_state()
            classification=ledger.reconcile(snapshot,generation=intent['generation'],boot=boot,invocation=invocation,transaction=transaction,current=observed)
            validate_public(attempt,public)
            first,owned=_owned_history(snapshot,boot,observed)
            # The public restore may only be reused when all values it would
            # claim are already proven by this generation's own history.
            public_safe=(public is not None and public['before']==first and
                all(set(public['owned'][p]) <= owned[p]|{first[p]} for p in first))
            if public_safe:
                result=_module()['restore_locked']();external=result['external_changes']
            else:
                external=[];writer=_module()['write_verified']
                for path,value in first.items():
                    if observed[path]==value:continue
                    if observed[path] not in owned[path]:external.append(path);continue
                    actual=Path(path).read_text().strip()
                    if actual==value:continue
                    if actual not in owned[path]:external.append(path);continue
                    record_before_write(dict(proof,target=path,target_before=value,target_owned=actual))
                    again=Path(path).read_text().strip()
                    if again==value:continue
                    if again!=actual:external.append(path);continue
                    writer(path,value)
            after=cpu_sidecar._observations(cpu_live_probe.probe_observe())
            if set(after)!=set(first):raise RecoveryError('CPU policy identity changed during restore')
            for path,value in after.items():
                if value!=first[path] and value in owned[path]:raise RecoveryError('Owned CPU value remains after recovery')
                if value!=first[path]:external.append(path)
            external=sorted(set(external))
            if public is not None and not public_safe:
                # A mismatched original public baseline must not be used by a
                # later unload after this generation's selective restoration.
                with _directory(cpu_sidecar.PUBLIC_STATE.parent) as runtime:
                    os.unlink(cpu_sidecar.PUBLIC_STATE.name,dir_fd=runtime);os.fsync(runtime)
            cpu_sidecar_store._transition(Path('/'),'reconciled',generation=intent['generation'],boot=boot,invocation=invocation,transaction=transaction,after=after,external=external)
        finally:os.close(fd)
    result=dict(proof,phase='stopped',external_preserved=external)
    record_before_write(dict(result));return result
