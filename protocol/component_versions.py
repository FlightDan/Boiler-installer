"""Independent component releases carried by the existing signed device index."""
from __future__ import annotations

import re

from .manifest import ProtocolError, _ID, compatible


ADAPTATION_REPOSITORY = 'FlightDan/Boiler-adaptations'
COMPONENT_RELEASE_ENGINE = '0.1.6'
_RELEASE_VERSION = re.compile(r'(\d+(?:\.\d+){1,3})(?:-([A-Za-z0-9.-]+))?(?:\+([A-Za-z0-9.-]+))?')
_RESOURCE_ID = re.compile(r'resource:[a-z0-9][a-z0-9_.-]*')
_SHA256 = re.compile(r'[0-9a-f]{64}')


def version_key(version):
    match = _RELEASE_VERSION.fullmatch(version) if isinstance(version, str) else None
    if not match:
        raise ProtocolError('Unsupported installed or candidate version')
    numbers = tuple(map(int, match[1].split('.')))
    numbers += (0,) * (4 - len(numbers))
    prerelease = tuple((0, int(part)) if part.isdigit() else (1, part) for part in match[2].split('.')) if match[2] else ()
    revision = (int(match[3].split('.')[1]),) if match[3] and re.fullmatch(r'maintenance\.\d+', match[3]) else ()
    return numbers, int(match[2] is None), prerelease, revision


def validate_model_requests(rows):
    if not isinstance(rows, list):
        raise ProtocolError('Invalid model resource references')
    requests = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) - {'id', 'default', 'version'}
                or not isinstance(row.get('id'), str) or not _RESOURCE_ID.fullmatch(row['id'])
                or ('default' in row and type(row['default']) is not bool)
                or ('version' in row and not version_key(row['version'])[1])):
            raise ProtocolError('Invalid model resource reference')
        if row['id'] in requests:
            raise ProtocolError('Duplicate model resource reference')
        requests[row['id']] = row
    return requests


def validate_release_record(record, *, source):
    release = record['component_release']
    if (not isinstance(release, dict) or set(release) != {'resource_id', 'channel'}
            or not isinstance(release['resource_id'], str) or not _RESOURCE_ID.fullmatch(release['resource_id'])
            or release['channel'] not in ('stable', 'preview')):
        raise ProtocolError('Invalid component release declaration')
    if not version_key(record['version'])[1] and release['channel'] == 'stable':
        raise ProtocolError('Prerelease component versions require the preview channel')
    if record['id'].startswith(('device:', 'bundle:')) or record['id'] == 'core:maintenance':
        raise ProtocolError('Model and maintenance bundles cannot be independent component releases')
    if (not isinstance(source, dict) or source.get('kind') != 'github'
            or source.get('repository') != ADAPTATION_REPOSITORY
            or not isinstance(source.get('commit'), str) or not re.fullmatch(r'[0-9a-f]{40}', source['commit'])):
        raise ProtocolError('Component release must declare an exact canonical adaptation commit')
    if not record.get('compatibility', {}).get('device_ids'):
        raise ProtocolError('Component release must declare supported model identities')
    minimum = record.get('minimum_engine_version')
    if (not isinstance(minimum, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', minimum)
            or version_key(minimum) < version_key(COMPONENT_RELEASE_ENGINE)):
        raise ProtocolError('Independent component releases require engine 0.1.6 or newer')
    for key in ('requires', 'conflicts'):
        values = record.get(key, [])
        if (not isinstance(values, list) or any(not isinstance(value, str) or not _ID.fullmatch(value) for value in values)
                or len(values) != len(set(values))):
            raise ProtocolError('Invalid signed component dependency data')


def validate_model_resolution(record, components=None):
    resolution = record['component_resolution']
    if not record['id'].startswith('device:') or not isinstance(resolution, dict) or not resolution:
        raise ProtocolError('Invalid model component resolution')
    for identity, row in resolution.items():
        if (not _ID.fullmatch(identity) or not isinstance(row, dict)
                or set(row) != {'version', 'resource_id', 'manifest_sha256', 'index_sha256', 'source'}
                or any(not isinstance(row[field], str) or not _SHA256.fullmatch(row[field])
                       for field in ('manifest_sha256', 'index_sha256'))):
            raise ProtocolError('Invalid model component resolution record')
        validate_release_record(dict(id=identity, version=row['version'], compatibility=record.get('compatibility', {}),
                                     minimum_engine_version=record.get('minimum_engine_version'),
                                     component_release=dict(resource_id=row['resource_id'], channel='stable')),
                                source=row['source'])
        if components is not None and components.get(identity) != row['version']:
            raise ProtocolError('Resolved component version differs from the package')


def resolution_revoked(record, revocations):
    return any(row['manifest_sha256'] in revocations for row in record.get('component_resolution', {}).values())


def validate_release_package(package):
    validate_release_record(package, source=package['source'])
    if package.get('trust', {}).get('status') == 'unsigned-development':
        raise ProtocolError('Development inputs cannot be independent component releases')
    components = package['components']
    if len(components) != 1 or (components[0]['id'], components[0]['version']) != (package['id'], package['version']):
        raise ProtocolError('Component release must contain its single exact component version')
    component = components[0]
    forbidden = {'ssd_boot_policy', 'dynamic_handler', 'cpu_mutable_config', 'retain_owned_paths'}
    if (not component.get('files') or component.get('hooks') or forbidden.intersection(component)
            or any(key in package for key in ('resources', 'maintenance_entry', 'purpose', 'legacy_capabilities',
                                             'selection_contract', 'legacy_recovery_contract', 'be201_firmware'))):
        raise ProtocolError('Independent component releases require the static file lifecycle')
    for key, allowed in component.get('compatibility', {}).items():
        package_allowed = package['compatibility'].get(key)
        if not package_allowed or not set(package_allowed) <= set(allowed):
            raise ProtocolError('Component release package must preserve its compatibility restrictions')
    minimum = component.get('minimum_engine_version')
    if minimum and version_key(package.get('minimum_engine_version', '0.0.0')) < version_key(minimum):
        raise ProtocolError('Component release package must preserve its minimum engine version')
    return component


def _matches(record, environment):
    devices = record.get('compatibility', {}).get('device_ids')
    return compatible(record, environment) and (not devices or environment.get('device_id') in devices)


def component_releases(index, identity, environment, *, resource_id=None, available_components=None):
    """Eligible versions from an already verified DeviceIndex, newest first."""
    revoked = set(index.get('revocations', ()))
    releases = []
    for entry in index['packages']:
        release = entry.get('component_release')
        if (release and entry['id'] == identity and release['channel'] == 'stable'
                and (resource_id is None or release['resource_id'] == resource_id)
                and (available_components is None or (set(entry.get('requires', ())).issubset(available_components)
                     and not set(entry.get('conflicts', ())).intersection(available_components)))
                and entry['sha256'] not in revoked and _matches(entry, environment)):
            releases.append(entry)
    releases.sort(key=lambda entry: version_key(entry['version']), reverse=True)
    for left, right in zip(releases, releases[1:]):
        if version_key(left['version']) == version_key(right['version']):
            raise ProtocolError('Ambiguous component release version: ' + identity)
    return releases


def validate_component_release(entry, package, environment):
    """Bind an integrity-checked payload to the signed entry and real target."""
    component = validate_release_package(package)
    if ((entry['id'], entry['version']) != (package['id'], package['version'])
            or entry.get('component_release') != package['component_release']
            or entry.get('declared_source') != package['source']
            or entry.get('compatibility') != package['compatibility']
            or entry.get('minimum_engine_version') != package.get('minimum_engine_version')
            or any(entry.get(field, []) != component.get(field, []) for field in ('requires', 'conflicts'))):
        raise ProtocolError('Component release differs from its signed index entry')
    if not all(_matches(record, environment) for record in (entry, package, component)):
        raise ProtocolError('Component release is incompatible with the target')
    return component
