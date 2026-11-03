"""Export reviewed public sources into a fresh directory without private state."""
import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
import shutil
import sys
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.manifest import confined, relative_path


def reviewed_files(root=ROOT):
    policy = json.loads((root/'scripts/source-files.json').read_text(encoding='utf-8'))
    files = policy.get('files')
    if (policy.get('kind') != 'BoilerSourceWhitelist' or not isinstance(files, list)
            or any(not isinstance(name, str) for name in files)
            or len({name.casefold() for name in files}) != len(files)):
        raise ValueError('Invalid or duplicate source whitelist entry')
    for name in files:
        relative_path(name)
        lower_name = name.casefold()
        parts = lower_name.split('/')
        if (any(part in {'.local', '.git', '.codex', '.agents', '__pycache__', 'research'} for part in parts)
                or (name.lower().endswith(('.pem', '.key', '.private', '.pfx', '.p12', '.pyc', '.pdb'))
                    and name != 'protocol/trusted-public-key.pem')
                or any(fnmatch.fnmatchcase(lower_name, pattern.casefold()) for pattern in policy.get('excluded', []))):
            raise ValueError('Private or generated path conflicts with source whitelist: '+name)
        if not confined(root, name).is_file():
            raise ValueError('Whitelisted source missing: '+name)
    return files


def verify_adaptation_snapshots(root, files):
    # Preserve the historical source lock while validating the explicit
    # relocation of its BMI260 sources into the common device tree.
    record=json.loads((root/'adaptations/records/migrations/bmi260-source-relocation-20261103.json').read_bytes())
    if record.get('kind')!='BoilerSourceRelocation' or record.get('schema_version')!=1:
        raise ValueError('Invalid BMI source relocation')
    mapping={}
    for row in record['files']:
        old=row['old_path'];new=row['new_path']
        if (old in mapping or not old.startswith('brands/onexplayer/common/scripts/gyro/')
                or new!='common/sensors/bosch/bmi260/gyro/sources/'+old.rsplit('/',1)[1]):
            raise ValueError('Invalid BMI source relocation member')
        mapping[old]=row
    for name in ('source-lock.json', 'current-source-lock.json', 'upstream-source-lock.json'):
        lock = root/'adaptations'/name
        if not lock.exists():
            raise ValueError('Required adaptation source inventory is missing: '+name)
        for entry in json.loads(lock.read_bytes())['files']:
            relocation=mapping.get(entry['path']) if name=='source-lock.json' else None
            if relocation and (relocation['size']!=entry['size'] or relocation['sha256']!=entry['sha256']):
                raise ValueError('BMI relocation differs from historical inventory')
            relative = 'adaptations/'+(relocation['new_path'] if relocation else entry['path'])
            if relative not in files:
                raise ValueError('Adaptation source inventory is outside the reviewed source whitelist: '+relative)
            data = confined(root, relative).read_bytes()
            if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('Adaptation source differs from reviewed inventory: '+relative)
    verify_model_catalog(root, files)


def verify_model_catalog(root, files):
    """TOON is authoritative; reject omitted models/parser or stale JSON views."""
    path = confined(root, 'adaptations/scripts/toon_catalog.py')
    spec = importlib.util.spec_from_file_location('boiler_source_toon_catalog', path)
    catalog = importlib.util.module_from_spec(spec); spec.loader.exec_module(catalog)
    adaptations = root/'adaptations'
    catalog.generate(adaptations, check=True)
    required = {'adaptations/scripts/toon_catalog.py', 'adaptations/toon-parser-lock.json'}
    required.update('adaptations/'+name for name in catalog.views(adaptations))
    required.update('adaptations/'+name for name in catalog.definition_sources(adaptations))
    for name,model in catalog.models(adaptations):
        required.add('adaptations/'+name)
        required.add(catalog.ADAPTERS[model['build']['adapter']][0])
        if model['build']['adapter']=='oxp3-current-v1':
            required.update(('adaptations/scripts/build-upstream-repairs.py','adaptations/scripts/build-shared-base.py'))
        if model['build']['adapter']=='x2-complete-v1':
            required.update(('adaptations/scripts/build-complete-bridge.py','adaptations/scripts/build-x2-hdr-overlay.py'))
        required.update(row['lock'] for row in model['build']['materials'])
        catalog.check_sources(root,model)
        for material in model['build']['materials']:
            inventory=catalog.json_document(catalog.local(root,material['lock']).read_bytes())
            if inventory.get('kind')=='KeyboardRuntimeSourceInventory':
                required.update(row['source'] for row in inventory['runtime_sources'])
                required.add('adaptations/scripts/build-keyboard-runtime.py')
    parser = catalog.json_document((adaptations/'toon-parser-lock.json').read_bytes())
    required.update('adaptations/vendor/'+row['path'] for row in parser['files'])
    from protocol.model_catalog import read_lock, snapshot_records
    snapshot_records(root)
    required.add('components/model-catalog/lock.json')
    required.update('components/model-catalog/snapshot/'+row['path'] for row in read_lock(root)['files'])
    if not required.issubset(files):
        raise ValueError('TOON source, generated view or parser omitted from whitelist: '+str(sorted(required-set(files))))


def export(destination):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Use a new isolated source checkout directory')
    files = reviewed_files()
    verify_adaptation_snapshots(ROOT, set(files))
    inventory = []
    for name in files:
        source = confined(ROOT, name)
        target = confined(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as incoming, target.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        inventory.append(dict(path=name, size=target.stat().st_size,
                              sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    return dict(directory=str(destination), files=inventory, history='none')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(export(args.destination), indent=2))
