"""Current-boot, inode-bound read access for fixed telemetry sensors."""
import contextlib, json, os, pwd, re, stat, subprocess, tempfile
from pathlib import Path

STATE=Path('/run/boiler-overlay-permissions/state.json')
SYS=Path('/sys')

def _meta(path):
    s=path.stat();return {'uid':s.st_uid,'gid':s.st_gid,'mode':stat.S_IMODE(s.st_mode)}

def _save(path, value):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(path.parent,0o700)
    fd,name=tempfile.mkstemp(prefix='.state-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(value,f,sort_keys=True);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
        d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(d)
        finally:os.close(d)
    finally:
        if os.path.exists(name):os.unlink(name)

def _targets(sys=SYS):
    """Resolve only uniquely identified energy nodes, never arbitrary catalog paths."""
    devices=(sys/'devices').resolve(); found=[]
    base=sys/'class/powercap/intel-rapl:0'
    roots=[base,*base.parent.glob(base.name+':*')]
    groups={}
    for root in roots:
        try:
            name=(root/'name').read_text().strip()
            if name not in ('package-0','core','uncore'):continue
            p=(root/'energy_uj').resolve(strict=True)
            if not p.is_relative_to(devices):continue
            groups.setdefault(name,[]).append(p)
        except (OSError,ValueError):continue
    for paths in groups.values():
        unique=list(dict.fromkeys(paths))
        if len(unique)==1:found.append(unique[0])
    pmt=[]
    for root in (sys/'class/intel_pmt').glob('telem*'):
        try:
            if int((root/'guid').read_text(),16)!=0x03086000 or int((root/'size').read_text())!=3352:continue
            p=(root/'telem').resolve(strict=True)
            if p.is_relative_to(devices):pmt.append(p)
        except (OSError,ValueError):continue
    pmt=list(dict.fromkeys(pmt))
    if len(pmt)==1:found.extend(pmt)
    return list(dict.fromkeys(found))

def _readable(meta,uid,gid,groups=()):
    return bool(meta['mode'] & (0o400 if meta['uid']==uid else 0o040 if meta['gid'] in (gid,*groups) else 0o004))

def _identity(path):
    s=path.stat();return [s.st_dev,s.st_ino]

def _step(row, path, desired, state, state_path, *, tolerate_unavailable=False):
    row['planned']=desired;_save(state_path,state)
    current=_meta(path)
    try:
        if current['uid']!=desired['uid'] or current['gid']!=desired['gid']:
            os.chown(path,desired['uid'],desired['gid'])
        if stat.S_IMODE(path.stat().st_mode)!=desired['mode']:os.chmod(path,desired['mode'])
    except OSError:
        if not tolerate_unavailable:raise
        row['unavailable']=True;_save(state_path,state);return False
    row['ack']=desired;row['planned']=None;row.pop('unavailable',None);_save(state_path,state);return True

def _load(path,boot):
    if path.exists():
        from .adoption_blobs import _file
        with _file(path,private=True) as (stream,info):
            if info.st_size>1048576:raise ValueError('Oversized permission ledger')
            value=json.loads(stream.read())
        if value.get('kind')!='BoilerOverlayPermissions' or value.get('boot_id')!=boot or not isinstance(value.get('rows'),list):
            raise ValueError('permission ledger boot or schema differs')
        return value
    return {'kind':'BoilerOverlayPermissions','boot_id':boot,'rows':[]}

def grant(*,state_path=STATE,sys=SYS,boot=None,account=None):
    boot=boot or (sys/'kernel/random/boot_id').read_text().strip()
    account=account or pwd.getpwnam('deck');state=_load(state_path,boot)
    groups=os.getgrouplist(account.pw_name,account.pw_gid) if hasattr(account,'pw_name') else ()
    conflicts=[]
    for path in _targets(sys):
        inode=_identity(path);current=_meta(path)
        row=next((r for r in state['rows'] if r['path']==str(path) and r['inode']==inode and r.get('phase') not in ('retired','restored')),None)
        if row is None:
            row={'path':str(path),'inode':inode,'original':current,'ack':current,'planned':None,'phase':'grant'}
            state['rows'].append(row);_save(state_path,state)
        elif current not in (row['original'],row['ack'],row['planned']):
            conflicts.append(str(path));continue
        if _readable(current,account.pw_uid,account.pw_gid,groups):
            row['ack']=current;row['planned']=None;_save(state_path,state);continue
        desired=dict(current)
        if current['uid']==account.pw_uid:desired['mode']|=0o400
        else:desired['gid']=account.pw_gid;desired['mode']|=0o040
        # Distinct intents make the intermediate chown state recoverable.
        intermediate=dict(current,gid=desired['gid'])
        if intermediate!=current and not _step(row,path,intermediate,state,state_path,tolerate_unavailable=True):continue
        if desired!=intermediate:_step(row,path,desired,state,state_path,tolerate_unavailable=True)
    return conflicts

def restore(*,state_path=STATE,sys=SYS,boot=None):
    boot=boot or (sys/'kernel/random/boot_id').read_text().strip()
    if not state_path.exists():return []
    state=_load(state_path,boot);conflicts=[]
    for row in state['rows']:
        if row.get('phase') in ('restored','retired'):continue
        path=Path(row['path'])
        try:
            if _identity(path)!=row['inode']:
                row['phase']='retired';_save(state_path,state);continue
            current=_meta(path)
        except FileNotFoundError:
            row['phase']='retired';_save(state_path,state);continue
        if current not in (row['original'],row['ack'],row['planned']):
            conflicts.append(str(path));continue
        row['phase']='restoring';_save(state_path,state)
        original=row['original']
        # Restore mode first, then group, recording both intermediate states.
        intermediate=dict(current,mode=original['mode'])
        if current!=intermediate:_step(row,path,intermediate,state,state_path)
        if intermediate!=original:_step(row,path,original,state,state_path)
        row['phase']='restored';_save(state_path,state)
    return conflicts

@contextlib.contextmanager
def private_lock(state_path=STATE):
    import fcntl
    from .adoption_blobs import _directory
    with _directory(state_path.parent,create=True,create_mode=0o700) as directory:
        if os.fstat(directory).st_uid!=0 or os.fstat(directory).st_mode&0o077:raise ValueError('Unsafe overlay permission directory')
        fd=os.open('lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or info.st_mode&0o077:
                raise ValueError('Unsafe overlay permission lock')
            fcntl.flock(fd,fcntl.LOCK_EX);yield
        finally:os.close(fd)

def service_action(action):
    """Fixed service-only dispatcher; authenticate state while holding the WAL lock."""
    if os.name!='posix' or os.geteuid()!=0 or action not in ('grant','restore'):
        raise ValueError('Invalid overlay permission service action')
    invocation=os.environ.get('INVOCATION_ID','')
    if not re.fullmatch('[0-9a-f]{32}',invocation):raise ValueError('Missing systemd unit invocation')
    result=subprocess.run(['/usr/bin/systemctl','show','boiler-overlay-read.service','--property=InvocationID','--value'],capture_output=True,text=True,check=True,timeout=30)
    if result.stdout.strip()!=invocation:raise ValueError('Unexpected systemd unit invocation')
    from .engine import Engine
    from .resource_firstboot import checked_intent,read_private,STATE as FIRSTBOOT_STATE
    with private_lock():
        if not Engine('/',read_only=True).verify_readonly('core:maintenance')['ok'] or not Engine('/',read_only=True).verify_readonly('core:resource-firstboot')['ok']:
            raise ValueError('Retained core ownership changed')
        from .live_root import RootCommands
        intent=checked_intent('/',identity=RootCommands().identity()['partuuid'])
        if 'resource:common-telemetry-overlay' not in intent['selection']['components']:raise ValueError('Overlay is absent from installed selection')
        state=read_private(Path('/')/FIRSTBOOT_STATE)
        if any(state.get(k)!=intent.get(k) for k in ('generation','slot','root_partuuid')):raise ValueError('Overlay intent differs from firstboot state')
        row=state.get('resources',{}).get('resource:common-telemetry-overlay',{})
        if action=='grant':
            if state.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():raise ValueError('Overlay firstboot belongs to another boot')
            gate=Path('/var/lib/boiler-resource-approved/common-telemetry-overlay.json')
            if (row.get('status') not in ('activating','active','configured') or read_private(gate)!={'generation':intent['generation'],'identity':'resource:common-telemetry-overlay'}):
                raise ValueError('Overlay activation gate is closed')
            conflicts=grant()
        else:
            if row.get('status') not in ('restoring','activation_restored','restored','activating','active','configured'):
                raise ValueError('Overlay restore phase is not owned')
            conflicts=restore()
        if conflicts:raise ValueError('Overlay permissions changed externally: '+', '.join(conflicts))
