"""Fixed input activation, with Engine file transactions and durable live-state intent.

The caller persists the recovery descriptor before the first mutation. DMI is
advisory; USB/I2C interfaces, the running kernel and actual service results decide
whether activation succeeds. This module does not accept hooks or command text.
"""
from pathlib import Path
import hashlib
import copy
import json
import os
import re
import stat
import subprocess
import time
import uuid

INPUT='resource:onexplayer-input'
GYRO='resource:bosch-bmi260-gyro'
IDS={INPUT,GYRO}
KERNEL='7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
MANIFEST='devices/Controller/onexplayer-input/runtime-manifest.toon'
COMPONENTS={INPUT:'runtime:boiler-input',GYRO:'runtime:boiler-gyro'}
UNITS={INPUT:['boiler-input-hid.service','inputplumber.service'],GYRO:['oxp3-gyro.service']}
ENABLE_ONLY={INPUT:['inputplumber-suspend.service'],GYRO:[]}
CONFIGS={INPUT:'etc/inputplumber/capability_maps.d/99-boiler-onexplayer.yaml',GYRO:'etc/boiler-input/calibration.json'}
ENV={'PATH':'/usr/bin:/usr/sbin','LANG':'C.UTF-8','LC_ALL':'C'}

class InputActivationError(RuntimeError): pass

def command(*args,accepted=(0,)):
 result=subprocess.run(args,capture_output=True,text=True,timeout=35,env=ENV)
 if result.returncode not in accepted:
  raise InputActivationError('Input command failed: '+Path(args[0]).name+' '+str(result.returncode)+' '+result.stderr.strip()[:300])
 return result.stdout.strip()

def live(root):
 if Path(root).absolute()!=Path('/') or os.name!='posix' or os.geteuid()!=0:
  raise InputActivationError('Input activation requires the actual Linux root')

def _boot_id():
 value=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
 if not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value):raise InputActivationError('Invalid input boot identity')
 return value

def _safe_file(path,*,missing=False):
 from .adoption_blobs import _directory
 path=Path(path)
 # Retain no-follow directory descriptors while reading the final regular inode.
 with _directory(path.parent) as directory:
  try: fd=os.open(path if os.name=='nt' else path.name,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0),**({} if os.name=='nt' else {'dir_fd':directory}))
  except FileNotFoundError:
   if missing:return None
   raise
  with os.fdopen(fd,'rb') as stream:
   info=os.fstat(stream.fileno())
   if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>256*1024*1024:
    raise InputActivationError('Input runtime requires a bounded single regular file')
   return stream.read()

def _regular_or_absent(path):
 from protocol.manifest import confined
 root=Path(path).anchor
 confined(Path(root),Path(path).relative_to(root).as_posix())
 if not os.path.lexists(path):return None
 info=Path(path).lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise InputActivationError('Unsafe input user configuration')
 return dict(sha256=hashlib.sha256(_safe_file(path)).hexdigest(),mode=stat.S_IMODE(info.st_mode),uid=info.st_uid,gid=info.st_gid)

def collect_facts(root='/'):
 live(root)
 interfaces=[]
 for path in Path('/sys/bus/usb/devices').iterdir():
  if not (path/'bInterfaceNumber').is_file():continue
  parent=path.resolve().parent
  try:
   if ((parent/'idVendor').read_text().strip().lower(),(parent/'idProduct').read_text().strip().lower(),(path/'bInterfaceNumber').read_text().strip().lower())==('1a86','fe00','02'):
    interfaces.append(str(path.resolve()))
  except FileNotFoundError:continue
 gyro=[p for p in Path('/sys/bus/i2c/devices').glob('i2c-10EC5280:*')]
 iio=[]
 for p in Path('/sys/bus/iio/devices').glob('iio:device*'):
  try:
   if 'bmi260' not in (p/'name').read_text().strip().lower():continue
   six=all((p/('in_'+kind+'_'+axis+'_raw')).is_file() for kind in ('accel','anglvel') for axis in 'xyz')
   iio.append(dict(path=str(p.resolve()),six_axes=six,name=(p/'name').read_text().strip()))
  except FileNotFoundError:continue
 return dict(running_kernel=os.uname().release,input_interfaces=interfaces,gyro_devices=[p.name for p in gyro],iio=iio)

def _hardware(identity):
 facts=collect_facts()
 if facts['running_kernel']!=KERNEL:raise InputActivationError('Input drivers require exact kernel '+KERNEL)
 if len(facts['input_interfaces'])!=1:raise InputActivationError('Expected one USB 1a86:fe00 interface 02')
 if identity==GYRO and facts['gyro_devices']!=['i2c-10EC5280:00']:
  raise InputActivationError('Expected the unique BMI260 ACPI I2C interface')
 return facts

def _pool_root(pool):
 for key in ('root','directory'):
  value=getattr(pool,key,None)
  if value is not None:return Path(value)
 raise InputActivationError('Input activation needs the validated resource pool')

def bundle(pool,identity):
 from protocol.manifest import confined,validate_manifest,verify_file
 if getattr(pool,'local_test',None) is not True:raise InputActivationError('This unreleased input lifecycle requires explicit local-test resource trust')
 base=_pool_root(pool)
 rows=[row for row in pool.catalog.get('files',[]) if row.get('path')==MANIFEST]
 if len(rows)!=1:raise InputActivationError('Input runtime manifest is not bound by the validated catalog')
 verify_file(confined(base,MANIFEST),rows[0])
 from protocol.resource_pool import decoder
 value=decoder().loads(_safe_file(confined(base,MANIFEST)).decode('utf-8'),strict=True)
 if value.get('kind')!='BoilerInputRuntime' or value.get('schema_version')!=1 or value.get('kernel')!=KERNEL:
  raise InputActivationError('Invalid fixed input runtime manifest')
 component=value['components'][identity]
 if component['id']!=COMPONENTS[identity] or component.get('hooks') or component.get('requires')!=([] if identity==INPUT else [COMPONENTS[INPUT]]):
  raise InputActivationError('Input runtime cannot supply hooks or dependencies')
 validate_manifest(component,'ComponentManifest')
 for row in component['files']:
  target=row['target']
  allowed=(target.startswith('usr/lib/boiler-input/') or target.startswith('usr/share/inputplumber/') or
           target.startswith('usr/share/dbus-1/') or target.startswith('usr/share/polkit-1/') or target.startswith('usr/lib/udev/') or
           target=='usr/lib/systemd/system/inputplumber-suspend.service' or
           target=='etc/inputplumber/devices.d/00-boiler-onexplayer.yaml' or
           target in ('usr/bin/inputplumber','etc/systemd/system/inputplumber.service','etc/systemd/system/boiler-input-hid.service',
           'etc/systemd/system/oxp3-gyro.service','etc/systemd/system/inputplumber.service.d/90-boiler-gyro.conf') or
           target.startswith('var/lib/oxp3-gyro-v1/'))
  if not allowed or target in CONFIGS.values():raise InputActivationError('Input runtime target outside fixed installation surface')
  verify_file(confined(base,row['source']),row)
 config=value['configs'][identity]
 if config['target']!=CONFIGS[identity]:raise InputActivationError('Invalid fixed input configuration target')
 verify_file(confined(base,config['source']),config)
 from protocol.input_live_contract import validate_contract
 resource=validate_contract(pool.resource(identity))
 if component.get('version')!=resource['version']:raise InputActivationError('Authoritative input version and actual Engine component differ')
 expected_rows=[dict(source=row['source'],target=row['target'],scope='root-slots',mode=row['mode']) for row in [*component['files'],config]]
 if sorted(resource['install']['files'],key=lambda r:r['target'])!=sorted(expected_rows,key=lambda r:r['target']):
  raise InputActivationError('Authoritative active resource and actual runtime installation differ')
 expected_payload=[dict(path=row['source'],size=row['size'],sha256=row['sha256']) for row in [*component['files'],config]]
 if sorted(resource['payload']['files'],key=lambda r:r['path'])!=sorted(expected_payload,key=lambda r:r['path']):
  raise InputActivationError('Authoritative active resource and runtime bytes differ')
 return base,component,config

def _service_state(unit):
 enabled=command('/usr/bin/systemctl','is-enabled',unit,accepted=(0,1,3,4))
 active=command('/usr/bin/systemctl','is-active',unit,accepted=(0,3,4))
 if enabled not in ('enabled','disabled','static','indirect','not-found') or active not in ('active','inactive','failed','unknown'):
  raise InputActivationError('Unsupported preexisting input service state: '+unit+' '+enabled+' '+active)
 return dict(enabled=enabled,active=active)

def _restore_services(services):
 command('/usr/bin/systemctl','daemon-reload')
 for unit,prior in services.items():
  if prior['enabled']=='enabled':command('/usr/bin/systemctl','enable',unit)
  elif prior['enabled']=='disabled':command('/usr/bin/systemctl','disable',unit)
  elif prior['enabled']=='not-found':command('/usr/bin/systemctl','disable',unit,accepted=(0,1))
 for unit,prior in services.items():
  if prior['active']=='active':command('/usr/bin/systemctl','start',unit)

def _current_generation(identity,root,state,*,restoring=False,restoring_ids=()):
 from .engine import Engine,_read
 if (identity not in IDS or state.get('kind')!='BoilerInputRecovery' or state.get('identity')!=identity or
     state.get('component')!=COMPONENTS[identity] or not re.fullmatch('[0-9a-f]{32}',state.get('generation',''))):
  raise InputActivationError('Invalid fixed input recovery generation')
 expected=set(['inputplumber.service',*UNITS[identity],*ENABLE_ONLY[identity]])
 if set(state.get('services',{}))!=expected or state.get('config',{}).get('target')!=CONFIGS[identity]:raise InputActivationError('Input recovery surface differs')
 readonly=Engine(root,read_only=True)
 if os.path.lexists(readonly.state_dir/'adoption-pending.json'):raise InputActivationError('Pending adoption requires explicit recovery before input lifecycle')
 for path in readonly.journals.glob('*.json'):
  if path.is_symlink() or _read(path).get('status') not in ('committed','restored','rolled_back'):
   raise InputActivationError('Pending maintenance transaction requires explicit recovery before input lifecycle')
 installed=readonly._state()['components'];current=installed.get(COMPONENTS[identity])
 if current and current['transaction']!=state.get('prior_transaction'):
  if state.get('transaction') and current['transaction']!=state['transaction']:raise InputActivationError('Input recovery was superseded by another generation')
  journal=_read(readonly._journal(current['transaction']))
  if journal.get('component',{}).get('input_live_generation')!=state['generation'] or journal.get('component',{}).get('id')!=COMPONENTS[identity]:
   raise InputActivationError('Input recovery was superseded by another generation')
  allowed_components={COMPONENTS[i] for i in set(restoring_ids)&IDS}
  if restoring and any(COMPONENTS[identity] in row.get('requires',[]) for cid,row in installed.items() if cid!=COMPONENTS[identity] and cid not in allowed_components):
   raise InputActivationError('Restore dependent gyro before input runtime')
  if not readonly.verify_readonly(COMPONENTS[identity])['ok']:raise InputActivationError('Changed immutable input files require inspection before live recovery')
  return current['transaction']
 return None

def preflight_restore(identity,*,root,state,restoring_ids=()):
 """Read-only whole-root pending, generation, dependency and file preflight."""
 transaction=_current_generation(identity,Path(root),state,restoring=True,restoring_ids=restoring_ids)
 return dict(status='ready',transaction=transaction,generation=state['generation'])

def _preflight_preloaded_custom_ownership(identity,*,root,pool=None,generation=None):
 """Reject unknown custom modules without writing a record or touching services."""
 from . import input_ownership
 if identity==INPUT:
  from . import input_hid_binding as helper
  device=helper.target();driver=helper.driver(device);stock='hid-generic';custom='hid-oxp';loaded=Path('/sys/module/hid_oxp').exists();state=helper.STATE/'state.json'
 elif identity==GYRO:
  from . import input_gyro_binding as helper
  backend=helper.SystemBackend();device=helper.DEVICE;driver=backend.driver();stock=helper.STOCK;custom=helper.CUSTOM;loaded=backend.loaded();state=helper.STATE/'state.json'
 else:raise InputActivationError('Unknown fixed input identity')
 try:
  try:record=input_ownership.protected_record(state)
  except FileNotFoundError:record=None
  digest=None
  if pool is not None and record is not None:
   _,component,_=bundle(pool,identity);target=input_ownership.SPECS[identity][1]
   rows=[row for row in component['files'] if row['target']==target]
   if len(rows)!=1:raise ValueError('Selected input module is missing')
   digest=rows[0]['sha256']
  expected=input_ownership.context(identity,device,root=root,expected_sha256=digest) if record is not None else None
  if generation is not None and expected is not None and expected['generation']!=generation:raise ValueError('Retained generation differs')
  phase=input_ownership.classify(record,expected,loaded=loaded,driver=driver,stock=stock,custom=custom)
 except Exception as error:raise InputActivationError('Unknown preloaded input module needs inspection') from error
 return phase!='absent'

def activate(identity,*,root,pool,facts,selection,record_before_write,approve=None):
 live(root)
 if identity not in IDS or not callable(record_before_write):raise InputActivationError('Fixed input identity and durable recovery callback required')
 if identity not in set(selection) or not callable(approve):raise InputActivationError('Reviewed input selection and fixed approval callback required')
 actual=_hardware(identity)
 base,component,config=bundle(pool,identity)
 if _preflight_preloaded_custom_ownership(identity,root=root,pool=pool):
  raise InputActivationError('Input generation already owns this module; resume its retained recovery state')
 services={unit:_service_state(unit) for unit in dict.fromkeys(['inputplumber.service',*UNITS[identity],*ENABLE_ONLY[identity]])}
 target=Path(root)/config['target']
 before=_regular_or_absent(target)
 from .engine import Engine
 prior_component=Engine(root,read_only=True)._state()['components'].get(COMPONENTS[identity])
 recovery=dict(schema_version=1,kind='BoilerInputRecovery',identity=identity,component=COMPONENTS[identity],
               generation=uuid.uuid4().hex,services=services,config=dict(config,before=before),transaction=None,
               prior_transaction=prior_component['transaction'] if prior_component else None)
 # No mkdir, Engine construction, service command or file write precedes this.
 record_before_write(recovery)
 approve(identity)
 component=copy.deepcopy(component);component['input_live_generation']=recovery['generation']
 from .engine import Engine,_sync_dir
 from .adoption_blobs import _directory
 from .live_root import write_scope
 engine=Engine(root)
 try:
  command('/usr/bin/systemctl','stop','inputplumber.service',accepted=(0,5))
  result=engine.apply(component,base)
  recovery['transaction']=result['transaction'];record_before_write(recovery)
  with write_scope(engine):
   if before is None:
    with _directory(target.parent,create=True,create_mode=0o755):pass
    # Repeat the observation after stopping services and immediately before write.
    if _regular_or_absent(target) is not None:raise InputActivationError('User configuration appeared during input installation')
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0),config['mode'])
    with os.fdopen(fd,'wb') as stream:
     if hasattr(os,'fchmod'):os.fchmod(stream.fileno(),config['mode'])
     if hasattr(os,'fchown'):os.fchown(stream.fileno(),0,0)
     stream.write((base/config['source']).read_bytes());stream.flush();os.fsync(stream.fileno())
     info=os.fstat(stream.fileno());recovery['config_created']=dict(dev=info.st_dev,ino=info.st_ino)
    _sync_dir(target.parent);record_before_write(recovery)
   command('/usr/bin/systemctl','daemon-reload')
   command('/usr/bin/udevadm','control','--reload-rules')
   for unit in [*UNITS[identity],*ENABLE_ONLY[identity]]:command('/usr/bin/systemctl','enable',unit)
  for unit in UNITS[identity]:command('/usr/bin/systemctl','start',unit)
  if identity==GYRO:command('/usr/bin/systemctl','start','inputplumber.service')
  verified=verify(identity,root=root,state=recovery)
  return dict(status='active',runtime='validated',evidence=verified,recovery=recovery,
              hardware_accepted=False,config_preserved=before is not None)
 except Exception as error:
  try:restore(identity,root=root,state=recovery,record_before_write=record_before_write)
  except Exception as failure:raise InputActivationError('Input activation failed; recovery remains required: '+str(failure)) from error
  raise InputActivationError('Input activation failed; prior state restored: '+str(error)) from error

def verify(identity,*,root,pool=None,facts=None,selection=None,state=None):
 live(root)
 if identity not in IDS or (selection is not None and identity not in set(selection)):raise InputActivationError('Input verification is outside the selected scope')
 if state is not None and _current_generation(identity,root,state,restoring=False) is None:
  raise InputActivationError('Input recovery generation is not currently installed')
 from .engine import Engine
 result=Engine(root,read_only=True).verify_readonly(COMPONENTS[identity])
 if not result['ok']:raise InputActivationError('Installed input runtime files differ')
 for unit in UNITS[identity]:
  if command('/usr/bin/systemctl','is-active',unit,accepted=(0,3,4))!='active':raise InputActivationError('Input service is not active: '+unit)
 version=command('/usr/bin/inputplumber','--version')
 if version!='inputplumber 0.78.0':raise InputActivationError('Unexpected active InputPlumber version')
 actual=_hardware(identity)
 hid=[p for p in Path('/sys/bus/hid/devices').iterdir() if p.resolve().parent==Path(actual['input_interfaces'][0])]
 if len(hid)!=1 or (hid[0]/'driver').resolve().name!='hid-oxp':raise InputActivationError('Exact HID interface is not bound to hid-oxp')
 dbus_version=command('/usr/bin/busctl','--system','get-property','org.shadowblip.InputPlumber','/org/shadowblip/InputPlumber/Manager','org.shadowblip.InputManager','Version')
 if dbus_version!='s "0.78.0"':raise InputActivationError('Running InputPlumber D-Bus version differs')
 composite=None;sources=None;capabilities=None;target_capabilities=None
 for attempt in range(30):
  tree=command('/usr/bin/busctl','--system','--list','tree','org.shadowblip.InputPlumber')
  for path in tree.splitlines():
   if not re.fullmatch('/org/shadowblip/InputPlumber/CompositeDevice[0-9]+',path):continue
   name=command('/usr/bin/busctl','--system','get-property','org.shadowblip.InputPlumber',path,'org.shadowblip.Input.CompositeDevice','Name')
   if name=='s "Boiler ONEXPLAYER USB interface 02"':
    composite=path
    sources=command('/usr/bin/busctl','--system','get-property','org.shadowblip.InputPlumber',path,'org.shadowblip.Input.CompositeDevice','SourceDevicePaths')
    capabilities=command('/usr/bin/busctl','--system','get-property','org.shadowblip.InputPlumber',path,'org.shadowblip.Input.CompositeDevice','Capabilities')
    target_capabilities=command('/usr/bin/busctl','--system','get-property','org.shadowblip.InputPlumber',path,'org.shadowblip.Input.CompositeDevice','TargetCapabilities')
  if composite and sources and '/source/' in sources:break
  time.sleep(.2)
 if not composite or not sources or '/source/' not in sources:raise InputActivationError('InputPlumber did not create the selected hardware composite')
 if identity==GYRO and not any(row['six_axes'] for row in actual['iio']):raise InputActivationError('BMI260 did not expose all six IIO axes after binding')
 if identity==GYRO:
  if (Path('/sys/bus/i2c/devices/i2c-10EC5280:00/driver').resolve().name!='oxp3_bmi260' or 'Gyro' not in capabilities or 'Accelerometer' not in capabilities or 'Gyro' not in target_capabilities or 'Accelerometer' not in target_capabilities):
   raise InputActivationError('InputPlumber composite does not consume the bound six-axis gyro')
 return dict(files=result,version=version,hardware=actual,service_active=True,
             dbus_version=dbus_version,composite=composite,sources=sources,capabilities=capabilities,target_capabilities=target_capabilities,
             physical_motion_verified=False,buttons_verified=False)

def restore(identity,*,root,state,record_before_write):
 live(root)
 if not callable(record_before_write) or state.get('kind')!='BoilerInputRecovery' or state.get('identity')!=identity or state.get('component')!=COMPONENTS.get(identity):
  raise InputActivationError('Invalid input recovery descriptor')
 if not re.fullmatch('[0-9a-f]{32}',state.get('generation','')):raise InputActivationError('Invalid input recovery generation')
 preflight_restore(identity,root=root,state=state)
 _preflight_preloaded_custom_ownership(identity,root=root,generation=state['generation'])
 expected_units=set(['inputplumber.service',*UNITS[identity],*ENABLE_ONLY[identity]])
 if set(state.get('services',{}))!=expected_units or state.get('config',{}).get('target')!=CONFIGS[identity]:raise InputActivationError('Input recovery surface differs')
 from .engine import Engine
 readonly=Engine(root,read_only=True);installed=readonly._state()['components']
 current=installed.get(COMPONENTS[identity])
 if current and current['transaction']!=state.get('prior_transaction'):
  if state.get('transaction') and current['transaction']!=state['transaction']:raise InputActivationError('Input recovery was superseded by another generation')
  from .engine import _read
  journal=_read(readonly._journal(current['transaction']))
  if journal.get('component',{}).get('input_live_generation')!=state['generation'] or journal.get('component',{}).get('id')!=COMPONENTS[identity]:
   raise InputActivationError('Input recovery was superseded by another generation')
  if any(COMPONENTS[identity] in row.get('requires',[]) for cid,row in installed.items() if cid!=COMPONENTS[identity]):
   raise InputActivationError('Restore dependent gyro before input runtime')
  if not readonly.verify_readonly(COMPONENTS[identity])['ok']:raise InputActivationError('Changed immutable input files require inspection before live recovery')
 record_before_write(dict(state,restoring=True))
 from .engine import Engine,_sync_dir
 engine=Engine(root)
 # Stop consumers before driver services; ExecStopPost restores per-boot bindings.
 from .live_root import write_scope
 for unit in ENABLE_ONLY[identity]:command('/usr/bin/systemctl','stop',unit,accepted=(0,5))
 command('/usr/bin/systemctl','stop','inputplumber.service',accepted=(0,5))
 for unit in reversed(UNITS[identity]):command('/usr/bin/systemctl','stop',unit,accepted=(0,5))
 with write_scope(engine):
  for unit in [*UNITS[identity],*ENABLE_ONLY[identity]]:command('/usr/bin/systemctl','disable',unit,accepted=(0,1))
 current=engine._state()['components'].get(COMPONENTS[identity])
 if current and current['transaction']!=state.get('prior_transaction'):
  transaction=state.get('transaction') or current['transaction']
  if current['transaction']!=transaction:raise InputActivationError('Input recovery was superseded by another generation')
  engine.restore(transaction)
 with write_scope(engine):
  config=state['config'];target=Path(root)/CONFIGS[identity]
  current_config=_regular_or_absent(target)
  preserved=current_config is not None
  created=state.get('config_created');info=target.lstat() if current_config else None
  if config['before'] is None and created and info and (info.st_dev,info.st_ino)==(created['dev'],created['ino']) and current_config['sha256']==config['sha256'] and current_config['mode']==config['mode'] and current_config['uid']==0 and current_config['gid']==0:
   target.unlink();_sync_dir(target.parent);preserved=False
  _restore_services(state['services'])
 return dict(status='restored',runtime='not_validated',config_preserved=preserved)

def restore_offline(identity,*,root,state,record_before_write):
 """Restore only the retained inactive-slot files and offline unit enablement.

 The caller owns the probed inactive mount and its lease. No start, stop,
 module, sysfs, user-bus or running-kernel operation is permitted here.
 """
 root=Path(root).absolute()
 if root==Path('/') or root.resolve()!=root or os.name!='posix' or os.geteuid()!=0:
  raise InputActivationError('Offline input recovery requires an explicit inactive Linux root')
 if not callable(record_before_write) or identity not in IDS or state.get('kind')!='BoilerInputRecovery' or state.get('identity')!=identity or state.get('component')!=COMPONENTS[identity]:
  raise InputActivationError('Invalid offline input recovery descriptor')
 if not re.fullmatch('[0-9a-f]{32}',state.get('generation','')):raise InputActivationError('Invalid offline input generation')
 preflight_restore(identity,root=root,state=state)
 from .engine import Engine,_read,_sync_dir
 readonly=Engine(root,read_only=True);installed=readonly._state()['components'];current=installed.get(COMPONENTS[identity])
 expected=set(['inputplumber.service',*UNITS[identity],*ENABLE_ONLY[identity]])
 if set(state.get('services',{}))!=expected or state.get('config',{}).get('target')!=CONFIGS[identity]:raise InputActivationError('Offline input recovery surface differs')
 if current and current['transaction']!=state.get('prior_transaction'):
  if state.get('transaction') and current['transaction']!=state['transaction']:raise InputActivationError('Offline input generation was superseded')
  journal=_read(readonly._journal(current['transaction']))
  if journal.get('component',{}).get('input_live_generation')!=state['generation']:raise InputActivationError('Offline input generation was superseded')
  if any(COMPONENTS[identity] in row.get('requires',[]) for cid,row in installed.items() if cid!=COMPONENTS[identity]):raise InputActivationError('Restore inactive gyro before inactive input')
  if not readonly.verify_readonly(COMPONENTS[identity])['ok']:raise InputActivationError('Changed inactive input files need inspection')
 record_before_write(dict(state,restoring_offline=True))
 # --root acts only on the inactive namespace's enablement links. It cannot
 # contact systemd or execute unit ExecStart/ExecStop hooks.
 for unit in reversed([*UNITS[identity],*ENABLE_ONLY[identity]]):command('/usr/bin/systemctl','--root',str(root),'disable',unit,accepted=(0,1))
 engine=Engine(root);current=engine._state()['components'].get(COMPONENTS[identity])
 if current and current['transaction']!=state.get('prior_transaction'):engine.restore(current['transaction'])
 config=state['config'];target=root/CONFIGS[identity];current_config=_regular_or_absent(target);preserved=current_config is not None
 created=state.get('config_created');info=target.lstat() if current_config else None
 if config['before'] is None and created and info and (info.st_dev,info.st_ino)==(created['dev'],created['ino']) and current_config['sha256']==config['sha256'] and current_config['mode']==config['mode'] and current_config['uid']==0 and current_config['gid']==0:
  target.unlink();_sync_dir(target.parent);preserved=False
 for unit,prior in state['services'].items():
  if prior['enabled']=='enabled':command('/usr/bin/systemctl','--root',str(root),'enable',unit)
  elif prior['enabled']=='disabled':command('/usr/bin/systemctl','--root',str(root),'disable',unit,accepted=(0,1))
 return dict(status='restored',runtime='offline-not-running',config_preserved=preserved,services_executed=False,drivers_touched=False)

def resume_boot(identity,*,root,pool,facts,selection,state,record_before_write,approve):
 """Resume an installed generation without replacing its original before state."""
 live(root)
 if identity not in set(selection) or not callable(record_before_write) or not callable(approve):
  raise InputActivationError('Input boot resume requires reviewed scope and durable callbacks')
 if _current_generation(identity,root,state) is None:raise InputActivationError('No installed input generation to resume')
 _hardware(identity)
 _preflight_preloaded_custom_ownership(identity,root=root,pool=pool,generation=state['generation'])
 for unit in [*UNITS[identity],*ENABLE_ONLY[identity]]:
  if _service_state(unit)['enabled']!='enabled':raise InputActivationError('Input service enablement changed outside the installed generation: '+unit)
 recovery=copy.deepcopy(state)
 recovery.update(last_boot_id=_boot_id(),boot_resuming=True)
 record_before_write(recovery);approve(identity)
 command('/usr/bin/systemctl','daemon-reload')
 for unit in UNITS[identity]:command('/usr/bin/systemctl','start',unit)
 if identity==GYRO:command('/usr/bin/systemctl','restart','inputplumber.service')
 verified=verify(identity,root=root,pool=pool,facts=facts,selection=selection,state=recovery)
 recovery['boot_resuming']=False;record_before_write(recovery)
 return dict(status='active',runtime='validated',evidence=verified,recovery=recovery,hardware_accepted=False,resumed_generation=True)
