"""Inspect only the immutable source image before installation confirmation."""
import json
import os
import stat
from pathlib import Path
import subprocess
from layout import PARTS
from source_profile import inspect_source


def run(*arguments):
    return subprocess.check_output(arguments, text=True).strip()


def reject_bound_ssd_home(home):
    """Known destination-specific ledger is not a reusable source image."""
    home=Path(home)
    for path in (home/'.boiler',home/'.boiler/ssd-boot-v1'):
        try: info=path.lstat()
        except FileNotFoundError: return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError('Source has an unsafe destination-bound SSD record path; use an unbound original system source')
    raise ValueError('Source contains destination-bound SSD records; use an unbound original system source')


def source_partitions(table):
    """Identify recovery or the exact prepared SteamOS suffix on a GPT source."""
    if table.get('label') != 'gpt' or table.get('sectorsize') != 512:
        raise ValueError('Source is not a supported GPT SteamOS image')
    partitions = table['partitions']
    names = [part.get('name') for part in partitions]
    recovery = ['esp', 'efi-A', 'rootfs-A', 'var-A', 'home']
    if names == recovery:
        return 'steamos-recovery', {part['name']: part['node'] for part in partitions}
    roles = {name: kind for name, _, kind in PARTS}
    roles['home'] = '933AC7E1-2EB4-4F13-B844-0E14E2AEF915'
    count = len(roles)
    if (names[-count:] != list(roles) or set(names[:-count]).intersection(roles)
            or any(not isinstance(part.get('type'), str)
                   or part['type'].upper() != roles[part['name']]
                   for part in partitions[-count:])):
        raise ValueError('Source is not a supported recovery or prepared SteamOS image')
    return 'steamos-ab', {part['name']: part['node'] for part in partitions[-count:]}


def inspect_image():
    if Path('/sys/class/dmi/id/sys_vendor').read_text().strip() != 'QEMU': raise RuntimeError('Managed VM required')
    if Path('/sys/class/block/vdc/ro').read_text().strip() != '1': raise RuntimeError('Source image must be read-only')
    table = json.loads(run('sfdisk', '--json', '/dev/vdc'))['partitiontable']
    layout, nodes = source_partitions(table)
    root = Path('/mnt/boiler-source-info'); root.mkdir(exist_ok=True)
    run('mount', '-t', 'ext4', '-o', 'ro,noload', nodes['home'], str(root))
    try: reject_bound_ssd_home(root)
    finally: run('umount', str(root))
    profiles = {}
    wireless = {vendor: False for vendor in ('intel', 'realtek', 'mediatek')}
    for slot in ('A', 'B') if layout == 'steamos-ab' else ('A',):
        run('mount', '-t', 'btrfs', '-o', 'ro,rescue=nologreplay', nodes['rootfs-' + slot], str(root))
        try:
            profiles[slot] = inspect_source(root)
            if run('btrfs', 'property', 'get', '-ts', str(root), 'ro') != 'ro=true':
                raise ValueError('Source system root protection is disabled')
            for file in (root / 'usr/lib/modules').rglob('*.ko*'):
                if not file.is_file() or file.is_symlink(): continue
                name = file.name.split('.ko', 1)[0]
                if name.startswith('iwl'): wireless['intel'] = True
                if name.startswith(('rtw', 'rtl')) and ('wireless' in file.parts or not name.startswith('rtl81')): wireless['realtek'] = True
                if name.startswith(('mt76', 'mt79')): wireless['mediatek'] = True
        finally: run('umount', str(root))
    return dict(profiles['A'], layout=layout,
                slots=profiles, wireless_modules=wireless,
                wireless_scope='Existing kernel modules only; physical wireless hardware is not verified')


if __name__ == '__main__':
    print('BOILER_SOURCE_JSON=' + json.dumps(inspect_image(), sort_keys=True), flush=True)
