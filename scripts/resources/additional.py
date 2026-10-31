"""Compile six project-owned source TOON declarations to normal journaled resources."""
import copy,hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from protocol.resource_pool import decoder,local,validate_pool,json_document
sha=lambda b:hashlib.sha256(b).hexdigest()
def render(raw,kind,identity,target,toon=None):
 if kind=='toon-json-v1':
  if identity!='resource:onexplayer-fan' or target not in ('var/lib/boiler/decky/runtime/plugins/OXP3-Fan/plugin.json','var/lib/boiler/decky/runtime/plugins/OXP3-Fan/package.json') or toon is None:raise ValueError('Invalid fixed TOON-to-JSON target')
  return (json.dumps(toon.loads(raw.decode('utf-8'),strict=True),ensure_ascii=False,indent=2)+'\n').encode('utf-8')
 if kind=='identity-v1':return raw
 text=raw.decode('utf-8')
 if kind=='fixed-approval-gate-v1':
  old='ExecCondition=/usr/bin/python3 -I -B /usr/local/lib/oxp3-integration/integration_runtime.py guard\n'
  if text.count(old)!=1 or text.count('[Unit]\n')!=1:raise ValueError('Legacy unit fixed guard anchor differs')
  text=text.replace(old,'').replace('[Unit]\n','[Unit]\nConditionPathExists=/var/lib/boiler-resource-approved/'+identity.split(':',1)[1]+'.json\n',1)
 elif kind=='fan-private-config-v1':
  if identity!='resource:onexplayer-fan' or target!='var/lib/oxp3-fan/fan.py' or text.count('tmp.chmod(0o644)')!=1:raise ValueError('Unexpected fixed fan config renderer')
  text=text.replace('tmp.chmod(0o644)','tmp.chmod(0o600 if path == CONFIG else 0o644)')
 else:raise ValueError('Unknown fixed additional-resource renderer')
 return text.encode()
def build(project,output,*,source_root=None,source_names=None):
 project=Path(project);output=Path(output);adapt=Path(source_root) if source_root is not None else project/'adaptations';toon=decoder(project/'adaptations')
 if source_root is None:index=toon.loads(local(adapt,'additional-resource-sources.toon').read_text(encoding='utf-8'),strict=True)
 else:index=dict(kind='AdditionalResourceSourceDeclarations',files=source_names,locked_inputs=[])
 if output.exists():raise FileExistsError('Fresh output required')
 if index.get('kind')!='AdditionalResourceSourceDeclarations' or len(index['files'])!=6 or len(set(index['files']))!=6:raise ValueError('Invalid source declaration index')
 for pin in index['locked_inputs']:
  raw=local(adapt,pin['path']).read_bytes()
  if len(raw)!=pin['size'] or sha(raw)!=pin['sha256']:raise ValueError('Locked source/notice differs: '+pin['path'])
 output.mkdir(parents=True);refs=[];seen=set();proof=[]
 def write(name,data):
  target=local(output,name);target.parent.mkdir(parents=True,exist_ok=True)
  if target.exists():
   if target.read_bytes()!=data:raise ValueError('Output collision')
  else:target.write_bytes(data)
 for name in index['files']:
  path=local(adapt,name);raw=path.read_bytes();spec=toon.loads(raw.decode(),strict=True);folder=path.parent.relative_to(adapt).as_posix()
  is_ec_live=source_root is not None and spec.get('renderer')=='fixed-ec-profile-v1'
  if source_root is not None:
   from .source_envelope import legacy
   spec=legacy(spec)
  required={'schema_version','kind','id','version','dependencies','hardware_requires','compatibility','payload','install','verify','restore','license_files','external_component_dependencies','source_lock','runtime_state'}
  if is_ec_live:required=required|{'profile_build'}
  if set(spec)!=required or type(spec['schema_version']) is not int or spec['schema_version']!=1 or spec['kind']!='BoilerAdditionalResourceSource' or spec['id'] in seen:raise ValueError('Invalid additional resource declaration')
  if not re.fullmatch('resource:[a-z][a-z0-9-]*',spec['id']) or spec['install']!={'kind':'files-v1'} or spec['verify']!={'kind':'sha256-v1'} or spec['restore']!={'kind':'transaction-backup-v1'}:raise ValueError('Unsupported declarative lifecycle')
  seen.add(spec['id']);lock=toon.loads(local(path.parent,spec['source_lock']).read_text(encoding='utf-8'),strict=True);pins={r['path']:r for r in lock['files']};payload=[];install=[];stored={}
  if is_ec_live:
   from protocol.ec_profile_contract import IDENTITY,PROFILE,KERNEL,MODULE_SHA,SOURCE_SHA
   expected={'board_profile':PROFILE,'source':'sources/005-oxpec-profile.c','patch':'sources/006-profile.patch','module':'compiled/oxpec-profile.ko','evidence':'profile-build-evidence.toon','toolchain_inputs':'devices/GPU/Intel/xe/xevmafix-724/build-evidence/xevma-rebuild-inputs.toon'}
   if spec['id']!=IDENTITY or spec['profile_build']!=expected:raise ValueError('Invalid fixed EC profile recipe')
   evidence=toon.loads(local(path.parent,expected['evidence']).read_text(encoding='utf-8'),strict=True)
   source=local(path.parent,expected['source']).read_bytes();patch=local(path.parent,expected['patch']).read_bytes();module=local(path.parent,expected['module']).read_bytes()
   fixed=toon.loads(local(adapt,expected['toolchain_inputs']).read_text(encoding='utf-8'),strict=True)
   packages=[dict(path='arch-pkgs/'+r['local_file'],size=r['size'],sha256=r['sha256']) for r in fixed['packages']]
   if (sha(source)!=SOURCE_SHA or sha(module)!=MODULE_SHA or evidence.get('kind')!='FixedEcProfileOfflineBuild' or evidence.get('kernel')!=KERNEL or
       evidence.get('board_profile')!=PROFILE or evidence.get('source',{}).get('sha256')!=SOURCE_SHA or
       evidence.get('patch',{}).get('sha256')!=sha(patch) or evidence.get('module',{}).get('sha256')!=MODULE_SHA or
       evidence.get('module',{}).get('size')!=len(module) or evidence.get('module_loaded') is not False or evidence.get('hardware_accepted') is not False or
       evidence.get('toolchain_source_lock_sha256')!=fixed['module_lock']['sha256'] or evidence.get('build_environment')!=fixed['build_environment'] or evidence.get('packages')!=packages):
    raise ValueError('EC profile build evidence differs from locked source/toolchain')
   pins[expected['module']]={'size':len(module),'sha256':MODULE_SHA}

  for item in spec['payload']:
   if set(item)!={'input','renderer','target','mode','scope'} or type(item['mode']) is not int or item['mode'] not in (420,493):raise ValueError('Invalid payload declaration')
   if item['target']=='home' or item['scope']!=('shared-home' if item['target'].startswith('home/') else 'root-slots'):raise ValueError('Declared scope differs from actual target domain')
   data=local(path.parent,item['input']).read_bytes();pin=pins[item['input']]
   if len(data)!=pin['size'] or sha(data)!=pin['sha256']:raise ValueError('Pinned payload input differs')
   data=render(data,item['renderer'],spec['id'],item['target'],toon);key=sha(data)
   if key not in stored:
    member=folder+'/files/'+str(len(stored)).zfill(3)+'-'+Path(item['target']).name;write(member,data);stored[key]=member;payload.append(dict(path=member,size=len(data),sha256=key))
   install.append(dict(source=stored[key],target=item['target'],scope=item['scope'],mode=item['mode']))
  licenses=[]
  for notice in spec['license_files']:
   member=folder+'/'+notice;write(member,local(path.parent,notice).read_bytes());licenses.append(member)
  source_member='provenance/resource-sources/'+spec['id'].split(':',1)[1]+'.toon';write(source_member,raw)
  resource=dict(schema_version=1,kind='BoilerResource',id=spec['id'],version=spec['version'],dependencies=spec['dependencies'],hardware_requires=spec['hardware_requires'],compatibility=spec['compatibility'],
     install=dict(kind=spec['install']['kind'],files=install),verify=spec['verify'],restore=spec['restore'],payload={'files':payload},license_files=licenses,external_component_dependencies=spec['external_component_dependencies'],runtime_state=spec['runtime_state'],
     provenance=dict(resource_source=source_member,resource_source_sha256=sha(raw),input=lock['input'],fixed_renderers=sorted({r['renderer'] for r in spec['payload']})),
     acceptance=dict(files_only=True,live_provider_required=True,physical_hardware_accepted=False))
  if is_ec_live:
   from protocol.ec_profile_contract import MARKER,validate_contract
   resource['ec_profile_contract']=dict(MARKER)
   resource['activation']=dict(kind='fixed-ec-profile-provider-v1',phase='first-boot',autoload=True)
   resource['provenance']['native_profile_build']=copy.deepcopy(evidence)
   resource['acceptance']=dict(files_only=False,live_provider_required=True,software_lifecycle_verified=False,physical_hardware_accepted=False)
   validate_contract(resource)
  member=folder+'/resource.toon';write(member,(toon.dumps(resource)+'\n').encode());refs.append(dict(id=resource['id'],version=resource['version'],path=member,sha256=sha(local(output,member).read_bytes())))
  proof.append(dict(source=name,sha256=sha(raw),id=resource['id']))
 write('provenance/additional-source-build.json',(json.dumps(dict(kind='AdditionalSourceBuild',resources=proof,old_installer_executed=False,fresh_native_compile=False),indent=2)+'\n').encode())
 inventory=[dict(path=p.relative_to(output).as_posix(),size=p.stat().st_size,sha256=sha(p.read_bytes())) for p in sorted(output.rglob('*')) if p.is_file()]
 catalog=dict(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False,resources=refs,models=[],files=inventory)
 write('catalog.toon',(toon.dumps(catalog)+'\n').encode());write('catalog.json',(json.dumps(catalog,indent=2)+'\n').encode());validate_pool(output)
 return dict(resources=len(refs),members=len(inventory),catalog_sha256=sha(local(output,'catalog.toon').read_bytes()),runtime_installed=False)
if __name__=='__main__':print(json.dumps(build(Path(sys.argv[1]),Path(sys.argv[2])),indent=2))
