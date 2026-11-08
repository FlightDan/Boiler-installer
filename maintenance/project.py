"""Live project discovery and the desktop signed-update boundary."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

from protocol import compatible, device_advisory, validate_manifest
from protocol.fetch import cached_index, fetch_index
from protocol.manifest import MAX_MANIFEST, _decode_json, confined
from protocol.model_catalog import REPOSITORY, ROOT, load_models, remote_bytes
from . import updates
from .engine import Engine, MaintenanceError, _read as read_journal


DEVICE = 'https://github.com/' + REPOSITORY + '/releases/latest/download'
GENERIC_REPO = 'FlightDan/Boiler-installer'
REGISTRIES = {'device': 'registry:boiler-devices', 'maintenance': 'registry:boiler-maintenance'}
CORE = 'bundle:maintenance'
CORE_TARGETS = {
    'usr/local/bin/boiler-maintenance', 'usr/local/bin/boiler-maintenance-ui',
    'usr/share/applications/Boiler-Maintenance.desktop',
}
LEGACY = {('ONE-NETBOOK', 'ONEXPLAYER 3'): 'FlightDan/OneXPlayer3-SteamOS'}
DMI = Path('/sys/class/dmi/id')
VERSION = re.compile(r'maintenance-v(\d+\.\d+\.\d+)(?:[+-]maintenance\.(\d+))?\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
OPTI_LOCK = 'adaptations/records/oxp3-shared-base/opti031-inputs.json'


def hardware_match(*, dmi=DMI, catalog=None):
    catalog = load_models(online=False, include_bundles=False) if catalog is None else catalog
    try:
        manufacturer, product = ((dmi / field).read_text(encoding='utf-8').strip()
                                 for field in ('sys_vendor', 'product_name'))
    except (OSError, UnicodeError):
        return {'status': 'unavailable'}, None
    match = {'manufacturer': manufacturer, 'product': product}
    if not manufacturer or not product:
        return dict(match, status='unavailable'), None
    models = [model for model in catalog['models']
              if {'manufacturer': manufacturer, 'model': product} in model['hardware_matching']['pairs']]
    if len(models) != 1:
        return dict(match, status='ambiguous' if models else 'no-model'), None
    model = models[0]
    return dict(match, status='matched', model_id=model['id'], name=model['name']), model


def _releases(repository):
    url = 'https://api.github.com/repos/' + repository + '/releases?per_page=50'
    try:
        rows = json.loads(remote_bytes(url, 4 * 1024**2))
    except HTTPError as error:
        if error.code not in (403, 429):
            raise
        project = 'https://github.com/' + repository + '/releases'
        try:
            feed = ElementTree.fromstring(remote_bytes(project + '.atom', 512 * 1024))
        except ElementTree.ParseError as invalid:
            raise MaintenanceError('Invalid GitHub release feed') from invalid
        tags = []
        for link in feed.findall('{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}link'):
            href = link.get('href', '')
            if href.startswith(project + '/tag/'):
                tags.append({'tag_name': unquote(href.removeprefix(project + '/tag/'))})
        return tags
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise MaintenanceError('Invalid GitHub release listing')
    return [row for row in rows if row.get('draft') is False]


def _maintenance_releases(repository):
    result = []
    for release in _releases(repository):
        tag = release.get('tag_name')
        version = VERSION.fullmatch(tag) if isinstance(tag, str) else None
        if version:
            result.append(((tuple(map(int, version[1].split('.'))), int(version[2] or 0)), release))
    return sorted(result, key=lambda row: row[0], reverse=True)


def _generic_endpoint():
    for _, release in _maintenance_releases(GENERIC_REPO):
        names = {asset.get('name') for asset in release.get('assets', []) if isinstance(asset, dict)}
        if 'assets' not in release or {'index.json', 'index.sig'} <= names:
            return 'https://github.com/' + GENERIC_REPO + '/releases/download/' + release['tag_name']
    return None


def _fallback(match, offline):
    if match['status'] != 'no-model':
        return None
    repository = LEGACY.get((match['manufacturer'], match['product']))
    if repository is None:
        return None
    url = 'https://github.com/' + repository
    result = dict(repository=repository, url=url + '/blob/main/steamos-maintenance/README.md',
                  status='offline' if offline else 'empty')
    if not offline:
        try:
            releases = _maintenance_releases(repository)
            if releases:
                release = releases[0][1]
                result.update(status='ready', version=release['tag_name'].removeprefix('maintenance-v'),
                              release_url=url + '/releases/tag/' + release['tag_name'])
        except (OSError, ValueError, MaintenanceError) as error:
            result.update(status='error', message=str(error))
    return result


def _upstream_plugin(model):
    if not model or 'opti2xmx' not in {row['id'] for row in model['components']}:
        return None
    try:
        reference = _decode_json((ROOT / OPTI_LOCK).read_bytes())['plugin_release']
        source = urlparse(reference['release_url'])
        if source.scheme != 'https' or source.netloc != 'github.com':
            raise MaintenanceError('Invalid pinned plugin release source')
        repository, separator, _ = source.path.lstrip('/').partition('/releases/tag/')
        if not separator or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise MaintenanceError('Invalid pinned plugin repository')
        versions = []
        for release in _releases(repository):
            tag = release.get('tag_name', '')
            if isinstance(tag, str) and re.fullmatch(r'v\d+\.\d+\.\d+', tag):
                versions.append(tag[1:])
        current = reference['plugin_version']
        newest = max(versions, key=updates._version_key, default=current)
        return dict(id='opti2xmx', repository=repository, reference_version=current,
                    version=newest, newer=updates._version_key(newest) > updates._version_key(current))
    except (OSError, ValueError, MaintenanceError, KeyError) as error:
        return dict(id='opti2xmx', message=str(error))


def _registry(index, stream):
    if index.get('id') != REGISTRIES[stream]:
        raise MaintenanceError('Signed index belongs to a different update channel')
    return index


def _transport(entry, stream):
    if stream != 'maintenance':
        return
    source = entry['source']
    parsed = urlparse(source.get('url', ''))
    if (source['kind'] != 'https' or parsed.scheme != 'https' or parsed.netloc != 'github.com'
            or parsed.query or parsed.fragment or
            not re.fullmatch(r'/FlightDan/Boiler-installer/releases/download/[^/]+/[^/]+\.zip', parsed.path)):
        raise MaintenanceError('Generic maintenance requires an official Boiler Release ZIP')


def _stream(session, stream, identity, environment, offline):
    cache = session / stream
    try:
        status = 'offline' if offline else 'ready'
        if offline:
            index = cached_index(cache)
        else:
            try:
                endpoint = DEVICE if stream == 'device' else _generic_endpoint()
                if endpoint is None:
                    return dict(id=stream, status='empty', candidates=[], message='No signed maintenance release index found')
                index = fetch_index(endpoint + '/index.json', endpoint + '/index.sig', cache, timeout=12,
                                    expected_id=REGISTRIES[stream])['index']
            except (URLError, TimeoutError, ConnectionError):
                index = cached_index(cache)
                status = 'offline'
        _registry(index, stream)
        installed = (_decode_json(Path(__file__).with_name('release.json').read_bytes())['version']
                     if stream == 'maintenance' else None)
        candidates = []
        for row in index['packages']:
            if row['id'] != identity or row['sha256'] in index.get('revocations', []):
                continue
            _transport(row, stream)
            canonical = row.get('declared_source', {}).get('repository') == REPOSITORY
            historical = stream == 'device' and not canonical
            candidate = dict(row, compatible=not historical and compatible(row, environment)
                             and not device_advisory(row, environment), installed_version=installed,
                             update_available=installed is None or
                             updates._version_key(row['version']) > updates._version_key(installed))
            if historical:
                candidate['message'] = 'historical-device-release'
            candidates.append(candidate)
        candidates.sort(key=lambda row: updates._version_key(row['version']), reverse=True)
        return dict(id=stream, status=status, candidates=candidates)
    except FileNotFoundError:
        return dict(id=stream, status='offline' if offline else 'error', candidates=[],
                    message='No verified index is cached')
    except (OSError, ValueError, MaintenanceError) as error:
        return dict(id=stream, status='error', message=str(error), candidates=[])


def check(root, session, *, offline=False):
    try:
        match, model = hardware_match()
    except (OSError, ValueError) as error:
        match, model = {'status': 'unavailable', 'message': str(error)}, None
    device_id = model['id'].removeprefix('device:') if model else None
    environment = updates.target_environment(root, device_id=device_id)
    session = Path(session)
    streams = [_stream(session, 'maintenance', CORE, environment, offline)]
    if model is not None:
        streams.append(_stream(session, 'device', model['id'], environment, offline))
    return dict(match=match, streams=streams, fallback=_fallback(match, offline),
                components=model['components'] if model else [],
                upstream_plugin=None if offline else _upstream_plugin(model))


def _identity(stream, identity):
    if stream == 'maintenance':
        if identity != CORE:
            raise MaintenanceError('Only the generic maintenance bundle is permitted in this channel')
        return None
    match, _ = hardware_match()
    if match.get('model_id') != identity:
        raise MaintenanceError('Signed device update must match this live model')
    return identity.removeprefix('device:')


def _package_path(session, stream, entry):
    return Path(session) / (stream + '-package-' + entry['sha256'])


def _entry(index, identity, version):
    for row in index['packages']:
        if (row['id'], row['version']) == (identity, version) and row['sha256'] not in index.get('revocations', []):
            return row
    raise MaintenanceError('Package is absent from the signed index or revoked')


def inventory(root):
    from .models import _privileged, pending
    _privileged()
    result = updates.inventory(root)
    engine = Engine(root, read_only=True)
    result['pending_model_recovery'] = bool(pending(engine))
    for row in result['components'].values():
        journal = read_journal(engine._journal(row['transaction']))
        row['scripted'] = bool(journal['component'].get('hooks'))
        if row['scripted'] and not journal.get('operations'):
            row['verified'] = None
    return result


def acquire(session, stream, identity, version):
    _identity(stream, identity)
    cache = Path(session) / stream
    index = _registry(cached_index(cache), stream)
    entry = _entry(index, identity, version)
    _transport(entry, stream)
    destination = _package_path(session, stream, entry)
    if destination.is_dir():
        from protocol import validate_package
        package = validate_package(destination, expected_manifest_sha256=entry['sha256'])
        return dict(package=package, directory=str(destination), manifest_sha256=entry['sha256'])
    return updates.acquire_package(cache, identity, version, destination)


def _read(path, maximum):
    with updates._regular_stream(path) as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise MaintenanceError('Update input exceeds its size limit')
    return data


@contextmanager
def _private_index(session, stream):
    """The privileged updater only locks and reads its own verified cache."""
    source = Path(session) / stream
    pointer = _decode_json(_read(source / 'current.json', 4096))
    generation = pointer.get('generation')
    if not isinstance(generation, str) or not SHA256.fullmatch(generation):
        raise MaintenanceError('Invalid signed index cache generation')
    with tempfile.TemporaryDirectory(prefix='boiler-project-index-') as temporary:
        cache = Path(temporary)
        folder = cache / generation
        folder.mkdir()
        raw = _read(confined(source, generation + '/index.json'), 8 * 1024**2)
        (folder / 'index.json').write_bytes(raw)
        (folder / 'index.sig').write_bytes(_read(confined(source, generation + '/index.sig'), 64))
        if hashlib.sha256(raw).hexdigest() != generation:
            raise MaintenanceError('Signed index differs from its cached generation')
        (cache / 'current.json').write_text(json.dumps(pointer), encoding='utf-8')
        yield cache, _registry(cached_index(cache), stream)


def _validate_channel(stream, entry, package):
    repository = REPOSITORY if stream == 'device' else GENERIC_REPO
    if package['source'].get('repository') != repository:
        raise MaintenanceError('Update payload source differs from its project repository')
    if stream == 'maintenance':
        if package.get('purpose') != 'maintenance' or len(package['components']) != 1:
            raise MaintenanceError('The maintenance channel only upgrades the generic runtime')
        component = package['components'][0]
        if (component['id'] != 'core:maintenance' or component['version'] != package['version']
                or component.get('hooks') or not component['files'] or
                any(item['target'] not in CORE_TARGETS and not item['target'].startswith('usr/local/lib/boiler/')
                    for item in component['files'])):
            raise MaintenanceError('Generic maintenance requires the static runtime component and its fixed targets')
    if entry.get('declared_source', package['source']) != package['source']:
        raise MaintenanceError('Signed declared source differs from the package')


def reviewed_update(root, session, stream, identity, version, *, components=None, plan=None, digest=None):
    from .models import _privileged, check_pending
    _privileged()
    device_id = _identity(stream, identity)
    engine = Engine(root, read_only=True)
    check_pending(engine)
    with _private_index(session, stream) as (cache, index):
        entry = _entry(index, identity, version)
        _transport(entry, stream)
        source = _package_path(session, stream, entry)
        raw = _read(confined(source, 'manifest.json'), MAX_MANIFEST)
        if len(raw) != entry['size'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise MaintenanceError('Package manifest differs from the signed index')
        _validate_channel(stream, entry, validate_manifest(_decode_json(raw), 'DevicePackage'))
        if plan is None:
            reviewed = updates.plan_update(root, cache, source, identity, version, components, device_id=device_id)
            if reviewed['device_warnings']:
                raise MaintenanceError('Update compatibility differs from the matched TOON model')
            return reviewed
        if (plan.get('environment', {}).get('device_id') != device_id or plan.get('device_warnings')
                or plan.get('package', {}).get('manifest_sha256') != entry['sha256']):
            raise MaintenanceError('Live model or package differs from the reviewed update')
        return updates.execute_plan(root, plan, cache, source, reviewed_sha256=digest)


def dispatch(args):
    if Path(args.root).resolve() != Path('/') or args.state_dir is not None or os.name != 'posix':
        raise MaintenanceError('Desktop project maintenance requires the live Linux root')
    if args.project_command == 'check':
        return check(args.root, args.session, offline=args.offline)
    if args.project_command == 'inventory':
        return inventory(args.root)
    if args.project_command == 'acquire':
        return acquire(args.session, args.stream, args.identity, args.version)
    if args.project_command == 'plan':
        return reviewed_update(args.root, args.session, args.stream, args.identity, args.version,
                               components=args.component)
    plan = _decode_json(_read(args.plan, MAX_MANIFEST))
    return reviewed_update(args.root, args.session, args.stream, plan['package']['id'],
                           plan['package']['version'], plan=plan, digest=args.reviewed_sha256)
