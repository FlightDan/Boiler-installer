"""Describe explicitly supplied, fully verified local test device packages."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.manifest import validate_package
from protocol.model_catalog import model_identities


def prepare(root, packages):
    root = Path(root).resolve()
    destination = root/'cache/model-bundles/manifest.json'
    if destination.exists():
        raise ValueError('Use a fresh local test bundle descriptor')
    entries = []
    identities = set()
    allowed=model_identities(root)
    for directory in packages:
        directory = Path(directory).resolve()
        manifest_data = (directory/'manifest.json').read_bytes()
        sha256 = hashlib.sha256(manifest_data).hexdigest()
        package = validate_package(directory, expected_manifest_sha256=sha256)
        identity = package['id']
        if identity not in allowed or identity in identities:
            raise ValueError('Unexpected or duplicate local test model')
        identities.add(identity)
        relative = 'cache/model-bundles/packages/'+identity.removeprefix('device:')
        if directory != root/relative or package.get('purpose') == 'maintenance':
            raise ValueError('Package must be in its exact local test model directory')
        if identity == 'device:oxp3' and package.get('selection_contract', {}).get('kind') != 'oxp3-legacy-options-v1':
            raise ValueError('OXP3 test package must implement the dynamic selection contract')
        entries.append(dict(id=identity, directory=relative, version=package['version'],
                            manifest_sha256=sha256, manifest_size=len(manifest_data),
                            files=len(package['files']), official_release=False))
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = dict(schema_version=1, kind='LocalTestModelBundles', packages=entries)
    destination.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8', newline='\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--package', action='append', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.package), indent=2))
