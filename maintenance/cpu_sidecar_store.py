"""Durable private storage for the fixed CPU sidecar transition ledger.

This internal store is not a launcher or authorization check. Its caller must
hold an independently verified fixed CPU context before invoking it.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import stat
from maintenance.adoption_blobs import _directory, _file
from maintenance.engine import _json_write
from maintenance import cpu_sidecar_ledger as ledger

DIRECTORY='var/lib/oxp3-cpu/.boiler-live'
MAX_BYTES=1024*1024

@contextlib.contextmanager
def _locked(root, *, create):
    directory=Path(root)/DIRECTORY
    with _directory(directory,create=create,create_mode=0o700) as parent:
        info=os.fstat(parent)
        if info.st_uid!=0 or info.st_gid!=0 or stat.S_IMODE(info.st_mode)!=0o700:
            raise ledger.LedgerError('Unsafe CPU sidecar directory')
        if create:
            os.fsync(parent)
            with _directory(directory.parent) as ancestor: os.fsync(ancestor)
        flags=(os.O_RDWR|os.O_CREAT if create else os.O_RDONLY)|os.O_NOFOLLOW
        fd=os.open('lock',flags,0o600,dir_fd=parent)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=0 or info.st_gid!=0 or stat.S_IMODE(info.st_mode)!=0o600:
                raise ledger.LedgerError('Unsafe CPU sidecar lock')
            fcntl.flock(fd,fcntl.LOCK_EX if create else fcntl.LOCK_SH)
            yield directory
        finally:
            os.close(fd)


def _read(path):
    try:
        with _file(path,private=True) as (stream,info):
            if info.st_gid!=0 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>MAX_BYTES:
                raise ledger.LedgerError('Unsafe CPU sidecar ledger')
            def pairs(items):
                value={}
                for k,v in items:
                    if k in value:raise ledger.LedgerError('Duplicate CPU ledger key')
                    value[k]=v
                return value
            return json.loads(stream.read(MAX_BYTES+1).decode('utf-8'),object_pairs_hook=pairs)
    except FileNotFoundError:
        return None


def _compact(directory, state, *, generation, transaction, boot):
    if not isinstance(state,dict) or len(state.get('attempts',[])) < ledger.MAX_ATTEMPTS:
        return state
    if (state.get('generation')!=generation or state.get('transaction')!=transaction or
        any(a.get('phase')=='prepared' for a in state['attempts']) or
        state['attempts'][-1].get('phase')!='stopped'):
        raise ledger.LedgerError('CPU ledger cannot archive an unfinished invocation')
    head=state.get('archive_head')
    if (head is not None and (not isinstance(head,str) or not re.fullmatch('[0-9a-f]{64}',head))) or type(state.get('archive_count',0)) is not int or state.get('archive_count',0)<0:
        raise ledger.LedgerError('Invalid CPU archive chain')
    old_boots={k:v for k,v in state.get('boots',{}).items() if k!=boot}
    archive={'kind':'BoilerCpuLedgerArchive','schema_version':1,'generation':generation,
             'transaction':transaction,'previous':state.get('archive_head'),
             'attempts':state['attempts'],'boots':old_boots}
    raw=(json.dumps(archive,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
    digest=hashlib.sha256(raw).hexdigest();path=directory/('archive-'+digest+'.json')
    existing=_read(path)
    if existing is None:
        _json_write(path,archive)
    elif existing!=archive:
        raise ledger.LedgerError('CPU archive digest collision or changed file')
    state=dict(state,archive_head=digest,archive_count=state.get('archive_count',0)+len(state['attempts']),
               attempts=[],boots={k:v for k,v in state['boots'].items() if k==boot})
    return state


def _transition(root, action, **kwargs):
    if action not in ('prepare','complete','stopped','original_stop','reconciled'):
        raise ledger.LedgerError('Unknown CPU ledger transition')
    with _locked(root,create=True) as directory:
        path=directory/'ledger.json'; state=_read(path)
        if state is not None:
            _history(directory,state);_handover_chain(directory,state,None)
        if action=='prepare' and state is not None:
            state=_compact(directory,state,generation=kwargs['generation'],transaction=kwargs['transaction'],boot=kwargs['boot'])
        result=getattr(ledger,action)(state,**kwargs)
        encoded=json.dumps(result,sort_keys=True,separators=(',',':')).encode()
        if len(encoded)>MAX_BYTES:raise ledger.LedgerError('CPU ledger size limit reached')
        _json_write(path,result)
        return result


def _inspect(root, action, **kwargs):
    if action!='reconcile':raise ledger.LedgerError('Unknown CPU ledger inspection')
    with _locked(root,create=False) as directory:
        state=_read(directory/'ledger.json')
        if state is None:raise ledger.LedgerError('CPU ledger is missing')
        _history(directory,state)
        _handover_chain(directory,state,None)
        return ledger.reconcile(state,**kwargs)



def _snapshot(root):
    try:
        with _locked(root,create=False) as directory:
            state=_read(directory/'ledger.json')
            if state is None:raise FileNotFoundError(directory/'ledger.json')
            _history(directory,state)
            _handover_chain(directory,state,None)
            return state
    except FileNotFoundError:
        # A missing lock is not evidence that a surviving ledger is absent.
        if os.path.lexists(Path(root)/DIRECTORY/'ledger.json'):
            raise ledger.LedgerError('CPU ledger exists without its private store')
        raise

def _history(directory, state):
    """Authenticate invocation archives without acquiring a nested flock."""
    if (not isinstance(state,dict) or state.get('kind')!='BoilerCpuSidecarLedger' or
        state.get('schema_version')!=1 or not isinstance(state.get('boots'),dict) or
        not isinstance(state.get('attempts'),list)):
        raise ledger.LedgerError('Invalid CPU ledger history')
    current=state.get('archive_head');seen=set();archives=[];count=0
    while current is not None:
        if not isinstance(current,str) or not re.fullmatch('[0-9a-f]{64}',current) or current in seen:
            raise ledger.LedgerError('Invalid CPU archive chain')
        seen.add(current);archive=_read(directory/('archive-'+current+'.json'))
        if (not isinstance(archive,dict) or archive.get('kind')!='BoilerCpuLedgerArchive' or archive.get('schema_version')!=1 or
            (archive.get('generation'),archive.get('transaction'))!=(state.get('generation'),state.get('transaction')) or not isinstance(archive.get('attempts'),list)):
            raise ledger.LedgerError('CPU archive identity differs')
        raw=(json.dumps(archive,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
        if hashlib.sha256(raw).hexdigest()!=current:raise ledger.LedgerError('CPU archive digest differs')
        archives.append(archive['attempts']);count+=len(archive['attempts']);current=archive.get('previous')
    if count!=state.get('archive_count',0):raise ledger.LedgerError('CPU archive count differs')
    result=dict(state);result['attempts']=[entry for group in reversed(archives) for entry in group]+state['attempts']
    return result


def _bytes(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()


def _handover_chain(directory, state, identity):
    current=state.get('handover_head');expected=state.get('transaction');seen=set()
    while current is not None:
        if not isinstance(current,str) or not re.fullmatch('[0-9a-f]{64}',current) or current in seen:
            raise ledger.LedgerError('Invalid CPU handover chain')
        seen.add(current);envelope=_read(directory/('handover-'+current+'.json'))
        if not isinstance(envelope,dict):raise ledger.LedgerError('CPU handover archive is missing')
        if identity is None:identity=envelope.get('engine_identity')
        if (hashlib.sha256(_bytes(envelope)).hexdigest()!=current or
            envelope.get('kind')!='BoilerCpuLedgerHandover' or envelope.get('schema_version')!=1 or
            envelope.get('generation')!=state.get('generation') or envelope.get('engine_identity')!=identity or
            envelope.get('new_transaction')!=expected or not isinstance(envelope.get('ledger'),dict)):
            raise ledger.LedgerError('CPU handover archive differs')
        old=envelope['ledger']
        if ((old.get('generation'),old.get('transaction'))!=(state.get('generation'),envelope.get('old_transaction')) or
            envelope.get('previous')!=old.get('handover_head')):
            raise ledger.LedgerError('CPU handover ownership differs')
        _history(directory,old)
        expected=old['transaction'];current=envelope['previous']


def _handover(root, *, generation, transaction, engine_identity, validate):
    """Archive a completed, authenticated old transaction before succession."""
    with _locked(root,create=True) as directory:
        path=directory/'ledger.json';state=_read(path)
        if state is None:return
        _history(directory,state);_handover_chain(directory,state,engine_identity)
        if state.get('generation')!=generation:raise ledger.LedgerError('CPU handover generation differs')
        if state.get('transaction')==transaction:return
        history=_history(directory,state);attempts=history.get('attempts')
        if (not attempts or attempts[-1].get('phase')!='stopped' or
            any(a.get('phase')=='prepared' for a in attempts)):
            raise ledger.LedgerError('CPU handover requires a stopped ledger')
        last=attempts[-1]
        ledger._identity(generation,last.get('boot'),last.get('invocation'),state.get('transaction'))
        ledger._values(last.get('before'));ledger._values(last.get('stop_after'))
        ledger._values(state['boots'].get(last['boot']))
        validate(state,attempts[-1])
        envelope={'kind':'BoilerCpuLedgerHandover','schema_version':1,'generation':generation,
            'old_transaction':state['transaction'],'new_transaction':transaction,'engine_identity':engine_identity,
            'previous':state.get('handover_head'),'ledger':state}
        digest=hashlib.sha256(_bytes(envelope)).hexdigest();archive=directory/('handover-'+digest+'.json')
        existing=_read(archive)
        if existing is None:_json_write(archive,envelope)
        elif existing!=envelope:raise ledger.LedgerError('CPU handover archive changed')
        fresh={'kind':'BoilerCpuSidecarLedger','schema_version':1,'generation':generation,
               'transaction':transaction,'boots':{},'attempts':[],'handover_head':digest}
        _json_write(path,fresh)


def _snapshot_with_history(root):
    """Read and authenticate the complete archived invocation chain."""
    try:
        with _locked(root,create=False) as directory:
            state=_read(directory/'ledger.json')
            if state is None:raise FileNotFoundError(directory/'ledger.json')
            _handover_chain(directory,state,None)
            return _history(directory,state)
    except FileNotFoundError:
        if os.path.lexists(Path(root)/DIRECTORY/'ledger.json'):
            raise ledger.LedgerError('CPU ledger exists without its private store')
        raise
