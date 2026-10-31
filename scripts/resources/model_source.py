"""Build model declarations from explicitly whitelisted, project-owned TOONs."""
import copy
import hashlib
import json
import re
from pathlib import Path
from protocol.resource_pool import decoder, local, json_document
from protocol.hdr_contract import IDENTITY as HDR, PROFILES
DESKTOP_HDR='resource:gamma22-hdr-desktop'

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

def repository_sources(root, toon):
    root=Path(root)
    spec=toon.loads(local(root,'repository.toon').read_text(encoding='utf-8'),strict=True)
    names=spec.get('models')
    if (type(spec.get('schema_version')) is not int or spec['schema_version']!=1
            or spec.get('kind')!='BoilerAdaptationsSourceRepository' or not isinstance(names,list)
            or not names or any(not isinstance(name,str) for name in names) or len(names)!=len(set(names))):
        raise ValueError('Invalid independent adaptation model index')
    pattern=re.compile(r'brands/[a-z][a-z0-9-]*/models/[a-z][a-z0-9-]*\.toon')
    if any(not isinstance(name,str) or not pattern.fullmatch(name) for name in names):raise ValueError('Unsafe adaptation model source path')
    actual={p.relative_to(root).as_posix() for p in local(root,'brands').glob('*/models/*.toon') if p.is_file()}
    if actual!=set(names):raise ValueError('Independent model source index differs from files')
    return sorted(names)


def apply(pool, project, catalog, *, source_root=None):
    pool, project = Path(pool), Path(project);toon = decoder(project/'adaptations')
    refs={row['id']:row for row in catalog['models']}; originals={}
    for identity,ref in refs.items(): originals[identity]=toon.loads(local(pool,ref['path']).read_text(encoding='utf-8'),strict=True)
    resources={row['id']:toon.loads(local(pool,row['path']).read_text(encoding='utf-8'),strict=True) for row in catalog['resources']}
    evidence=[]; output_refs=[];seen=set()
    source_base=Path(source_root) if source_root is not None else project
    source_names=repository_sources(source_base,toon) if source_root is not None else sources(project)
    for source in source_names:
        raw=local(source_base,source).read_bytes();spec=toon.loads(raw.decode('utf-8'),strict=True)
        required={'schema_version','kind','id','name','hardware_matching','parameters','resources'}
        if (not isinstance(spec,dict) or not required <= set(spec) or set(spec)-required-{'hdr','pending','ec_profile'}
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
        if not isinstance(rows,list) or not rows or any(not isinstance(x,dict) or 'id' not in x or set(x)-{'id','default','optional'} or not isinstance(x['id'],str) or any(type(x[k]) is not bool for k in ('default','optional') if k in x) or (x.get('optional',True) is False and x.get('default',False) is not True) for x in rows):
            raise ValueError('Invalid model resource references: '+source)
        if any(row.get('optional',True) is False for row in rows):raise ValueError('Required resource choices are not supported by this SDK: '+source)
        ids=[x['id'] for x in rows]
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate model resource references: '+source)
        if not set(ids)<=set(resources):raise ValueError('Model references an unavailable resource: '+source)
        for item in ids:
            if set(resources[item]['dependencies'])-set(ids):raise ValueError('Model resource has missing dependencies: '+item)
        defaults={row['id'] for row in rows if row.get('default',False)}
        for item in defaults:
            if set(resources[item]['dependencies'])-defaults:raise ValueError('Default resource has missing default dependencies: '+item)
        model=copy.deepcopy(originals.get(identity,dict(schema_version=1,kind='BoilerResourceModel',id=identity,
            name=spec['name'],hardware_matching=matching,resources=[],parameters={},pending=[],complete=False,official_release=False)))
        model['name']=spec['name'];model['hardware_matching']=copy.deepcopy(matching)
        model['parameters']=copy.deepcopy(spec['parameters'])
        pending=spec.get('pending',[])
        if not isinstance(pending,list) or any(not isinstance(row,dict) or set(row)!={'component','reason'} or any(not isinstance(row[k],str) or not row[k].strip() for k in ('component','reason')) for row in pending):raise ValueError('Invalid model pending declarations: '+source)
        if 'pending' in spec or source_root is not None:model['pending']=copy.deepcopy(pending)
        model['resources']=[dict(resource=row['id'],enabled=False,default=row.get('default',False),optional=row.get('optional',True),parameters={}) for row in rows]
        if HDR in ids or DESKTOP_HDR in ids:
            hdr=spec.get('hdr')
            if not isinstance(hdr,dict) or set(hdr)!={'profile','full_frame','peak'}:raise ValueError('Invalid HDR mapping: '+source)
            profile=hdr['profile']
            if (not isinstance(profile,str) or profile not in PROFILES or type(hdr['full_frame']) is not int or type(hdr['peak']) is not int
                    or hdr['full_frame']!=PROFILES[profile]['full_frame'] or hdr['peak']!=PROFILES[profile]['peak']):
                raise ValueError('HDR mapping is not supported by fixed shared handler: '+source)
            for row in model['resources']:
                if row['resource'] in (HDR,DESKTOP_HDR):row['parameters']={'profile':profile}
            if HDR in ids:
                model['parameters']['hdr']=dict(sdr_white_nits=hdr['full_frame'],peak_nits=hdr['peak'],
                    status=('shared-game-handler-candidate' if DESKTOP_HDR in ids else 'shared-game-handler-candidate-desktop-native-blocked'),resource=HDR)
            if DESKTOP_HDR in ids:
                model['parameters']['hdr_desktop']=dict(sdr_white_nits=hdr['full_frame'],peak_nits=hdr['peak'],
                    status='desktop-runtime-pending-binding-and-trial',resource=DESKTOP_HDR)
        elif 'hdr' in spec:raise ValueError('HDR parameters require the HDR resource: '+source)
        ec_id='resource:onexplayer-ec-fan-driver'
        ec_profile=spec.get('ec_profile')
        if ec_id in ids and resources[ec_id].get('ec_profile_contract') is not None:
            from protocol.ec_profile_contract import PROFILE, validate_contract
            validate_contract(resources[ec_id])
            if ec_profile != {'board_profile':PROFILE}:raise ValueError('Invalid reviewed EC profile: '+source)
            model['parameters']['ec_board_profile']=PROFILE
        elif ec_profile is not None:
            raise ValueError('EC profile requires the fixed EC live resource: '+source)
        path=refs[identity]['path'] if identity in refs else source.removeprefix('adaptations/')
        if identity not in refs and any(r['path']==path for r in refs.values()):raise ValueError('New model output collides with a generated model')
        dest=local(pool,path);dest.parent.mkdir(parents=True,exist_ok=True);output=(toon.dumps(model)+'\n').encode('utf-8');dest.write_bytes(output)
        output_refs.append(dict(id=identity,path=path,sha256=sha(output)))
        evidence.append(dict(path=source,size=len(raw),sha256=sha(raw),model=identity))
    for identity,ref in refs.items():
        if identity not in seen:local(pool,ref['path']).unlink()
    catalog['models']=output_refs
    proof=pool/'provenance/model-resource-sources.json';proof.write_text(json.dumps(dict(kind='ModelResourceSources',files=evidence),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
