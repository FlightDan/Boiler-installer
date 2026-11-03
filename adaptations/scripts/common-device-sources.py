"""Validate the source-only common device records and their model references.

This deliberately validates metadata and immutable source bytes only.  It does
not build a payload, call a handler, or activate a device repair.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor'
sys.path.insert(0, str(VENDOR))

RESOURCES = {
    'resource:intel-xevmafix-724': 'common/gpu/intel/xe/xevmafix-724/source.toon',
    'resource:intel-be201-firmware': 'common/network/intel/be201/source.toon',
    'resource:oxp3-speaker-route': 'common/audio/realtek/alc245/speaker-route/source.toon',
    'resource:bmi260-gyro': 'common/sensors/bosch/bmi260/gyro/source.toon',
}
MODELS = {
    'device:oxp3': 'brands/onexplayer/oxp3/common-device-sources.toon',
    'device:x2-mini': 'brands/onexplayer/x2-mini/common-device-sources.toon',
}
GM7 = 'common/ssd/acer/gm7/components.toon'
INDEX = 'common-device-source-index.json'
MIGRATIONS = ('records/migrations/common-device-resource-ownership-20261103.json',
              'records/migrations/common-bmi260-inputplumber-20261103.json')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(name: str) -> Path:
    value = PurePosixPath(name)
    if not name or value.is_absolute() or '\\' in name or ':' in name or any(part in ('', '.', '..') for part in value.parts):
        raise ValueError('unsafe repository-relative path: ' + name)
    return ROOT.joinpath(*value.parts)


def load(name: str) -> dict:
    path = relative(name)
    if not path.is_file() or path.is_symlink():
        raise ValueError('missing regular TOON record: ' + name)
    value = toon_format.loads(path.read_text(encoding='utf-8'), strict=True)
    if not isinstance(value, dict):
        raise ValueError('TOON root must be an object: ' + name)
    return value


def validate_codec() -> None:
    """Accept exactly the pinned TOON decoder source, not local bytecode."""
    lock_path = VENDOR / 'toon-parser-lock.json'
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    if lock != json.loads((ROOT / 'toon-parser-lock.json').read_text(encoding='utf-8')):
        raise ValueError('vendored TOON codec lock differs from project lock')
    if lock.get('package') != 'toon-format' or lock.get('version') != '1.2.0' or lock.get('spec') != '4.4':
        raise ValueError('unexpected TOON codec lock')
    expected = {row['path']: row for row in lock.get('files', [])}
    actual = {path.relative_to(VENDOR).as_posix() for path in VENDOR.rglob('*')
              if path.is_file() and '__pycache__' not in path.parts and path.name != 'toon-parser-lock.json'}
    if actual != set(expected):
        raise ValueError('TOON codec file set differs from lock')
    for name, row in expected.items():
        path = VENDOR.joinpath(*PurePosixPath(name).parts)
        if path.is_symlink() or path.stat().st_size != row['size'] or digest(path) != row['sha256']:
            raise ValueError('TOON codec bytes differ from lock: ' + name)


# Authenticate vendored parser bytes before importing executable parser code.
validate_codec()
import toon_format  # noqa: E402


def checked_file(row: dict, source: str) -> None:
    required = {'path', 'size', 'sha256', 'origin', 'license', 'availability'}
    if set(row) != required or not isinstance(row['path'], str):
        raise ValueError('invalid source file declaration: ' + source)
    availability = row['availability']
    if availability == 'checked-in':
        path = relative(str(PurePosixPath(source).parent / row['path']))
        if not path.is_file() or path.is_symlink() or path.stat().st_size != row['size'] or digest(path) != row['sha256']:
            raise ValueError('source bytes differ: ' + str(path.relative_to(ROOT)))
    elif availability == 'external-locked':
        path = relative(str(PurePosixPath(source).parent / row['path']))
        if path.exists() and (not path.is_file() or path.is_symlink() or path.stat().st_size != row['size'] or digest(path) != row['sha256']):
            raise ValueError('available external source bytes differ: ' + str(path.relative_to(ROOT)))
    else:
        raise ValueError('unknown source availability: ' + str(availability))


def resource(identifier: str, source: str) -> dict:
    value = load(source)
    if value.get('schema_version') != 1 or value.get('kind') != 'BoilerCommonDeviceSource':
        raise ValueError('not a source-only common device record: ' + source)
    if value.get('id') != identifier or value.get('source_only') is not True:
        raise ValueError('common device identity/source-only state differs: ' + source)
    for key in ('hardware_matching', 'compatibility', 'lifecycle', 'licenses'):
        if key not in value:
            raise ValueError('missing common device field '+key+': '+source)
    files = value.get('files')
    if not isinstance(files, list) or not files:
        raise ValueError('common source file list missing: ' + source)
    for row in files:
        if not isinstance(row, dict):
            raise ValueError('common source file is not an object: ' + source)
        checked_file(row, source)
    for name in value['licenses']:
        path = relative(str(PurePosixPath(source).parent / name))
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            raise ValueError('missing declared source license: ' + str(path.relative_to(ROOT)))
    return {'id': identifier, 'source': source, 'source_sha256': digest(relative(source)),
            'files': len(files), 'external_inputs': sum(row['availability'] == 'external-locked' for row in files)}


def validate_migration() -> None:
    actual = []
    for name in MIGRATIONS:
        value = json.loads(relative(name).read_text(encoding='utf-8'))
        actual.extend(value.get('file_maps', []))
        actual.extend(value.get('external_input_maps', []))
    mapped = {(row.get('id'), row.get('new_path'), row.get('size'), row.get('sha256')) for row in actual}
    expected = set()
    for identifier, source in RESOURCES.items():
        for row in load(source)['files']:
            expected.add((identifier, str(PurePosixPath(source).parent / row['path']), row['size'], row['sha256']))
    if mapped != expected or len(mapped) != len(actual):
        raise ValueError('migration file maps differ from declared common source files')
    if any(not isinstance(row.get('old_path'), str) or not row['old_path'] for row in actual):
        raise ValueError('migration source path missing')


def gm7() -> dict:
    value = load(GM7)
    definition = (value.get('definitions') or {}).get('ssd-gm7-boot-repair')
    expected = {'schema_version': 1, 'kind': 'ComponentManifest', 'version': '0.1.0',
                'minimum_engine_version': '0.1.3', 'default': False, 'optional': True,
                'requires': [], 'conflicts': [], 'files': [], 'hooks': {}, 'compatibility': {},
                'ssd_boot_policy': {'id': 'ssd:gm7-boot-repair', 'version': 1}}
    if value.get('schema_version') != 1 or value.get('kind') != 'BoilerComponentDefinitions' or not isinstance(definition, dict):
        raise ValueError('GM7 fileless provider declaration missing')
    if any(definition.get(key) != expected[key] for key in expected):
        raise ValueError('GM7 provider contract differs')
    return {'id': 'ssd:gm7-boot-repair', 'component_id': 'common:ssd-gm7-boot-repair',
            'version': 1, 'source': GM7, 'source_sha256': digest(relative(GM7))}


def model(model_id: str, source: str, policy: dict) -> dict:
    guide_path = str(PurePosixPath(source).parent / (model_id.split(':', 1)[1] + '.toon'))
    guide = load(guide_path)
    value = guide.get('resource_model', {}).get('common_references')
    if not isinstance(value, dict) or load(source) != value:
        raise ValueError('generated common references differ from model TOON: ' + source)
    if value.get('schema_version') != 1 or value.get('kind') != 'BoilerModelCommonReferences' or value.get('source_only') is not True or value.get('model_id') != model_id:
        raise ValueError('model common reference identity differs: ' + source)
    seen = {(row.get('id'), row.get('source')) for row in value.get('resources', []) if isinstance(row, dict)}
    if seen != set(RESOURCES.items()) or len(value.get('resources', [])) != len(RESOURCES):
        raise ValueError('model common resources differ: ' + source)
    policies = value.get('boot_policies')
    expected = {'id': policy['id'], 'version': policy['version'], 'source': policy['source'], 'component_ref': 'ssd-gm7-boot-repair'}
    if policies != [expected]:
        raise ValueError('model GM7 source provider differs: ' + source)
    return {'model_id': model_id, 'source': source, 'source_sha256': digest(relative(source)),
            'resources': [row['id'] for row in value['resources']], 'boot_policy': policy['component_id']}


def build() -> dict:
    validate_codec()
    validate_migration()
    rows = [resource(identifier, source) for identifier, source in RESOURCES.items()]
    policy = gm7()
    models = [model(identifier, source, policy) for identifier, source in MODELS.items()]
    return {'schema_version': 1, 'kind': 'BoilerCommonDeviceSourceIndex', 'source_only': True,
            'resources': rows, 'boot_policy': policy, 'models': models}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'write'))
    action = parser.parse_args().action
    if action == 'write':
        for model_id, source in MODELS.items():
            guide_path = str(PurePosixPath(source).parent / (model_id.split(':', 1)[1] + '.toon'))
            value = load(guide_path).get('resource_model', {}).get('common_references')
            if not isinstance(value, dict): raise ValueError('missing model common references')
            relative(source).write_text(toon_format.dumps(value) + '\n', encoding='utf-8', newline='\n')
    output = json.dumps(build(), ensure_ascii=False, indent=2) + '\n'
    path = ROOT / INDEX
    if action == 'write':
        path.write_text(output, encoding='utf-8', newline='\n')
    elif not path.is_file() or path.read_text(encoding='utf-8') != output:
        raise SystemExit('common device source index is stale; run scripts/common-device-sources.py write')


if __name__ == '__main__':
    main()
