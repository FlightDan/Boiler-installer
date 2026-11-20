"""Build a signed-index-ready model variant from a canonical TOON and component releases."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import urlparse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.component_versions import (ADAPTATION_REPOSITORY, component_releases, resolution_revoked,
                                         validate_component_release, validate_model_requests, version_key)
from protocol.fetch import _cache_lock, cached_index, fetch_package
from protocol.local import _safe_path, import_local
from protocol.manifest import (ProtocolError, compatible, confined, device_advisory, load_json, resolve_plan,
                               validate_manifest, validate_package)
from protocol.resource_pool import decoder
from scripts.resources import model_source

DEVICE_REGISTRY = 'registry:boiler-devices'


def _file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _canonical_source(commit):
    return dict(kind='github', repository=ADAPTATION_REPOSITORY, commit=commit)


def _target(environment):
    keys = ('architecture', 'os_id', 'os_version', 'device_id', 'kernels')
    if (set(environment) != set(keys)
            or any(not isinstance(environment[key], str) or not environment[key] for key in keys[:-1])
            or not isinstance(environment['kernels'], list) or not environment['kernels']
            or any(not isinstance(item, str) or not item for item in environment['kernels'])):
        raise ProtocolError('Target OS, version, kernel and model are required')
    return dict(architectures=[environment['architecture']], os_ids=[environment['os_id']],
                os_versions=[environment['os_version']], device_ids=[environment['device_id']],
                kernels=sorted(set(environment['kernels'])))


def _entry(index, digest, folder):
    entry = next((row for row in index['packages'] if row['sha256'] == digest), None)
    if entry is None or digest in index.get('revocations', ()):
        raise ProtocolError('Base model package is absent from the signed index or revoked')
    package = validate_package(folder, expected_manifest_sha256=digest)
    if ((package['id'], package['version'], (folder/'manifest.json').stat().st_size)
            != (entry['id'], entry['version'], entry['size'])
            or package['source'].get('repository') != ADAPTATION_REPOSITORY
            or entry.get('declared_source', package['source']) != package['source']
            or package.get('purpose') == 'maintenance' or 'component_release' in package
            or package.get('trust', {}).get('status') == 'unsigned-development'):
        raise ProtocolError('Base must be the exact published canonical installation package')
    return entry, package


def _guides(project, commit):
    names = model_source.sources(project)
    lock = load_json(project/model_source.SNAPSHOT)
    if lock['commit'] != commit:
        raise ProtocolError('Canonical model snapshot differs from the requested commit')
    toon = decoder(project/'adaptations')
    for name in names:
        model = toon.loads(confined(project, name).read_text(encoding='utf-8'), strict=True)
        declaration = model.get('resource_model', {})
        if 'id' in declaration:
            yield declaration['id'], name, validate_model_requests(declaration['resources'])


def affected_models(project, commit, resource, version):
    """Find subscribers without a maintained per-component model list."""
    validate_model_requests([dict(id=resource, version=version)])
    return [dict(model=identity, toon=path) for identity, path, requests in _guides(project, commit)
            if resource in requests and requests[resource].get('version', version) == version]


def _copy_model(base, manifest, staging):
    inventory = {}
    for row in manifest['files']:
        name = row['path']
        target = confined(staging, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(confined(base, name), target)
        inventory[name] = dict(row)
    return inventory


def _add_release(staging, package_root, package, inventory, component):
    prefix = 'payload/component-releases/' + component['id'].replace(':', '/') + '/' + component['version']
    source_paths = {}
    for row in package['files']:
        name = prefix + '/' + row['path']
        if name in inventory:
            if any(inventory[name][key] != row[key] for key in ('sha256', 'size')):
                raise ProtocolError('Component release conflicts with an immutable payload path')
        else:
            path = confined(staging, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(confined(package_root, row['path']), path)
            inventory[name] = dict(row, path=name)
        source_paths[row['path']] = name
    for row in component['files']:
        row['source'] = source_paths[row['source']]


def _compose(index, anchor, model, requested, base, staging, environment, acquire):
    """Use pinned inputs and return a normal DevicePackage plus its resolution record."""
    manifest = deepcopy(model)
    previous_releases = model.get('component_resolution', {})
    if any(row['resource_id'] not in requested for row in previous_releases.values()):
        raise ProtocolError('Base model variant contains resources no longer requested by its TOON')
    inventory = _copy_model(base, manifest, staging)
    owned = {row['id']: row for row in manifest['components']}
    reserved = {row['id'] for row in manifest.get('resources', ())}
    resolved = {}
    identities = {entry['id'] for entry in index['packages']
                  if entry.get('component_release', {}).get('resource_id') in requested}
    available = set(owned) | {identity for identity in identities if identity in requested and identity not in reserved}
    for identity in sorted(identities):
        old = owned.get(identity)
        candidates = [entry for entry in component_releases(index, identity, environment, available_components=available)
                      if entry['component_release']['resource_id'] in requested]
        candidates = [entry for entry in candidates if requested[entry['component_release']['resource_id']].get(
            'version', entry['version']) == entry['version']]
        if not candidates or identity in reserved:
            continue
        entry = candidates[0]
        resource = entry['component_release']['resource_id']
        request = requested[resource]
        if old is None and identity != resource:
            # A legacy runtime identity needs a concrete component in the reviewed
            # base. A new runtime uses its TOON resource ID directly.
            continue
        default = request.get('default', True)
        policy = None if default else 'explicit-opt-in'
        previous = previous_releases.get(identity)
        if (previous and previous['manifest_sha256'] == entry['sha256'] and old.get('default') == default
                and old.get('selection_policy') == policy and 'version' not in request):
            continue
        acquired = acquire(entry)
        package_root = Path(acquired['directory'])
        package = validate_package(package_root, expected_manifest_sha256=entry['sha256'])
        if (package_root/'manifest.json').stat().st_size != entry['size']:
            raise ProtocolError('Component release manifest size differs from the signed index')
        candidate = deepcopy(validate_component_release(entry, package, environment))
        candidate['minimum_engine_version'] = package['minimum_engine_version']
        if old is not None:
            if (old.get('hooks') or not old.get('files')
                    or any(field in old for field in ('dynamic_handler', 'ssd_boot_policy', 'cpu_mutable_config',
                                                       'retain_owned_paths'))
                    or {row['target'] for row in old['files']} != {row['target'] for row in candidate['files']}):
                raise ProtocolError('Component release changes the existing model installation lifecycle: ' + identity)
            if version_key(old['version']) == version_key(candidate['version']):
                def installed_files(component):
                    return {row['target']: (row['sha256'], row['size'], row.get('mode', 420)) for row in component['files']}
                if installed_files(old) != installed_files(candidate):
                    raise ProtocolError('Equal-precedence release changes installed files: ' + identity)
        candidate['default'] = default
        if default:
            candidate.pop('selection_policy', None)
        else:
            candidate['selection_policy'] = policy
        _add_release(staging, package_root, package, inventory, candidate)
        owned[identity] = candidate
        resolved[identity] = dict(version=entry['version'], resource_id=resource, manifest_sha256=entry['sha256'],
                                  index_sha256=anchor, source=package['source'])
    for resource, request in requested.items():
        if 'version' in request and not any(row['resource_id'] == resource for row in resolved.values()):
            raise ProtocolError('Pinned component version is unavailable: ' + resource)
    if not resolved:
        raise ProtocolError('No newer independent component release matches the model TOON and target')

    components = list(owned.values())
    for identity in resolved:
        if resolve_plan(components, [identity], environment)['device_warnings']:
            raise ProtocolError('Component dependency is incompatible with the model: ' + identity)
    defaults = [component['id'] for component in components if component.get('available', True)
                and component.get('selection_policy') != 'explicit-opt-in' and 'ssd_boot_policy' not in component]
    resolve_plan(components, defaults, environment)
    for identity in resolved:
        previous = previous_releases.get(identity)
        if previous:
            prefix = 'payload/component-releases/' + identity.replace(':', '/') + '/' + previous['version'] + '/'
            referenced = {row['source'] for component in components for row in component.get('files', [])}
            for name in list(inventory):
                if name.startswith(prefix) and name not in referenced and previous['version'] != owned[identity]['version']:
                    confined(staging, name).unlink()
                    inventory.pop(name)
    manifest['components'] = components
    manifest['files'] = sorted(inventory.values(), key=lambda row: row['path'])
    manifest['component_resolution'] = {**previous_releases, **resolved}
    minimum = max((record['minimum_engine_version'] for record in [manifest, *components]
                   if 'minimum_engine_version' in record), default=None, key=version_key)
    if minimum:
        manifest['minimum_engine_version'] = minimum
    return manifest


def _archive(folder, output, url, manifest):
    raw = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    (folder/'manifest.json').write_bytes(raw)
    validate_package(folder, expected_manifest_sha256=digest)
    archive = folder.parent/'model.zip'
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(['manifest.json', *(row['path'] for row in manifest['files'])]):
            member = zipfile.ZipInfo(name)
            member.compress_type = zipfile.ZIP_DEFLATED
            with confined(folder, name).open('rb') as source, bundle.open(member, 'w', force_zip64=True) as target:
                shutil.copyfileobj(source, target)
    imported = import_local(archive, folder.parent/'recheck', developer=True)
    if imported['manifest_sha256'] != digest:
        raise ProtocolError('Packaged model manifest changed during archive verification')
    index_entry = dict(id=manifest['id'], version=manifest['version'], size=len(raw), sha256=digest,
                       source=dict(kind='https', url=url, size=archive.stat().st_size, sha256=_file_hash(archive)),
                       declared_source=manifest['source'], compatibility=manifest['compatibility'])
    for key in ('component_release', 'hardware_matching', 'minimum_engine_version', 'component_resolution', 'name'):
        if key in manifest:
            index_entry[key] = manifest[key]
    if 'component_release' in manifest:
        for key in ('requires', 'conflicts'):
            index_entry[key] = manifest['components'][0].get(key, [])
    os.link(archive, output)
    return index_entry


def _output(output, release_url):
    output = _safe_path(output)
    parsed_url = urlparse(release_url)
    if (parsed_url.scheme != 'https' or parsed_url.netloc != 'github.com' or parsed_url.query or parsed_url.fragment
            or not re.fullmatch('/FlightDan/Boiler-adaptations/releases/download/[^/]+/[^/]+[.]zip', parsed_url.path)):
        raise ProtocolError('Model archive needs an immutable canonical GitHub Release ZIP URL')
    if output.suffix.lower() != '.zip' or os.path.lexists(output):
        raise ProtocolError('Choose a fresh model ZIP destination')
    return output


def build_component(source, digest, output, *, release_url):
    """Package one reviewed static component without executing it or signing anything."""
    source, output = _safe_path(source), _output(output, release_url)
    manifest = validate_package(source, expected_manifest_sha256=digest)
    if 'component_release' not in manifest:
        raise ProtocolError('A canonical component release manifest is required')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.component-release-', dir=output.parent) as temporary:
        staging = Path(temporary)/'package'
        staging.mkdir()
        _copy_model(source, manifest, staging)
        return _archive(staging, output, release_url, manifest)


def build(project, base, digest, cache, environment, output, *, version, commit, release_url, local_payloads=None):
    project, base, cache = (_safe_path(path) for path in (project, base, cache))
    output = _output(output, release_url)
    target = _target(environment)
    with _cache_lock(cache):
        index = cached_index(cache)
        anchor = load_json(confined(cache, 'current.json'))['sha256']
    if index['id'] != DEVICE_REGISTRY:
        raise ProtocolError('Model resolution requires the official device registry')
    base_entry, package = _entry(index, digest, base)
    if (not compatible(base_entry, environment) or device_advisory(base_entry, environment)
            or not compatible(package, environment) or device_advisory(package, environment)
            or package.get('device_id', package['id'].removeprefix('device:')) != environment['device_id']):
        raise ProtocolError('Base model package is incompatible with the requested target')
    if any(row['id'] == package['id'] and version_key(version) <= version_key(row['version'])
           for row in index['packages']):
        raise ProtocolError('Model variant needs a new immutable version newer than indexed variants')
    requested = next((requests for identity, _, requests in _guides(project, commit) if identity == package['id']), None)
    if requested is None:
        raise ProtocolError('Base identity has no TOON declaration in the canonical snapshot')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.resolve-model-', dir=output.parent) as temporary:
        temporary = Path(temporary)
        staging = temporary/'package'
        staging.mkdir()

        def acquire(entry):
            if local_payloads is not None:
                return {'directory': confined(Path(local_payloads), entry['sha256'])}
            return fetch_package(cache, entry['id'], entry['version'], temporary/entry['sha256'])

        manifest = _compose(index, anchor, package, requested, base, staging, environment, acquire)
        manifest.update(version=version, compatibility=target, source=_canonical_source(commit))
        if resolution_revoked(manifest, index.get('revocations', ())):
            raise ProtocolError('Resolved variant still contains a revoked component release')
        validate_manifest(manifest, 'DevicePackage')
        return _archive(staging, output, release_url, manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    component = commands.add_parser('component')
    component.add_argument('--source', type=Path, required=True)
    component.add_argument('--manifest-sha256', required=True)
    affected = commands.add_parser('affected')
    affected.add_argument('--source-commit', required=True)
    affected.add_argument('--resource', required=True)
    affected.add_argument('--version', required=True)
    model = commands.add_parser('model')
    model.add_argument('--base', type=Path, required=True)
    model.add_argument('--base-manifest-sha256', required=True)
    model.add_argument('--index-cache', type=Path, required=True)
    model.add_argument('--environment', type=Path, required=True)
    model.add_argument('--version', required=True)
    model.add_argument('--source-commit', required=True)
    model.add_argument('--local-payloads', type=Path)
    for command in (component, model):
        command.add_argument('--release-url', required=True)
        command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'affected':
        print(json.dumps({'models': affected_models(ROOT, args.source_commit, args.resource, args.version)}, indent=2))
        raise SystemExit(0)
    if args.command == 'component':
        result = build_component(args.source, args.manifest_sha256, args.output, release_url=args.release_url)
    else:
        result = build(ROOT, args.base, args.base_manifest_sha256, args.index_cache, load_json(args.environment),
                       args.output, version=args.version, commit=args.source_commit, release_url=args.release_url,
                       local_payloads=args.local_payloads)
    print(json.dumps({'index_entry': result}, indent=2))
