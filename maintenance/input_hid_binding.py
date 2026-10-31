"""Per-boot ownership of the unique 1a86:fe00 interface 02 HID binding."""
from pathlib import Path
from contextlib import contextmanager
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
sys.path.insert(0,'/usr/local/lib/boiler')

KERNEL='7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
STATE=Path('/run/boiler-input-hid')
MODULE='/usr/lib/boiler-input/hid-oxp.ko'
BUS=Path('/sys/bus/hid')

def command(*args):
 subprocess.run(args,check=True,timeout=15,env={'PATH':'/usr/bin:/usr/sbin','LC_ALL':'C'})

def target():
 if os.geteuid()!=0 or os.uname().release!=KERNEL:raise RuntimeError('Input HID requires root and exact target kernel')
 interfaces=[]
 for path in Path('/sys/bus/usb/devices').iterdir():
  try:
   if (path/'bInterfaceNumber').read_text().strip()!='02':continue
   parent=path.resolve().parent
   if (parent/'idVendor').read_text().strip().lower()=='1a86' and (parent/'idProduct').read_text().strip().lower()=='fe00':interfaces.append(path.resolve())
  except FileNotFoundError:continue
 if len(interfaces)!=1:raise RuntimeError('Input HID configuration interface is absent or ambiguous')
 devices=[p for p in (BUS/'devices').iterdir() if p.resolve().parent==interfaces[0]]
 if len(devices)!=1 or not re.fullmatch(r'0003:1A86:FE00\.[0-9A-F]+',devices[0].name,re.I):raise RuntimeError('Expected exactly one HID device on interface 02')
 return devices[0].name

def driver(device):
 link=BUS/'devices'/device/'driver'
 if not os.path.lexists(link):return None
 resolved=link.resolve(strict=True)
 if resolved.parent!=(BUS/'drivers').resolve() or resolved.name not in ('hid-generic','hid-oxp'):raise RuntimeError('Refuse to replace an unrecognized HID driver')
 return resolved.name

def bind(device,name):
 if name not in ('hid-generic','hid-oxp') or driver(device) is not None:raise RuntimeError('HID device binding changed')
 (BUS/'drivers'/name/'bind').write_text(device+'\n')
 if driver(device)!=name:raise RuntimeError('HID device did not bind to expected driver')

def unbind(device,name):
 if driver(device)!=name:raise RuntimeError('HID device binding changed')
 (BUS/'drivers'/name/'unbind').write_text(device+'\n')
 if driver(device) is not None:raise RuntimeError('HID device did not unbind')

def save(value):
 from maintenance.engine import _json_write
 _json_write(STATE/'state.json',value)

@contextmanager
def locked():
 from maintenance.adoption_blobs import _directory,_file
 with _directory(STATE,create=True) as directory:
  info=os.fstat(directory)
  if info.st_uid or stat.S_IMODE(info.st_mode)!=0o700:raise RuntimeError('HID ownership directory must be root-private')
  fd=os.open('lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600,dir_fd=directory)
  try:
   info=os.fstat(fd)
   if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_nlink!=1 or info.st_mode&0o077:raise RuntimeError('Unsafe HID ownership lock')
   fcntl.flock(fd,fcntl.LOCK_EX);yield
  finally:os.close(fd)

def state():
 from maintenance import input_ownership
 try:
  value=input_ownership.protected_record(STATE/'state.json')
 except FileNotFoundError:return None
 device=target();input_ownership.validate(value,input_ownership.context('resource:onexplayer-input',device),active=False)
 if value.get('previous') not in (None,'hid-generic'):raise RuntimeError('Invalid per-boot HID ownership record')
 return value

def _stop(value):
 device=target()
 if device!=value['device']:raise RuntimeError('HID enumeration changed; retain recovery record')
 current=driver(device)
 if current not in (None,'hid-oxp',value['previous']):raise RuntimeError('HID owner changed externally')
 save(dict(value,phase='restoring'))
 if current!=value['previous']:
  if current is not None:unbind(device,current)
  if value['previous'] is not None:bind(device,value['previous'])
 if not value['loaded_before'] and Path('/sys/module/hid_oxp').exists():
  command('/usr/bin/rmmod','hid_oxp')
 (STATE/'state.json').unlink()
 fd=os.open(STATE,os.O_DIRECTORY);os.fsync(fd);os.close(fd)

def _preflight(device):
 from maintenance import input_ownership
 value=state()
 if value is None and (Path('/sys/module/hid_oxp').exists() or driver(device)=='hid-oxp'):
  raise RuntimeError('Unknown preloaded custom HID module requires inspection')
 try:
  phase=input_ownership.classify(value,input_ownership.context('resource:onexplayer-input',device) if value is not None else None,
      loaded=Path('/sys/module/hid_oxp').exists(),driver=driver(device),stock='hid-generic',custom='hid-oxp')
 except (ValueError,TypeError) as error:raise RuntimeError('Invalid or stale HID ownership state') from error
 return value,phase

def start():
 from maintenance import input_ownership
 device=target()
 _preflight(device)
 with locked():
  device=target();value,phase=_preflight(device)
  if value is not None:
   if value['device']!=device:raise RuntimeError('HID enumeration changed during recovery')
   if phase!='restoring' and driver(device)=='hid-oxp':
    if phase=='prepared':save(dict(value,phase='active'))
    return
   if phase=='restoring':_stop(value);value=None
  prior=driver(device) if value is None else value['previous']
  # Registering a driver can attach other currently unbound matching devices.
  # Require every matching sibling to have its existing owner before insmod.
  for sibling in (BUS/'devices').glob('0003:1A86:FE00.*'):
   if sibling.name!=device and not os.path.lexists(sibling/'driver'):raise RuntimeError('Unbound matching HID sibling needs inspection')
  loaded=Path('/sys/module/hid_oxp').exists()
  if value is None:
   if loaded:raise RuntimeError('A preexisting custom HID module requires inspection')
   value=dict(input_ownership.context('resource:onexplayer-input',device),previous=prior,loaded_before=False,phase='prepared')
   save(value)
  try:
   if not loaded:command('/usr/bin/insmod',MODULE)
   if driver(device)!='hid-oxp':
    if driver(device) is not None:unbind(device,driver(device))
    bind(device,'hid-oxp')
   save(dict(value,phase='active'))
  except Exception:
   _stop(value);raise

def stop():
 _preflight(target())
 with locked():
  value,_=_preflight(target())
  if value is not None:_stop(value)

if __name__=='__main__':
 if sys.argv[1:]==['start']:start()
 elif sys.argv[1:]==['stop']:stop()
 else:raise SystemExit('Choose start or stop')
