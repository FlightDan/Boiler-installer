"""Read-only verification of Boiler generic component files in prepared A/B images."""
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, '/mnt/payload/components')
sys.path.insert(0, '/mnt/payload')
from offload_mounts import mount_etc, mount_opt
from maintenance import Engine
from payload_lock import checked

if Path('/sys/class/dmi/id/sys_vendor').read_text().strip() != 'QEMU':
    raise RuntimeError('Managed VM required')
options = json.loads(Path('/mnt/payload/selection.json').read_text())
if options.get('install_mode') == 'imported':
    print('OXP_INTEGRATION_VERIFIED=preserved-image', flush=True)
    raise SystemExit(0)
empty = not any(options.get(key) for key in ('repairs','plugins','wireless','device_components'))
table = json.loads(subprocess.check_output(['sfdisk', '--json', '/dev/vdc']))['partitiontable']
parts = {p.get('name'): p['node'] for p in table['partitions']}
root = Path('/mnt/integration-verify-root'); root.mkdir(exist_ok=True)
for slot in ('A', 'B'):
    mounts = []
    def mount(node, path, *flags):
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(['mount', *flags, node, str(path)], check=True); mounts.append(path)
    try:
        mount(parts['rootfs-' + slot], root, '-t', 'btrfs', '-o', 'ro,rescue=nologreplay')
        if subprocess.check_output(['btrfs', 'property', 'get', '-ts', str(root), 'ro'], text=True).strip() != 'ro=true':
            raise RuntimeError('Prepared root protection missing')
        mount(parts['var-' + slot], root / 'var', '-t', 'ext4', '-o', 'ro,noload')
        mount(parts['home'], root / 'home', '-t', 'ext4', '-o', 'ro,noload')
        if not empty:
            mount_etc(root, mounts, readonly=True); mount_opt(root, mounts, readonly=True)
        if not options.get('device_package'):
            for relative in ('etc/oxp3-installer/update-hold', 'etc/systemd/system/atomupd.service.d/90-oxp3-update-hold.conf',
                             'etc/modprobe.d/xe-oxp3.conf', 'usr/local/lib/oxp3-integration'):
                if (root / relative).exists() or (root / relative).is_symlink():
                    raise RuntimeError('Unexpected model policy in generic image: ' + relative)
            print('OXP_GENERIC_POLICY_VERIFIED='+slot+':no-model-update-hold', flush=True)
        if empty:
            for relative in ('var/lib/boiler', 'home/.boiler', 'etc/systemd/system/plugin_loader.service'):
                if (root / relative).exists() or (root / relative).is_symlink(): raise RuntimeError('Vanilla received an unselected component')
            print('OXP_INTEGRATION_VERIFIED='+slot+':no-components-selected', flush=True)
            continue
        result = json.loads((root / 'home/.boiler/integration' / slot / 'result.json').read_text())
        if result['options'] != {key: value for key,value in options.items() if key != '_target_disk'}:
            raise RuntimeError('Verification selection differs from installed request')
        for identity, component in result['components'].items():
            if component['status'] != 'files_installed': raise RuntimeError('Component was not installed: ' + identity)
            if identity == 'core:decky':
                manifest = json.loads(Path('/mnt/payload/integration/manifest.json').read_text())['components'][identity]
                binary = checked('/mnt/payload/integration', manifest['files'][0])
                if (root / 'var/lib/boiler/decky/runtime/services/PluginLoader').read_bytes() != binary:
                    raise RuntimeError('Decky executable differs from lock')
                unit = (root / 'etc/systemd/system/plugin_loader.service').read_text()
                if 'PRIVILEGED_PATH=/var/lib/boiler/decky/runtime' not in unit: raise RuntimeError('Decky ownership runtime differs')
                from maintenance.plugins import PluginMaintenance
                if not PluginMaintenance(root).verify('decky')['ok']:
                    raise RuntimeError('Decky transaction files or ownership differ')
            elif not identity.startswith('oxp3:'):
                # Engine.verify only reads state and targets. It does not recover
                # or acquire a write lock during this read-only verification.
                proof = Engine(root, root / 'var/lib/boiler/maintenance', read_only=True).verify_readonly(identity)
                if not proof['ok']: raise RuntimeError('Component files differ: ' + identity)
        print('OXP_INTEGRATION_VERIFIED=' + slot, flush=True)
    finally:
        for path in reversed(mounts): subprocess.run(['umount', str(path)], check=True)
