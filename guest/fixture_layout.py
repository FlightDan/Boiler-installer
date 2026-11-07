"""Create and validate the tool-owned QEMU disk fixture.

The layout is data, not shell duplication: guest/fixture-layout.json is the
single source for the production self-test disk, the seed disk and the legacy
two-partition fixture. Only tool-created QEMU disks are accepted.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

from layout import plan as space_plan, MIB, GIB

HERE = Path(__file__).resolve().parent
LAYOUT = json.loads((HERE / 'fixture-layout.json').read_text(encoding='utf-8'))
ROLES = LAYOUT['roles']
MOUNT = Path('/mnt/fixture-preserved')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def table(device):
    value = json.loads(subprocess.check_output(['sfdisk', '--json', device], text=True))['partitiontable']
    value.setdefault('partitions', [])
    return value


def existing_partitions(device):
    try:
        return table(device)['partitions']
    except subprocess.CalledProcessError:
        return []


def profile(name):
    if name not in LAYOUT['profiles']:
        raise RuntimeError('Unknown fixture profile: ' + name)
    return LAYOUT['profiles'][name]


def expected_names(name):
    item = profile(name)
    names = [part['name'] for part in item['preserved']]
    if item.get('range'):
        names.extend(ROLES)
    return names


def role_numbers(name):
    item = profile(name)
    return list(range(len(item['preserved']) + 1, len(item['preserved']) + 1 + len(ROLES)))


def range_bytes(name):
    item = profile(name)
    if not item.get('range'):
        raise RuntimeError('Fixture profile has no role range')
    return item['range']['start_mib'] * MIB, item['range']['size_mib'] * MIB


def profile_for(actual):
    names = [part.get('name') for part in actual['partitions']]
    for name in LAYOUT['profiles']:
        if names == expected_names(name):
            return name
    raise RuntimeError('Unrecognized tool fixture layout: ' + repr(names))


def validate(device, name=None):
    actual = table(device)
    detected = profile_for(actual)
    if name is not None and detected != name:
        raise RuntimeError('Fixture profile differs: expected %s, got %s' % (name, detected))
    item = profile(detected)
    names = [part.get('name') for part in actual['partitions']]
    if names != expected_names(detected):
        raise RuntimeError('Fixture partition names differ')
    preserved = item['preserved']
    for index, expected in enumerate(preserved):
        part = actual['partitions'][index]
        if (part.get('name'), part['start'], part['size']) != (
                expected['name'], expected['start_mib'] * 2048, expected['size_mib'] * 2048):
            raise RuntimeError('Fixture preserved partition differs: ' + expected['name'])
    if item.get('range'):
        before = dict(actual, partitions=actual['partitions'][:len(preserved)])
        planned = space_plan(before, item['range']['start_mib'] * MIB, item['range']['size_mib'] * MIB)
        selected = actual['partitions'][len(preserved):]
        if len(selected) != len(planned):
            raise RuntimeError('Fixture role count differs')
        for part, expected in zip(selected, planned):
            if (part.get('name'), part['start'], part['size'], str(part.get('type')).lower()) != (
                    expected['name'], expected['start'], expected['size'], expected['type'].lower()):
                raise RuntimeError('Fixture role differs: ' + expected['name'])
    return detected


def create_preserved(device, name):
    if existing_partitions(device):
        raise RuntimeError('Fixture disk already has partitions')
    item = profile(name)
    lines = ['label: gpt']
    for part in item['preserved']:
        lines.append('start=%d,size=%d,type=%s,name="%s"' % (
            part['start_mib'] * 2048, part['size_mib'] * 2048, part['type'], part['name']))
    run('sfdisk', device, input='\n'.join(lines) + '\n')
    run('mdev', '-s')
    for part in item['preserved']:
        node = '%s%d' % (device, part['number'])
        for _ in range(20):
            if Path(node).exists():
                break
            time.sleep(0.1)
            run('mdev', '-s')
        if part.get('fs') == 'vfat':
            run('mkfs.vfat', node, stdout=subprocess.DEVNULL)
        elif part.get('fs') == 'ntfs':
            run('mkntfs', '-F', '-Q', node, stdout=subprocess.DEVNULL)
        if part.get('sentinel'):
            MOUNT.mkdir(parents=True, exist_ok=True)
            kind = 'vfat' if part['fs'] == 'vfat' else 'ntfs-3g'
            run('mount', '-t', kind, node, str(MOUNT))
            try:
                (MOUNT / part['sentinel']).write_text(part['content'])
                run('sync')
            finally:
                run('umount', str(MOUNT))


def append_roles(device, name):
    item = profile(name)
    if not item.get('range'):
        raise RuntimeError('Fixture profile has no role range')
    before = table(device)
    planned = space_plan(before, item['range']['start_mib'] * MIB, item['range']['size_mib'] * MIB)
    lines = '\n'.join('start=%d,size=%d,type=%s,name="%s"' % (
        part['start'], part['size'], part['type'], part['name']) for part in planned) + '\n'
    run('sfdisk', '--append', device, input=lines)
    run('mdev', '-s')
    validate(device, name)


def create(device, name):
    create_preserved(device, name)
    if profile(name).get('range'):
        append_roles(device, name)
    validate(device, name)
    print('OXP_FIXTURE_PROFILE=' + name, flush=True)


def role_node(device, role):
    actual = table(device)
    for part in actual['partitions']:
        if part.get('name') == role:
            return part['node']
    raise RuntimeError('Fixture role is missing: ' + role)


def preserved_node(device, name):
    actual = table(device)
    item = profile(profile_for(actual))
    for part in item['preserved']:
        if part['name'] == name:
            return '%s%d' % (device, part['number'])
    raise RuntimeError('Fixture preserved partition is missing: ' + name)


def preserved_nodes(device):
    actual = table(device)
    item = profile(profile_for(actual))
    return [part['node'] for part in actual['partitions'][:len(item['preserved'])]]


def preserved_files(name):
    return [part for part in profile(name)['preserved']
            if part.get('sentinel')]


def main(argv):
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[1]
    if command == 'create':
        create(argv[2], argv[3])
    elif command == 'validate':
        print(validate(argv[2], argv[3] if len(argv) > 3 else None))
    elif command == 'profile':
        print(profile_for(table(argv[2])))
    elif command == 'names':
        print(json.dumps(expected_names(argv[2])))
    elif command == 'node':
        print(role_node(argv[2], argv[3]))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
