"""Assemble checked independent resources plus model context; no legacy seed."""
import copy
import hashlib
import json
from pathlib import Path

from protocol.resource_pool import decoder, validate_pool, local
from .model_context import models as model_context
from . import xevma_proof

PLACEMENT = {'resource:onexplayer-microphone': 'devices/Audio/microphone-mute',
    'resource:oxp3-speaker-route': 'common/audio/realtek/alc245/speaker-route',
    'resource:intel-xevmafix-724': 'common/gpu/intel/xe/xevmafix-724'}
PREFERENCE = list(PLACEMENT)
CURRENT_PROOF = 'adaptations/resource-public-source-proof.json'


def verified_current_sources(project):
    raw = local(project, CURRENT_PROOF).read_bytes(); proof = json.loads(raw)
    if (proof.get('kind') != 'VerifiedPublicCurrentResourceSources' or proof.get('repository') != 'FlightDan/Boiler-installer'
            or proof.get('commit') != '00d26ea7ed0aa4f1d1002216242199bc72736e80' or proof.get('public_repository') is not True):
        raise ValueError('Unexpected public current-source proof')
    rows = {row['path']: row for row in proof['files']}
    if len(rows) != len(proof['files']): raise ValueError('Duplicate public source proof')
    for name, row in rows.items():
        data = local(project, name).read_bytes()
        blob = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        expected_url = 'https://github.com/'+proof['repository']+'/blob/'+proof['commit']+'/'+name
        if (len(data) != row['size'] or sha(data) != row['sha256'] or row.get('byte_sha256_matches_local') is not True
                or row.get('git_blob_matches_api') is not True or row.get('git_blob') != blob or row.get('url') != expected_url):
            raise ValueError('Public source proof no longer matches: '+name)
    return raw, proof, rows


def sha(raw): return hashlib.sha256(raw).hexdigest()


def build(project, component_pool, output):
    project, component_pool, output = map(Path, (project, component_pool, output))
    catalog = validate_pool(component_pool); toon = decoder(project/'adaptations')
    proof_raw, public_proof, public_rows = verified_current_sources(project)
    xevma_proof.verified_rebuild(project)
    if output.exists(): raise FileExistsError('Use a new source pool output')
    output.mkdir(parents=True); files = {}; content = {}; resources = []
    def rename(value):
        if isinstance(value, str) and value.startswith('provenance/'): return 'provenance/source-2/'+value.removeprefix('provenance/')
        if isinstance(value, list): return [rename(v) for v in value]
        if isinstance(value, dict): return {k: rename(v) for k, v in value.items()}
        return value
    def write(name, raw):
        if name in files and files[name] != raw: raise ValueError('Source byte collision')
        files[name] = raw; path = local(output, name); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    def document(name, value): write(name, (toon.dumps(value)+'\n').encode())
    for entry in catalog['files']:
        if entry['path'].startswith(('licenses/', 'provenance/')): write(rename(entry['path']), local(component_pool, entry['path']).read_bytes())
    write('provenance/current-public-source-proof.json', proof_raw)
    for name in (xevma_proof.PROOF, xevma_proof.RECIPE, xevma_proof.SCRIPT, xevma_proof.LOG, xevma_proof.ROOT+'REBUILD.md'):
        write('provenance/xevma-source-rebuild/'+Path(name).name, local(project, name).read_bytes())
    for original in catalog['resources']:
        resource = rename(copy.deepcopy(original)); reference = next(r for r in catalog['resource_refs'] if r['id'] == resource['id'])
        root = PLACEMENT.get(resource['id'], str(Path(reference['path']).parent).replace('\\', '/')); mapping = {}; payload = {}
        for entry in resource['payload']['files']:
            old = entry['path']; raw = local(component_pool, old).read_bytes(); key = (entry['sha256'], entry['size'])
            if len(raw) != entry['size'] or sha(raw) != entry['sha256']: raise ValueError('Source payload changed')
            name = content.setdefault(key, root+'/files/'+Path(old).name); mapping[old] = name
            current = dict(entry, path=name); payload[name] = current; write(name, raw)
        resource['payload']['files'] = list(payload.values())
        for row in resource['install']['files']: row['source'] = mapping[row['source']]
        resource['license_files'] = [mapping.get(name, name) for name in resource['license_files']]
        for row in resource.get('provenance', {}).get('files', []):
            if row.get('payload') in mapping: row['payload'] = mapping[row['payload']]
        for row in resource.get('provenance', {}).get('transforms', []):
            if row.get('output') in mapping: row['output'] = mapping[row['output']]
        if resource['id'] in ('resource:onexplayer-battery-common', 'resource:onexplayer-battery-charge-backend', 'resource:onexplayer-microphone'):
            for row in resource['provenance']['files']:
                name = 'adaptations/'+row['source_path']; checked = public_rows.get(name)
                if checked is None or checked['sha256'] != row['source_sha256']: raise ValueError('Current resource lacks a public source proof: '+name)
                row['public_source_url'] = checked['url']
            resource['provenance'].update(public_repository_commit_verified=True, public_repository=public_proof['repository'],
                public_commit=public_proof['commit'], public_source_proof='provenance/current-public-source-proof.json',
                public_source_scope='Exact origin input bytes; transformed units and fixed guards are generated by the current reviewed project code')
            resource['limitations'] = [line for line in resource.get('limitations', []) if 'does not assert an immutable public repository commit for current battery inputs' not in line]
        if resource['id'] == 'resource:intel-xevmafix-724':
            resource['provenance'].update(independent_source_rebuild_proof='provenance/xevma-source-rebuild/xevma-rebuild-proof.json',
                independent_source_recipe='provenance/xevma-source-rebuild/xevma-rebuild-inputs.json', source_binary_correspondence_verified=True)
            resource['acceptance'].update(build_chain_reproduced=True, independent_source_rebuild_verified=True, fresh_kernel_module_build=False)
            resource['limitations'].append('An independently executed public source rebuild reproduced the exact module; this pool build only verifies and packages the explicit module input. Hardware/loading/service/signing acceptance remains separate.')
        name = root+'/resource.toon'; document(name, resource)
        resources.append(dict(id=resource['id'], version=resource['version'], path=name, sha256=sha(files[name])))
    ids = [r['id'] for r in resources]
    if not set(PREFERENCE) <= set(ids): raise ValueError('Required independent resources are missing')
    ordered = PREFERENCE+[identity for identity in ids if identity not in PREFERENCE]
    models = []; evidence = []
    for value, source in model_context(project):
        value['resources'] = [dict(resource=identity, enabled=False, parameters={}) for identity in ordered]
        name = 'brands/onexplayer/models/'+value['id'].split(':', 1)[1]+'.toon'; document(name, value)
        models.append(dict(id=value['id'], path=name, sha256=sha(files[name]))); evidence.append(source)
    write('provenance/model-context.json', (json.dumps(dict(kind='ProjectSourceModelContext', guides=evidence,
        legacy_package_or_cache_read=False, predecessor_catalog_sha256='6672bdd06e2b6b7a23e0de71532b56c0052a7d2e85e4eaf24bb2ceacde487730'), indent=2)+'\n').encode())
    inventory = [dict(path=n, size=len(d), sha256=sha(d)) for n, d in sorted(files.items())]
    result = dict(schema_version=1, kind='BoilerResourceCatalog', trust='unsigned-development', official_release=False, resources=resources, models=models, files=inventory)
    document('catalog.toon', result); write('catalog.json', (json.dumps(result, ensure_ascii=False, indent=2)+'\n').encode())
    validate_pool(output); return result
