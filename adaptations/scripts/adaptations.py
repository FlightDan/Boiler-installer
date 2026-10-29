"""Prepare and check the source-only brand/model adaptation collection.

This tool reads committed, explicitly listed public source bytes. It never runs
adaptation code, builds an install payload, changes DeviceIndex or publishes.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
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
        pending.append((destination, data))
    for path, data in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    lock = dict(schema_version=1, kind='AdaptationSourceInventory', source=source, files=inventory)
    (root/'source-lock.json').write_text(json.dumps(lock, ensure_ascii=False, indent=2)+'\n',
                                       encoding='utf-8', newline='\n')
    for name, model in records(root):
        model['source_revision'] = source
        model['source_inventory'] = 'source-lock.json'
        local(root, name).write_text(
            json.dumps(model, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
        guide(root, model, write=True)
    return check(root)


def check(root=ROOT):
    plan, lock = read(root/'source-plan.json'), read(root/'source-lock.json')
    if lock['source'] != plan['source']:
        raise ValueError('Source inventory is from another upstream revision')
    planned = {item['path']: item for item in plan['files']}
    inventory = {item['path']: item for item in lock['files']}
    if (len(planned) != len(plan['files']) or len(inventory) != len(lock['files'])
            or set(planned) != set(inventory)):
        raise ValueError('Source plan and generated inventory differ')
    for name, item in inventory.items():
        if item['upstream_path'] != planned[name]['upstream_path']:
            raise ValueError('Source provenance mismatch: ' + name)
        data = local(root, name).read_bytes()
        if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Adaptation source changed; prepare from reviewed source again: ' + name)
        if name.endswith('.py'):
            ast.parse(data, filename=name)
    # Extra files in the source trees must also receive an explicit provenance entry.
    actual = {path.relative_to(root).as_posix() for folder in (root/'common/scripts', root/'brands')
              for path in folder.rglob('*') if path.is_file() and 'scripts' in path.relative_to(root).parts
              and 'bridge' not in path.relative_to(root).parts
              and '__pycache__' not in path.parts and path.suffix != '.pyc'}
    if actual != set(inventory):
        raise ValueError('Uninventoried or missing adaptation script files: '+str(sorted(actual ^ set(inventory))))
    models = records(root)
    for name, model in models:
        if model['kind'] != 'ModelInstallGuide' or model['installable'] is not False:
            raise ValueError('A source guide is not a DevicePackage: '+name)
        if model['source_revision'] != plan['source'] or model['source_inventory'] != 'source-lock.json':
            raise ValueError('Model source revision or inventory differs from the prepared collection: '+name)
        components = {item['id']: item for item in model['components']}
        if len(components) != len(model['components']):
            raise ValueError('Duplicate component ID: '+name)
        for item in components.values():
            if item['recommendation'] not in LEVELS or item.get('default') is not False:
                raise ValueError('Source-only choices cannot become installation defaults')
            for dependency in item['dependencies']:
                if dependency not in components or components[dependency]['recommendation'] == 'excluded':
                    raise ValueError('Missing or excluded dependency: '+dependency)
            for path in item['sources']:
                if path not in inventory:
                    raise ValueError('Uninventoried component source: '+path)
            if item['recommendation'] == 'pending' and item['sources']:
                raise ValueError('Pending implementation must not claim an existing script')
        guide(root, model)
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
