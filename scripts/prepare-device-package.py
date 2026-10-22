"""Validate and copy a device package without executing any model code.

Production packages require a manifest digest from an authenticated DeviceIndex.
An unsigned local snapshot requires the explicit --allow-unsigned-development
switch and is never converted into an approved production package by this tool.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol.manifest import validate_package


def prepare(source, output, *, manifest_sha256=None, allow_unsigned=False):
    source, output = Path(source), Path(output)
    manifest = validate_package(source, expected_manifest_sha256=manifest_sha256, allow_unsigned=allow_unsigned)
    if manifest.get('trust', {}).get('status') == 'unsigned-development' and not allow_unsigned:
        raise ValueError('Unsigned development package requires an explicit development grant')
    if output.exists() or output.is_symlink():
        raise ValueError('Package destination already exists')
    if output.resolve().is_relative_to(source.resolve()):
        raise ValueError('Destination cannot be inside source package')
    shutil.copytree(source, output, symlinks=False)
    validate_package(output, expected_manifest_sha256=manifest_sha256, allow_unsigned=allow_unsigned)
    digest = hashlib.sha256((output/'manifest.json').read_bytes()).hexdigest()
    return {'schema_version': 1, 'device_package': manifest['id'], 'version': manifest['version'],
            'manifest_sha256': digest, 'trust': 'unsigned-development' if allow_unsigned and manifest_sha256 is None else 'anchored-manifest',
            'official_release': False if allow_unsigned else manifest.get('trust', {}).get('official_release', False)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--allow-unsigned-development', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, manifest_sha256=args.manifest_sha256, allow_unsigned=args.allow_unsigned_development), indent=2))
