"""Import only an explicitly inventoried runtime bundle into the source library."""
import argparse,copy,hashlib,importlib.util,json,sys
from pathlib import Path
LIBRARY='adaptations/brands/onexplayer/common/resource-inputs/input-runtime-v1'
DEFINITIONS={'resource:onexplayer-input':'adaptations/devices/Controller/onexplayer-input/resource.toon','resource:bosch-bmi260-gyro':'adaptations/devices/Sensor/bosch-bmi260-gyro/resource.toon'}
def sha(raw):return hashlib.sha256(raw).hexdigest()
def import_bundle(bundle,project,tools,*,multifunction=False,standalone_r5=False):
 bundle=Path(bundle);project=Path(project);sys.path.insert(0,str(tools))
 from protocol.resource_pool import decoder,local
 from protocol.manifest import _filesystem_path
 toon=decoder(Path(tools)/'adaptations');index=toon.loads((bundle/'input-runtime-index.toon').read_text(),strict=True)
 if index.get('kind')!='BoilerInputRuntimeBundle' or index.get('schema_version')!=1:raise ValueError('Fixed input runtime bundle index required')
 variant='r5' if multifunction else 'keyboard';prefix=LIBRARY+'/'+variant;written={}
 def write(name,raw):
  path=_filesystem_path(local(project,name))
  if path.exists() and path.read_bytes()!=raw:raise ValueError('Do not overwrite a different source-library input: '+name)
  path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);written[name]=dict(path=name,size=len(raw),sha256=sha(raw))
 for row in index['files']:
  raw=local(bundle,row['path']).read_bytes()
  if len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Runtime input member differs')
  write(prefix+'/'+row['path'],raw)
 definitions=[]
 for ref in index['resources']:
  resource=toon.loads(local(bundle,ref['path']).read_text(),strict=True)
  if resource['parameters']['multifunction']!=(multifunction if resource['id']=='resource:onexplayer-input' else False):raise ValueError('Source variant parameter differs')
  for row in resource['payload']['files']:row['path']=prefix+'/'+row['path']
  for row in resource['install']['files']:row['source']=prefix+'/'+row['source']
  resource['license_files']=[prefix+'/'+name for name in resource['license_files']]
  resource['provenance']={key:prefix+'/'+name for key,name in resource['provenance'].items()}
  path=DEFINITIONS[resource['id']]
  if multifunction and resource['id']=='resource:onexplayer-input':path=path.removesuffix('/resource.toon')+'/variants/multifunction-r5.resource.toon'
  elif multifunction and not standalone_r5:continue # Legacy combined library retains the keyboard gyro definition.
  raw=(toon.dumps(resource)+'\n').encode();write(path,raw);definitions.append(dict(path=path,size=len(raw),sha256=sha(raw)))
 lock=dict(kind='BoilerInputRuntimeSourceLock',schema_version=1,multifunction=multifunction,pool_resources=index['resources'],
           files=[row for name,row in written.items() if name.startswith(prefix+'/')],definitions=definitions,
           native_build_sha256=sha(local(bundle,'provenance/input-runtime/native-build-lock.toon').read_bytes()))
 name=prefix+'/source-input-lock.toon';write(name,(toon.dumps(lock)+'\n').encode())
 return dict(kind='InputRuntimeSourceImport',source_prefix=prefix,files=list(written.values()))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);p.add_argument('--project',type=Path,required=True);p.add_argument('--tools',type=Path,required=True);p.add_argument('--multifunction',action='store_true');p.add_argument('--standalone-r5',action='store_true');p.add_argument('--report',type=Path,required=True);a=p.parse_args()
 report=import_bundle(a.bundle,a.project,a.tools,multifunction=a.multifunction,standalone_r5=a.standalone_r5)
 from protocol.resource_pool import decoder
 a.report.write_text(decoder(a.tools/'adaptations').dumps(dict(report,schema_version=1))+'\n');print(json.dumps(dict(files=len(report['files']),source_prefix=report['source_prefix']),indent=2))
