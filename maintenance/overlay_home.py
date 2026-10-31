"""One shared-home ledger for managed MangoHud preset bytes."""
import contextlib,copy,os,re,stat,tempfile,uuid
from pathlib import Path
from . import adoption_blobs as blobs,overlay_layout
from .engine import Engine,_json_write
from .resource_runtime import ResourceError

class Store:
    def __init__(self,root=Path('/')):
        self.root=Path(root);self.path=self.root/'home/.boiler/overlay-v1';self.state=self.path/'state.json'
        self.target=self.root/'home/deck/.config/MangoHud/presets.conf'
    @contextlib.contextmanager
    def lock(self):
        import fcntl
        with blobs._directory(self.path,create=True) as d:
            fd=os.open('lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=d)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode&0o077:raise ResourceError('Unsafe overlay shared lock')
                fcntl.flock(fd,fcntl.LOCK_EX);yield
            finally:os.close(fd)
    def read(self):
        if not self.state.exists():return None
        from .resource_firstboot import read_private
        state=read_private(self.state)
        if (state.get('kind')!='BoilerOverlayHomeLedger' or state.get('schema_version')!=1 or state.get('status') not in ('acquiring','active','restoring','closed')
            or not re.fullmatch('[0-9a-f]{32}',state.get('generation','')) or not re.fullmatch('[0-9a-f]{32}',state.get('epoch',''))
            or not isinstance(state.get('owners'),list) or len(state['owners'])>2 or len(state['owners'])!=len(set(state['owners']))
            or any(not isinstance(owner,str) or not re.fullmatch('[0-9a-f-]{36}',owner) for owner in state['owners'])
            or (state['status']=='active' and not state['owners']) or (state['status']=='closed' and state['owners'])
            or not isinstance(state.get('before'),dict) or not isinstance(state.get('after'),dict)):raise ResourceError('Invalid overlay ledger')
        return state
    def save(self,state):_json_write(self.state,state)
    def engine(self,read_only=False):return Engine(self.root,read_only=read_only)
    def observe(self,engine):return blobs.observe(engine,self.target)
    def _snapshot(self,engine,account):
        before=blobs.capture(engine,self.target)
        if before['exists']:
            with blobs._file(self.target,**blobs._target_user(engine,self.target)) as (stream,_):original=stream.read()
        else:original=b''
        candidate=overlay_layout.render(original)
        with tempfile.NamedTemporaryFile(dir=self.path,delete=False) as f:
            f.write(candidate);f.flush();os.fsync(f.fileno());name=Path(f.name)
        try:
            import hashlib
            entry={'sha256':hashlib.sha256(candidate).hexdigest(),'size':len(candidate),'mode':before.get('mode',0o644)}
            after=blobs.payload_snapshot(engine,name,entry,before,target=self.target)
            if not before['exists']:after.update(uid=account.pw_uid,gid=account.pw_gid)
        finally:name.unlink(missing_ok=True)
        return before,after
    def _apply(self,engine,snapshot,account):
        if snapshot['exists']:
            with blobs._directory(self.target.parent,create=True,create_mode=0o755,create_uid=account.pw_uid,create_gid=account.pw_gid,
                    user_from=self.root/'home/deck',user_uid=account.pw_uid):pass
            blobs.restore_blob(engine,self.target,snapshot)
        else:blobs.remove_blob_target(engine,self.target)
    def acquire(self,generation,partuuid,record,account):
        if not re.fullmatch('[0-9a-f]{32}',generation) or not re.fullmatch('[0-9a-f-]{36}',partuuid):raise ResourceError('Invalid overlay holder')
        with self.lock():
            engine=self.engine();state=self.read()
            if state is None or state['status']=='closed':
                before,after=self._snapshot(engine,account)
                state=dict(kind='BoilerOverlayHomeLedger',schema_version=1,generation=generation,epoch=uuid.uuid4().hex,status='acquiring',owners=[],pending_owner=partuuid,before=before,after=after)
                self.save(state);record(copy.deepcopy(state))
            if state['generation']!=generation or state['status']=='restoring':raise ResourceError('Overlay ledger requires recovery')
            if state['status']=='acquiring':
                if state['pending_owner']!=partuuid:raise ResourceError('Interrupted overlay owner differs')
                current=self.observe(engine)
                if current=={k:v for k,v in state['before'].items() if k!='storage'}:self._apply(engine,state['after'],account)
                elif current!={k:v for k,v in state['after'].items() if k!='storage'}:raise ResourceError('Overlay preset changed during acquisition')
                state.update(status='active',pending_owner=None)
            elif self.observe(engine)!={k:v for k,v in state['after'].items() if k!='storage'}:
                state['preserved']=True
            if partuuid not in state['owners']:state['owners'].append(partuuid);state['owners'].sort()
            self.save(state);record(copy.deepcopy(state));return state
    def verify(self,generation,owners):
        with self.lock():
            state=self.read()
            if not state or state['generation']!=generation or state['status']!='active' or set(state['owners'])!=set(owners):raise ResourceError('Overlay shared ownership differs')
            current=self.observe(self.engine(read_only=True));after={k:v for k,v in state['after'].items() if k!='storage'}
            return dict(epoch=state['epoch'],preserved=current!=after)
    def release(self,generation,partuuid,record,account,*,allow_unacquired=False):
        with self.lock():
            engine=self.engine();state=self.read()
            if state is None and allow_unacquired:return {'status':'never-acquired'}
            if not state or state['generation']!=generation:raise ResourceError('Overlay ownership missing')
            if state['status']=='closed':return {'status':'already-restored'}
            if state['status']=='acquiring':
                if state.get('pending_owner')!=partuuid:raise ResourceError('Interrupted acquisition belongs to another holder')
                # Its durable before/after snapshots also authorize cancelling
                # acquisition. A failed install need not finish first.
                state.update(status='restoring',owners=[partuuid]);self.save(state);record(copy.deepcopy(state))
            if state['status']=='restoring' and state['pending_owner']!=partuuid:raise ResourceError('Interrupted restore belongs to another holder')
            if partuuid not in state['owners']:return {'status':'not-owned'}
            if len(state['owners'])>1:
                state['owners'].remove(partuuid);self.save(state);record(copy.deepcopy(state));return {'status':'other-slot-retains'}
            current=self.observe(engine);before={k:v for k,v in state['before'].items() if k!='storage'};after={k:v for k,v in state['after'].items() if k!='storage'}
            if current not in (after,before) or (current==before and state['status']!='restoring'):
                state.update(status='closed',owners=[],pending_owner=None,preserved=True);self.save(state);record(copy.deepcopy(state));return {'status':'user-changed-preserved'}
            state.update(status='restoring',pending_owner=partuuid);self.save(state);record(copy.deepcopy(state))
            if current==after:self._apply(engine,state['before'],account)
            state.update(status='closed',owners=[],pending_owner=None,preserved=False);self.save(state);record(copy.deepcopy(state));return {'status':'restored'}
