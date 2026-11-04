"""Prepare and check the source-only brand/model adaptation collection.

This tool reads committed, explicitly listed public source bytes. It never runs
adaptation code, builds an install payload, changes DeviceIndex or publishes.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BEGIN = '<!-- BEGIN GENERATED INSTALL LIST -->'
END = '<!-- END GENERATED INSTALL LIST -->'
SOURCE_BEGIN = '<!-- BEGIN GENERATED SOURCE -->'
SOURCE_END = '<!-- END GENERATED SOURCE -->'
LEVELS = {'required': '必装', 'recommended': '推荐', 'optional': '可选',
          'conditional': '条件适用', 'pending': '待补实现', 'excluded': '排除'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def local(root, name):
    value = PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or value.is_absolute()
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Expected repository-relative path: ' + name)
    path = root.joinpath(*value.parts)
    if any(item.is_symlink() or getattr(item, 'is_junction', lambda: False)()
           for item in (path, *path.parents) if item.is_relative_to(root)):
        raise ValueError('Linked source path: ' + name)
    return path


def records(root):
    catalog = read(root/'adaptations.json')
    if catalog['kind'] != 'AdaptationSourceCatalog' or catalog['installable'] is not False:
        raise ValueError('Source catalog cannot advertise an installable package')
    return [(name, read(local(root, name))) for name in catalog['models']]


def table(model):
    lines = ['| 组件 ID | 适配项目 | 建议 | 验证与适用边界 |',
             '| --- | --- | --- | --- |']
    for item in model['components']:
        dependency = '；依赖：' + '、'.join(item['dependencies']) if item['dependencies'] else ''
        notes = item['notes'].rstrip('。') if dependency else item['notes']
        cells = (item['id'], item['name'], LEVELS[item['recommendation']], notes + dependency)
        lines.append('| ' + ' | '.join(cell.replace('|', '\\|').replace('\n', ' ') for cell in cells) + ' |')
    return '\n'.join(lines)


def guide(root, model, *, write=False):
    path = local(root, model['guide'])
    value = path.read_text(encoding='utf-8')
    if value.count(BEGIN) != 1 or value.count(END) != 1:
        raise ValueError('Missing unique generated guide markers: ' + model['guide'])
    before, rest = value.split(BEGIN)
    _, after = rest.split(END)
    expected = before + BEGIN + '\n' + table(model) + '\n' + END + after
    if expected.count(SOURCE_BEGIN) != 1 or expected.count(SOURCE_END) != 1:
        raise ValueError('Missing unique generated source markers: ' + model['guide'])
    before_source, rest_source = expected.split(SOURCE_BEGIN)
    _, after_source = rest_source.split(SOURCE_END)
    source = model['source_revision']
    source_text = ('适配源码来源：`' + source['repository'] + '`，固定提交 `' + source['commit']
                   + '`。逐文件原路径、大小和 SHA-256 见 [源码库存](../../../source-lock.json)。')
    expected = before_source + SOURCE_BEGIN + '\n' + source_text + '\n' + SOURCE_END + after_source
    if write:
        path.write_text(expected, encoding='utf-8', newline='\n')
    elif value != expected:
        raise ValueError('Guide differs from component manifest: ' + model['guide'])



# The original inventory is immutable. Only these ten historical BMI260 paths
# resolve to the separately documented shared resource.
OLD_GYRO = 'brands/onexplayer/common/scripts/gyro/'
NEW_GYRO = 'common/sensors/bosch/bmi260/gyro/sources/'
RELOCATION = 'records/migrations/bmi260-source-relocation-20261103.json'
GYRO_FILES = frozenset(('gyro_binding.py', 'Makefile', 'bmi270.h', 'inputplumber.conf',
                        'launch.sh', 'load.sh', 'onexplayer3-bmi260.patch',
                        'oxp3-gyro.service', 'oxp3_bmi260.c', 'unload.sh'))


def relocations(root, lock):
    record = read(root/RELOCATION)
    historical_hashes = {name: hashlib.sha256(local(root, name).read_bytes()).hexdigest()
                         for name in ('source-plan.json', 'source-lock.json')}
    if record.get('historical_source_sha256') != historical_hashes:
        raise ValueError('Historical source bytes differ from relocation record')
    if (record.get('kind') != 'BoilerSourceRelocation' or record.get('schema_version') != 1
            or record.get('source') != lock['source']
            or record.get('repository_baseline') != '5c79ead752e21970303f0a2d03b810a1ee77ac43'):
        raise ValueError('BMI260 relocation provenance differs')
    expected = {item['path']: item for item in lock['files'] if item['path'].startswith(OLD_GYRO)}
    if set(expected) != {OLD_GYRO + name for name in GYRO_FILES}:
        raise ValueError('Historical BMI260 inventory differs')
    rows = record.get('files')
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError('BMI260 relocation file count differs')
    mapping = {}
    for row in rows:
        old = row.get('old_path')
        if old not in expected or old in mapping:
            raise ValueError('Unexpected BMI260 relocation')
        item = expected[old]
        new = NEW_GYRO + old[len(OLD_GYRO):]
        if row != {'old_path': old, 'new_path': new, 'upstream_path': item['upstream_path'],
                   'size': item['size'], 'sha256': item['sha256'],
                   'license': 'MIT' if old[len(OLD_GYRO):] in ('gyro_binding.py', 'inputplumber.conf') else 'GPL-2.0'}:
            raise ValueError('BMI260 relocation metadata differs: ' + old)
        if local(root, old).exists():
            raise ValueError('Legacy BMI260 source still exists: ' + old)
        mapping[old] = new
    return mapping


def validate_common(root):
    spec = importlib.util.spec_from_file_location('common_device_sources', Path(__file__).with_name('common-device-sources.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    module.VENDOR = root/'vendor'
    module.build()
    return module


def validate_gyro_reference(module, name, model):
    identifier = model.get('id', '')
    if not isinstance(identifier, str) or not identifier.startswith('guide:') or not identifier[6:]:
        raise ValueError('Invalid gyro model guide identity: ' + name)
    reference = module.load(name)
    if (reference.get('schema_version') != 1 or reference.get('kind') != 'BoilerModelCommonReferences'
            or reference.get('source_only') is not True
            or reference.get('model_id') != 'device:' + identifier[6:]):
        raise ValueError('Gyro model TOON identity differs: ' + name)
    resources = reference.get('resources')
    if not isinstance(resources, list):
        raise ValueError('Gyro model TOON resources missing: ' + name)
    gyro = [row for row in resources if isinstance(row, dict) and row.get('id') == 'resource:bmi260-gyro']
    if gyro != [{'id': 'resource:bmi260-gyro', 'source': 'common/sensors/bosch/bmi260/gyro/source.toon'}]:
        raise ValueError('Gyro model TOON resource differs: ' + name)


def prepare(checkout, root=ROOT):
    plan = read(root/'source-plan.json')
    source = plan['source']
    commit = source['commit']
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('An exact public source commit is required')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(checkout), *args], stderr=subprocess.PIPE)
    origin = git('remote', 'get-url', 'origin').decode().strip()
    repository = source['repository']
    if origin not in ('https://github.com/'+repository, 'https://github.com/'+repository+'.git',
                      'git@github.com:'+repository+'.git'):
        raise ValueError('Source checkout origin differs from the public source plan')
    # Read Git objects rather than the potentially modified working files.
    if git('rev-parse', commit+'^{commit}').decode().strip() != commit:
        raise ValueError('Public source commit is unavailable')
    historical = read(root/'source-lock.json')
    mapping = relocations(root, historical)
    inventory, pending = [], []
    seen = set()
    for entry in plan['files']:
        target, original = entry['path'], entry['upstream_path']
        destination = local(root, target)
        if target.casefold() in seen:
            raise ValueError('Duplicate source destination: ' + target)
        seen.add(target.casefold())
        if (not original.startswith(('windows-installer/components/', 'windows-installer/licenses/'))
                and original != 'steamos-maintenance/oxp3_maintenance/gyro_binding.py'):
            raise ValueError('Source is outside the reviewed public component scope')
        local(Path('.'), original)
        metadata = git('ls-tree', commit, '--', original).decode().split()
        if len(metadata) < 3 or metadata[0] not in ('100644', '100755') or metadata[1] != 'blob':
            raise ValueError('Expected a committed regular source file: ' + original)
        data = git('show', commit+':'+original)
        data.decode('utf-8')  # No binary drivers, firmware or runtime payloads.
        inventory.append(dict(entry, size=len(data), sha256=hashlib.sha256(data).hexdigest()))
        pending.append((local(root, mapping.get(target, target)), data))
    lock = dict(schema_version=1, kind='AdaptationSourceInventory', source=source, files=inventory)
    if lock != historical:
        raise ValueError('Regenerated source inventory differs from immutable historical lock')
    for path, data in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name, model in records(root):
        model['source_revision'] = source
        model['source_inventory'] = 'source-lock.json'
        local(root, name).write_text(
            json.dumps(model, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
        guide(root, model, write=True)
    return check(root)


def validate_models(root, plan, inventory, current_rows, common):
    """Validate TOON-derived guide semantics after source inventories are checked."""
    models = records(root)
    for name, model in models:
        if model['kind'] != 'ModelInstallGuide' or model['installable'] is not False:
            raise ValueError('A source guide is not a DevicePackage: '+name)
        if model['source_revision'] != plan['source'] or model['source_inventory'] != 'source-lock.json':
            raise ValueError('Model source revision or inventory differs from the prepared collection: '+name)
        components = {item['id']: item for item in model['components']}
        if len(components) != len(model['components']):
            raise ValueError('Duplicate component ID: '+name)
        if name in ('brands/onexplayer/oxp3/manifest.json', 'brands/onexplayer/x2-mini/manifest.json') and 'gyro' not in components:
            raise ValueError('Missing gyro component: '+name)
        for item in components.values():
            fingerprint_default = (item.get('default') is True and item['id']=='fingerprint-unlock'
                and name in ('brands/onexplayer/oxp3/manifest.json','brands/onexplayer/x2-mini/manifest.json'))
            if item['recommendation'] not in LEVELS or not (item.get('default') is False or fingerprint_default):
                raise ValueError('Source-only choices cannot become installation defaults')
            for dependency in item['dependencies']:
                if dependency not in components or components[dependency]['recommendation'] == 'excluded':
                    raise ValueError('Missing or excluded dependency: '+dependency)
            expected_toon = str(PurePosixPath(name).parent / 'common-device-sources.toon')
            bmi_gyro = (item['id'] == 'gyro' and
                        (name in ('brands/onexplayer/oxp3/manifest.json', 'brands/onexplayer/x2-mini/manifest.json')
                         or expected_toon in item['sources']
                         or 'common/sensors/bosch/bmi260/gyro/source.toon' in item['sources']))
            if bmi_gyro:
                if item['sources'] != [expected_toon]:
                    raise ValueError('Gyro must reference its own model TOON: ' + name)
                local(root, expected_toon)
                validate_gyro_reference(common, expected_toon, model)
            for path in item['sources']:
                if not bmi_gyro and path not in inventory and path not in current_rows and path != expected_toon:
                    raise ValueError('Uninventoried component source: '+path)
            if item['recommendation'] == 'pending' and item['sources']:
                raise ValueError('Pending implementation must not claim an existing script')
        guide(root, model)
    return models


def check(root=ROOT):
    spec = importlib.util.spec_from_file_location('model_identity', Path(__file__).with_name('model-identity.py'))
    identity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(identity)
    identity.generate(root, check=True)
    plan, lock = read(root/'source-plan.json'), read(root/'source-lock.json')
    if lock['source'] != plan['source']:
        raise ValueError('Source inventory is from another upstream revision')
    planned = {item['path']: item for item in plan['files']}
    inventory = {item['path']: item for item in lock['files']}
    if (len(planned) != len(plan['files']) or len(inventory) != len(lock['files'])
            or set(planned) != set(inventory)):
        raise ValueError('Source plan and generated inventory differ')
    mapping = relocations(root, lock)
    common = validate_common(root)
    for name, item in inventory.items():
        if item['upstream_path'] != planned[name]['upstream_path']:
            raise ValueError('Source provenance mismatch: ' + name)
        data = local(root, mapping.get(name, name)).read_bytes()
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Adaptation source changed; prepare from reviewed source again: ' + name)
        if name.endswith('.py'):
            ast.parse(data, filename=name)
    # Extra files in the source trees must also receive an explicit provenance entry.
    actual = {path.relative_to(root).as_posix() for folder in (root/'common/scripts', root/'brands')
              for path in folder.rglob('*') if path.is_file() and 'scripts' in path.relative_to(root).parts
              and '__pycache__' not in path.parts and path.suffix != '.pyc'}
    # The original upstream lock remains immutable. Additional reviewed source
    # members have their own explicit canonical source inventory.
    current = read(root/'model-source-files.json')
    if current.get('kind') != 'CanonicalAdaptationSourceFiles':
        raise ValueError('Missing canonical source inventory')
    current_rows = {row['path']: row for row in current['files']}
    if len(current_rows) != len(current['files']): raise ValueError('Duplicate canonical source member')
    imported = read(root/'records/installer-adaptation-source-import-20261104.json')['files']
    for row in imported:
        name = row['path']
        if name not in current_rows or any(current_rows[name][k] != row[k] for k in ('size','sha256')):
            raise ValueError('Imported source missing from canonical inventory: '+name)
        data = local(root, name).read_bytes()
        if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('Canonical source member differs: '+name)
    expected_actual = (set(inventory) - set(mapping)) | {name for name in current_rows
        if name.startswith('brands/') and '/scripts/' in name}
    if actual != expected_actual:
        raise ValueError('Uninventoried or missing adaptation script files: '+str(sorted(actual ^ expected_actual)))
    models = validate_models(root, plan, inventory, current_rows, common)
    for item in read(root/'licenses/license-inventory.json')['files']:
        data = local(root, item['path']).read_bytes()
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Supplementary license inventory mismatch: '+item['path'])
    return dict(models=len(models), source_files=len(inventory), status='source-collection-verified', installable=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check'))
    parser.add_argument('--source-checkout', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        if not args.source_checkout:
            parser.error('prepare requires --source-checkout')
        result = prepare(args.source_checkout)
    else:
        result = check()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
