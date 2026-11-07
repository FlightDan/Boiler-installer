"""Build read-disks fixtures from sanitized install-session snapshots.

Usage:
    python build-fixture-disk-snapshots.py FIXTURE_ROOT DISKS_DIR
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROLES = [
    ('esp', 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'),
    ('efi-A', 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'),
    ('efi-B', 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'),
    ('rootfs-A', '4f68bce3-e8cd-4db1-96e7-fbcaf984b709'),
    ('rootfs-B', '4f68bce3-e8cd-4db1-96e7-fbcaf984b709'),
    ('var-A', '4d21b016-b534-45c2-a9fb-5c16e091fd2d'),
    ('var-B', '4d21b016-b534-45c2-a9fb-5c16e091fd2d'),
    ('home', '933ac7e1-2eb4-4f13-b844-0e14e2aef915'),
]


def build_after_install(internal: dict, layout: dict) -> dict:
    disk = json.loads(json.dumps(internal))
    roles = sorted(layout['roles'], key=lambda item: item['offset'])
    for index, role in enumerate(roles):
        disk['partitions'].append({
            'number': role['number'], 'guid': role['id'],
            'offset': role['offset'], 'size': role['size'],
            'type': 'System' if index == 0 else 'Unknown',
            'gptType': role['type'], 'gptName': role['name'], 'gptAttributes': '0',
            'nativeGptVerified': True, 'letter': '', 'label': '',
            'isBoot': False, 'isSystem': False, 'isHidden': index >= 3,
            'nativeUnrecognizedSafe': index >= 3, 'deletionAllowed': index >= 3,
            'deletionBlockReason': ('EFI, MSR, recovery and boot partitions are protected.'
                                    if index == 0 else
                                    'Volume has no verifiable access path.' if index < 3 else ''),
            'filesystem': 'FAT32' if index < 3 else '', 'filesystemSource': '',
            'sharedStorageSafe': False, 'filesystemProbeError': '', 'free': None,
        })
    disk['partitions'].sort(key=lambda item: item['offset'])
    return disk


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    root, output = Path(argv[1]), Path(argv[2])
    output.mkdir(parents=True, exist_ok=True)
    external = json.loads((root / '1105-external/disk-snapshots.json').read_text(encoding='utf-8'))
    internal = json.loads((root / '1106-internal/disk-snapshots.json').read_text(encoding='utf-8'))
    layout = json.loads((root / '1106-internal/partition-layout.json').read_text(encoding='utf-8'))
    if not isinstance(external, list) or not isinstance(internal, list):
        raise SystemExit('expected disk snapshot arrays')
    (output / '1105-session-disks.json').write_text(
        json.dumps(external, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (output / '1106-gm7-internal.json').write_text(
        json.dumps(internal[0], ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (output / '1106-gm7-after-install.json').write_text(
        json.dumps(build_after_install(internal[0], layout), ensure_ascii=False, indent=1) + '\n',
        encoding='utf-8')
    for path in sorted(output.iterdir()):
        print(path.name, path.stat().st_size)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
