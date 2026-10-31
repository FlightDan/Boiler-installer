"""Fixed exact-kernel EC profile identity and read-only ownership verification."""
import hashlib,os,re,subprocess
from pathlib import Path
from maintenance.adoption_blobs import _file
from maintenance.engine import Engine
from maintenance import ec_readonly_probe
from maintenance.resource_firstboot import checked_intent,read_private,STATE
from maintenance.live_root import RootCommands
from protocol.ec_profile_contract import MARKER,validate_contract

IDENTITY='resource:onexplayer-ec-fan-driver'
PROFILE='oxp3-g1i-v1'
KERNEL=ec_readonly_probe.KERNEL
MODULE=Path('/var/lib/oxp3-fan/oxpec.ko')
MODULE_SHA='a819b435f1214252d1d0b15b039baa00d31642495075e864b3153299ecd6e372'
SRCVERSION='5E1470CE1EB3B89D6E8C5BD'
GATE=Path('/var/lib/boiler-resource-approved/onexplayer-ec-fan-driver.json')

class ECProfileError(RuntimeError):pass


def _fixed_module():
    with _file(MODULE) as (stream,info):
        if info.st_uid!=0 or info.st_gid!=0 or info.st_mode & 0o7777 != 0o644 or info.st_size!=472736:
            raise ECProfileError('EC module ownership or size differs')
        digest=hashlib.sha256(stream.read(472737)).hexdigest()
    if digest!=MODULE_SHA:raise ECProfileError('EC module bytes differ from compiled profile')
    def modinfo(field):
        return subprocess.check_output(['/usr/bin/modinfo','-F',field,str(MODULE)],text=True,timeout=30).strip()
    if not modinfo('vermagic').startswith(KERNEL+' ') or modinfo('srcversion')!=SRCVERSION or 'board_profile:' not in modinfo('parm'):
        raise ECProfileError('EC module target or profile metadata differs')


def _acpi(root):
    paths=list((Path(root)/'sys/bus/acpi/devices').glob('PNP0C09:*'))
    try:
        return any((p/'hid').read_text().strip()=='PNP0C09' and int((p/'status').read_text().strip()) & 1 for p in paths)
    except (OSError,ValueError):return False


def _context(*,gate):
    if os.name!='posix' or os.geteuid()!=0 or os.uname().release!=KERNEL:raise ECProfileError('EC profile requires root and exact kernel')
    intent=checked_intent(Path('/'),identity=RootCommands().identity()['partuuid'])
    if IDENTITY not in intent['selection']['components'] or intent.get('ec_board_profile')!=PROFILE:
        raise ECProfileError('EC profile differs from the reviewed intent')
    early=intent.get('ec_initramfs')
    if (not isinstance(early,dict) or set(early)!={'path','sha256','entries','stock_oxpec'} or
        early.get('path')!='boot/initramfs-linux-neptune-72.img' or early.get('stock_oxpec') is not False or
        not isinstance(early.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',early['sha256']) or
        type(early.get('entries')) is not int or early['entries']<1):
        raise ECProfileError('EC early-boot initramfs proof is missing')
    engine=Engine(Path('/'),read_only=True)
    for name in ('core:maintenance','core:resource-firstboot',IDENTITY):
        if not engine.verify_readonly(name)['ok']:raise ECProfileError('EC installed transaction differs: '+name)
    installed=engine._state()['components'].get(IDENTITY)
    if not installed:raise ECProfileError('EC transaction missing')
    journal=read_private(engine._journal(installed['transaction']))
    if journal.get('component',{}).get('ec_profile_contract')!=MARKER:
        raise ECProfileError('EC transaction is not the fixed reviewed profile')
    from maintenance.resource_firstboot import open_pool
    with open_pool(Path('/'),intent) as (pool,references): validate_contract(pool.resource(IDENTITY))
    if gate and read_private(GATE)!={'generation':intent['generation'],'identity':IDENTITY}:
        raise ECProfileError('EC approval gate differs')
    _fixed_module()
    if not _acpi(Path('/')):raise ECProfileError('ACPI embedded controller unavailable')
    return intent,installed['transaction']


def _loaded():
    module=Path('/sys/module/oxpec')
    if not module.is_dir():return False
    profile=module/'parameters/board_profile';source=module/'srcversion'
    if not profile.is_file() or not source.is_file() or profile.read_text().strip()!=PROFILE or source.read_text().strip()!=SRCVERSION:
        raise ECProfileError('Existing oxpec driver is unknown or uses another profile; preserve it')
    ec_readonly_probe.probe()
    return True


def load():
    """Called only from the fixed approved module service; never unload a driver."""
    intent,transaction=_context(gate=True)
    state=read_private(Path('/')/STATE);row=state.get('resources',{}).get(IDENTITY,{})
    recovery=row.get('activation_recovery',{})
    if (state.get('generation')!=intent['generation'] or row.get('status')!='activating' or
        recovery.get('kind')!='BoilerEcProfileRecovery' or recovery.get('generation')!=intent['generation'] or
        recovery.get('transaction')!=transaction or recovery.get('load_pending') is not True):
        raise ECProfileError('EC load has no durable write-ahead record')
    if _loaded():return {'status':'reused'}
    subprocess.run(['/usr/bin/insmod',str(MODULE),'board_profile='+PROFILE],check=True,timeout=60)
    if not _loaded():raise ECProfileError('Loaded EC profile did not register the expected interfaces')
    return {'status':'loaded'}

