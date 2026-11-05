"""Build a standalone X2 package from the exact complete OXP3 public package.

TOON declares the inherited feature set and independently pins both overlays.
No source/cache discovery, package execution or physical-device operation occurs.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

BASE_ANCHOR = 'cb39c94bbe7eb351221c380ebdb7a875a097b52c17e90bfb0f1edc22b12ab684'
BASE_VERSION = '0.1.0-localtest.20261030.8'
VERSION = '0.1.0-localtest.20261103.x2-fixed-efi.3'
MODEL = 'guide:x2-mini'
IDENTITY = 'device:x2-mini'
REUSE_RECORD = 'x2-full-reuse.json'
GENERATED = 'payload/legacy/maintenance/manifest.json'
BRIDGE_PATHS = {'adapter.py', 'maintenance_adapter.py', 'scripts/apply.sh',
                'scripts/verify.sh', 'scripts/restore.sh', 'scripts/maintenance.sh',
                'upstream-repairs/speaker/speaker_route.py', 'upstream-repairs/xevmafix/load.py'}
HDR_SHARED_PATHS = {'payload/legacy/components/profile.json',
    'payload/legacy/components/controls/profile.json',
    'payload/legacy/components/x2mini_hdr_device.py',
    'payload/legacy/maintenance/app/oxp3_maintenance/components.py',
    'payload/legacy/maintenance/app/oxp3_maintenance/x2mini_hdr_device.py'}
HDR_PREFIXES = ('payload/legacy/components/hdr/', 'payload/legacy/components/desktop-hdr/',
                'payload/legacy/integration/desktop-hdr/', 'payload/legacy/maintenance/resources/desktop-hdr/')
CPU_PREFIX = 'payload/legacy/components/controls/cpu/'

class BuildError(ValueError): pass

def require(ok, message):
    if not ok: raise BuildError(message)

def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()

def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8')

def document(data):
    def pairs(rows):
        value = {}
        for key, item in rows:
            require(key not in value, 'Duplicate JSON member: '+key); value[key] = item
        return value
    return json.loads(data, object_pairs_hook=pairs)

def relative(name):
    require(isinstance(name, str) and name and '\\' not in name and ':' not in name
        and not name.startswith('/') and all(part not in ('', '.', '..') for part in name.split('/'))
        and not any(ord(char)<32 for char in name), 'Unsafe relative path')
    require(not any(part.casefold() in {'.local','.git','.aws','.codex','.agents','__pycache__'} for part in name.split('/')),
            'Private/internal path is not a package input')
    require(not any(part.rstrip(' .') != part for part in name.split('/')), 'Noncanonical relative path')
    reserved={'CON','PRN','AUX','NUL',*[f'COM{n}' for n in range(1,10)],*[f'LPT{n}' for n in range(1,10)]}
    require(not any(part.split('.')[0].upper() in reserved for part in name.split('/')), 'Reserved relative path')
    return name

def regular(root, name):
    root = Path(root).absolute()
    for ancestor in (root, *root.parents):
        require(not ancestor.is_symlink() and not getattr(ancestor,'is_junction',lambda:False)(), 'Linked input root/ancestor')
    current = root
    for part in relative(name).split('/'):
        current = current/part
        require(not current.is_symlink() and not getattr(current,'is_junction',lambda:False)(), 'Linked input: '+name)
    require(current.is_file() and current.resolve().is_relative_to(root.resolve()), 'Missing or escaping input: '+name)
    return current

def inventory(root):
    result = []
    for path in sorted(Path(root).rglob('*')):
        if path.is_file():
            name = path.relative_to(root).as_posix()
            if name == 'manifest.json': continue
            regular(root, name)
            result.append(dict(path=name, size=path.stat().st_size, sha256=sha(path)))
    return result

def load_recipe(root, adaptations):
    path = regular(root, 'scripts/toon_catalog.py')
    spec = importlib.util.spec_from_file_location('boiler_x2_full_catalog', path)
    catalog = importlib.util.module_from_spec(spec); spec.loader.exec_module(catalog)
    matches = [value for _, value in catalog.models(adaptations) if value['id'] == MODEL]
    require(len(matches) == 1, 'Exactly one authoritative X2 TOON model is required')
    model = matches[0]
    require(model['build']['adapter'] == 'x2-complete-v1' and model['build']['complete'] is True
        and model['build']['missing'] == [], 'TOON does not declare the complete reuse builder')
    definition = model['package']
    require(definition.get('kind') == 'BoilerInheritedPackageDefinition' and definition.get('base_input') == 'base',
            'TOON must explicitly inherit the complete base package')
    inputs = {row['name']: row for row in model['build']['inputs']}
    require(set(inputs) == {'base','bridge_overlay','hdr_overlay'} and inputs['base']['sha256'] == BASE_ANCHOR,
            'Explicit complete-base and independently pinned overlays required')
    return model, inputs

def check_coverage(base, model):
    definition = model['package']
    components = [row['id'] for row in base['components']]
    capabilities = [row['id'] for row in base['legacy_capabilities']]
    require(definition['inherited_components'] == components, 'TOON omits, reorders or adds inherited top-level components')
    require(definition['inherited_legacy_capabilities'] == capabilities, 'TOON omits, reorders or adds inherited adaptations')
    require(len(components) == 4 and len(capabilities) == 28, 'Unexpected complete OXP3 scope')
    require(not any(cid.startswith('x2-mini:') for cid in components), 'Do not stack old partial X2 components onto complete legacy ownership')
    return components, capabilities

def overlay(root, role, expected, base_files):
    lock_path = regular(root, 'overlay-lock.json')
    require(sha(lock_path) == expected, 'Overlay lock differs from the TOON independent pin: '+role)
    value = document(lock_path.read_bytes())
    require(value.get('schema_version') == 1 and value.get('kind') == 'BoilerX2CompleteOverlay'
        and value.get('role') == role and value.get('base_manifest_sha256') == BASE_ANCHOR,
        'Wrong overlay role or complete base')
    require(isinstance(value.get('files'), list) and value['files'], 'Nonempty explicit overlay required')
    entries, sources = {}, {'overlay-lock.json'}
    for row in value['files']:
        require(isinstance(row, dict) and set(row) >= {'path','source','size','sha256','base_sha256'}, 'Invalid overlay row')
        name, source = relative(row['path']), relative(row['source'])
        require(name not in entries and source not in sources, 'Duplicate overlay target/source')
        require(not name.startswith(CPU_PREFIX) and name not in ('manifest.json', GENERATED, REUSE_RECORD), 'Protected payload/manifest cannot be supplied by an overlay')
        if role == 'bridge': require(name in BRIDGE_PATHS, 'Bridge overlay changes a non-bridge file: '+name)
        else: require(name in HDR_SHARED_PATHS or name.startswith(HDR_PREFIXES), 'HDR overlay changes a non-HDR file: '+name)
        old = base_files.get(name)
        require(row['base_sha256'] == (old['sha256'] if old else None), 'Overlay does not bind original bytes: '+name)
        path = regular(root, source)
        require(type(row['size']) is int and path.stat().st_size == row['size'] and sha(path) == row['sha256'], 'Changed overlay source: '+name)
        entries[name] = dict(row, input_path=path, role=role); sources.add(source)
    actual=set()
    for path in Path(root).rglob('*'):
        require(not path.is_symlink() and not getattr(path,'is_junction',lambda:False)(), 'Overlay contains a link')
        if path.is_file(): actual.add(path.relative_to(root).as_posix())
    require(actual == sources, 'Unlisted files in overlay directory')
    return entries

def output_path(output, roots):
    output = Path(output).absolute()
    for path in (output, *output.parents):
        require(not path.is_symlink() and not getattr(path,'is_junction',lambda:False)(), 'Linked output ancestor')
    require(not output.exists(), 'Use a fresh immutable output directory')
    resolved = output.resolve()
    require(not any(resolved.is_relative_to(Path(root).resolve()) for root in roots), 'Output would contaminate an input')
    return output

def copy_approved(source, target, pin):
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target)
    require(target.stat().st_size == pin['size'] and sha(target) == pin['sha256'],
            'Input changed while copying approved bytes: '+str(target.name))

def refresh_maintenance(directory):
    path = regular(directory, GENERATED)
    value = document(path.read_bytes())
    require(value.get('schema') == 1 and isinstance(value.get('files'), dict), 'Unexpected retained maintenance inventory')
    for name in value['files']:
        value['files'][name] = sha(regular(directory/'payload/legacy/maintenance/app', name))
    before = path.read_bytes(); data = encoded(value)
    if data != before: path.write_bytes(data)
    return data != before

def deterministic_zip(directory, target, rows):
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in ['manifest.json'] + [row['path'] for row in rows]:
            info = zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
            info.create_system = 3; info.external_attr = (0o100644 << 16); info.compress_type = zipfile.ZIP_DEFLATED
            with archive.open(info, 'w', force_zip64=True) as output, regular(directory,name).open('rb') as source:
                shutil.copyfileobj(source, output, length=1024*1024)

def refresh_component_inventory(metadata, rows):
    by_path={row['path']:row for row in rows}
    for component in metadata['components']:
        for entry in component.get('files', []):
            require(entry['source'] in by_path, 'Inherited component source disappeared')
            entry['size']=by_path[entry['source']]['size']
            entry['sha256']=by_path[entry['source']]['sha256']


def finalize_xevma_loader(directory, root, changes):
    """Apply the fixed correction after authenticating the historical X2 overlay."""
    loader_name = 'upstream-repairs/xevmafix/load.py'
    require(loader_name in changes, 'Reviewed X2 xevma overlay is required')
    spec = importlib.util.spec_from_file_location('boiler_xevma_kmod_x2', regular(root, 'adaptations/common/xevma/kmod_transform.py'))
    transformer = importlib.util.module_from_spec(spec); spec.loader.exec_module(transformer)
    loader_path = regular(directory, loader_name)
    loader_path.write_bytes(transformer.transform(loader_path.read_bytes()))
    return loader_name

def build(base, bridge_overlay, hdr_overlay, output, *, root, adaptations=None, recipe=None):
    root = Path(root).resolve(); adaptations = Path(adaptations or root/'adaptations').resolve()
    sys.path.insert(0, str(root))
    from protocol.manifest import validate_package
    from protocol.local import import_local
    base, bridge_overlay, hdr_overlay = map(lambda p: Path(p).absolute(), (base,bridge_overlay,hdr_overlay))
    model, inputs = recipe if recipe is not None else load_recipe(root, adaptations)
    baseline = validate_package(base, expected_manifest_sha256=BASE_ANCHOR)
    require(baseline['id'] == 'device:oxp3' and baseline['version'] == BASE_VERSION, 'Wrong complete OXP3 base')
    components, capabilities = check_coverage(baseline, model)
    originals = {row['path']:row for row in baseline['files']}
    changes = {}
    for role, folder, key in [('bridge',bridge_overlay,'bridge_overlay'),('hdr',hdr_overlay,'hdr_overlay')]:
        added = overlay(folder, role, inputs[key]['sha256'], originals)
        require(not set(changes).intersection(added), 'Overlays both change the same file; resolve ownership explicitly')
        changes.update(added)
    require({'adapter.py','maintenance_adapter.py'} <= set(changes), 'X2 identity and maintenance bridge overlay is required')
    output = output_path(output, (base,bridge_overlay,hdr_overlay))
    directory = output/'package'; directory.mkdir(parents=True)
    for name, row in originals.items():
        copy_approved(regular(base,name),directory/name,row)
    for name, row in changes.items():
        copy_approved(row['input_path'],directory/name,row)
    source_path = regular(root, 'adaptations/brands/onexplayer/x2-mini/bridge/fixed_efi_sources.py')
    require(sha(source_path) == '44b04774e27d5bded09b6803d4fd124980e739518e41c8cf59c44c606e26f856',
            'X2 fixed EFI transform differs from reviewed bytes')
    spec = importlib.util.spec_from_file_location('boiler_x2_fixed_efi_sources', source_path)
    source_transform = importlib.util.module_from_spec(spec); spec.loader.exec_module(source_transform)
    for name in ('adapter.py','maintenance_adapter.py'):
        source = regular(root, 'adaptations/brands/onexplayer/oxp3/bridge/'+name)
        regular(directory, name).write_bytes(source_transform.transform(name, source.read_bytes()))
    lock_builder_path = regular(root, 'scripts/prepare-x2-efi-provider-lock.py')
    spec = importlib.util.spec_from_file_location('boiler_x2_fixed_efi_lock', lock_builder_path)
    lock_builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(lock_builder)
    expected_lock = (json.dumps(lock_builder.build(base, bridge_overlay, hdr_overlay), sort_keys=True, indent=2)+'\n').encode()
    require(regular(root, 'components/x2_efi_provider.lock.json').read_bytes() == expected_lock,
            'X2 provider closure differs from reviewed source inputs')
    generated = {GENERATED} if refresh_maintenance(directory) else set()
    generated.add(finalize_xevma_loader(directory, root, changes))
    metadata = document(encoded(baseline)); metadata['id'] = IDENTITY; metadata['device_id'] = 'x2-mini'; metadata['version'] = VERSION
    metadata['minimum_engine_version'] = '0.1.4'
    metadata.pop('oxp3_efi_contract', None)
    metadata['x2_efi_contract'] = {'kind':'x2-split-lock-efi-v1','component':'oxp3:legacy-adaptation'}
    metadata['name'] = {'zh':'ONEXPLAYER X2 Mini / Mini PRO 完整适配','en':'ONEXPLAYER X2 Mini / Mini PRO complete adaptation'}
    for item in [metadata] + metadata['components']:
        compatibility = item.get('compatibility', {})
        if 'device_ids' in compatibility: compatibility['device_ids'] = ['x2-mini']
    for item in metadata['components']:
        item['minimum_engine_version'] = '0.1.4'
        if item['id'] == 'oxp3:xevmafix': item['version'] = '2026.10.29+kernel7.2.4.2'
    metadata['components'][0]['version'] = VERSION
    metadata['components'][0]['name'] = {'zh':'X2 Mini 完整共用适配','en':'X2 Mini complete shared adaptations'}
    metadata['hardware_matching'] = {'mode':'manual','automatic_selection':False,
        'note':'Model-name mismatch is advisory; actual component interfaces and dependencies retain their checks.'}
    metadata['source'] = {'kind':'local','path':'adaptations/devices/onexplayer/x2-mini.toon',
        'base_manifest_sha256':BASE_ANCHOR,'bridge_overlay_sha256':inputs['bridge_overlay']['sha256'],
        'hdr_overlay_sha256':inputs['hdr_overlay']['sha256']}
    metadata['complete_reuse'] = {'kind':'boiler-x2-complete-reuse-v1','base_manifest_sha256':BASE_ANCHOR,
        'base_source':baseline['source'],
        'components':components,'legacy_capabilities':capabilities,'hdr_mapping':{'paper_white_nits':475,'peak_nits':700}}
    (directory/REUSE_RECORD).write_bytes(encoded(metadata['complete_reuse']))
    for name, row in originals.items():
        if name not in changes and name not in generated:
            require(sha(regular(directory,name)) == row['sha256'], 'Unrelated base asset changed: '+name)
    require(all(sha(regular(directory,name)) == row['sha256'] for name,row in originals.items() if name.startswith(CPU_PREFIX)), 'Public CPU baseline changed')
    rows = inventory(directory); metadata['files'] = rows
    refresh_component_inventory(metadata, rows)
    (directory/'manifest.json').write_bytes(encoded(metadata))
    manifest_sha = sha(directory/'manifest.json'); final = validate_package(directory, expected_manifest_sha256=manifest_sha)
    from components.x2_efi_provider import verify as verify_x2_provider
    verify_x2_provider(directory, root/'components/x2_efi_provider.lock.json')
    require([row['id'] for row in final['components']] == components and [row['id'] for row in final['legacy_capabilities']] == capabilities,
            'Final package lost complete inherited scope')
    archive = output/('boiler-device-x2-mini-'+VERSION+'.zip'); deterministic_zip(directory,archive,rows)
    imported = import_local(archive, output/'verified-import', developer=True)
    validate_package(output/'verified-import', expected_manifest_sha256=manifest_sha)
    report = dict(kind='X2CompleteReuseBuildVerification',build_verified=True,full_x2_adaptation_complete=True,
        hardware_acceptance=False,official_release=False,deployment_executed=False,id=IDENTITY,version=VERSION,
        archive=archive.name,size=archive.stat().st_size,sha256=sha(archive),manifest_sha256=manifest_sha,
        all_base_assets_preserved=True,inherited_components=components,inherited_legacy_capabilities=capabilities,
        base_inventory_files=len(originals),files=len(rows),allowed_differences=sorted(set(changes)|generated),
        identity_metadata_changed=True,package_record=REUSE_RECORD)
    (output/'verification.json').write_bytes(encoded(report))
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',type=Path,required=True); parser.add_argument('--adaptations-root',type=Path)
    parser.add_argument('--base',type=Path,required=True); parser.add_argument('--bridge-overlay',type=Path,required=True)
    parser.add_argument('--hdr-overlay',type=Path,required=True); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.base,args.bridge_overlay,args.hdr_overlay,args.output,
        root=args.project_root,adaptations=args.adaptations_root),ensure_ascii=False,indent=2))

if __name__ == '__main__': main()
