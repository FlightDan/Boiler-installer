"""Build model declarations from explicitly whitelisted, project-owned TOONs."""
import copy
import hashlib
import json
import re
from pathlib import Path
from protocol.resource_pool import decoder, local, json_document
from protocol.hdr_contract import IDENTITY as HDR, PROFILES

PATTERN = re.compile(r'adaptations/brands/([a-z][a-z0-9-]*)/([a-z][a-z0-9-]*)/\2\.toon')
DEVICE_PATTERN = re.compile(r'adaptations/devices/([a-z][a-z0-9-]*)/([a-z][a-z0-9-]*)\.toon')


def model_path(name):
    return DEVICE_PATTERN.fullmatch(name) or PATTERN.fullmatch(name)


def onexplayer_source(names, model):
    current = 'devices/onexplayer/' + model + '.toon'
    return current if current in names else 'brands/onexplayer/' + model + '/' + model + '.toon'

def locked_input_path(name, verified_guides):
    """Resolve an old lock's guide path only through verified snapshot members."""
    legacy = PATTERN.fullmatch(name)
    if legacy is None:
        return name
    brand, model = legacy.groups()
    relocated = f'adaptations/devices/{brand}/{model}.toon'
    return relocated if relocated in verified_guides else name


SNAPSHOT = 'adaptations/canonical-model-snapshot.json'
def sha(data): return hashlib.sha256(data).hexdigest()

def validate_model_index(contents, toon):
    """Validate the derived v2 index against all inventoried model TOONs.

    A missing index is accepted only for historical, pre-index snapshots.
    """
    names=sorted(name for name in contents if model_path('adaptations/'+name))
    if any(name.startswith('devices/') for name in names) and any(name.startswith('brands/') for name in names):
        raise ValueError('Canonical snapshot contains both model source layouts')
    guides={}
    for name in names:
        raw=contents[name]
        if len(raw)>1024*1024: raise ValueError('Model source too large')
        guide=toon.loads(raw.decode('utf-8'),strict=True)
        if not isinstance(guide,dict) or guide.get('kind')!='ModelInstallGuide': raise ValueError('Invalid model guide: '+name)
        guides[name]=guide
    if 'models.toon' not in contents:
        if any('canonical_id' in guide for guide in guides.values()): raise ValueError('Canonical model index missing')
        return False
    index=toon.loads(contents['models.toon'].decode('utf-8'),strict=True)
    # v1 has a different, display-oriented index. Preserve old snapshot behavior;
    # v2 identity-bearing guides must never be accepted through this path.
    if (isinstance(index,dict) and type(index.get('schema_version')) is int
            and index['schema_version']==1 and index.get('kind')=='BoilerModelSourceIndex'
            and index.get('generated') is True and isinstance(index.get('models'),list)
            and all('canonical_id' not in guide for guide in guides.values())):
        rows=index['models']
        if (len(rows)!=len(names) or any(not isinstance(row,dict) or not isinstance(row.get('source'),str)
                or row.get('source') not in guides or row.get('id')!=guides[row['source']].get('id') for row in rows)
                or sorted(row['source'] for row in rows)!=names):
            raise ValueError('Legacy model index differs from inventory')
        return False
    if (not isinstance(index,dict) or set(index)!={'schema_version','kind','generated','models'}
            or type(index['schema_version']) is not int or index['schema_version']!=2
            or index['kind']!='BoilerModelSourceIndex' or index['generated'] is not True
            or not isinstance(index['models'],list)):
        raise ValueError('Invalid canonical model index')
    expected=[]; claimed=set()
    for name,guide in guides.items():
        match=model_path('adaptations/'+name);brand,model=match.groups()
        canonical='device:'+brand+'-'+model
        aliases=guide.get('legacy_aliases')
        if (guide.get('canonical_id')!=canonical or not isinstance(aliases,dict)
                or set(aliases)!={'guide','package'} or any(not isinstance(aliases[k],list) for k in aliases)
                or any(not isinstance(v,str) or not re.fullmatch(prefix+r':[a-z][a-z0-9-]*',v)
                    for key,prefix in (('guide','guide'),('package','device')) for v in aliases[key])
                or len(aliases['guide'])>1 or len(aliases['package'])>1):
            raise ValueError('Invalid canonical model identity: '+name)
        legacy={
            ('onexplayer','oxp3'): ('guide:oxp3','device:oxp3'),
            ('onexplayer','x2-mini'): ('guide:x2-mini','device:x2-mini'),
        }.get((brand,model))
        if (aliases['guide'] or aliases['package']) and (legacy is None or aliases != {'guide':[legacy[0]],'package':[legacy[1]]}):
            raise ValueError('Unrecognized legacy model alias: '+name)
        package=aliases['package'][0] if aliases['package'] else canonical
        guide_id=aliases['guide'][0] if aliases['guide'] else 'guide:'+canonical.split(':',1)[1]
        if guide.get('id')!=guide_id or not isinstance(guide.get('resource_model'),dict) or guide['resource_model'].get('id')!=package:
            raise ValueError('Model guide and resource identities differ: '+name)
        for ident in [canonical,*aliases['package']]:
            if ident in claimed: raise ValueError('Duplicate canonical model identity: '+ident)
            claimed.add(ident)
        expected.append(dict(id=canonical,source=name))
    actual=index['models']
    if (any(not isinstance(row,dict) or set(row)!={'id','source'} or not isinstance(row['id'],str) or not isinstance(row['source'],str) for row in actual)
            or sorted(actual,key=lambda row:row['source'])!=expected):
        raise ValueError('Canonical model index differs from inventory')
    return True

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
        if model_path(name): found.append(name)
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
    candidates=sorted(n for n in names if isinstance(n,str) and model_path(n))
    adaptations=project/'adaptations'
    discovered=sorted('adaptations/'+p.relative_to(adaptations).as_posix()
        for folder in ('brands','devices') for p in (adaptations/folder).rglob('*.toon')
        if model_path('adaptations/'+p.relative_to(adaptations).as_posix()))
    if sorted(found)!=candidates or sorted(found)!=discovered: raise ValueError('Canonical model snapshot differs from whitelist')
    validate_model_index({name.removeprefix('adaptations/'):local(project,name).read_bytes() for name in checked if name != index_name}, decoder(project/'adaptations'))
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
        if not isinstance(spec,dict) or ('canonical_id' not in guide and guide.get('id','').replace('guide:','device:',1)!=spec.get('id')): raise ValueError('Canonical guide/resource identity differs: '+source)
        if 'canonical_id' in guide:
            runtime=guide.get('runtime')
            if (not isinstance(runtime,dict) or runtime.get('id')!=spec.get('id')
                    or not isinstance(runtime.get('name'),dict) or not isinstance(runtime['name'].get('en'),str)
                    or not runtime['name']['en'].strip() or not isinstance(runtime.get('hardware_matching'),dict)):
                raise ValueError('Invalid canonical runtime projection: '+source)
            projected={'name':runtime['name']['en'],'hardware_matching':runtime['hardware_matching']}
            if any(key in spec and spec[key]!=value for key,value in projected.items()):
                raise ValueError('Conflicting canonical runtime projection: '+source)
            spec=dict(spec,**projected)
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
