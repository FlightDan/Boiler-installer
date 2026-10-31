"""Build the common RAPL remote solely from the fixed public CPU baseline."""
from pathlib import Path
import copy,hashlib,json
ID='resource:common-intel-rapl'
COMMIT='72f864e0498c10366493c74608c5ffed62cc99ca'
SOURCE='adaptations/brands/onexplayer/oxp3/scripts/controls/rapl/provider.py'
PREFIX='common/CPU/Intel/rapl'
PUBLIC_PROVIDER_SHA256='11af7da8d69a59b3e6413380a6516468da2d50248043cf6a93cf813381f2116b'
PATCHED_PROVIDER_SHA256='44741aee85a943fa025e9846e01733322f756a283c300ff4744fdddd201ac14c'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def build(project,output,*,source_root=None):
 from protocol.resource_pool import decoder,local,validate_pool
 project=Path(project);output=Path(output);toon=decoder(project/'adaptations')
 if output.exists():raise FileExistsError('Fresh RAPL output required')
 source=Path(source_root) if source_root is not None else project
 prefix='' if source_root is not None else 'adaptations/'
 lock=toon.loads(local(source,'locks/public-baseline.toon').read_text(encoding='utf-8'),strict=True) if source_root is not None else json.loads(local(project,'adaptations/source-lock.json').read_bytes())
 if lock['source']!={'kind':'github','repository':'FlightDan/OneXPlayer3-SteamOS','commit':COMMIT}:raise ValueError('RAPL public baseline differs')
 relative=SOURCE.removeprefix('adaptations/');rows=[r for r in lock['files'] if r.get('original_path',r['path'])==relative]
 if len(rows)!=1:raise ValueError('Public RAPL source is not uniquely declared')
 raw=local(source,rows[0]['path'] if source_root is not None else SOURCE).read_bytes()
 if len(raw)!=rows[0]['size'] or sha(raw)!=rows[0]['sha256'] or sha(raw)!=PUBLIC_PROVIDER_SHA256:raise ValueError('RAPL public source bytes differ')
 text=raw.decode('utf-8');old='''    if result.returncode != 0 or state not in ("inactive", "failed"):
''';new='''    if result.returncode != 0:
        load = subprocess.run(["/usr/bin/systemctl", "show", "--property=LoadState", "--value", SYNC_UNIT],
                              capture_output=True, text=True, timeout=5, check=False)
        if load.stdout.strip() == "not-found" and state in ("", "inactive", "failed"):
            return
    if result.returncode != 0 or state not in ("inactive", "failed"):
'''
 if text.count(old)!=1:raise ValueError('RAPL sync guard patch anchor differs')
 provider=text.replace(old,new).encode()
 if sha(provider)!=PATCHED_PROVIDER_SHA256:raise ValueError('RAPL fixed transform bytes differ')
 license=local(source,prefix+'LICENSE').read_bytes()
 declaration=prefix+PREFIX+'/resource.toon'
 declaration_raw=local(source,declaration).read_bytes();resource=toon.loads(declaration_raw.decode('utf-8'),strict=True)
 if source_root is not None:
     from .source_envelope import legacy
     resource=legacy(resource)
 if (resource.get('kind')!='BoilerResource' or resource.get('schema_version')!=1 or resource.get('id')!=ID or resource.get('install',{}).get('kind')!='files-v1'
         or resource.get('verify')!={'kind':'sha256-v1'} or resource.get('restore')!={'kind':'transaction-backup-v1'} or resource.get('hooks') or resource.get('dynamic_handler')):
     raise ValueError('Invalid declarative RAPL source resource')
 files={};mapping={};payload=[]
 for entry in resource['payload']['files']:
     name=entry['path'];base=prefix+PREFIX+'/files/'
     if not name.startswith(base) or '/' in name.removeprefix(base):raise ValueError('RAPL input is outside its owned source directory')
     data=local(source,name).read_bytes()
     if len(data)!=entry['size'] or sha(data)!=entry['sha256']:raise ValueError('RAPL declarative source payload differs: '+name)
     if name.endswith('/provider.py') and data!=provider:raise ValueError('RAPL provider does not match the fixed public source transform')
     output_name=PREFIX+'/files/'+Path(name).name
     if output_name in files:raise ValueError('Duplicate RAPL declared payload')
     mapping[name]=output_name;files[output_name]=data;payload.append(dict(path=output_name,size=len(data),sha256=sha(data)))
 if prefix+PREFIX+'/files/provider.py' not in mapping:raise ValueError('RAPL provider source must be declared')
 for row in resource['install']['files']:
     if row['source'] not in mapping:raise ValueError('RAPL install source is not declared')
     row['source']=mapping[row['source']]
 resource['payload']['files']=payload
 resource['license_files']=['licenses/project-AGPL-3.0'];files['licenses/project-AGPL-3.0']=license
 resource['provenance'].update(resource_source=declaration,resource_source_sha256=sha(declaration_raw))
 # The manager dependency is resolved only in the final shared catalog; this
 # independent fragment is not a self-contained installable model pool.
 files[PREFIX+'/resource.toon']=(toon.dumps(resource)+'\n').encode()
 files['provenance/rapl-source-build.json']=(json.dumps(dict(kind='PublicRAPLResourceBuild',public_commit=COMMIT,source=SOURCE,source_sha256=sha(raw),provider_sha256=sha(provider),default_power_write=False,script='scripts/resources/rapl.py'),indent=2)+'\n').encode()
 output.mkdir(parents=True)
 for name,data in files.items():p=output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 return dict(kind='ResourceBuildFragment',id=ID,path=PREFIX+'/resource.toon',sha256=sha(files[PREFIX+'/resource.toon']),files=[dict(path=n,size=len(data),sha256=sha(data)) for n,data in sorted(files.items())])
