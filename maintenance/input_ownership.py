"""Read-only binding of input helper ownership to a committed live generation."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

KERNEL='7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
SPECS={
 'resource:onexplayer-input':('runtime:boiler-input','usr/lib/boiler-input/hid-oxp.ko','BoilerHidBinding'),
 'resource:bosch-bmi260-gyro':('runtime:boiler-gyro','var/lib/oxp3-gyro-v1/oxp3_bmi260.ko','BoilerGyroBinding'),
}

def context(identity,device,*,root='/',expected_sha256=None):
 from .engine import Engine
 from .adoption_blobs import _file
 from .live_root import RootCommands
 if identity not in SPECS:raise ValueError('Unknown fixed input identity')
 component,target,kind=SPECS[identity];engine=Engine(root,read_only=True)
 def read_private(path):
  with _file(path,private=True) as (stream,info):
   if info.st_size>8*1024*1024:raise ValueError('Input journal exceeds limit')
   return _json(stream.read())
 current=read_private(engine.state_dir/'state.json').get('components',{}).get(component)
 if not current:raise ValueError('No committed input generation owns the custom module')
 journal=read_private(engine._journal(current['transaction']));manifest=journal.get('component',{})
 generation=manifest.get('input_live_generation','')
 if journal.get('status')!='committed' or manifest.get('id')!=component or not re.fullmatch('[0-9a-f]{32}',generation):raise ValueError('Uncommitted input generation')
 rows=[r for r in manifest.get('files',[]) if r.get('target')==target]
 if len(rows)!=1 or not re.fullmatch('[0-9a-f]{64}',rows[0].get('sha256','')):raise ValueError('Missing committed input module identity')
 digest=rows[0]['sha256']
 if expected_sha256 is not None and digest!=expected_sha256:raise ValueError('Selected input module differs from installed generation')
 with _file(Path(root)/target) as (stream,info):
  if info.st_size!=rows[0]['size'] or hashlib.sha256(stream.read()).hexdigest()!=digest:raise ValueError('Installed input module differs from committed generation')
 root_id=RootCommands().identity()
 boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
 if not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',boot):raise ValueError('Invalid input boot identity')
 if os.uname().release!=KERNEL:raise ValueError('Input kernel differs')
 return dict(kind=kind,schema_version=1,boot=boot,kernel=KERNEL,device=device,root_partuuid=root_id['partuuid'],root_uuid=root_id['uuid'],generation=generation,module_sha256=digest)

def validate(record,expected,*,active=True):
 keys=set(expected)|{'previous','loaded_before','phase'}
 if not isinstance(record,dict) or set(record)!=keys or any(record.get(k)!=v for k,v in expected.items()) or type(record.get('schema_version')) is not int or record.get('schema_version')!=1 or record.get('loaded_before') is not False or record.get('phase') not in (('active',) if active else ('prepared','active','restoring')):
  raise ValueError('Input module ownership record does not match current generation')
 return record

def classify(record,expected,*,loaded,driver,stock,custom):
 """Accept only observations reachable by the fixed start/restore sequence."""
 if record is None:
  if loaded or driver==custom:raise ValueError('Unknown custom module')
  return 'absent'
 validate(record,expected,active=False)
 previous=record['previous']
 if previous not in (None,stock):raise ValueError('Invalid previous driver')
 phase=record['phase']
 if phase=='active':valid=loaded and driver==custom
 elif phase=='prepared':valid=(not loaded and driver==previous) or (loaded and driver in (previous,None,custom))
 else:valid=(loaded and driver in (custom,None,previous)) or (not loaded and driver==previous)
 if not valid:raise ValueError('Unreachable input ownership state')
 return phase

def protected_record(path):
 from .adoption_blobs import _directory,_file
 path=Path(path)
 with _directory(path.parent) as directory:
  if os.name!='nt':
   info=os.fstat(directory)
   if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('Input ownership directory must be root-private')
 with _file(path,private=True) as (stream,info):
  if info.st_size>8192 or (os.name!='nt' and stat.S_IMODE(info.st_mode)!=0o600):raise ValueError('Input ownership record must be bounded and private')
  return _json(stream.read())

def _json(raw):
 def unique(rows):
  result={}
  for key,value in rows:
   if key in result:raise ValueError('Duplicate input ownership field')
   result[key]=value
  return result
 return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Invalid input JSON')))
