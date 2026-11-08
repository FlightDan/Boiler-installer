"""Merge the fixed shared game HDR handler into the reviewed 13-resource pool."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from protocol.local import _safe_path
from protocol.resource_pool import decoder,local,validate_pool
from protocol.hdr_contract import IDENTITY,validate_contract
def sha(raw):return hashlib.sha256(raw).hexdigest()
def jb(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
def snapshot(root,catalog):
    result={}
    for row in catalog['files']:
        raw=local(root,row['path']).read_bytes()
        if len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('HDR merge input changed after validation')
        result[row['path']]=raw
    return result
def build(base_root,hdr_root,output,*,base_sha256,hdr_sha256):
    base_root,hdr_root,output=map(_safe_path,(base_root,hdr_root,output))
    if os.path.lexists(output):raise FileExistsError('Fresh HDR merge output required')
    if any(output==p or p in output.parents for p in (base_root,hdr_root)):raise ValueError('HDR output cannot overlap either input')
    base=validate_pool(base_root,expected_catalog_sha256=base_sha256);hdr=validate_pool(hdr_root,expected_catalog_sha256=hdr_sha256)
    if len(base['resources'])!=13 or {m['id'] for m in base['models']}!={'device:oxp3','device:x2-mini'}:raise ValueError('Expected the reviewed thirteen-resource/two-model base')
    if any(r['id']==IDENTITY for r in base['resources']) or len(hdr['resources'])!=1:raise ValueError('HDR resource must be unique and separate')
    resource=copy.deepcopy(validate_contract(hdr['resources'][0]));files=snapshot(base_root,base);extra=snapshot(hdr_root,hdr)
    profiles=resource['profiles']
    content={(row['sha256'],row['size']):row['path'] for item in base['resources'] for row in item['payload']['files']}
    for row in resource['payload']['files']:
        previous=content.get((row['sha256'],row['size']))
        if previous is not None and previous!=row['path']:raise ValueError('HDR fixed source path would duplicate existing payload; explicit contract migration required')
    # Licenses are ordinary inventoried source bytes too: retain one canonical
    # member when the identical reviewed MIT notice is already in the base.
    all_content={(row['sha256'],row['size']):row['path'] for row in base['files']}
    mapping={}
    for name in resource['license_files']:
        raw=extra[name];mapping[name]=all_content.get((sha(raw),len(raw)),name)
    resource['license_files']=[mapping.get(n,n) for n in resource['license_files']]
    excluded={r['path'] for r in hdr['model_refs']}
    path=hdr['resource_refs'][0]['path'];derived=str(Path(path).with_suffix('.json')).replace('\\','/')
    excluded|={path,derived}
    def write(name,raw):
        if name in files and files[name]!=raw:raise ValueError('Conflicting HDR/base member: '+name)
        files[name]=raw
    for name,raw in extra.items():
        if name not in excluded:write(mapping.get(name,name),raw)
    toon=decoder();write(path,(toon.dumps(resource)+'\n').encode())
    if derived in extra:write(derived,jb(resource))
    refs=copy.deepcopy(base['resource_refs'])+[dict(id=IDENTITY,version=resource['version'],path=path,sha256=sha(files[path]))]
    models=[]
    for original,reference in zip(base['models'],base['model_refs']):
        model=copy.deepcopy(original);profile=model['id'].split(':',1)[1]
        if profile not in profiles:raise ValueError('HDR model profile is not registered')
        model['resources'].append(dict(resource=IDENTITY,enabled=False,default=False,parameters={'profile':profile}))
        parameters=model['parameters']['hdr']
        if (parameters['sdr_white_nits'],parameters['peak_nits'])!=(profiles[profile]['full_frame'],profiles[profile]['peak']):raise ValueError('Existing model luminance parameters differ from fixed profile')
        parameters['status']='shared-game-handler-candidate-desktop-native-blocked';parameters['resource']=IDENTITY
        for pending in model['pending']:
            if pending['component']=='hdr':
                pending['status']='partially-implemented-game-handler-only'
                pending['reason']='Shared game HDR file lifecycle implemented; trusted panel facts/live Gamescope and desktop native rebuild/activation remain unverified'
        model['pending'].append(dict(component=IDENTITY,status='blocked-runtime-and-panel-acceptance',reason='Files and parameters are local-test candidates; unknown panel/software facts reject apply. Desktop native HDR remains unavailable.'))
        files[reference['path']]=(toon.dumps(model)+'\n').encode()
        models.append(dict(id=model['id'],path=reference['path'],sha256=sha(files[reference['path']])))
    write('provenance/merge-shared-hdr.json',jb(dict(kind='SharedGameHdrResourceMerge',base_catalog_sha256=base_sha256,hdr_catalog_sha256=hdr_sha256,
        base_resource_records_preserved=True,defaults_enabled=False,shared_implementation=True,parameters=profiles,
        license_deduplication=mapping,physical_hardware_accepted=False,desktop_installable=False)))
    catalog=dict(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False,resources=refs,models=models,
        files=[dict(path=n,size=len(raw),sha256=sha(raw)) for n,raw in sorted(files.items())])
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.merge-hdr-',dir=output.parent) as temporary:
        ready=Path(temporary)
        for name,raw in files.items():
            target=local(ready,name);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        (ready/'catalog.toon').write_bytes((toon.dumps(catalog)+'\n').encode());(ready/'catalog.json').write_bytes(jb(catalog));validate_pool(ready)
        if os.path.lexists(output):raise FileExistsError('HDR output appeared during merge')
        os.rename(ready,output)
    return validate_pool(output)
