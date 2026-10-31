"""Bounded CPU sidecar write-ahead state transitions.

Only trusted sidecar code may provide observations. This module performs no
hardware I/O and does not by itself grant an activation or file capability.
"""
import copy
import re

class LedgerError(RuntimeError): pass
UUID=re.compile(r'[0-9a-f]{32}\Z')
BOOT=re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z')
PATH=re.compile(r'/sys/devices/system/cpu/cpufreq/policy[0-9]+/(?:scaling_max_freq|energy_performance_preference)\Z')
EPP={'performance','balance_performance','balance_power','power'}
MAX_ATTEMPTS=64


def _identity(generation,boot,invocation,transaction):
    if not isinstance(generation,str) or not UUID.fullmatch(generation): raise LedgerError('Invalid CPU generation')
    if not isinstance(boot,str) or not BOOT.fullmatch(boot): raise LedgerError('Invalid CPU boot')
    if not isinstance(invocation,str) or not UUID.fullmatch(invocation): raise LedgerError('Invalid CPU invocation')
    if not isinstance(transaction,str) or not UUID.fullmatch(transaction): raise LedgerError('Invalid CPU transaction')


def _values(values):
    if not isinstance(values,dict) or len(values)!=28: raise LedgerError('Expected fourteen CPU policy pairs')
    policy={}
    for path,value in values.items():
        if not isinstance(path,str) or not PATH.fullmatch(path) or not isinstance(value,str): raise LedgerError('Invalid CPU observation')
        prefix,name=path.rsplit('/',1);policy.setdefault(prefix,set()).add(name)
        if name=='scaling_max_freq':
            if not re.fullmatch('[0-9]+',value) or int(value)<=0: raise LedgerError('Invalid CPU frequency observation')
        elif value not in EPP: raise LedgerError('Invalid CPU EPP observation')
    if len(policy)!=14 or any(v!={'scaling_max_freq','energy_performance_preference'} for v in policy.values()):
        raise LedgerError('Invalid CPU policy set')


def prepare(state, *, generation, boot, invocation, transaction, config_sha256, expected_config_sha256, before, desired, expected_after, public_state=None):
    _identity(generation,boot,invocation,transaction);_values(before)
    if any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in (config_sha256,expected_config_sha256)): raise LedgerError('Invalid CPU config digest')
    if not isinstance(desired,dict) or (desired and set(desired)!=set(before)):
        raise LedgerError('Invalid desired CPU policy set')
    if desired: _values(desired)
    _values(expected_after)
    if set(expected_after) != set(before) or (desired and expected_after != desired):
        raise LedgerError('Invalid expected CPU result')
    # Retain the public transaction state seen before this invocation so an
    # interrupted public write can be authenticated without inventing values.
    expected_after_public=expected_after if desired else globals()['expected_after'](before,desired,public_state)
    if expected_after_public!=expected_after:raise LedgerError('Public CPU before-state differs')
    if public_state is not None: globals()['expected_after'](before,{},public_state)
    if state is None:
        state={'kind':'BoilerCpuSidecarLedger','schema_version':1,'generation':generation,'transaction':transaction,'boots':{},'attempts':[]}
    else:
        state=copy.deepcopy(state)
        if (state.get('kind')!='BoilerCpuSidecarLedger' or state.get('schema_version')!=1 or
            state.get('generation')!=generation or state.get('transaction')!=transaction or not isinstance(state.get('boots'),dict) or not isinstance(state.get('attempts'),list)):
            raise LedgerError('Invalid CPU sidecar generation')
    if len(state['attempts'])>=MAX_ATTEMPTS: raise LedgerError('CPU ledger requires explicit archival')
    if any(a.get('invocation')==invocation for a in state['attempts']): raise LedgerError('CPU invocation already recorded')
    if any(a.get('phase')=='prepared' for a in state['attempts']): raise LedgerError('CPU attempt requires reconciliation')
    first=state['boots'].setdefault(boot,copy.deepcopy(before))
    _values(first)
    if set(first)!=set(before): raise LedgerError('CPU policy identity changed during boot')
    attempt={'boot':boot,'invocation':invocation,'phase':'prepared','config_sha256':config_sha256,'expected_config_sha256':expected_config_sha256,
             'before':copy.deepcopy(before),'desired':copy.deepcopy(desired),
             'expected_after':copy.deepcopy(expected_after),'public_state_before':copy.deepcopy(public_state)}
    state['attempts'].append(attempt)
    return state


def complete(state, *, generation, boot, invocation, transaction, after, config_sha256=None):
    _identity(generation,boot,invocation,transaction);_values(after);state=copy.deepcopy(state)
    if not isinstance(state,dict) or state.get('generation')!=generation or state.get('transaction')!=transaction or not state.get('attempts'):
        raise LedgerError('Missing CPU write-ahead state')
    attempt=state['attempts'][-1]
    if (attempt.get('boot'),attempt.get('invocation'),attempt.get('phase'))!=(boot,invocation,'prepared'):
        raise LedgerError('CPU attempt does not match write-ahead state')
    if set(after)!=set(attempt['before']): raise LedgerError('CPU policy identity changed')
    if config_sha256!=attempt['expected_config_sha256']: raise LedgerError('CPU config changed during apply')
    if after!=attempt['expected_after']:
        raise LedgerError('CPU apply did not reach the recorded targets')
    attempt.update(phase='completed',after=copy.deepcopy(after))
    return state


def stopped(state, *, generation, boot, invocation, transaction, after):
    _identity(generation,boot,invocation,transaction);_values(after);state=copy.deepcopy(state)
    if not isinstance(state,dict) or state.get('generation')!=generation or state.get('transaction')!=transaction or not state.get('attempts'):
        raise LedgerError('Missing CPU stop ownership')
    attempt=state['attempts'][-1]
    if (attempt.get('boot'),attempt.get('invocation'),attempt.get('phase'))!=(boot,invocation,'completed'):
        raise LedgerError('CPU stop does not match completed invocation')
    if set(after)!=set(attempt['before']): raise LedgerError('CPU policy identity changed')
    attempt.update(phase='stopped',stop_after=copy.deepcopy(after),
                   external=[p for p in after if after[p] not in {state['boots'][boot][p],attempt['before'][p],attempt['after'][p]}])
    return state



def reconcile(state, *, generation, boot, invocation, transaction, current):
    """Classify an interrupted attempt without changing state or sysfs."""
    _identity(generation,boot,invocation,transaction);_values(current)
    if not isinstance(state,dict) or state.get('generation')!=generation or state.get('transaction')!=transaction or not state.get('attempts'):
        raise LedgerError('Missing CPU write-ahead state')
    attempt=state['attempts'][-1]
    if (attempt.get('boot'),attempt.get('invocation'),attempt.get('phase'))!=(boot,invocation,'prepared'):
        raise LedgerError('CPU reconciliation does not match prepared invocation')
    before=attempt['before']; expected=attempt['expected_after']
    _values(before);_values(expected)
    if set(expected)!=set(before):raise LedgerError('CPU expected policy identity changed')
    if set(current)!=set(before): raise LedgerError('CPU policy identity changed')
    external=[p for p in current if current[p] not in {before[p],expected[p]}]
    return {'safe_to_restore':{p:before[p] for p in current
                               if current[p]==expected[p] and current[p]!=before[p]},
            'external_preserved':sorted(external), 'before':copy.deepcopy(before)}

def reconciled(state, *, generation, boot, invocation, transaction, after, external):
    _identity(generation,boot,invocation,transaction);_values(after);state=copy.deepcopy(state)
    if not isinstance(state,dict) or state.get('generation')!=generation or state.get('transaction')!=transaction or not state.get('attempts'):
        raise LedgerError('Missing CPU recovery ownership')
    attempt=state['attempts'][-1]
    if (attempt.get('boot'),attempt.get('invocation'),attempt.get('phase'))!=(boot,invocation,'prepared') or set(after)!=set(attempt['before']):
        raise LedgerError('CPU recovered invocation differs')
    if not isinstance(external,list) or any(p not in after for p in external):raise LedgerError('Invalid external CPU recovery list')
    attempt.update(phase='stopped',stop_after=copy.deepcopy(after),external=sorted(set(external)),reconciled=True)
    return state


def expected_after(before, desired, public_state):
    """Compute the fixed public apply result, including disabled restore."""
    _values(before)
    if desired:
        _values(desired)
        if set(before)!=set(desired): raise LedgerError('CPU desired paths differ')
        return copy.deepcopy(desired)
    if public_state is None: return copy.deepcopy(before)
    if not isinstance(public_state,dict) or set(public_state)!={'before','owned'}:
        raise LedgerError('Invalid public CPU runtime state')
    original=public_state['before'];owned=public_state['owned']
    _values(original)
    if set(original)!=set(before) or not isinstance(owned,dict) or set(owned)!=set(before):
        raise LedgerError('Public CPU runtime paths differ')
    result=copy.deepcopy(before)
    for path,current in before.items():
        candidates=owned[path]
        if not isinstance(candidates,list) or not 1<=len(candidates)<=16:
            raise LedgerError('Invalid public CPU ownership values')
        for value in candidates:
            if not isinstance(value,str): raise LedgerError('Invalid public CPU ownership value')
            if path.endswith('/scaling_max_freq'):
                if not re.fullmatch('[0-9]+',value) or int(value)<=0:raise LedgerError('Invalid CPU owned frequency')
            elif value not in EPP:raise LedgerError('Invalid CPU owned EPP')
        if current==original[path] or current in candidates:
            result[path]=original[path]
    return result



def original_stop(state, *, generation, boot, invocation, transaction, before, after):
    """Record the reviewed stop of a pre-existing service invocation."""
    _identity(generation,boot,invocation,transaction);_values(before);_values(after)
    if state is not None: raise LedgerError('Original CPU stop cannot replace an existing ledger')
    if set(before)!=set(after):raise LedgerError('CPU policy identity changed while stopping original service')
    return {'kind':'BoilerCpuSidecarLedger','schema_version':1,'generation':generation,'transaction':transaction,
            'boots':{boot:copy.deepcopy(before)},'attempts':[{'boot':boot,'invocation':invocation,
              'phase':'original-stopped','before':copy.deepcopy(before),'stop_after':copy.deepcopy(after)}]}
