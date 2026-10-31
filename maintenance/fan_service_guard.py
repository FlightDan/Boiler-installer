"""Fixed per-invocation fan start/stop write-ahead guard."""
import contextlib,fcntl,json,os,re,stat,subprocess
from pathlib import Path
from maintenance.adoption_blobs import _directory,_file
from maintenance.engine import _json_write
from maintenance import ec_readonly_probe,fan_live_config
from maintenance.resource_firstboot import checked_intent,read_private,STATE,open_pool
from maintenance.live_root import RootCommands

IDENTITY='resource:onexplayer-fan'
ROOT=Path('/')
DIRECTORY='var/lib/oxp3-fan/.boiler-live'
class FanGuardError(RuntimeError):pass

@contextlib.contextmanager
def _lock(*,create):
    path=ROOT/DIRECTORY
    with _directory(path,create=create,create_mode=0o700) as directory:
        info=os.fstat(directory)
        if info.st_uid!=0 or info.st_gid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise FanGuardError('Unsafe fan guard directory')
        if create:
            os.fsync(directory)
            with _directory(path.parent) as parent:os.fsync(parent)
        fd=os.open('lock',((os.O_RDWR|os.O_CREAT) if create else os.O_RDONLY)|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=0 or info.st_gid!=0 or stat.S_IMODE(info.st_mode)!=0o600:raise FanGuardError('Unsafe fan guard lock')
            fcntl.flock(fd,fcntl.LOCK_EX if create else fcntl.LOCK_SH);yield path
        finally:os.close(fd)


def _read(path):
    try:
        with _file(path,private=True) as (stream,info):
            if info.st_size>65536 or info.st_gid!=0 or stat.S_IMODE(info.st_mode)!=0o600:raise FanGuardError('Unsafe fan guard record')
            return json.loads(stream.read(65537).decode('utf-8'))
    except FileNotFoundError:return None


def _context(*,require_config=True):
    if os.name!='posix' or os.geteuid()!=0:raise FanGuardError('Fan guard requires Linux root')
    intent=checked_intent(ROOT,identity=RootCommands().identity()['partuuid'])
    state=read_private(ROOT/STATE);row=state.get('resources',{}).get(IDENTITY,{})
    recovery=row.get('activation_recovery',{})
    if (state.get('generation')!=intent['generation'] or row.get('status') not in ('activating','active','verifying','restoring') or
        recovery.get('kind')!='BoilerFanRecovery' or recovery.get('generation')!=intent['generation']):
        raise FanGuardError('Fan generation has no durable recovery state')
    if read_private(ROOT/'var/lib/boiler-resource-approved/onexplayer-fan.json')!={'generation':intent['generation'],'identity':IDENTITY}:
        raise FanGuardError('Fan approval gate differs')
    from maintenance.engine import Engine
    engine=Engine(ROOT,read_only=True)
    for identity in ('core:maintenance','core:resource-firstboot',IDENTITY):
        if not engine.verify_readonly(identity)['ok']:raise FanGuardError('Fan installed transaction differs')
    from maintenance.fan_live_activation import _check
    with open_pool(ROOT,intent) as (pool,references):
        _check(IDENTITY,ROOT,pool,intent['selection']['components'])
    from maintenance.resource_activation import LiveSystem
    boot=LiveSystem(ROOT).boot_id();invocation=os.environ.get('INVOCATION_ID','')
    if recovery.get('boot_id')!=boot or not re.fullmatch('[0-9a-f]{32}',invocation):
        raise FanGuardError('Fan boot or invocation differs')
    config=fan_live_config.verify(ROOT,generation=intent['generation'],state=recovery['config']) if require_config else {'configuration':None}
    return intent,boot,invocation,config


def _transition(action):
    intent,boot,invocation,config=_context(require_config=action in ('prestart','poststart'));observed=ec_readonly_probe.probe()
    with _lock(create=True) as directory:
        path=directory/'start.json';old=_read(path)
        if action=='prestart':
            if old and (old.get('generation')!=intent['generation'] or old.get('phase') in ('prepared','stopping') or
                        (old.get('boot_id')==boot and old.get('phase')!='stopped')):
                raise FanGuardError('Interrupted or prior-generation fan invocation needs reconciliation')
            state={'kind':'BoilerFanInvocation','generation':intent['generation'],'boot_id':boot,
                   'invocation':invocation,'phase':'prepared','ec_before':observed,'config':config['configuration']}
        elif action=='prestop' and old is None:
            firstboot=read_private(ROOT/STATE);row=firstboot.get('resources',{}).get(IDENTITY,{})
            recovery=row.get('activation_recovery',{});services=recovery.get('services',[])
            if (row.get('status')!='activating' or recovery.get('original_invocation')!=invocation or
                len(services)!=1 or services[0].get('before')!='active' or services[0].get('start_pending') is not True):
                raise FanGuardError('Original fan invocation has no durable before-state')
            state={'kind':'BoilerFanInvocation','generation':intent['generation'],'boot_id':boot,
                   'invocation':invocation,'phase':'stopping','ec_before':recovery['initial_ec_before'],
                   'stop_before':observed,'config':config['configuration']}
        else:
            if not old or (old.get('generation'),old.get('boot_id'),old.get('invocation'))!=(intent['generation'],boot,invocation):
                raise FanGuardError('Fan invocation ownership differs')
            state=dict(old)
            expected={'poststart':'prepared','prestop':'started','poststop':'stopping'}[action]
            if state.get('phase')!=expected:raise FanGuardError('Fan invocation phase differs')
            if action=='poststart' and state['config']!=config['configuration']:
                raise FanGuardError('Fan configuration changed during start')
            state.update(phase={'poststart':'started','prestop':'stopping','poststop':'stopped'}[action],
                         **({'stop_before':observed} if action=='prestop' else {'stop_after':observed} if action=='poststop' else {}))
        _json_write(path,state);return state


def prestart():return _transition('prestart')
def poststart():return _transition('poststart')
def prestop():return _transition('prestop')
def poststop():return _transition('poststop')


def stop_auto():
    # Stopping must remain possible if the user has removed the mutable config.
    intent,boot,invocation,config=_context(require_config=False)
    with _lock(create=False) as directory:
        state=_read(directory/'start.json')
        if not state or (state.get('generation'),state.get('boot_id'),state.get('invocation'),state.get('phase'))!=(intent['generation'],boot,invocation,'stopping'):
            raise FanGuardError('Fan automatic stop lacks write-ahead ownership')
        subprocess.run(['/usr/bin/python3','-I','/var/lib/oxp3-fan/fan.py','auto'],check=True,timeout=30)


def owned(generation,boot,invocation,*,phase='started'):
    if phase not in ('started','stopped'):raise FanGuardError('Invalid fan ownership phase')
    with _lock(create=False) as directory:state=_read(directory/'start.json')
    if not state or (state.get('generation'),state.get('boot_id'),state.get('invocation'),state.get('phase'))!=(generation,boot,invocation,phase):
        raise FanGuardError('Fan invocation has no matching ownership record')
    return state


