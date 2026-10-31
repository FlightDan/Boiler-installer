"""Data contract for the fixed, executable input lifecycle (not files-v1)."""
INPUT='resource:onexplayer-input';GYRO='resource:bosch-bmi260-gyro';IDS={INPUT,GYRO}
KERNEL='7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
PATHS={INPUT:'devices/Controller/onexplayer-input',GYRO:'devices/Sensor/bosch-bmi260-gyro'}
MANIFEST=PATHS[INPUT]+'/runtime-manifest.toon'
KINDS={'install':'boiler-input-runtime-v1','verify':'input-live-state-v1','restore':'input-live-transaction-v1'}
CONFIGS={INPUT:'etc/inputplumber/capability_maps.d/99-boiler-onexplayer.yaml',GYRO:'etc/boiler-input/calibration.json'}
ACTIVATION=dict(kind='fixed-input-provider-v1',autoload=True,phase='first-boot')
HARDWARE={INPUT:dict(input_usb_vid=['1a86'],input_usb_pid=['fe00'],input_usb_interface=['02'],input_usb_mcu_unique=[True]),GYRO:dict(input_usb_vid=['1a86'],input_usb_pid=['fe00'],input_usb_interface=['02'],input_usb_mcu_unique=[True],bmi260_acpi_id=['10EC5280:00'])}
REQUIRED={INPUT:{'usr/bin/inputplumber','usr/lib/boiler-input/runtime/bin/inputplumber','usr/lib/boiler-input/runtime/lib/ld-linux-x86-64.so.2','usr/lib/boiler-input/hid-oxp.ko','usr/lib/boiler-input/input-hid-binding.py','usr/lib/boiler-input/prepare-profile.py','etc/systemd/system/inputplumber.service','etc/systemd/system/boiler-input-hid.service','usr/lib/systemd/system/inputplumber-suspend.service','usr/share/inputplumber/devices/00-boiler-onexplayer.yaml',CONFIGS[INPUT]},GYRO:{'var/lib/oxp3-gyro-v1/oxp3_bmi260.ko','var/lib/oxp3-gyro-v1/gyro_binding.py','etc/systemd/system/oxp3-gyro.service','etc/systemd/system/inputplumber.service.d/90-boiler-gyro.conf','etc/inputplumber/devices.d/00-boiler-onexplayer.yaml',CONFIGS[GYRO]}}
def validate_contract(resource,*,source_prefix=''):
 identity=resource.get('id');install=resource.get('install',{})
 if identity not in IDS or install.get('kind')!=KINDS['install']:raise ValueError('Fixed active input identity/install kind required')
 if resource.get('verify')!={'kind':KINDS['verify']} or resource.get('restore')!={'kind':KINDS['restore']} or resource.get('activation')!=ACTIVATION:
  raise ValueError('Fixed active input lifecycle differs')
 if install.get('runtime_manifest')!=MANIFEST or install.get('config_policy')!={'kind':'preserve-user-input-config-v1','target':CONFIGS[identity]}:
  raise ValueError('Fixed active input runtime/configuration contract differs')
 if resource.get('dependencies')!=([] if identity==INPUT else [INPUT]) or resource.get('hooks') or resource.get('dynamic_handler'):
  raise ValueError('Active input dependencies/hooks differ')
 if resource.get('compatibility')!=dict(architectures=['x86_64'],os_ids=['steamos'],os_versions=['3.10'],kernels=[KERNEL]):raise ValueError('Active input target ABI differs')
 if resource.get('hardware_requires')!=HARDWARE[identity] or any(type(resource['hardware_requires'][key][0]) is not type(value[0]) for key,value in HARDWARE[identity].items()):raise ValueError('Active input interface requirements differ')
 choice=resource.get('parameters')
 if choice not in ({'multifunction':False},{'multifunction':True}) or type(choice['multifunction']) is not bool or (identity==GYRO and choice['multifunction']):
  raise ValueError('Active input fixed parameters differ')
 rows=install.get('files',[]);payload=resource.get('payload',{}).get('files',[])
 if not rows or len(rows)!=len({r['target'] for r in rows}) or len(payload)!=len({r['path'] for r in payload}) or {r['source'] for r in rows}!={r['path'] for r in payload}:
  raise ValueError('Active input complete installation inventory required')
 prefix=source_prefix.rstrip('/')+'/' if source_prefix else ''
 if any(r.get('scope')!='root-slots' or r.get('mode') not in (0o600,0o644,0o755) or not r['source'].startswith(prefix+PATHS[identity]+'/') for r in rows):
  raise ValueError('Active input payload scope/mode differs')
 if not any(r['target']==CONFIGS[identity] for r in rows):raise ValueError('Active input configuration missing')
 if not REQUIRED[identity]<={r['target'] for r in rows}:raise ValueError('Active input runtime is missing required executable/configuration members')
 if any(r['target'].startswith('var/lib/boiler-input-staging/') for r in rows):raise ValueError('Active input cannot point at staging files')
 return resource
