"""Fixed combined GM7/split-lock renderer; no shell hooks and one GRUB owner."""
from pathlib import Path
import hashlib,re

IDENTITY='resource:common-boot-fixes'
GRUB='efi/EFI/steamos/grub.cfg'
SPLIT='etc/default/grub.d/boiler-splitlock.cfg'
GM7='etc/default/grub.d/boiler-nvme.cfg'

class BootResourceError(RuntimeError):pass

def affected_gm7(target):
 return isinstance(target,dict) and target.get('bus')=='NVMe' and isinstance(target.get('model'),str) and re.search(r'\bGM7\b',target['model'],re.I) is not None

def patch(text,*,split_lock,gm7):
 if type(split_lock) is not bool or type(gm7) is not bool:raise BootResourceError('Boot policy values must be booleans')
 count=0;lines=text.splitlines(keepends=True)
 for index,line in enumerate(lines):
  if not re.match(r'^\s*(?:steamenv_boot|linux|linuxefi)\s',line) or '/boot/vmlinuz-' not in line:continue
  tokens=line.split()
  if any(t.startswith('#') for t in tokens) or '\\' in line or ';' in line:raise BootResourceError('Boot line requires manual review')
  ending='\r\n' if line.endswith('\r\n') else '\n'
  wanted=[]
  if split_lock:
   flags=[t for t in tokens if t.startswith('split_lock_detect=')]
   if any(t!='split_lock_detect=off' for t in flags) or len(flags)>1:raise BootResourceError('Existing split-lock policy conflicts')
   if not flags:wanted.append('split_lock_detect=off')
  if gm7:
   flags=[t for t in tokens if t.startswith(('nvme.noacpi=','nvme.quirks='))]
   if any(t!='nvme.noacpi=1' for t in flags) or len(flags)>1:raise BootResourceError('Existing NVMe policy conflicts')
   if not flags:wanted.append('nvme.noacpi=1')
  if wanted:lines[index]=line.rstrip('\r\n')+' '+' '.join(wanted)+ending
  count+=1
 if not count:raise BootResourceError('No supported Valve boot entries in slot EFI')
 return ''.join(lines)

def render(root,*,split_lock=True,gm7_resume=True,target_disk=None):
 """Return bytes and input digests; caller must bind them inside Engine apply."""
 from protocol.manifest import confined
 from maintenance.adoption_blobs import _file
 root=Path(root)
 if type(split_lock) is not bool or type(gm7_resume) is not bool:raise BootResourceError('Boot policy values must be booleans')
 use_gm7=gm7_resume and affected_gm7(target_disk)
 files={};inputs={}
 if split_lock or use_gm7:
  target=confined(root,GRUB)
  with _file(target) as (stream,info):
   if info.st_size>8*1024*1024:raise BootResourceError('Boot configuration exceeds limit')
   before=stream.read(8*1024*1024+1)
  after=patch(before.decode('utf-8'),split_lock=split_lock,gm7=use_gm7).encode('utf-8')
  files[GRUB]=dict(data=after,mode=420);inputs[GRUB]=hashlib.sha256(before).hexdigest()
 if split_lock:files[SPLIT]=dict(data=b'GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX} split_lock_detect=off"\n',mode=420)
 if use_gm7:files[GM7]=dict(data=b'GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX} nvme.noacpi=1"\n',mode=420)
 for name,row in files.items():
  if name==GRUB:continue
  target=confined(root,name)
  if target.exists():
   with _file(target) as (stream,info):existing=stream.read(8193)
   if existing!=row['data']:raise BootResourceError('Existing boot drop-in differs; preserve it for review')
 return dict(identity=IDENTITY,files=files,input_sha256=inputs,split_lock=split_lock,gm7_applied=use_gm7,gm7_applicable=affected_gm7(target_disk),restart_required=bool(files))


def validate_contract(resource):
 if (resource.get('id')!=IDENTITY or resource.get('install')!={'kind':'boiler-boot-settings-v1','files':[]}
     or resource.get('payload')!={'files':[]} or resource.get('fixed_targets')!=[GRUB,SPLIT,GM7]
     or resource.get('verify')!={'kind':'sha256-v1'} or resource.get('restore')!={'kind':'transaction-backup-v1'}
     or resource.get('hooks') or resource.get('dynamic_handler')):raise BootResourceError('Invalid fixed combined boot resource contract')
 parameters=resource.get('parameters')
 if not isinstance(parameters,dict) or set(parameters)!={'split_lock','gm7_resume'} or any(type(v) is not bool for v in parameters.values()) or not any(parameters.values()):raise BootResourceError('Invalid fixed combined boot choices')
 return resource


def prepare(resource,template,engine,*,target_disk=None):
 import copy
 validate_contract(resource)
 rendered=render(engine.root,**resource['parameters'],target_disk=target_disk)
 manifest=copy.deepcopy(template);rows=[];payload={}
 for target,item in rendered['files'].items():
  source='boot-render/'+target;data=item['data'];payload[source]=data
  rows.append(dict(source=source,target=target,size=len(data),sha256=hashlib.sha256(data).hexdigest(),mode=item['mode']))
 manifest['files']=rows
 manifest['renderer_input_sha256']=rendered['input_sha256'].get(GRUB)
 manifest['boot_policy']={k:rendered[k] for k in ('split_lock','gm7_applied','gm7_applicable','restart_required')}
 return dict(manifest=manifest,files=payload)


def verification(resource,journal,engine):
 """Recompute the fixed generated bytes from the journal's immutable original."""
 from maintenance.adoption_blobs import blob_cache,_file,_hash_file,validate_backups
 import copy,base64
 validate_contract(resource);manifest=copy.deepcopy(journal.get('component',{}));manifest.pop('resource_group',None)
 if manifest.get('id')!=IDENTITY or manifest.get('version')!=resource['version'] or manifest.get('hooks'):return dict(ok=False,reason='boot-manifest-identity')
 policy=manifest.get('boot_policy',{})
 if set(policy)!={'split_lock','gm7_applied','gm7_applicable','restart_required'} or any(type(v) is not bool for v in policy.values()):return dict(ok=False,reason='boot-policy-shape')
 if policy['split_lock']!=resource['parameters']['split_lock'] or (policy['gm7_applied'] and (not policy['gm7_applicable'] or not resource['parameters']['gm7_resume'])):return dict(ok=False,reason='boot-policy-choice')
 operations={op['target']:op for op in journal['operations']};rows={row['target']:row for row in manifest.get('files',[])}
 expected={}
 if policy['split_lock'] or policy['gm7_applied']:
  if GRUB not in operations:return dict(ok=False,reason='boot-original-missing')
  original=operations[GRUB]['before'];validate_backups(engine,[operations[GRUB]])
  if not original.get('exists') or original.get('size',0)>8*1024*1024:return dict(ok=False,reason='boot-original-invalid')
  if original.get('storage')=='blob-v1':
   path=blob_cache(engine)/original['sha256'];_hash_file(path,original,private=True)
   with _file(path,private=True) as (stream,info):raw=stream.read(8*1024*1024+1)
  else:raw=base64.b64decode(original['data'],validate=True)
  if hashlib.sha256(raw).hexdigest()!=manifest.get('renderer_input_sha256'):return dict(ok=False,reason='boot-original-binding')
  expected[GRUB]=patch(raw.decode('utf-8'),split_lock=policy['split_lock'],gm7=policy['gm7_applied']).encode()
 if policy['split_lock']:expected[SPLIT]=b'GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX} split_lock_detect=off"\n'
 if policy['gm7_applied']:expected[GM7]=b'GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX} nvme.noacpi=1"\n'
 if set(rows)!=set(expected) or set(operations)!=set(expected):return dict(ok=False,reason='boot-target-set')
 for target,data in expected.items():
  row=rows[target]
  if row!={'source':'boot-render/'+target,'target':target,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mode':420}:return dict(ok=False,reason='boot-generated-bytes')
 return dict(ok=True,policy=policy,scope='fixed-generated-boot-files',restart_required=policy['restart_required'])
