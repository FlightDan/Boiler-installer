"""Inspect only the immutable source image before installation confirmation."""
import json
from pathlib import Path
import subprocess
from source_profile import inspect_source


def run(*arguments):
    return subprocess.check_output(arguments, text=True).strip()


def inspect_image():
    if Path('/sys/class/dmi/id/sys_vendor').read_text().strip() != 'QEMU': raise RuntimeError('Managed VM required')
    if Path('/sys/class/block/vdc/ro').read_text().strip() != '1': raise RuntimeError('Source image must be read-only')
    table = json.loads(run('sfdisk', '--json', '/dev/vdc'))['partitiontable']
    names = [part.get('name') for part in table['partitions']]
    recovery = ['esp', 'efi-A', 'rootfs-A', 'var-A', 'home']
    prepared = ['esp', 'efi-A', 'efi-B', 'rootfs-A', 'rootfs-B', 'var-A', 'var-B', 'home']
    if table.get('label') != 'gpt' or table.get('sectorsize') != 512 or names not in (recovery, prepared):
        raise ValueError('Source is not a supported recovery or eight-partition SteamOS image')
    nodes = {part['name']: part['node'] for part in table['partitions']}
    root = Path('/mnt/boiler-source-info'); root.mkdir(exist_ok=True)
    profiles = {}
    wireless = {vendor: False for vendor in ('intel', 'realtek', 'mediatek')}
    for slot in ('A', 'B') if names == prepared else ('A',):
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
    return dict(profiles['A'], layout='steamos-ab' if names == prepared else 'steamos-recovery',
                slots=profiles, wireless_modules=wireless,
                wireless_scope='Existing kernel modules only; physical wireless hardware is not verified')


if __name__ == '__main__':
    print('BOILER_SOURCE_JSON=' + json.dumps(inspect_image(), sort_keys=True), flush=True)
