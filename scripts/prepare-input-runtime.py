"""Build the real offline InputPlumber/HID/BMI260 activation bundle."""
import argparse
import hashlib
import gzip
import io
import importlib.util
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile

HERE=Path(__file__).resolve().parents[1]
INPUT='resource:onexplayer-input';GYRO='resource:bosch-bmi260-gyro'
KERNEL='7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
SOURCE_PREFIX='adaptations/brands/onexplayer/common/resource-inputs/onexplayer-input-stage'
CAPMAP='etc/inputplumber/capability_maps.d/99-boiler-onexplayer.yaml'
CALIBRATION='etc/boiler-input/calibration.json'
RESOURCE_PATHS={INPUT:'devices/Controller/onexplayer-input',GYRO:'devices/Sensor/bosch-bmi260-gyro'}
RUNTIME_MANIFEST=RESOURCE_PATHS[INPUT]+'/runtime-manifest.toon'
PROVENANCE='provenance/input-runtime'
MODULE_HASHES={'hid-oxp.ko':'7a3aa7204a0f7427bf8ad867b42eea76d973a1f627aaede1709e281702e5d1ed','oxp3_bmi260.ko':'9d576c2b8ae7e9b1f4fedea7fdde296e80767fab46eac4812d14f5b90aaae395'}

HID_UNIT='''[Unit]
Description=Boiler exact-interface OneXPlayer HID binding
After=systemd-udev-trigger.service
Before=inputplumber.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 -I /usr/lib/boiler-input/input-hid-binding.py start
ExecStopPost=/usr/bin/python3 -I /usr/lib/boiler-input/input-hid-binding.py stop
TimeoutStartSec=25
TimeoutStopSec=25

[Install]
WantedBy=multi-user.target
'''
INPUT_UNIT='''[Unit]
Description=InputPlumber 0.78.0 (Boiler source build)
After=boiler-input-hid.service
Requires=boiler-input-hid.service

[Service]
Type=dbus
BusName=org.shadowblip.InputPlumber
Environment=LOG_LEVEL=info
ExecStart=/usr/bin/inputplumber
ProtectSystem=full
TimeoutStartSec=30

[Install]
WantedBy=multi-user.target
'''
GYRO_UNIT='''[Unit]
Description=Boiler exact-interface BMI260 binding
After=systemd-udev-trigger.service
Before=inputplumber.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 -I /var/lib/oxp3-gyro-v1/gyro_binding.py start
ExecStopPost=/usr/bin/python3 -I /var/lib/oxp3-gyro-v1/gyro_binding.py stop
TimeoutStartSec=25
TimeoutStopSec=25

[Install]
WantedBy=multi-user.target
'''
PROFILE=dict(version=1,kind='CompositeDevice',name='Boiler ONEXPLAYER USB interface 02',single_source=False,matches=[],source_devices=[
 dict(group='gamepad',evdev=dict(name='Microsoft X-Box 360 pad',handler='event*')),
 dict(group='keyboard',evdev=dict(name='HID 1a86:fe00',handler='event*')),
 dict(group='keyboard',evdev=dict(name='AT Translated Set 2 keyboard',handler='event*')),
 dict(group='gamepad',hidraw=dict(vendor_id=0x1a86,product_id=0xfe00,interface_num=2))],
 options=dict(auto_manage=True),target_devices=['xbox-elite','mouse','keyboard'],capability_map_id='oxp3-installer')
WRAPPER='''#!/bin/sh
if [ "$#" -gt 0 ] && [ "$1" != run ]; then
    exec /usr/lib/boiler-input/runtime/lib/ld-linux-x86-64.so.2 --library-path /usr/lib/boiler-input/runtime/lib /usr/lib/boiler-input/runtime/bin/inputplumber "$@"
fi
/usr/bin/python3 -I /usr/lib/boiler-input/prepare-profile.py || exit $?
cd /run/boiler-input-runtime || exit $?
exec /usr/lib/boiler-input/runtime/lib/ld-linux-x86-64.so.2 --library-path /usr/lib/boiler-input/runtime/lib /usr/lib/boiler-input/runtime/bin/inputplumber "$@"
'''

def sha(raw):return hashlib.sha256(raw).hexdigest()

def source_archive(members):
 buffer=io.BytesIO()
 with gzip.GzipFile(fileobj=buffer,mode='wb',mtime=0) as compressed,tarfile.open(fileobj=compressed,mode='w|') as stream:
  for name,raw in sorted(members.items()):
   if name.startswith('/') or any(part in ('','.','..') for part in name.split('/')) or '\\' in name or ':' in name:raise ValueError('Unsafe corresponding source archive path')
   info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644;stream.addfile(info,io.BytesIO(raw))
 return buffer.getvalue()

def build(project,ip_source,native_runtime,output,*,native_proof,expected_native_proof_sha256,dependency_archives=None,multifunction=False):
 project=Path(project);ip_source=Path(ip_source);output=Path(output)
 if output.exists():raise ValueError('Fresh input runtime output required')
 sys.path.insert(0,str(project))
 from protocol.resource_pool import decoder
 toon=decoder(project/'adaptations')
 proof_raw=Path(native_proof).read_bytes()
 if sha(proof_raw)!=expected_native_proof_sha256:raise ValueError('Externally reviewed native build proof changed')
 proof=toon.loads(proof_raw.decode('utf-8'),strict=True)
 if (proof.get('kind')!='BoilerInputNativeBuildLock' or proof.get('schema_version')!=1 or proof.get('source_build_verified') is not True or
     proof.get('source_commit')!='082f67fba6aaff88441abdc482ae76b711ad2885' or
     proof.get('cargo_command')!=['cargo','build','--release','--offline','--locked','--bin','inputplumber']):
  raise ValueError('Fixed completed native source-build provenance is required')
 native_raw=Path(native_runtime).read_bytes()
 if dict(size=len(native_raw),sha256=sha(native_raw))!=proof['native_runtime']:raise ValueError('Native runtime differs from the external build lock')
 locked_source={row['path']:row for row in proof['source_files']}
 for name,row in locked_source.items():
  path=ip_source/name
  if path.is_symlink():raise ValueError('Source lock cannot follow links')
  raw=path.read_bytes()
  if len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Native corresponding source changed')
 if {r['path'] for r in proof['rootfs']}!={name for name in locked_source if name.startswith('rootfs/')}:
  raise ValueError('Explicit native rootfs source inventory differs')
 dependency_members={};dependency_lock=proof.get('dependency_sources')
 if dependency_lock:
  if dependency_archives is None:raise ValueError('Explicit corresponding dependency archive directory is required')
  for row in dependency_lock['registry_archives']+dependency_lock['git_archives']:
   name=row['local_file']
   if Path(name).name!=name or '\\' in name or ':' in name:raise ValueError('Unsafe corresponding dependency archive name')
   raw=(Path(dependency_archives)/name).read_bytes()
   if len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Corresponding dependency source archive differs')
   dependency_members[name]=raw
 # Read the verified closure without extracting links or arbitrary members.
 native={}
 with tarfile.open(fileobj=io.BytesIO(native_raw),mode='r:gz') as archive:
  for member in archive:
   path=member.name
   if not member.isfile() or path.startswith('/') or '..' in Path(path).parts or path in native or member.size>256*1024*1024:
    raise ValueError('Unsafe native runtime archive member')
   native[path]=archive.extractfile(member).read()
 closure=json.loads(native.pop('closure.json'))
 if closure.get('kind')!='InputPlumberNativeELFClosure' or closure.get('source_build') is not True or closure.get('version')!='inputplumber 0.78.0':
  raise ValueError('Completed source-built native runtime required')
 if set(native)!={r['path'] for r in closure['files']} or len(closure['files'])!=len(native):raise ValueError('Native runtime inventory differs')
 for row in closure['files']:
  raw=native[row['path']]
  if len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Native runtime member differs')
  if row['path']!='bin/inputplumber' and not row['path'].startswith('lib/'):raise ValueError('Native runtime path differs')
 if native['bin/inputplumber'][:5]!=b'\x7fELF\x02' or 'lib/ld-linux-x86-64.so.2' not in native:raise ValueError('Missing native ELF/loader closure')
 module_proof=json.loads((project/SOURCE_PREFIX/'module-build-proof.json').read_bytes())
 if module_proof.get('fresh_source_build') is not True or module_proof.get('old_module_input') is not False or module_proof.get('kernel')!=KERNEL:raise ValueError('Reviewed driver source-build evidence required')
 for name,digest in MODULE_HASHES.items():
  if sha((project/SOURCE_PREFIX/name).read_bytes())!=digest:raise ValueError('Fixed source-built module differs')
 output.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='.input-runtime-',dir=output.parent) as name:
  work=Path(name);records={};components={};configs={}
  def write(relative,raw,mode=0o644):
   target=work/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
   records[relative]=dict(path=relative,size=len(raw),sha256=sha(raw),mode=mode)
  def component(identity):return dict(schema_version=1,kind='ComponentManifest',id='runtime:boiler-input' if identity==INPUT else 'runtime:boiler-gyro',version='0.78.0-localtest.20261031.activation2'+('.r5' if identity==INPUT and multifunction else ''),requires=[] if identity==INPUT else ['runtime:boiler-input'],files=[])
  components={identity:component(identity) for identity in (INPUT,GYRO)}
  def install(identity,target,raw,mode=0o644):
   source=RESOURCE_PATHS[identity]+'/files/'+target;write(source,raw,mode);components[identity]['files'].append(dict(source=source,target=target,size=len(raw),sha256=sha(raw),mode=mode))
  for path,raw in sorted(native.items()):install(INPUT,'usr/lib/boiler-input/runtime/'+path,raw,0o755)
  install(INPUT,'usr/bin/inputplumber',WRAPPER.encode(),0o755)
  install(INPUT,'usr/lib/boiler-input/hid-oxp.ko',(project/SOURCE_PREFIX/'hid-oxp.ko').read_bytes())
  install(INPUT,'usr/lib/boiler-input/input-hid-binding.py',(HERE/'maintenance/input_hid_binding.py').read_bytes())
  install(INPUT,'usr/lib/boiler-input/prepare-profile.py',(HERE/'maintenance/input_profile_runtime.py').read_bytes())
  install(INPUT,'etc/systemd/system/boiler-input-hid.service',HID_UNIT.encode())
  install(INPUT,'etc/systemd/system/inputplumber.service',INPUT_UNIT.encode())
  # Corresponding rootfs is part of the same pinned InputPlumber source tree.
  for row in sorted(proof['rootfs'],key=lambda r:r['path']):
   path=ip_source/row['path'];rel=row['path'].removeprefix('rootfs/')
   if rel=='usr/lib/systemd/system/inputplumber.service':continue
   if rel.startswith('usr/lib/udev/') and rel!='usr/lib/udev/rules.d/60-inputplumber-uaccess.rules':continue
   raw=path.read_bytes()
   if rel=='usr/share/dbus-1/system-services/org.shadowblip.InputPlumber.service':
    raw=b'[D-BUS Service]\nName=org.shadowblip.InputPlumber\nUser=root\nExec=/usr/bin/inputplumber\nSystemdService=inputplumber.service\n'
   if rel=='usr/lib/systemd/system/inputplumber-suspend.service':raw=raw.replace(b'ExecStart=busctl',b'ExecStart=/usr/bin/busctl').replace(b'ExecStop=busctl',b'ExecStop=/usr/bin/busctl')
   install(INPUT,rel,raw)
  install(INPUT,'usr/share/inputplumber/devices/00-boiler-onexplayer.yaml',(json.dumps(PROFILE,indent=2)+'\n').encode())
  install(GYRO,'var/lib/oxp3-gyro-v1/oxp3_bmi260.ko',(project/SOURCE_PREFIX/'oxp3_bmi260.ko').read_bytes())
  install(GYRO,'var/lib/oxp3-gyro-v1/gyro_binding.py',(HERE/'maintenance/input_gyro_binding.py').read_bytes())
  install(GYRO,'etc/systemd/system/oxp3-gyro.service',GYRO_UNIT.encode())
  install(GYRO,'etc/systemd/system/inputplumber.service.d/90-boiler-gyro.conf',b'[Unit]\nRequires=oxp3-gyro.service\nAfter=oxp3-gyro.service\n')
  # Xbox targets have no motion channel. A selected gyro uses the supported
  # DualSense Edge target, which carries motion and the paddle mappings.
  gyro_profile=json.loads(json.dumps(PROFILE));gyro_profile['target_devices']=['ds5-edge','mouse','keyboard']
  # The native IIO driver reads the device's mount matrix, falling back to its
  # own documented default. Do not substitute another model's axis mapping.
  gyro_profile['source_devices'].append(dict(group='imu',iio=dict(name='{i2c-BMI0260:00,i2c-BOSC0260:00,bmi260}')))
  install(GYRO,'etc/inputplumber/devices.d/00-boiler-onexplayer.yaml',(json.dumps(gyro_profile,indent=2)+'\n').encode())
  for identity,target,filename,mode in ((INPUT,CAPMAP,'capmap-r5.yaml' if multifunction else 'capmap-keyboard.yaml',0o644),(GYRO,CALIBRATION,'calibration-default.json',0o600)):
   raw=(project/SOURCE_PREFIX/filename).read_bytes();source=RESOURCE_PATHS[identity]+'/defaults/'+filename;write(source,raw,mode)
   configs[identity]=dict(source=source,target=target,size=len(raw),sha256=sha(raw),mode=mode)
  write(PROVENANCE+'/native-closure.toon',(toon.dumps(closure)+'\n').encode())
  write(PROVENANCE+'/native-build-lock.toon',proof_raw)
  module_proof_raw=(project/SOURCE_PREFIX/'module-build-proof.json').read_bytes()
  module_public_proof=dict(module_proof,schema_version=1,original_json_sha256=sha(module_proof_raw))
  write(PROVENANCE+'/module-build-proof.toon',(toon.dumps(module_public_proof)+'\n').encode())
  module_sources=(project/SOURCE_PREFIX/'source-manifest.json').read_bytes();module_members={'source-manifest.json':module_sources,'module-build-proof.json':module_proof_raw}
  for row in json.loads(module_sources)['files']:
   raw=(project/SOURCE_PREFIX/'sources'/row['path']).read_bytes()
   if len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Corresponding driver source changed')
   module_members['sources/'+row['path']]=raw
  module_archive=source_archive(module_members);write(PROVENANCE+'/module-sources.tar.gz',module_archive)
  write(PROVENANCE+'/module-sources.toon',(toon.dumps(dict(kind='BoilerInputModuleCorrespondingSources',schema_version=1,archive=dict(path=PROVENANCE+'/module-sources.tar.gz',size=len(module_archive),sha256=sha(module_archive)),files=json.loads(module_sources)['files'],original_manifest_json_sha256=sha(module_sources)))+'\n').encode())
  ip_archive=source_archive({name:(ip_source/name).read_bytes() for name in locked_source});write(PROVENANCE+'/InputPlumber-source.tar.gz',ip_archive)
  write(PROVENANCE+'/InputPlumber-source.toon',(toon.dumps(dict(kind='BoilerInputPlumberCorrespondingSource',schema_version=1,archive=dict(path=PROVENANCE+'/InputPlumber-source.tar.gz',size=len(ip_archive),sha256=sha(ip_archive)),files=proof['source_files']))+'\n').encode())
  if dependency_lock:
   dependency_archive=source_archive(dependency_members);write(PROVENANCE+'/InputPlumber-dependency-sources.tar.gz',dependency_archive)
   write(PROVENANCE+'/InputPlumber-dependencies.toon',(toon.dumps(dict(dependency_lock,archive=dict(path=PROVENANCE+'/InputPlumber-dependency-sources.tar.gz',size=len(dependency_archive),sha256=sha(dependency_archive))))+'\n').encode())
  for name in sorted(locked_source):
   if name.startswith('LICENSES/'):write('licenses/input-runtime/inputplumber/'+name.removeprefix('LICENSES/'),(ip_source/name).read_bytes())
  for name in ('LICENSE','LICENSE.md','COPYING'):
   if name in locked_source:write('licenses/input-runtime/inputplumber/'+name,(ip_source/name).read_bytes())
  write('licenses/input-runtime/GPL-2.0.txt',(project/'adaptations/licenses/GPL-2.0.txt').read_bytes())
  value=dict(kind='BoilerInputRuntime',schema_version=1,kernel=KERNEL,multifunction=multifunction,components=components,configs=configs,
             hardware_accepted=False,calibration_policy='preserved calibration JSON is applied to the source-built IIO bias and auto-calibration fields on every InputPlumber start')
  write(RUNTIME_MANIFEST,(toon.dumps(value)+'\n').encode())
  spec=importlib.util.spec_from_file_location('fixed_live_input_contract',HERE/'protocol/input_live_contract.py');contract=importlib.util.module_from_spec(spec);spec.loader.exec_module(contract)
  resource_refs=[]
  for identity in (INPUT,GYRO):
   rows=[dict(row,scope='root-slots') for row in components[identity]['files']]+[dict(configs[identity],scope='root-slots')]
   rows=[{key:row[key] for key in ('source','target','scope','mode')} for row in rows]
   payload=[{key:records[row['source']][key] for key in ('path','size','sha256')} for row in rows]
   hardware=dict(input_usb_vid=['1a86'],input_usb_pid=['fe00'],input_usb_interface=['02'],input_usb_mcu_unique=[True])
   if identity==GYRO:hardware['bmi260_acpi_id']=['10EC5280:00']
   resource=dict(schema_version=1,kind='BoilerResource',id=identity,version='0.78.0-localtest.20261031.activation2'+('.r5' if identity==INPUT and multifunction else ''),
    name='OneXPlayer input / multifunction' if identity==INPUT else 'BMI260 six-axis motion',parameters=dict(multifunction=multifunction if identity==INPUT else False),
    payload=dict(files=payload),dependencies=[] if identity==INPUT else [INPUT],hardware_requires=hardware,
    compatibility=dict(architectures=['x86_64'],os_ids=['steamos'],os_versions=['3.10'],kernels=[KERNEL]),
    install=dict(kind='boiler-input-runtime-v1',files=rows,runtime_manifest=RUNTIME_MANIFEST,config_policy=dict(kind='preserve-user-input-config-v1',target=configs[identity]['target'])),
    verify=dict(kind='input-live-state-v1'),restore=dict(kind='input-live-transaction-v1'),activation=contract.ACTIVATION,
    license_files=sorted(name for name in records if name.startswith('licenses/input-runtime/')),
    provenance=dict(native_build=PROVENANCE+'/native-build-lock.toon',module_build=PROVENANCE+'/module-build-proof.toon'),
    limitations=['Physical buttons, motion, suspend/resume and firmware acceptance are tested by the device owner.'])
   contract.validate_contract(resource);path=RESOURCE_PATHS[identity]+'/resource.toon';write(path,(toon.dumps(resource)+'\n').encode())
   resource_refs.append(dict(id=identity,version=resource['version'],path=path,sha256=records[path]['sha256']))
  index=dict(kind='BoilerInputRuntimeBundle',schema_version=1,resources=resource_refs,runtime_manifest=RUNTIME_MANIFEST,files=[{key:row[key] for key in ('path','size','sha256')} for row in records.values()])
  write('input-runtime-index.toon',(toon.dumps(index)+'\n').encode())
  os.rename(work,output)
 return dict(output=str(output),files=len(records),manifest_sha256=sha((output/RUNTIME_MANIFEST).read_bytes()),source_built=True,resources=resource_refs)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--ip-source',type=Path,required=True);parser.add_argument('--native-runtime',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--native-proof',type=Path,required=True);parser.add_argument('--expected-native-proof-sha256',required=True);parser.add_argument('--dependency-archives',type=Path);parser.add_argument('--multifunction',action='store_true')
 a=parser.parse_args();print(json.dumps(build(a.project,a.ip_source,a.native_runtime,a.output,native_proof=a.native_proof,expected_native_proof_sha256=a.expected_native_proof_sha256,dependency_archives=a.dependency_archives,multifunction=a.multifunction),indent=2))
