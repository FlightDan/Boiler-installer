"""Generate the fixed OXP3 EFI bridge source allowlist from reviewed inputs.

This derives metadata only from the checked public adaptation snapshot and the
current explicit bridge files. It never reads a device package's self-declared
digest as an authority and never modifies historical source locks.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'adaptations/brands/onexplayer/oxp3/bridge'
BASE = 'windows-installer/components/'
PUBLIC = (
    'boot_repairs.py', 'decky_order.py', 'directory_transaction.py', 'file_transaction.py',
    'firstboot_stage.py', 'firstboot.py', 'input_profile.py', 'integration_runtime.py', 'integration.py',
    'maintenance_tool.py', 'ntfs_steam.py', 'partition_mount.py', 'plugin_installer.py',
    'release_notes.py', 'runtime_access.py', 'serial_console.py', 'shared_state.py', 'shared_storage.py',
    'slot_switch.py', 'split_lock.py', 'system_update_policy.py', 'update_hold_verify.py',
    'update_hold.py', 'waydroid_setup.py', 'windows_reboot.py', 'x2mini_hdr_device.py',
    'hdr/generate.py', 'hdr/installer.py', 'hdr/upstream.lua', 'hdr/UPSTREAM_LICENSE',
    'split-lock/UPSTREAM_LICENSE', 'options.json', 'release.json',
)
ADOPTION = 'payload/legacy/maintenance/app/oxp3_maintenance/adoption.py'
ADOPTION_SHA256 = 'f12a9a4b7185073dbfb03d8df2d1fe903ff66c6e093f05be5265adac810f90d7'
PUBLIC_ASSET_SHA256 = 'd8bee8bad14f4cd136c814ebcaa3299fa59202b6cdc6d26ec9c52af0e055f4d4'
BRIDGE_FILES = ('adapter.py', 'scripts/apply.sh', 'scripts/verify.sh', 'scripts/restore.sh')


def build(root=ROOT):
    root = Path(root)
    bridge = root / 'adaptations/brands/onexplayer/oxp3/bridge'
    source = json.loads((root / 'adaptations/source-lock.json').read_bytes())
    if source.get('source', {}).get('commit') != '72f864e0498c10366493c74608c5ffed62cc99ca':
        raise ValueError('Unexpected public source baseline')
    rows = {item['upstream_path']: item for item in source['files']}
    records = []
    for name in PUBLIC:
        item = rows[BASE + name]
        path = root / 'adaptations' / item['path']
        if path.is_symlink() or not path.is_file(): raise ValueError('Missing reviewed public source: ' + name)
        data = path.read_bytes(); sha = hashlib.sha256(data).hexdigest()
        if len(data) != item['size'] or sha != item['sha256']:
            raise ValueError('Reviewed public source differs: ' + name)
        records.append(dict(path='payload/legacy/components/' + name, size=len(data), sha256=sha))
    # Extracted byte-for-byte from cache/maintenance/app/oxp3_maintenance/adoption.py
    # in the authenticated, published installer asset named by the immutable lock.
    upstream = json.loads((bridge / 'upstream-lock.json').read_bytes())
    if upstream.get('asset', {}).get('sha256') != PUBLIC_ASSET_SHA256:
        raise ValueError('Unexpected public installer asset')
    adoption = bridge / 'public-adoption.py'
    data = adoption.read_bytes()
    if adoption.is_symlink() or hashlib.sha256(data).hexdigest() != ADOPTION_SHA256:
        raise ValueError('Reviewed public adoption decoder differs')
    records.append(dict(path=ADOPTION, size=len(data), sha256=ADOPTION_SHA256,
                        provenance=dict(repository=upstream['repository'],
                                        source_commit=upstream['source_commit'],
                                        release_tag=upstream['release_tag'],
                                        asset=upstream['asset']['name'],
                                        asset_sha256=PUBLIC_ASSET_SHA256,
                                        member='cache/maintenance/app/oxp3_maintenance/adoption.py',
                                        license='AGPL-3.0-only',
                                        license_path='adaptations/brands/onexplayer/oxp3/bridge/LICENSE')))
    for name in BRIDGE_FILES:
        path = bridge / name
        if path.is_symlink() or not path.is_file(): raise ValueError('Missing reviewed bridge source: ' + name)
        data = path.read_bytes()
        records.append(dict(path=name, size=len(data), sha256=hashlib.sha256(data).hexdigest()))
    return dict(schema=1, kind='FixedOxp3EfiProviderSourceLock',
                public_commit=source['source']['commit'], files=records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    raw = (json.dumps(build(), sort_keys=True, indent=2) + '\n').encode()
    if args.check:
        if args.output.read_bytes() != raw: raise ValueError('OXP3 provider lock differs from reviewed source bytes')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(raw)
    print(hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    main()
