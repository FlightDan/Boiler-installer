"""Derive X2 provider closure from the independently pinned complete inputs.

This emits only source-lock metadata; it does not build or authorize a package.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build(base, bridge, hdr):
    from protocol.manifest import validate_package
    builder = load('_x2_complete_source_lock', ROOT/'adaptations/scripts/build-x2-complete.py')
    source_path = ROOT/'adaptations/brands/onexplayer/x2-mini/bridge/fixed_efi_sources.py'
    if sha(source_path.read_bytes()) != '44b04774e27d5bded09b6803d4fd124980e739518e41c8cf59c44c606e26f856':
        raise ValueError('X2 bridge transform differs from the reviewed source')
    transform = load('_x2_fixed_efi_source_lock', source_path)
    base, bridge, hdr = map(Path, (base, bridge, hdr))
    manifest = validate_package(base, expected_manifest_sha256=builder.BASE_ANCHOR)
    if manifest['id'] != 'device:oxp3' or manifest['version'] != builder.BASE_VERSION:
        raise ValueError('Wrong authenticated complete base')
    base_files = {row['path']: row for row in manifest['files']}
    changes = {}
    for role, directory, pin in (
        ('bridge', bridge, 'a88f7d51fbe3b54d3aa1aab9ddfd8b5acc094c589dd9cfe1ee1415b2903c553d'),
        ('hdr', hdr, 'f1c159d826106eb038fe1845efc515fbd4ed2897089b0e1f8b68cd80c554d1d6'),
    ):
        entries = builder.overlay(directory, role, pin, base_files)
        if set(changes) & set(entries): raise ValueError('Overlays conflict')
        changes.update(entries)
    reference = json.loads((ROOT/'components/oxp3_efi_provider.lock.json').read_bytes())
    if reference.get('kind') != 'FixedOxp3EfiProviderSourceLock':
        raise ValueError('Unexpected reviewed closure definition')
    names = [row['path'] for row in reference['files']]
    names += ['maintenance_adapter.py', 'scripts/maintenance.sh']
    names += sorted(name for name in base_files
                    if name.startswith('payload/legacy/maintenance/app/oxp3_maintenance/')
                    and name.endswith('.py') and name not in names)
    if len(set(names)) != len(names): raise ValueError('Duplicate provider source')
    rows = []
    for name in names:
        if name in ('adapter.py','maintenance_adapter.py'):
            data = transform.transform(name, (ROOT/'adaptations/brands/onexplayer/oxp3/bridge'/name).read_bytes())
        elif name in changes:
            data = changes[name]['input_path'].read_bytes()
        else:
            original = base_files.get(name)
            if original is None: raise ValueError('Fixed provider source missing from base: '+name)
            data = builder.regular(base, name).read_bytes()
            if len(data) != original['size'] or sha(data) != original['sha256']:
                raise ValueError('Fixed provider source differs from authenticated base: '+name)
        rows.append(dict(path=name, size=len(data), sha256=sha(data)))
    return dict(schema=1, kind='FixedX2EfiProviderSourceLock', public_commit=reference['public_commit'],
                provenance=dict(base_manifest_sha256=builder.BASE_ANCHOR,
                    bridge_overlay_sha256='a88f7d51fbe3b54d3aa1aab9ddfd8b5acc094c589dd9cfe1ee1415b2903c553d',
                    hdr_overlay_sha256='f1c159d826106eb038fe1845efc515fbd4ed2897089b0e1f8b68cd80c554d1d6',
                    transform_sha256=sha(source_path.read_bytes()),
                    bridge_source='reviewed project-local bytes; public source commit pending'), files=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--bridge', type=Path, required=True)
    parser.add_argument('--hdr', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = (json.dumps(build(args.base, args.bridge, args.hdr), sort_keys=True, indent=2)+'\n').encode()
    if args.output.exists(): raise ValueError('Use a fresh output path')
    args.output.write_bytes(raw)
    print(sha(raw))
