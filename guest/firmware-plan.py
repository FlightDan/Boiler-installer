"""Plan selected firmware additions against read-only prepared A/B roots."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path[:0] = ['/mnt/payload/components', '/mnt/payload']
from integration import validate_options, merged_firmware_plan


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def inspect():
    if Path('/sys/class/dmi/id/sys_vendor').read_text().strip() != 'QEMU':
        raise RuntimeError('Managed VM required')
    if Path('/sys/class/block/vdc/ro').read_text().strip() != '1':
        raise RuntimeError('Firmware planning requires a read-only image')
    options = json.loads(Path('/mnt/payload/selection.json').read_text())
    validate_options(options)
    catalog_bytes = Path('/mnt/payload/integration/manifest.json').read_bytes()
    catalog = json.loads(catalog_bytes)['components']
    table = json.loads(run('sfdisk', '--json', '/dev/vdc'))['partitiontable']
    nodes = {part.get('name'): part['node'] for part in table['partitions']}
    if not {'rootfs-A', 'rootfs-B'}.issubset(nodes):
        raise ValueError('Firmware planning requires the final prepared A/B image')
    if options.get('graphics_profile') and not {'var-A', 'var-B'}.issubset(nodes):
        raise ValueError('Graphics planning requires both slot maintenance-state partitions')
    root = Path('/mnt/boiler-firmware-plan')
    root.mkdir(exist_ok=True)
    slots = {}
    for slot in ('A', 'B'):
        print('BOILER_FIRMWARE_PLANNING=' + slot + ':mount-readonly', flush=True)
        run('mount', '-t', 'btrfs', '-o', 'ro,rescue=nologreplay', nodes['rootfs-' + slot], str(root))
        var_mounted = False
        try:
            if run('btrfs', 'property', 'get', '-ts', str(root), 'ro') != 'ro=true':
                raise ValueError('Prepared source root protection is disabled')
            if options.get('graphics_profile'):
                # Maintenance journals live in the real offloaded slot var.
                # noload keeps interrupted transactions observable without any
                # journal replay or automatic recovery changing the plan input.
                var_path = root / 'var'
                if var_path.is_symlink() or not var_path.is_dir():
                    raise ValueError('Graphics planning requires an ordinary slot var mount point')
                run('mount', '-t', 'ext4', '-o', 'ro,noload', nodes['var-' + slot], str(var_path))
                var_mounted = True
            print('BOILER_FIRMWARE_PLANNING=' + slot + ':resolve-selected-files', flush=True)
            records, plans = merged_firmware_plan(root, catalog, options)
            slots[slot] = dict(records=records, plans=plans)
            print('BOILER_FIRMWARE_PLANNING=' + slot + ':complete:' + str(len(records)), flush=True)
        finally:
            if var_mounted:
                run('umount', str(root / 'var'))
            run('umount', str(root))
    return dict(schema=1, catalog_sha256=hashlib.sha256(catalog_bytes).hexdigest(), selection=options, slots=slots)


if __name__ == '__main__':
    print('BOILER_FIRMWARE_PLAN_JSON=' + json.dumps(inspect(), sort_keys=True), flush=True)
