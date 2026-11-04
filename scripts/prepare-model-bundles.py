"""Describe explicitly supplied, fully verified local test device packages."""
import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.brand_bundle import import_brand, stage_brand
from protocol.local import MAX_ARCHIVE, _copy_file, _safe_path
from protocol.manifest import _publish_directory, validate_package, verify_file
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


def prepare_brand(root, source, expected_sha256):
    root = Path(root).resolve()
    source = _safe_path(source)
    destination = root/'cache/model-bundles'
    if os.path.lexists(destination):
        raise ValueError('Use a fresh bundled model directory')
    archive = dict(path='cache/model-bundles/onexplayer.zip', size=source.stat().st_size, sha256=expected_sha256)
    verify_file(source, archive)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bundle-', dir=destination.parent) as scratch:
        scratch = Path(scratch)
        ready = scratch/'ready'
        ready.mkdir()
        packaged = ready/'onexplayer.zip'
        _copy_file(source, packaged, MAX_ARCHIVE)
        verify_file(packaged, archive)
        staged = scratch/'brand'
        result = stage_brand(packaged, staged, developer=True)
        if not result.get('recognized') or result['unresolved']:
            raise ValueError('Only a complete, reviewed brand archive can be bundled')
        ids = {model['id'] for model in result['models']}
        if ids != {'device:oxp3', 'device:x2-mini'} or not ids <= model_identities(root):
            raise ValueError('Expected the reviewed OXP3 and X2 Mini model set')
        entries = []
        for row in result['models']:
            identity = row['id']
            directory = scratch / identity.removeprefix('device:')
            item = import_brand(staged, directory, identity, row['manifest_sha256'], developer=True)
            package = item['package']
            if package.get('purpose') == 'maintenance':
                raise ValueError('Maintenance-only package cannot be bundled for installation')
            if package.get('selection_contract', {}).get('kind') == 'oxp3-legacy-options-v1':
                frozen = json.loads((directory/'selection.json').read_bytes())
                requested = set(frozen['repairs'] + frozen['plugins'])
                defaults = {cap['id'] for cap in package['legacy_capabilities'] if cap.get('default')}
                available = {cap['id'] for cap in package['legacy_capabilities'] if cap.get('available')}
                if requested != defaults or not defaults <= available:
                    raise ValueError('Bundled model differs from the reviewed frozen selection')
            entries.append(dict(id=identity, version=package['version'], manifest_sha256=row['manifest_sha256'],
                                manifest_size=(directory/'manifest.json').stat().st_size,
                                files=len(package['files']), official_release=False))
        value = dict(schema_version=1, kind='LocalTestModelBrandBundle', archive=archive, packages=entries)
        (ready/'manifest.json').write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8', newline='\n')
        _publish_directory(ready, destination, os.rename, guard=_safe_path)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--package', action='append', type=Path)
    sources.add_argument('--brand-archive', type=Path)
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    if args.brand_archive:
        import re
        if not args.expected_sha256 or not re.fullmatch('[0-9a-f]{64}', args.expected_sha256):
            parser.error('--brand-archive requires its reviewed --expected-sha256')
        value = prepare_brand(args.root, args.brand_archive, args.expected_sha256)
    else:
        value = prepare(args.root, args.package)
    print(json.dumps(value, indent=2))
