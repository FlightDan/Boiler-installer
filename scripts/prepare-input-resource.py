"""Compile authoritative active input resources from explicit locked local inputs."""
import argparse,copy,hashlib,json,os,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from protocol.resource_pool import decoder,local,validate_pool
from protocol.input_live_contract import INPUT,GYRO,MANIFEST,CONFIGS,validate_contract
LIBRARY='adaptations/brands/onexplayer/common/resource-inputs/input-runtime-v1'
DEFINITIONS={INPUT:'adaptations/devices/Controller/onexplayer-input/resource.toon',GYRO:'adaptations/devices/Sensor/bosch-bmi260-gyro/resource.toon'}
def sha(raw):return hashlib.sha256(raw).hexdigest()
def build(project,output,*,multifunction=False,source_root=None):
 project=Path(project);output=Path(output)
 if output.exists() or type(multifunction) is not bool:raise ValueError('Fresh output and exact multifunction boolean required')
 source=Path(source_root) if source_root is not None else project
 from protocol.manifest import _filesystem_path
 prefix=LIBRARY+'/'+('r5' if multifunction else 'keyboard');toon=decoder(project/'adaptations');lock=toon.loads(_filesystem_path(local(source,prefix+'/source-input-lock.toon')).read_text(),strict=True)
 if lock.get('kind')!='BoilerInputRuntimeSourceLock' or lock.get('multifunction') is not multifunction:raise ValueError('Fixed source-library variant lock required')
 records={}
 for row in lock['files']:
  if not row['path'].startswith(prefix+'/'):raise ValueError('Input source inventory escapes its fixed library')
  raw=_filesystem_path(local(source,row['path'])).read_bytes()
  if len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Explicit input source member changed')
  records[row['path'].removeprefix(prefix+'/')]=raw
 if sha(records['provenance/input-runtime/native-build-lock.toon'])!=lock['native_build_sha256']:raise ValueError('Native build provenance differs')
 refs=[];runtime=toon.loads(records[MANIFEST].decode('utf-8'),strict=True)
 for identity in (INPUT,GYRO):
  definition=DEFINITIONS[identity]
  if identity==INPUT and multifunction:definition=definition.removesuffix('/resource.toon')+'/variants/multifunction-r5.resource.toon'
  resource=toon.loads(_filesystem_path(local(source,definition)).read_text(),strict=True)
  # Gyro uses the common same-byte runtime assets in the selected variant.
  source_prefix=LIBRARY+'/keyboard' if identity==GYRO and multifunction and source_root is None else prefix
  validate_contract(resource,source_prefix=source_prefix)
  expected=multifunction if identity==INPUT else False
  if resource['parameters']!={'multifunction':expected}:raise ValueError('Authoritative input parameter differs')
  for row in resource['payload']['files']:
   name=row['path'].removeprefix(source_prefix+'/');raw=records[name]
   if len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Authoritative resource payload differs')
   row['path']=name
  for row in resource['install']['files']:row['source']=row['source'].removeprefix(source_prefix+'/')
  resource['license_files']=[name.removeprefix(source_prefix+'/') for name in resource['license_files']]
  resource['provenance']={key:name.removeprefix(source_prefix+'/') for key,name in resource['provenance'].items()}
  validate_contract(resource)
  payload={row['path']:row for row in resource['payload']['files']}
  actual=[dict(row,**{key:payload[row['source']][key] for key in ('size','sha256')}) for row in resource['install']['files']]
  actual=[{key:row[key] for key in ('source','target','size','sha256','mode')} for row in actual]
  config=[row for row in actual if row['target']==CONFIGS[identity]]
  if len(config)!=1:raise ValueError('Authoritative fixed input configuration differs')
  runtime['configs'][identity]=config[0]
  runtime['components'][identity]=dict(schema_version=1,kind='ComponentManifest',id='runtime:boiler-input' if identity==INPUT else 'runtime:boiler-gyro',version=resource['version'],requires=[] if identity==INPUT else ['runtime:boiler-input'],files=[row for row in actual if row['target']!=CONFIGS[identity]])
  if identity==INPUT:runtime['multifunction']=multifunction
  path='devices/'+('Controller/onexplayer-input' if identity==INPUT else 'Sensor/bosch-bmi260-gyro')+'/resource.toon'
  records[path]=(toon.dumps(resource)+'\n').encode();refs.append(dict(id=identity,version=resource['version'],path=path,sha256=sha(records[path])))
 records[MANIFEST]=(toon.dumps(runtime)+'\n').encode()
 output.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='.input-active-',dir=output.parent) as name:
  work=Path(name);models=[]
  for model in ('device:oxp3','device:x2-mini'):
   record=dict(schema_version=1,kind='BoilerResourceModel',id=model,name=model,hardware_matching=dict(mode='advisory-exact-dmi-v1',pairs=[]),parameters={},resources=[dict(resource=i,enabled=False,default=False,parameters={}) for i in (INPUT,GYRO)],pending=[])
   path='brands/onexplayer/models/'+model.split(':')[1]+'.toon';records[path]=(toon.dumps(record)+'\n').encode();models.append(dict(id=model,path=path,sha256=sha(records[path])))
  for name,raw in records.items():
   path=_filesystem_path(local(work,name));path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
  catalog=dict(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False,resources=refs,models=models,files=[dict(path=name,size=len(raw),sha256=sha(raw)) for name,raw in sorted(records.items())])
  (work/'catalog.toon').write_text(toon.dumps(catalog)+'\n',newline='\n');(work/'catalog.json').write_text(json.dumps(catalog,indent=2)+'\n')
  validate_pool(work);os.rename(work,output)
 return dict(resources=2,models=2,source_library_authoritative=True,active_lifecycle=True,multifunction=multifunction,catalog_sha256=sha((output/'catalog.toon').read_bytes()))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--project',type=Path,default=ROOT);p.add_argument('--output',type=Path,required=True);p.add_argument('--multifunction',action='store_true');a=p.parse_args();print(json.dumps(build(a.project,a.output,multifunction=a.multifunction),indent=2))
