"""Build firmware solely from twelve project-local public single-file inputs."""
import copy
import hashlib
import json
from pathlib import Path

from protocol.resource_pool import decoder, local, validate_pool, json_document
from protocol.firmware_contract import IDENTITY, validate_contract
from .model_context import models as model_context

LEGACY_SOURCE = 'adaptations/common/resource-inputs/intel-be201/source.json'
SOURCE = 'adaptations/common/network/intel/be201/build-recipe.json'
INPUT_MAP = 'adaptations/common/network/intel/be201/input-map.json'
LEGACY_PREFIX = 'devices/network/intel/be201/'
PREFIX = 'common/network/intel/be201/'


def sha(raw): return hashlib.sha256(raw).hexdigest()


def resolve(project, name):
    mapping=json_document(local(project,INPUT_MAP).read_bytes())
    if mapping.get('kind')!='CanonicalFirmwareInputMap' or mapping.get('schema_version')!=1:
        raise ValueError('Invalid canonical firmware input map')
    rows=[row for row in mapping.get('files',[]) if row.get('legacy')==name]
    if len(rows)!=1: raise ValueError('Missing or duplicate canonical firmware input: '+name)
    row=rows[0]; target=row.get('canonical')
    if not isinstance(target,str) or not target.startswith('adaptations/common/network/intel/be201/'):
        raise ValueError('Firmware input is outside canonical common directory')
    raw=local(project,target).read_bytes()
    if type(row.get('size')) is not int or len(raw)!=row['size'] or sha(raw)!=row.get('sha256'):
        raise ValueError('Canonical firmware input differs: '+name)
    return raw


def relocate(value):
    if isinstance(value,str): return PREFIX+value.removeprefix(LEGACY_PREFIX) if value.startswith(LEGACY_PREFIX) else value
    if isinstance(value,list): return [relocate(x) for x in value]
    if isinstance(value,dict): return {key:relocate(item) for key,item in value.items()}
    return value


def build(project, output):
    project, output = Path(project), Path(output); raw_source = resolve(project, LEGACY_SOURCE); source = json_document(raw_source)
    if source.get('schema_version') != 1 or source.get('kind') != 'LockedSingleFirmwareSources': raise ValueError('Unsupported firmware source recipe')
    resource = relocate(copy.deepcopy(source['resource'])); validate_contract(resource)
    expected = {r['path'] for r in resource['payload']['files']} | {PREFIX+'sources/'+name for name in ('000-LICENCE.ibt_firmware', '001-LICENCE.iwlwifi_firmware', '002-provenance.json')}
    if (len(source['files']) != 12 or {relocate(r['output']) for r in source['files']} != expected
            or len({r['path'] for r in source['files']}) != 12): raise ValueError('Firmware recipe must contain exactly nine firmware and three companions')
    if resource['license_files'] != [PREFIX+'sources/000-LICENCE.ibt_firmware', PREFIX+'sources/001-LICENCE.iwlwifi_firmware']: raise ValueError('Firmware licenses changed')
    contents = {}
    for row in source['files']:
        if not row['path'].startswith('adaptations/common/resource-inputs/intel-be201/'): raise ValueError('Firmware input is outside the explicit single-file source set')
        raw = resolve(project, row['path'])
        if len(raw) != row['size'] or sha(raw) != row['sha256']: raise ValueError('Firmware single source differs from lock: '+row['path'])
        contents[relocate(row['output'])] = raw
    record=decoder(project/'adaptations').loads(local(project,'adaptations/common/network/intel/be201/source.toon').read_text(encoding='utf-8'),strict=True)
    if record.get('id')!=IDENTITY or record.get('source_only') is not True: raise ValueError('Invalid canonical firmware source record')
    expected_firmware=sorted(row['sha256'] for row in record['files'] if row['path'].startswith('inputs/'))
    actual_firmware=sorted(row['sha256'] for row in source['files'] if '/inputs/' in row['path'])
    if len(expected_firmware)!=9 or expected_firmware!=actual_firmware: raise ValueError('Canonical firmware declaration differs from locked inputs')
    if output.exists(): raise FileExistsError('Use a fresh firmware pool output')
    output.mkdir(parents=True); toon = decoder(project/'adaptations'); files = {}
    def write(name, raw):
        path = local(output, name); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); files[name] = raw
    def document(name, value): write(name, (toon.dumps(value)+'\n').encode())
    for name, raw in contents.items(): write(name, raw)
    proof = dict(kind='ProjectSingleFirmwareSourceLock', resource=IDENTITY, source_recipe=SOURCE, source_recipe_sha256=sha(raw_source),
        origin=source['origin'], files=source['files'], old_package_or_archive_read=False)
    write('provenance/wifi-single-source-lock.json', (json.dumps(proof, ensure_ascii=False, indent=2)+'\n').encode())
    resource['provenance']['source_recipe_sha256'] = sha(raw_source)
    name = PREFIX+'resource.toon'; document(name, resource)
    write(PREFIX+'resource.json', (json.dumps(resource, ensure_ascii=False, indent=2)+'\n').encode())
    models = []
    for context, _ in model_context(project):
        value = dict(schema_version=1, kind='BoilerResourceModel', id=context['id'], name=context['name'],
            hardware_matching=dict(advisory_only=True), resources=[dict(resource=IDENTITY, default=False)],
            pending=[dict(component='physical-wifi-acceptance', reason='Trusted physical facts, firmware load and radio/boot/recovery acceptance are pending')])
        path = 'brands/onexplayer/models/'+value['id'].split(':', 1)[1]+'.toon'; document(path, value)
        models.append(dict(id=value['id'], path=path, sha256=sha(files[path])))
    inventory = [dict(path=name, size=len(raw), sha256=sha(raw)) for name, raw in sorted(files.items())]
    catalog = dict(schema_version=1, kind='BoilerResourceCatalog', trust='unsigned-development', official_release=False,
        resources=[dict(id=IDENTITY, version=resource['version'], path=PREFIX+'resource.toon', sha256=sha(files[PREFIX+'resource.toon']))], models=models, files=inventory)
    document('catalog.toon', catalog); write('catalog.json', (json.dumps(catalog, ensure_ascii=False, indent=2)+'\n').encode())
    validate_pool(output); return catalog
