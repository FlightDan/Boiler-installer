"""Single shared-home owner ledger for the fixed Flathub user remote."""
import contextlib,copy,os,re,stat,uuid
from pathlib import Path
from .adoption_blobs import _directory,_file
from .engine import _json_write
from .resource_runtime import ResourceError
from . import flathub_transaction as transaction
from protocol.manifest import _decode_json


class Store:
    def __init__(self,root=Path('/')):
        self.path=Path(root)/'home/.boiler/flathub-v1';self.state=self.path/'state.json'

    @contextlib.contextmanager
    def lock(self):
        if os.name!='posix' or os.geteuid()!=0:raise ResourceError('Flathub shared ledger requires Linux root')
        import fcntl
        with _directory(self.path,create=True) as directory:
            fd=os.open('lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=directory)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode&0o077:
                    raise ResourceError('Unsafe Flathub shared ledger lock')
                fcntl.flock(fd,fcntl.LOCK_EX)
                yield
            finally:os.close(fd)

    def read(self):
        if not self.state.exists():return None
        with _file(self.state,private=True) as (stream,info):
            if info.st_size>1024*1024:raise ResourceError('Flathub shared ledger exceeds limit')
            value=_decode_json(stream.read())
        if (not isinstance(value,dict) or value.get('kind')!='BoilerFlathubUserLedger' or value.get('schema_version')!=1
                or not re.fullmatch('[0-9a-f]{32}',value.get('generation',''))
                or not re.fullmatch('[0-9a-f]{32}',value.get('epoch',''))
                or value.get('status') not in ('acquiring','active','restoring','closed')
                or not isinstance(value.get('owners'),list) or len(value['owners'])>2 or len(value['owners'])!=len(set(value['owners']))
                or any(not isinstance(owner,str) or not re.fullmatch('[0-9a-f-]{36}',owner) for owner in value['owners'])
                or not isinstance(value.get('before'),dict) or set(value['before'])!={'user'}
                or not isinstance(value.get('rows'),dict) or set(value['rows'])-{'user'}
                or (value['status']=='active' and (set(value['rows'])!={'user'}
                    or value['rows']['user'].get('phase') not in ('applied','unchanged') or not value['owners']))
                or (value['status']=='closed' and value['owners'])):
            raise ResourceError('Invalid Flathub shared ledger')
        return value

    def save(self,value):_json_write(self.state,value)

    def verify(self,backend,generation,partuuid):
        import fcntl
        with _file(self.path/'lock',private=True) as (stream,info):
            fcntl.flock(stream.fileno(),fcntl.LOCK_SH)
            try:
                state=self.read()
                if (not state or state['generation']!=generation or state['status']!='active'
                        or partuuid not in state['owners']):raise ResourceError('Flathub shared user ownership differs')
                return dict(ok=all(transaction.verify(backend,state['rows']).values()),epoch=state['epoch'])
            finally:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

    def snapshot(self,backend):
        """No creation or recovery; suitable for a reviewed restore plan."""
        if not self.state.exists():return dict(ledger=None,remote=backend.observe('user'))
        import fcntl
        with _file(self.path/'lock',private=True) as (stream,info):
            fcntl.flock(stream.fileno(),fcntl.LOCK_SH)
            try:return dict(ledger=self.read(),remote=backend.observe('user'))
            finally:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

    def preflight_release(self,generation,partuuid):
        with self.lock():
            state=self.read()
            if not state or state['generation']!=generation:raise ResourceError('Flathub shared ownership is missing')
            if state['status']=='acquiring':raise ResourceError('Interrupted Flathub shared acquisition requires retry')
            if state['status']=='restoring' and state.get('pending_owner')!=partuuid:
                raise ResourceError('Interrupted Flathub shared action belongs to another slot')
            return copy.deepcopy(state)

    def acquire(self,backend,generation,partuuid,record):
        if not re.fullmatch('[0-9a-f]{32}',generation) or not re.fullmatch('[0-9a-f-]{36}',partuuid):raise ResourceError('Invalid Flathub owner identity')
        with self.lock():
            state=self.read()
            if state is None or state['status']=='closed':
                state=dict(schema_version=1,kind='BoilerFlathubUserLedger',generation=generation,epoch=uuid.uuid4().hex,
                           status='acquiring',pending_owner=partuuid,owners=[],before=transaction.prepare(backend,scopes=('user',)),rows={})
                self.save(state);record(copy.deepcopy(state))
            if state['generation']!=generation:raise ResourceError('Flathub shared ledger belongs to another installation')
            if state['status']=='restoring':raise ResourceError('Flathub shared restore requires retry')
            if state['status']=='acquiring' and state.get('pending_owner')!=partuuid:
                raise ResourceError('Interrupted Flathub user transaction belongs to another slot')
            def save(rows):state['rows']=rows;self.save(state);record(copy.deepcopy(state))
            if state['status']=='acquiring':
                if any(row.get('phase') in ('writing','restoring') for row in state['rows'].values()):
                    state['rows']=transaction.reconcile(backend,state['rows'],save)
                state['rows']=transaction.activate(backend,state['before'],save,rows=state['rows'],scopes=('user',))
                state['status']='active';state['pending_owner']=None
            else:
                if not all(transaction.verify(backend,state['rows']).values()):raise ResourceError('Shared Flathub remote was externally changed')
            if partuuid not in state['owners']:state['owners'].append(partuuid);state['owners'].sort()
            self.save(state);record(copy.deepcopy(state));return copy.deepcopy(state)

    def release(self,backend,generation,partuuid,record):
        with self.lock():
            state=self.read()
            if state is None or state['generation']!=generation:raise ResourceError('Flathub shared ownership is missing')
            if state['status']=='closed':return dict(status='already-restored',epoch=state['epoch'])
            if state['status']=='acquiring':raise ResourceError('Interrupted Flathub shared acquisition requires retry')
            if partuuid not in state['owners']:return dict(status='not-owned',epoch=state['epoch'])
            if state['status']=='restoring' and state.get('pending_owner')!=partuuid:
                raise ResourceError('Interrupted Flathub shared action belongs to another slot')
            if len(state['owners'])>1:
                state['owners'].remove(partuuid);self.save(state);record(copy.deepcopy(state))
                return dict(status='other-slot-retains',epoch=state['epoch'])
            row=state['rows']['user'];current=transaction._observation(backend.observe('user'))
            if state['status']=='active' and current!=row['after']:
                state.update(status='closed',owners=[],pending_owner=None,preserved=True);self.save(state);record(copy.deepcopy(state))
                return dict(status='user-changed-preserved',epoch=state['epoch'])
            state.update(status='restoring',pending_owner=partuuid);self.save(state);record(copy.deepcopy(state))
            def save(rows):state['rows']=rows;self.save(state);record(copy.deepcopy(state))
            state['rows']=transaction.reconcile(backend,state['rows'],save)
            state['rows']=transaction.restore(backend,state['rows'],save,scopes=('user',))
            state.update(status='closed',owners=[],pending_owner=None,preserved=False);self.save(state);record(copy.deepcopy(state))
            return dict(status='restored',epoch=state['epoch'])
