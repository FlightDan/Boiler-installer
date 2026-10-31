"""Merge fixed active input resources into one explicitly hash-bound shared pool."""
import copy,hashlib,json,os,tempfile
from pathlib import Path
from protocol.local import _safe_path
from protocol.resource_pool import decoder,local,validate_pool
from protocol.input_live_contract import IDS,INPUT,GYRO,validate_contract

def sha(raw):return hashlib.sha256(raw).hexdigest()
def snapshot(root,catalog):
 result={}
 for row in catalog['files']:
  raw=local(root,row['path']).read_bytes()
  if len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Input pool changed after validation')
  result[row['path']]=raw
 return result

def build(base_root,input_root,output,*,base_sha256,input_sha256):
 base_root,input_root,output=map(_safe_path,(base_root,input_root,output))
 if os.path.lexists(output):raise FileExistsError('Fresh active input merge output required')
 if any(output==root or root in output.parents for root in (base_root,input_root)):raise ValueError('Active merge cannot overlap inputs')
 base=validate_pool(base_root,expected_catalog_sha256=base_sha256);inputs=validate_pool(input_root,expected_catalog_sha256=input_sha256)
 if {r['id'] for r in inputs['resources']}!=IDS or {m['id'] for m in base['models']}!={'device:oxp3','device:x2-mini'}:
  raise ValueError('Two fixed active input resources and the explicit supported models required')
 old=[r for r in base['resources'] if r['id'] in IDS]
 if any(r['install']['kind'] not in ('onexplayer-input-staging-v1','bosch-bmi260-staging-v1','boiler-input-runtime-v1') for r in old):
  raise ValueError('Input identity is occupied by an unrelated lifecycle')
 files=snapshot(base_root,base);extra=snapshot(input_root,inputs);toon=decoder()
 protected={row['path'] for r in base['resources'] if r['id'] not in IDS for row in r['payload']['files']}
 protected.update(name for r in base['resources'] if r['id'] not in IDS for name in r['license_files'])
 for resource in old:
  for row in resource['payload']['files']:
   if row['path'] not in protected:files.pop(row['path'],None)
  for ref in base['resource_refs']:
   if ref['id']==resource['id']:files.pop(ref['path'],None)
 # Old inactive metadata is historical input, never final active provenance.
 if old:
  for name in list(files):
   if name.startswith(('devices/Controller/onexplayer-input/','devices/Sensor/bosch-bmi260-gyro/','provenance/input-runtime/','provenance/input-resource-definitions/','provenance/input-module-sources/')) or name in ('provenance/module-build-proof.json','provenance/merge-input-staging.json','provenance/merge-input-runtime.toon'):
    if name not in protected:files.pop(name)
 refs=[copy.deepcopy(r) for r in base['resource_refs'] if r['id'] not in IDS]
 excluded={r['path'] for r in inputs['model_refs']}
 for resource in inputs['resources']:validate_contract(resource)
 for name,raw in extra.items():
  if name in excluded:continue
  if name in files and files[name]!=raw:raise ValueError('Active input/base member conflict: '+name)
  files[name]=raw
 refs.extend(copy.deepcopy(inputs['resource_refs']))
 models=[]
 for original,ref in zip(base['models'],base['model_refs']):
  model=copy.deepcopy(original);known={r['resource'] for r in model['resources']}
  model['resources'].extend(dict(resource=i,enabled=False,default=False,parameters={}) for i in (INPUT,GYRO) if i not in known)
  model['pending']=[r for r in model['pending'] if r.get('component') not in IDS|{'input-controls'}]
  raw=(toon.dumps(model)+'\n').encode();files[ref['path']]=raw;models.append(dict(id=model['id'],path=ref['path'],sha256=sha(raw)))
 proof=dict(schema_version=1,kind='FixedInputRuntimeResourceMerge',base_catalog_sha256=base_sha256,input_catalog_sha256=input_sha256,
            base_non_input_records_preserved=True,base_model_parameters_preserved=True,active_lifecycle=True,defaults_enabled=False,
            hardware_accepted=False,old_staging_definitions_replaced=bool(old))
 files['provenance/merge-input-runtime.toon']=(toon.dumps(proof)+'\n').encode()
 catalog=dict(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False,resources=refs,models=models,
              files=[dict(path=name,size=len(raw),sha256=sha(raw)) for name,raw in sorted(files.items())])
 output.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='.merge-input-active-',dir=output.parent) as name:
  work=Path(name)
  for name,raw in files.items():
   path=local(work,name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
  (work/'catalog.toon').write_text(toon.dumps(catalog)+'\n',newline='\n');(work/'catalog.json').write_text(json.dumps(catalog,indent=2)+'\n')
  validate_pool(work);os.rename(work,output)
 return validate_pool(output)
