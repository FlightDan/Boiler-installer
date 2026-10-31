"""Build model declarations from explicitly whitelisted, project-owned TOONs."""
import copy
import hashlib
import json
import re
from pathlib import Path
from protocol.resource_pool import decoder, local, json_document
from protocol.hdr_contract import IDENTITY as HDR, PROFILES

PATTERN = re.compile(r'adaptations/brands/[a-z][a-z0-9-]*/models/[a-z][a-z0-9-]*\.toon')
def sha(data): return hashlib.sha256(data).hexdigest()

def sources(project):
    project=Path(project);data=json_document(local(project,'scripts/source-files.json').read_bytes());names=data.get('files')
    if not isinstance(names,list) or any(not isinstance(x,str) for x in names) or len(names)!=len(set(names)):
        raise ValueError('Invalid source whitelist')
    def is_model_candidate(name):
        parts=name.replace('\\','/').split('/')
        return len(parts)>=5 and parts[:2]==['adaptations','brands'] and parts[3]=='models' and name.lower().endswith('.toon')
    candidates=[name for name in names if is_model_candidate(name)]
    if any(not PATTERN.fullmatch(name) for name in candidates):raise ValueError('Unsupported model source path in whitelist')
    found=sorted(candidates)
    brands=local(project,'adaptations/brands')
    discovered={'adaptations/'+p.relative_to(project/'adaptations').as_posix() for p in brands.rglob('*')
        if p.is_file() and is_model_candidate('adaptations/'+p.relative_to(project/'adaptations').as_posix())}
    if discovered != set(found):raise ValueError('Model source files differ from the explicit whitelist')
    if not found:raise ValueError('No whitelisted model resource sources')
    return found

def apply(pool, project, catalog):
    pool, project = Path(pool), Path(project);toon = decoder(project/'adaptations')
    refs={row['id']:row for row in catalog['models']}; originals={}
    for identity,ref in refs.items(): originals[identity]=toon.loads(local(pool,ref['path']).read_text(encoding='utf-8'),strict=True)
    resources={row['id']:toon.loads(local(pool,row['path']).read_text(encoding='utf-8'),strict=True) for row in catalog['resources']}
    evidence=[]; output_refs=[];seen=set()
    for source in sources(project):
        raw=local(project,source).read_bytes();spec=toon.loads(raw.decode('utf-8'),strict=True)
        required={'schema_version','kind','id','name','hardware_matching','parameters','resources'}
        if (not isinstance(spec,dict) or not required <= set(spec) or set(spec)-required-{'hdr'}
                or type(spec['schema_version']) is not int or spec['schema_version']!=1
                or spec['kind']!='BoilerModelResourceSource' or not isinstance(spec['id'],str)
                or not re.fullmatch(r'device:[a-z][a-z0-9-]*',spec['id'])
                or not isinstance(spec['name'],str) or not spec['name'].strip() or not isinstance(spec['parameters'],dict)):
            raise ValueError('Invalid project model resource declaration: '+source)
        identity=spec['id']
        if identity in seen:raise ValueError('Duplicate project model identity: '+identity)
        seen.add(identity)
        matching=spec['hardware_matching']
        if (not isinstance(matching,dict) or set(matching)!={'mode','pairs'} or matching['mode']!='advisory-exact-dmi-v1'
                or not isinstance(matching['pairs'],list) or len(matching['pairs'])>32 or any(not isinstance(x,dict)
                or set(x)!={'manufacturer','model'} or any(not isinstance(x[k],str) or not x[k].strip() for k in ('manufacturer','model')) for x in matching['pairs'])):
            raise ValueError('Invalid advisory model names: '+source)
        rows=spec['resources']
        if not isinstance(rows,list) or not rows or any(not isinstance(x,dict) or set(x)!={'id'} or not isinstance(x['id'],str) for x in rows):
            raise ValueError('Invalid model resource references: '+source)
        ids=[x['id'] for x in rows]
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate model resource references: '+source)
        if not set(ids)<=set(resources):raise ValueError('Model references an unavailable resource: '+source)
        for item in ids:
            if set(resources[item]['dependencies'])-set(ids):raise ValueError('Model resource has missing dependencies: '+item)
        model=copy.deepcopy(originals.get(identity,dict(schema_version=1,kind='BoilerResourceModel',id=identity,
            name=spec['name'],hardware_matching=matching,resources=[],parameters={},pending=[],complete=False,official_release=False)))
        available={row['resource']:row for row in model['resources']}
        model['name']=spec['name'];model['hardware_matching']=copy.deepcopy(matching)
        model['parameters']=copy.deepcopy(spec['parameters'])
        model['resources']=[copy.deepcopy(available.get(item,dict(resource=item,enabled=False,default=False,parameters={}))) for item in ids]
        if HDR in ids:
            hdr=spec.get('hdr')
            if not isinstance(hdr,dict) or set(hdr)!={'profile','full_frame','peak'}:raise ValueError('Invalid HDR mapping: '+source)
            profile=hdr['profile']
            if (not isinstance(profile,str) or profile not in PROFILES or type(hdr['full_frame']) is not int or type(hdr['peak']) is not int
                    or hdr['full_frame']!=PROFILES[profile]['full_frame'] or hdr['peak']!=PROFILES[profile]['peak']):
                raise ValueError('HDR mapping is not supported by fixed shared handler: '+source)
            for row in model['resources']:
                if row['resource']==HDR:row['parameters']={'profile':profile}
            model['parameters']['hdr']=dict(sdr_white_nits=hdr['full_frame'],peak_nits=hdr['peak'],
                status='shared-game-handler-candidate-desktop-native-blocked',resource=HDR)
        elif 'hdr' in spec:raise ValueError('HDR parameters require the HDR resource: '+source)
        path=refs[identity]['path'] if identity in refs else source.removeprefix('adaptations/')
        if identity not in refs and any(r['path']==path for r in refs.values()):raise ValueError('New model output collides with a generated model')
        dest=local(pool,path);dest.parent.mkdir(parents=True,exist_ok=True);output=(toon.dumps(model)+'\n').encode('utf-8');dest.write_bytes(output)
        output_refs.append(dict(id=identity,path=path,sha256=sha(output)))
        evidence.append(dict(path=source,size=len(raw),sha256=sha(raw),model=identity))
    for identity,ref in refs.items():
        if identity not in seen:local(pool,ref['path']).unlink()
    catalog['models']=output_refs
    proof=pool/'provenance/model-resource-sources.json';proof.write_text(json.dumps(dict(kind='ModelResourceSources',files=evidence),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
