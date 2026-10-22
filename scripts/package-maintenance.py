"""Build an unsigned, reproducible generic maintainer update from a clean public checkout.

No signing key is read. Publication signs the exact DeviceIndex bytes with the
existing Ed25519 publisher; the index anchors manifest.json and the ZIP digest.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.manifest import confined, load_json, relative_path, validate_manifest, validate_package

REPOSITORY = 'FlightDan/Boiler-installer'
PUBLIC_KEY_SHA256 = 'c3f443312c74bcdc3013d7a9deb306473dc6c3b21f6625319012c9dbdb9b1e86'
# Explicit additions require source review; do not turn this into a directory walk.
RUNTIME_FILES = (
    'maintenance/__init__.py', 'maintenance/__main__.py', 'maintenance/engine.py',
    'maintenance/plugins.py', 'maintenance/updates.py', 'maintenance/adoption_blobs.py', 'maintenance/live_root.py',
    'protocol/__init__.py', 'protocol/__main__.py', 'protocol/fetch.py',
    'protocol/manifest.py', 'protocol/trusted-public-key.pem',
)
LAUNCHER = (b'#!/bin/sh\nset -eu\ncd /usr/local/lib/boiler\n'
            b'exec /usr/bin/python3 -I -B -c \'import sys; sys.path.insert(0, "/usr/local/lib/boiler"); '
            b'from maintenance.__main__ import main; raise SystemExit(main())\' "$@"\n')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode('utf-8')


def record(name, data):
    return {'path': name, 'size': len(data), 'sha256': sha256(data)}


def committed_sources(root, source_commit):
    """Reject the internal parent repository, dirty sources, links and unlisted commits."""
    if not re.fullmatch(r'[0-9a-f]{40}', source_commit):
        raise ValueError('Exact 40-character public source commit required')
    root = Path(root).resolve()

    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)

    if Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve() != root:
        raise ValueError('An independent public checkout rooted at --root is required')
    if git('rev-parse', 'HEAD').decode().strip() != source_commit:
        raise ValueError('Source commit must be the independent checkout HEAD')
    if git('status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('Public source checkout must be clean')
    whitelist = load_json(confined(root, 'scripts/source-files.json'))
    names = whitelist.get('files')
    if (whitelist.get('kind') != 'BoilerSourceWhitelist' or not isinstance(names, list) or
            any(not isinstance(name, str) for name in names) or len(names) != len(set(names))):
        raise ValueError('Invalid source whitelist')
    entries = {}
    for entry in git('ls-tree', '-rz', source_commit).split(b'\x00'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        mode, kind, _ = metadata.decode().split()
        if mode not in ('100644', '100755') or kind != 'blob':
            raise ValueError('Source links and submodules are forbidden')
        entries[name.decode('utf-8')] = mode
    if set(entries) != set(names):
        raise ValueError('Committed source differs from the explicit source whitelist')
    contents = {}
    for name in sorted(names):
        relative_path(name)
        data = git('show', source_commit+':'+name)
        if confined(root, name).read_bytes() != data:
            raise ValueError('Working bytes differ from committed public source: '+name)
        contents[name] = data
    return contents


def release_settings(contents):
    release = json.loads(contents['maintenance/release.json'])
    if (release.get('schema_version') != 1 or release.get('product') != 'Boiler Maintenance' or
            not re.fullmatch(r'\d+\.\d+\.\d+(?:\+maintenance\.\d+)?', release.get('version', ''))):
        raise ValueError('Invalid independent maintenance release identity')
    compatibility = release.get('compatibility', {})
    # Do not accidentally advertise all SteamOS versions or architectures.
    if (compatibility.get('os_ids') != ['steamos'] or not compatibility.get('os_versions') or
            not compatibility.get('architectures') or 'device_ids' in compatibility):
        raise ValueError('Generic release requires explicit SteamOS versions and architectures')
    validate_manifest(dict(schema_version=1, kind='ComponentManifest', id='core:maintenance',
                           version=release['version'], compatibility=compatibility))
    minimum = release.get('minimum_engine_version')
    if not isinstance(minimum, str) or not re.fullmatch(r'\d+\.\d+\.\d+', minimum):
        raise ValueError('Explicit minimum engine version required')
    # The old engine must be able to read the new manifest. Do not advertise a
    # stronger protocol requirement than this runtime itself implements.
    constants = [node.value.value for node in ast.parse(contents['protocol/manifest.py']).body
                 if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                 and any(isinstance(target, ast.Name) and target.id == 'ENGINE_VERSION' for target in node.targets)]
    if len(constants) != 1 or tuple(map(int, minimum.split('.'))) > tuple(map(int, constants[0].split('.'))):
        raise ValueError('Minimum engine version exceeds the bundled protocol engine')
    return release


def package(root, output, source_commit, asset_url):
    """Return unsigned publisher metadata; never silently replace a published artifact."""
    root, output = Path(root).resolve(), Path(output).resolve()
    parsed = urlparse(asset_url)
    if (parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.path.split('/')[1:4] != ['FlightDan', 'Boiler-installer', 'releases'] or
            not re.fullmatch(r'/FlightDan/Boiler-installer/releases/download/[^/]+/[^/]+\.zip', parsed.path) or
            parsed.netloc != 'github.com'):
        raise ValueError('An exact credential-free Boiler-installer GitHub Release ZIP URL is required')
    metadata_path = output.with_suffix(output.suffix+'.json')
    partial = output.with_suffix(output.suffix+'.partial')
    if any(path.exists() for path in (output, metadata_path, partial)):
        raise ValueError('Use fresh artifact and metadata destinations')
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError('Release artifacts must be outside the clean source checkout')
    contents = committed_sources(root, source_commit)
    required = {*RUNTIME_FILES, 'LICENSE', 'maintenance/release.json'}
    if not required <= contents.keys():
        raise ValueError('Required maintenance sources are absent from the source whitelist')
    if contents.get('scripts/package-maintenance.py') != Path(__file__).read_bytes():
        raise ValueError('Builder differs from the committed public source')
    if sha256(contents['protocol/trusted-public-key.pem']) != PUBLIC_KEY_SHA256:
        raise ValueError('Ordinary maintenance releases cannot change the existing public key')
    release = release_settings(contents)
    payload, runtime = {}, []
    for name in RUNTIME_FILES:
        data = contents[name]
        source = 'maintenance-runtime/'+name
        payload[source] = data
        runtime.append(dict(source=source, target='usr/local/lib/boiler/'+name,
                            size=len(data), sha256=sha256(data), mode=0o644))
    source = 'maintenance-runtime/boiler-maintenance'
    payload[source] = LAUNCHER
    runtime.append(dict(source=source, target='usr/local/bin/boiler-maintenance',
                        size=len(LAUNCHER), sha256=sha256(LAUNCHER), mode=0o755))
    component = dict(schema_version=1, kind='ComponentManifest', id='core:maintenance',
                     version=release['version'], compatibility=release['compatibility'], files=runtime)
    payload['component.json'] = json_bytes(component)
    payload['release.json'] = contents['maintenance/release.json']
    payload['LICENSE'] = contents['LICENSE']
    payload['source-identity.json'] = json_bytes({
        'schema_version': 1, 'source': {'kind': 'github', 'repository': REPOSITORY, 'commit': source_commit},
        'source_whitelist_sha256': sha256(contents['scripts/source-files.json']),
        'inputs': [record(name, contents[name]) for name in sorted(required)],
        'launcher_origin': 'scripts/package-maintenance.py',
        'builder': record('scripts/package-maintenance.py', contents['scripts/package-maintenance.py']),
        'acceptance': release['acceptance'],
    })
    manifest = dict(schema_version=1, kind='DevicePackage', id='bundle:maintenance', purpose='maintenance',
                    version=release['version'], source={'kind': 'github', 'repository': REPOSITORY, 'commit': source_commit},
                    minimum_engine_version=release['minimum_engine_version'], compatibility=release['compatibility'],
                    components=[component], files=[record(name, data) for name, data in sorted(payload.items())])
    validate_manifest(manifest, 'DevicePackage')
    payload['manifest.json'] = json_bytes(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Validate exactly the tree that will be archived through the production
    # validator; no trust grant is persisted or claimed by this integrity check.
    with tempfile.TemporaryDirectory(prefix='boiler-maintenance-') as temporary:
        tree = Path(temporary)
        for name, data in payload.items():
            path = confined(tree, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        validate_package(tree, expected_manifest_sha256=sha256(payload['manifest.json']))
    metadata_created = False
    try:
        with zipfile.ZipFile(partial, 'x', compression=zipfile.ZIP_STORED) as archive:
            for name, data in sorted(payload.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                mode = 0o755 if name == source else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, data)
        archive_bytes = partial.read_bytes()
        entry = dict(id=manifest['id'], version=manifest['version'], size=len(payload['manifest.json']),
                     sha256=sha256(payload['manifest.json']), compatibility=release['compatibility'],
                     minimum_engine_version=release['minimum_engine_version'],
                     source=dict(kind='https', url=asset_url, size=len(archive_bytes), sha256=sha256(archive_bytes)))
        result = dict(schema_version=1, kind='UnsignedMaintenanceArtifact', product=release['product'],
                      version=release['version'], source_commit=source_commit,
                      artifact=dict(name=output.name, size=len(archive_bytes), sha256=sha256(archive_bytes)),
                      index_entry=entry, inventory=[record(name, data) for name, data in sorted(payload.items())],
                      trusted_public_key_sha256=PUBLIC_KEY_SHA256,
                      signature_status='unsigned; append index_entry to the complete signed DeviceIndex before normal updates',
                      acceptance=release['acceptance'])
        # Exclusive creation refuses collisions; metadata has no local paths,
        # clock fields or signatures and is reproducible with the artifact.
        with metadata_path.open('xb') as stream:
            metadata_created = True
            stream.write(json_bytes(result))
        # Hard-link creation is atomic and refuses to replace an existing ZIP.
        os.link(partial, output)
        partial.unlink()
    except BaseException:
        partial.unlink(missing_ok=True)
        if metadata_created:
            metadata_path.unlink(missing_ok=True)
        raise
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--asset-url', required=True, help='Exact eventual GitHub Release asset URL; no network access occurs')
    args = parser.parse_args()
    print(json.dumps(package(args.root, args.output, args.source_commit, args.asset_url), indent=2))
