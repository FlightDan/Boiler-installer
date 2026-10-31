"""Draft fixed Waydroid Android initialization; requires an owned host lifecycle."""
import configparser,copy,hashlib,os,re,subprocess
from pathlib import Path
from maintenance.adoption_blobs import _directory,_file
SYSTEM='https://ota.waydro.id/system';VENDOR='https://ota.waydro.id/vendor'


def _state(path):
 path=Path(path)
 if path.is_symlink() or not path.is_file():raise RuntimeError('Waydroid initialized image missing or unsafe')
 h=hashlib.sha256();size=0
 with _file(path) as (f,info):
  for block in iter(lambda:f.read(1024*1024),b''):size+=len(block);h.update(block)
 if size==0:raise RuntimeError('Waydroid initialized image is empty')
 return dict(size=size,sha256=h.hexdigest())


def _paths(root,generation):
 root=Path(root)
 return root/'var/lib/waydroid',root/'var/lib/boiler/waydroid-images'/generation


def _inactive():
 result=subprocess.run(['/usr/bin/systemctl','show','--property=LoadState','--property=ActiveState','waydroid-container.service'],capture_output=True,text=True,timeout=15)
 fields=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
 if result.returncode or fields.get('LoadState')!='loaded' or fields.get('ActiveState') not in ('active','activating','deactivating','inactive','failed'):
  raise RuntimeError('Cannot establish Waydroid container state')
 return fields['ActiveState'] in ('inactive','failed')


def _execute(images):
 if Path(images).parent!=Path('/var/lib/boiler/waydroid-images') or not re.fullmatch('[0-9a-f]{32}',Path(images).name):
  raise RuntimeError('Waydroid initialization requires the fixed live staging path')
 return subprocess.run(['/usr/bin/waydroid','init','-i',str(images),'-c',SYSTEM,'-v',VENDOR,'-r','lineage','-s','GAPPS'],timeout=3600,check=True)


def _initialized(root,generation):
 work,images=_paths(root,generation);config=work/'waydroid.cfg'
 if config.is_symlink() or not config.is_file() or not (work/'rootfs').is_dir():raise RuntimeError('Waydroid initialization is incomplete')
 with _directory(work/'rootfs'):pass
 with _file(config) as (stream,info):raw=stream.read(1024*1024+1)
 if len(raw)>1024*1024:raise RuntimeError('Waydroid config exceeds limit')
 parser=configparser.ConfigParser(interpolation=None);parser.read_string(raw.decode('utf-8'));section=parser['waydroid']
 if section.get('images_path')!=str(images) or section.get('system_ota')!=SYSTEM+'/lineage/waydroid_x86_64/GAPPS.json' or not re.fullmatch(re.escape(VENDOR)+r'/waydroid_x86_64/[A-Za-z0-9_-]+\.json',section.get('vendor_ota','')):
  raise RuntimeError('Waydroid initialized with unexpected channels or image location')
 if int(section.get('system_datetime','0'))<=0 or int(section.get('vendor_datetime','0'))<=0:raise RuntimeError('Waydroid image verification timestamps missing')
 return dict(system=_state(images/'system.img'),vendor=_state(images/'vendor.img'))


def prepare(root,generation):
 if not re.fullmatch('[0-9a-f]{32}',generation):raise RuntimeError('Invalid Waydroid generation')
 work,images=_paths(root,generation)
 for extra in ('etc/waydroid-extra/images','usr/share/waydroid-extra/images'):
  path=Path(root)/extra
  if path.is_symlink():raise RuntimeError('Existing custom Android image path is linked')
  if path.exists() and any(path.iterdir()):raise RuntimeError('Existing custom Android images require user preservation')
 for data in (work,Path(root)/'home/deck/.local/share/waydroid'):
  if data.is_symlink():raise RuntimeError('Existing Waydroid data path is linked')
  if data.exists() and (not data.is_dir() or any(data.iterdir())):raise RuntimeError('Existing Waydroid data requires user preservation')
 if images.exists():raise RuntimeError('Waydroid image staging belongs to an existing action')
 return dict(kind='BoilerWaydroidAndroidInit',generation=generation,phase='prepared',attempts=0)


def activate(root,state,record):
 state=copy.deepcopy(state);generation=state.get('generation','')
 if state.get('kind')!='BoilerWaydroidAndroidInit' or not re.fullmatch('[0-9a-f]{32}',generation) or state.get('phase') not in ('prepared','initializing','failed','ready'):raise RuntimeError('Invalid Waydroid initialization action')
 if state['phase']=='ready':return verify(root,state)
 work,images=_paths(root,generation)
 if not _inactive():raise RuntimeError('Waydroid container is running; preserve the user session')
 for data in (work/'data',Path(root)/'home/deck/.local/share/waydroid'):
  if data.is_symlink():raise RuntimeError('Waydroid user data path is linked')
  if data.exists() and (not data.is_dir() or any(data.iterdir())):raise RuntimeError('Waydroid user data appeared; preserve it before retry')
 if state['phase']=='prepared':
  if images.exists() or (work.exists() and (not work.is_dir() or any(work.iterdir()))):raise RuntimeError('Waydroid staging changed before initialization')
  state.update(phase='initializing',attempts=1);record(copy.deepcopy(state))
  with _directory(images,create=True):pass
 else:
  if not images.exists() and not images.is_symlink():
   if work.exists() and (not work.is_dir() or any(work.iterdir())):raise RuntimeError('Waydroid retry staging disappeared')
   with _directory(images,create=True):pass
  if images.is_symlink() or not images.is_dir():raise RuntimeError('Waydroid retry staging disappeared')
  with _directory(images) as fd:
   if os.name!='nt' and os.fstat(fd).st_mode&0o077:raise RuntimeError('Waydroid retry staging is not private')
  state.update(phase='initializing',attempts=state['attempts']+1);record(copy.deepcopy(state))
 try:
  _execute(images);observed=_initialized(root,generation)
 except Exception as error:
  state.update(phase='failed',error=str(error));record(copy.deepcopy(state));return state
 state.update(phase='ready',images=observed);state.pop('error',None);record(copy.deepcopy(state));return state


def verify(root,state):
 if state.get('phase')!='ready' or _initialized(root,state['generation'])!=state.get('images'):raise RuntimeError('Waydroid Android images or initialization changed')
 return copy.deepcopy(state)
