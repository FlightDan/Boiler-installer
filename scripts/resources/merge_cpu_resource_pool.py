"""Merge a fixed CPU staging resource without replacing model context or payloads."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile

from protocol.local import _safe_path
from protocol.resource_pool import decoder, local, validate_pool
from protocol.cpu_contract import IDENTITY, validate_contract


def sha(raw): return hashlib.sha256(raw).hexdigest()
def jb(value): return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()


def snapshot(root, catalog):
    output = {}
    for row in catalog['files']:
        raw = local(root, row['path']).read_bytes()
        if len(raw) != row['size'] or sha(raw) != row['sha256']: raise ValueError('Merge source changed after validation')
        output[row['path']] = raw
    return output


def build(base_root, cpu_root, output, *, base_sha256, cpu_sha256):
    base_root, cpu_root, output = map(_safe_path, (base_root, cpu_root, output))
    if os.path.lexists(output): raise FileExistsError('Use a fresh CPU pool merge output')
    if any(output == root or root in output.parents for root in (base_root, cpu_root)):
        raise ValueError('CPU merge output cannot be inside either input')
    base = validate_pool(base_root, expected_catalog_sha256=base_sha256)
    cpu = validate_pool(cpu_root, expected_catalog_sha256=cpu_sha256)
    if len(base['resources']) != 12 or {r['id'] for r in base['models']} != {'device:oxp3','device:x2-mini'}:
        raise ValueError('Expected the reviewed twelve-resource/two-model base')
    if any(r['id'] == IDENTITY for r in base['resources']) or len(cpu['resources']) != 1:
        raise ValueError('CPU resource must be unique and separate')
    resource = copy.deepcopy(validate_contract(cpu['resources'][0]))
    files = snapshot(base_root, base); cpu_files = snapshot(cpu_root, cpu)
    content = { (r['sha256'],r['size']):r['path'] for item in base['resources'] for r in item['payload']['files'] }
    mapping = {}; payload = {}
    for row in resource['payload']['files']:
        key=(row['sha256'],row['size']); name=content.setdefault(key,row['path']); mapping[row['path']]=name
        payload[name]=dict(row,path=name)
    resource['payload']['files']=list(payload.values())
    for row in resource['install']['files']: row['source']=mapping[row['source']]
    resource['license_files']=[mapping.get(name,name) for name in resource['license_files']]
    cpu_model_paths={ref['path'] for ref in cpu['model_refs']}
    cpu_resource_path=cpu['resource_refs'][0]['path']
    def write(name, raw):
        if name in files and files[name] != raw: raise ValueError('Conflicting CPU/base member: '+name)
        files[name]=raw
    for name,raw in cpu_files.items():
        if name in cpu_model_paths or name == cpu_resource_path: continue
        write(mapping.get(name,name),raw)
    toon=decoder(); write(cpu_resource_path,(toon.dumps(resource)+'\n').encode())
    references=copy.deepcopy(base['resource_refs'])+[dict(id=IDENTITY,version=resource['version'],path=cpu_resource_path,sha256=sha(files[cpu_resource_path]))]
    models=[]
    for model,ref in zip(base['models'],base['model_refs']):
        model=copy.deepcopy(model)
        model['resources'].append(dict(resource=IDENTITY,enabled=False,default=False,parameters={}))
        model['pending'].extend(dict(component=IDENTITY,status='blocked-production-service-hardware-acceptance',reason=reason) for reason in resource['limitations'])
        files[ref['path']]=(toon.dumps(model)+'\n').encode()
        models.append(dict(id=model['id'],path=ref['path'],sha256=sha(files[ref['path']])))
    write('provenance/merge-public-cpu.json',jb(dict(kind='PublicCpuResourceMerge',base_catalog_sha256=base_sha256,cpu_catalog_sha256=cpu_sha256,
        public_cpu_source_commit=resource['provenance']['public_commit'],base_records_preserved=True,base_model_parameters_and_pending_preserved=True,
        cpu_model_context_used=False,cpu_defaults_enabled=False,cpu_production_activation_allowed=False,
        payload_members=[dict(source=old,canonical=name) for old,name in sorted(mapping.items())])))
    catalog=dict(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False,
        resources=references,models=models,files=[dict(path=name,size=len(raw),sha256=sha(raw)) for name,raw in sorted(files.items())])
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.merge-cpu-',dir=output.parent) as temporary:
        work=Path(temporary)
        for name,raw in files.items():
            path=local(work,name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        (work/'catalog.toon').write_bytes((toon.dumps(catalog)+'\n').encode());(work/'catalog.json').write_bytes(jb(catalog));validate_pool(work)
        if os.path.lexists(output): raise FileExistsError('CPU merge destination appeared during build')
        os.rename(work,output)
    return validate_pool(output)
