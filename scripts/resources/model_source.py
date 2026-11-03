"""Build model declarations from explicitly whitelisted, project-owned TOONs."""
import copy
import hashlib
import json
import re
from pathlib import Path
from protocol.resource_pool import decoder, local, json_document
from protocol.hdr_contract import IDENTITY as HDR, PROFILES

PATTERN = re.compile(r'adaptations/brands/([a-z][a-z0-9-]*)/([a-z][a-z0-9-]*)/\2\.toon')
SNAPSHOT = 'adaptations/canonical-model-snapshot.json'
def sha(data): return hashlib.sha256(data).hexdigest()

def sources(project):
    project=Path(project)
    names=json_document(local(project,'scripts/source-files.json').read_bytes()).get('files')
    if not isinstance(names,list) or len(names)!=len(set(names)):
        raise ValueError('Invalid source whitelist')
    lock=json_document(local(project,SNAPSHOT).read_bytes())
    if (lock.get('kind')!='CanonicalModelSnapshot' or lock.get('schema_version')!=1
            or lock.get('repository')!='FlightDan/Boiler-adaptations'
            or not re.fullmatch(r'[0-9a-f]{40}',lock.get('commit',''))):
        raise ValueError('Invalid canonical model snapshot')
    rows=lock.get('files')
    if not isinstance(rows,list) or not rows: raise ValueError('Missing canonical model snapshot members')
    found=[]; checked=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'path','size','sha256'}: raise ValueError('Invalid canonical model snapshot member')
        name=row['path']
        if not isinstance(name,str) or not name.startswith('adaptations/') or name not in names or name in checked:
            raise ValueError('Invalid canonical source snapshot path')
        raw=local(project,name).read_bytes()
        if type(row['size']) is not int or len(raw)!=row['size'] or sha(raw)!=row['sha256']:
            raise ValueError('Canonical model snapshot differs: '+name)
        checked.add(name)
        if PATTERN.fullmatch(name): found.append(name)
    index_name='adaptations/model-source-files.json'
    if index_name not in checked: raise ValueError('Canonical source inventory is not snapshot-bound')
    index=json_document(local(project,index_name).read_bytes())
    if index.get('kind')!='CanonicalAdaptationSourceFiles' or index.get('schema_version')!=1 or not isinstance(index.get('files'),list):
        raise ValueError('Invalid canonical source inventory')
    expected=[]
    for row in index['files']:
        if not isinstance(row,dict) or set(row)!={'path','size','sha256'} or not isinstance(row['path'],str):
            raise ValueError('Invalid canonical source inventory row')
        expected.append(dict(path='adaptations/'+row['path'],size=row['size'],sha256=row['sha256']))
    actual=[row for row in rows if row['path']!=index_name]
    if sorted(actual,key=lambda row:row['path'])!=sorted(expected,key=lambda row:row['path']):
        raise ValueError('Canonical source snapshot differs from inventory')
    candidates=sorted(n for n in names if isinstance(n,str) and PATTERN.fullmatch(n))
    discovered=sorted('adaptations/'+p.relative_to(project/'adaptations').as_posix() for p in (project/'adaptations/brands').rglob('*.toon')
        if PATTERN.fullmatch('adaptations/'+p.relative_to(project/'adaptations').as_posix()))
    if sorted(found)!=candidates or sorted(found)!=discovered: raise ValueError('Canonical model snapshot differs from whitelist')
    return sorted(found)

def apply(pool, project, catalog):
    pool, project = Path(pool), Path(project);toon = decoder(project/'adaptations')
    refs={row['id']:row for row in catalog['models']}; originals={}
    for identity,ref in refs.items(): originals[identity]=toon.loads(local(pool,ref['path']).read_text(encoding='utf-8'),strict=True)
    resources={row['id']:toon.loads(local(pool,row['path']).read_text(encoding='utf-8'),strict=True) for row in catalog['resources']}
    evidence=[]; output_refs=[];seen=set();requested=set()
    for source in sources(project):
        raw=local(project,source).read_bytes();guide=toon.loads(raw.decode('utf-8'),strict=True)
        if not isinstance(guide,dict) or guide.get('kind')!='ModelInstallGuide': raise ValueError('Invalid canonical model guide: '+source)
        spec=guide.get('resource_model')
        if not isinstance(spec,dict) or guide.get('id','').replace('guide:','device:',1)!=spec.get('id'): raise ValueError('Canonical guide/resource identity differs: '+source)
        required={'schema_version','kind','id','name','hardware_matching','parameters','resources'}
        if (not isinstance(spec,dict) or not required <= set(spec) or set(spec)-required-{'hdr','common_references'}
                or type(spec['schema_version']) is not int or spec['schema_version']!=1
                or spec['kind']!='BoilerModelResourceSource' or not isinstance(spec['id'],str)
                or not re.fullmatch(r'device:[a-z][a-z0-9-]*',spec['id'])
                or not isinstance(spec['name'],str) or not spec['name'].strip() or not isinstance(spec['parameters'],dict)):
            raise ValueError('Invalid project model resource declaration: '+source)
        common=spec.get('common_references')
        if common is not None:
            if (not isinstance(common,dict) or set(common)!={'schema_version','kind','model_id','source_only','resources','boot_policies'}
                    or common['schema_version']!=1 or common['kind']!='BoilerModelCommonReferences' or common['model_id']!=spec['id']
                    or common['source_only'] is not True or not isinstance(common['resources'],list) or not isinstance(common['boot_policies'],list)):
                raise ValueError('Invalid common source references: '+source)
            refs_common=common['resources']+common['boot_policies']; ids_common=[]
            for entry in refs_common:
                if not isinstance(entry,dict) or not isinstance(entry.get('id'),str) or not isinstance(entry.get('source'),str):
                    raise ValueError('Invalid common source reference: '+source)
                if not local(project,'adaptations/'+entry['source']).is_file(): raise ValueError('Missing common source: '+entry['source'])
                ids_common.append(entry['id'])
            if len(ids_common)!=len(set(ids_common)): raise ValueError('Duplicate common source reference: '+source)
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
        ids=[x['id'] for x in rows]; requested.update(ids)
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
    # Prune only files owned solely by omitted resource definitions. A payload
    # or license shared with any retained resource remains one physical file.
    kept=[ref for ref in catalog['resources'] if ref['id'] in requested]
    removed=[ref for ref in catalog['resources'] if ref['id'] not in requested]
    def owned(ref):
        item=resources[ref['id']]
        return {ref['path'],*item.get('license_files',[]),*(row['path'] for row in item.get('payload',{}).get('files',[]))}
    protected={path for ref in kept for path in owned(ref)} | {ref['path'] for ref in output_refs}
    for path in {path for ref in removed for path in owned(ref)}-protected:
        local(pool,path).unlink()
    catalog['resources']=kept
    proof=pool/'provenance/model-resource-sources.json';proof.write_text(json.dumps(dict(kind='ModelResourceSources',files=evidence),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
