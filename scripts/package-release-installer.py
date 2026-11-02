"""Build or verify a slim installer candidate; this tool never signs or publishes."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_URL = 'https://github.com/FlightDan/Boiler-installer.git'
sys.path.insert(0, str(ROOT))
from protocol.manifest import relative_path

_PACKAGER_SPEC = importlib.util.spec_from_file_location('boiler_installer_packager', ROOT / 'scripts/package-installer.py')
if _PACKAGER_SPEC is None or _PACKAGER_SPEC.loader is None:
    raise RuntimeError('Cannot load the reviewed installer package builder')
packager = importlib.util.module_from_spec(_PACKAGER_SPEC)
_PACKAGER_SPEC.loader.exec_module(packager)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def redirected(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _version(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9.+-]+', value):
        raise ValueError('Installer version cannot be represented in a slim artifact name')
    return value


def artifact_name(version: str) -> str:
    return f'boiler-installer-{_version(version)}-win-x64-slim.zip'


def assert_public_checkout(root: Path, source_commit: str) -> None:
    root = Path(root).resolve()
    if root != ROOT.resolve():
        raise ValueError('Run this script from the public checkout being packaged')
    if not re.fullmatch(r'[0-9a-f]{40}', source_commit):
        raise ValueError('An exact lowercase public source commit is required')

    def git(*args: str) -> str:
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()

    if Path(git('rev-parse', '--show-toplevel')).resolve() != root:
        raise ValueError('Release packaging requires an independent public checkout rooted at --root')
    if git('remote', 'get-url', 'origin') != REPOSITORY_URL:
        raise ValueError('Release packaging requires the existing Boiler Installer public origin')
    if git('rev-parse', 'HEAD') != source_commit:
        raise ValueError('The requested source commit must be the public checkout HEAD')
    if git('status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('Release packaging requires a clean public source checkout')


def _package_records(info: dict) -> dict[str, dict]:
    rows = info.get('files')
    if not isinstance(rows, list):
        raise ValueError('Installer build-info has no explicit file inventory')
    records: dict[str, dict] = {}
    folded: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid installer inventory row')
        name = relative_path(row.get('path'))
        size = row.get('size')
        digest = row.get('sha256')
        if (name == 'build-info.json' or type(size) is not int or size < 0
                or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or name.casefold() in folded):
            raise ValueError('Invalid or duplicate installer inventory entry: ' + name)
        folded.add(name.casefold())
        records[name] = dict(size=size, sha256=digest)
    return records


def _is_model_payload(name: str) -> bool:
    name = name.casefold()
    return (name.startswith('cache/model-bundles/')
            or name.startswith('cache/integration/maintenance-runtime/cache/model-bundles/'))


def verify_slim_archive(archive_path: Path, *, source_commit: str | None = None) -> dict:
    """Verify a real ZIP inventory and its slim-only distribution policy."""
    archive_path = Path(archive_path).absolute()
    if redirected(archive_path) or not archive_path.is_file():
        raise ValueError('Installer ZIP must be an ordinary, non-redirected file')
    for ancestor in archive_path.parents:
        if redirected(ancestor):
            raise ValueError('Installer ZIP has a redirected ancestor')

    with zipfile.ZipFile(archive_path) as bundle:
        items: dict[str, zipfile.ZipInfo] = {}
        folded: set[str] = set()
        for item in bundle.infolist():
            name = relative_path(item.filename)
            mode = item.external_attr >> 16
            if (item.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or item.flag_bits & 0x1 or name.casefold() in folded):
                raise ValueError('Installer ZIP has a directory, link, encrypted, or duplicate member: ' + name)
            folded.add(name.casefold())
            items[name] = item
        if 'build-info.json' not in items:
            raise ValueError('Installer ZIP is missing build-info.json')
        info = json.loads(bundle.read(items['build-info.json']))
        if (info.get('schema') != 1 or info.get('product') != 'Boiler Installer'
                or info.get('package_channel') != 'preview' or info.get('source_published') is not True
                or info.get('configuration') != 'Release'):
            raise ValueError('Installer ZIP is not a normal Release preview package')
        commit = info.get('source_commit')
        if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError('Installer ZIP has no exact public source commit')
        if source_commit is not None and commit != source_commit:
            raise ValueError('Installer ZIP source commit differs from the requested commit')
        version = _version(info.get('version'))
        display_version = _version(info.get('display_version'))
        if archive_path.name != artifact_name(version):
            raise ValueError('Installer ZIP name or display version is not the matching slim artifact identity')

        records = _package_records(info)
        if set(items) != set(records) | {'build-info.json'}:
            raise ValueError('Installer ZIP member set differs from build-info inventory')
        if any(_is_model_payload(name) for name in items):
            raise ValueError('Slim installer ZIP contains executable device model package bytes')
        for name, record in records.items():
            item = items[name]
            if item.file_size != record['size']:
                raise ValueError('Installer ZIP member size differs from build-info: ' + name)
            digest = hashlib.sha256()
            size = 0
            with bundle.open(item) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    size += len(block)
                    digest.update(block)
            if size != record['size'] or digest.hexdigest() != record['sha256']:
                raise ValueError('Installer ZIP member digest differs from build-info: ' + name)

        required = {'components/release.json', 'maintenance/release.json',
                    'components/model-catalog/lock.json', 'cache/integration/manifest.json'}
        if not required <= set(records):
            raise ValueError('Slim installer is missing release, maintenance, catalog, or integration metadata')
        release = json.loads(bundle.read(items['components/release.json']))
        maintenance_release = json.loads(bundle.read(items['maintenance/release.json']))
        if (release.get('version') != version
                or release.get('display_version') != info.get('display_version')
                or not isinstance(maintenance_release.get('version'), str)):
            raise ValueError('Installer or generic maintenance release metadata differs from build-info')
        catalog_files = [name for name in records if name.startswith('components/model-catalog/snapshot/')]
        if not catalog_files:
            raise ValueError('Slim installer must retain its pinned model catalog display snapshot')
        integration = json.loads(bundle.read(items['cache/integration/manifest.json']))
        if 'local_test_models' in integration:
            raise ValueError('Slim installer integration metadata declares local-test model assets')
        maintenance = integration.get('components', {}).get('core:maintenance', {})
        component = maintenance.get('component', {})
        runtime_files = maintenance.get('files', [])
        if (maintenance.get('available') is not True or component.get('id') != 'core:maintenance'
                or not isinstance(runtime_files, list) or not runtime_files
                or component.get('files') != runtime_files
                or component.get('version') != maintenance_release['version']):
            raise ValueError('Slim installer must retain the generic core:maintenance runtime')
        for entry in runtime_files:
            if not isinstance(entry, dict):
                raise ValueError('Invalid generic maintenance runtime inventory')
            source = relative_path(entry.get('source'))
            if _is_model_payload('cache/integration/' + source):
                raise ValueError('Slim integration manifest references a retained device model package')
            size = entry.get('size')
            digest = entry.get('sha256')
            if (type(size) is not int or size < 0 or not isinstance(digest, str)
                    or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                raise ValueError('Invalid generic maintenance runtime file digest')
            member = 'cache/integration/' + source
            actual = records.get(member)
            if actual is None:
                raise ValueError('Generic maintenance runtime references a missing ZIP member: ' + member)
            if actual['size'] != size:
                raise ValueError('Generic maintenance runtime size differs from its ZIP member: ' + member)
            if actual['sha256'] != digest:
                raise ValueError('Generic maintenance runtime digest differs from its ZIP member: ' + member)

    return dict(status='verified-slim-installer-candidate', path=str(archive_path),
        size=archive_path.stat().st_size, sha256=sha256(archive_path), source_commit=commit,
        version=version, package_channel=info['package_channel'],
        installer_inventory_files=len(records), model_catalog_files=len(catalog_files),
        generic_maintenance_version=maintenance_release['version'], model_package_files=0,
        signature_verified=False, publication_authorized=False, upload_performed=False)


def package_slim(root: Path, output: Path, source_commit: str, configuration: str = 'Release') -> dict:
    root = Path(root).resolve()
    output = Path(output).resolve()
    if configuration != 'Release':
        raise ValueError('Slim release candidates must use the Release configuration')
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError('Installer release artifacts must be written outside the public source checkout')
    release = json.loads((root / 'components/release.json').read_bytes())
    if output.name != artifact_name(_version(release.get('version'))):
        raise ValueError('Output filename must be the versioned -win-x64-slim.zip name')
    assert_public_checkout(root, source_commit)
    packaged = packager.package(root, output, source_commit, configuration, local_test=False)
    verified = verify_slim_archive(output, source_commit=source_commit)
    return dict(status='packaged-and-verified-slim-candidate', package=packaged,
        verification=verified, signature_verified=False, publication_authorized=False, upload_performed=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('package', help='Build through the normal non-local-test installer packager')
    build.add_argument('--root', type=Path, default=ROOT)
    build.add_argument('--output', type=Path, required=True)
    build.add_argument('--source-commit', required=True)
    build.add_argument('--configuration', choices=('Release',), default='Release')
    verify = commands.add_parser('verify', help='Verify an existing slim ZIP before a separately approved release step')
    verify.add_argument('--archive', type=Path, required=True)
    verify.add_argument('--source-commit', required=True)
    args = parser.parse_args()
    if args.command == 'package':
        result = package_slim(args.root, args.output, args.source_commit, args.configuration)
    else:
        result = verify_slim_archive(args.archive, source_commit=args.source_commit)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
