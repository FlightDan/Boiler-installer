"""Pinned model guides are display metadata; executable packages stay separate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.error import URLError
from urllib.request import Request, urlopen

from .manifest import ProtocolError, confined, relative_path, validate_package

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'FlightDan/Boiler-installer'
SOURCE_SUBTREE = 'adaptations'
MODELS = {
    'device:oxp3': ('guide:oxp3', 'ONEXPLAYER 3', ['ONEXPLAYER 3']),
    'device:x2-mini': ('guide:x2-mini', 'ONEXPLAYER X2 Mini / Mini PRO',
                       ['ONEXPLAYER X2 Mini', 'ONEXPLAYER X2 Mini PRO']),
}
ENGLISH = {
    'base-system': 'SteamOS, firmware and maintenance runtime', 'input': 'Back buttons / MCU input',
    'multifunction': 'Multifunction key', 'gyro': 'BMI260 gyroscope',
    'desktop-hdr': 'KDE desktop HDR', 'hdr': 'Game mode HDR', 'gm7-resume': 'GM7 suspend recovery',
    'split-lock': 'Split-lock report workaround', 'wifi': 'Wireless firmware',
    'overlay': 'Power and PVRAM overlay', 'manager': 'SteamOS Manager fixes', 'native-tdp': 'Native TDP',
    'pl4-175w': 'PL4 175W (implementation pending)', 'decky': 'Decky Loader',
    'slot-switch': 'KDE slot switch / OS recovery', 'partition-mount': 'KDE persistent partition mounts',
    'gamescope-env': 'Game mode environment isolation', 'keyboard': 'KDE touch keyboard',
    'flathub-ustc': 'USTC Flathub mirror', 'refind': 'rEFInd boot manager',
    'windows-reboot': 'Restart into Windows', 'opti2xmx': 'Opti2XMX', 'waydroid': 'Waydroid',
    'lsfg-vk': 'LSFG-VK', 'xe-dsb': 'OXP3 display DSB workaround',
    'fan-driver': 'OXP3 fan and charging driver', 'oxp3-fan': 'OXP3 Fan',
    'charging': 'OXP3 charging controls', 'oxp3-light': 'OXP3 Light', 'oxp3-cpu': 'OXP3 CPU controls',
    'legacy-runtime': 'OXP3 volume and battery runtime',
}


def document(data):
    if len(data) > 2 * 1024 * 1024:
        raise ProtocolError('Model guide exceeds size limit')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProtocolError('Duplicate model metadata field')
            result[key] = value
        return result
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs)


def checked(data, expected):
    if len(data) != expected['size'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
        raise ProtocolError('Model catalog differs from its pinned source: ' + expected['path'])
    return document(data)


def remote_bytes(url, maximum):
    request = Request(url, headers={'User-Agent': 'Boiler-installer/0.1', 'Accept': 'application/json'})
    with urlopen(request, timeout=4) as response:
        if not response.url.startswith('https://'):
            raise ProtocolError('Model source redirected away from HTTPS')
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ProtocolError('Model metadata exceeds its pinned size')
    return data


def read_lock(root):
    value = document(confined(root, 'components/model-catalog/lock.json').read_bytes())
    source = value.get('source', {})
    if (value.get('kind') != 'ModelCatalogLock' or value.get('schema_version') != 1
            or source.get('repository') != REPOSITORY
            or not re.fullmatch('[0-9a-f]{40}', source.get('commit', ''))):
        raise ProtocolError('Invalid pinned model catalog lock')
    relative_path(source.get('subtree'))
    paths = [entry['path'] for entry in value['files']]
    if len(paths) != len(set(paths)) or len(paths) != 3 or paths[0] != 'adaptations.json':
        raise ProtocolError('Unexpected model catalog source inventory')
    for entry in value['files']:
        if (type(entry.get('size')) is not int or not 0 < entry['size'] <= 2 * 1024 * 1024
                or not re.fullmatch('[0-9a-f]{64}', entry.get('sha256', ''))):
            raise ProtocolError('Invalid model source digest')
        confined(root/'components/model-catalog/snapshot', entry['path'])
    return value


def source_member(source, path):
    """Resolve a pinned member inside the explicitly recorded public subtree."""
    return relative_path(source['subtree']) + '/' + relative_path(path)


def guide_model(value, path, source):
    identity = next((identity for identity, (guide, _, _) in MODELS.items() if guide == value.get('id')), None)
    if (identity is None or value.get('kind') != 'ModelInstallGuide' or value.get('schema_version') != 1
            or value.get('installable') is not False):
        raise ProtocolError('Model guide cannot authorize an executable package')
    items = value.get('components')
    if not isinstance(items, list) or not 1 <= len(items) <= 128:
        raise ProtocolError('Invalid model component list')
    ids = {item.get('id') for item in items}
    if len(ids) != len(items) or any(not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9-]*', name) for name in ids):
        raise ProtocolError('Invalid or duplicate guide component identity')
    components = []
    for item in items:
        if (item.get('default') is not False or item.get('recommendation') not in
                {'required', 'recommended', 'optional', 'conditional', 'pending', 'excluded'}
                or not isinstance(item.get('dependencies'), list) or any(dep not in ids for dep in item['dependencies'])
                or not isinstance(item.get('name'), str) or not isinstance(item.get('notes'), str)):
            raise ProtocolError('Invalid model option metadata')
        english_note = ('Not offered for this model.' if item['recommendation'] == 'excluded'
                        else 'Implementation is not available.' if item['recommendation'] == 'pending'
                        else 'Check hardware, software and runtime requirements in the model guide.')
        components.append(dict(item, name={'zh': item['name'], 'en': ENGLISH.get(item['id'], item['name'])},
                               notes={'zh': item['notes'], 'en': english_note}))
    _, name, products = MODELS[identity]
    guide_url = ('https://github.com/' + source['repository'] + '/blob/' + source['commit']
                 + '/' + source_member(source, value['guide']))
    return dict(id=identity, guide_id=value['id'], name={'zh': name, 'en': name}, guide_url=guide_url,
                components=components, installable=False,
                hardware_matching={'mode': 'advisory-exact-dmi-v1',
                                   'pairs': [{'manufacturer': 'ONE-NETBOOK', 'model': product} for product in products]})


def bundled_packages(root):
    descriptor = root/'cache/model-bundles/manifest.json'
    if not descriptor.exists():
        return []
    value = document(confined(root, 'cache/model-bundles/manifest.json').read_bytes())
    if value.get('kind') != 'LocalTestModelBundles' or value.get('schema_version') != 1:
        raise ProtocolError('Invalid local test bundle descriptor')
    items = value.get('packages')
    if not isinstance(items, list) or len(items) > len(MODELS):
        raise ProtocolError('Invalid bundled model list')
    result, identities = [], set()
    for item in items:
        identity = item.get('id')
        if (identity not in MODELS or identity in identities
                or not re.fullmatch('[0-9a-f]{64}', item.get('manifest_sha256', ''))
                or not isinstance(item.get('version'), str)):
            raise ProtocolError('Invalid bundled model identity or anchor')
        identities.add(identity)
        expected_path = 'cache/model-bundles/packages/' + identity.removeprefix('device:')
        if item.get('directory') != expected_path:
            raise ProtocolError('Bundled model path differs from its fixed identity')
        directory = confined(root, expected_path)
        if not directory.is_dir():
            raise ProtocolError('Bundled model payload is missing: ' + identity)
        result.append(dict(id=identity, directory=str(directory), version=item['version'],
                           manifest_sha256=item['manifest_sha256'], trust='local-test'))
    return result


def bundled_model_package(identity, *, trust_local_test=False, root=ROOT):
    if not trust_local_test:
        raise ProtocolError('Explicit session trust is required for a local test package')
    root = Path(root).resolve()
    item = next((item for item in bundled_packages(root) if item['id'] == identity), None)
    if item is None:
        raise ProtocolError('No bundled executable package is available for this model')
    package = validate_package(item['directory'], expected_manifest_sha256=item['manifest_sha256'])
    if package['id'] != identity or package['version'] != item['version'] or package.get('purpose') == 'maintenance':
        raise ProtocolError('Bundled package does not match its model or installation purpose')
    return dict(directory=item['directory'], manifest_sha256=item['manifest_sha256'], package=package)


def load_models(*, root=ROOT, online=True, reader=None, include_bundles=True):
    root = Path(root).resolve()
    lock = read_lock(root)
    reader = reader or remote_bytes
    files, offline = {}, not online
    if online:
        try:
            for entry in lock['files']:
                url = ('https://raw.githubusercontent.com/' + REPOSITORY + '/' + lock['source']['commit']
                       + '/' + source_member(lock['source'], entry['path']))
                files[entry['path']] = checked(reader(url, entry['size']), entry)
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            if isinstance(error, ProtocolError):
                raise
            offline = True
            files.clear()
    if offline:
        for entry in lock['files']:
            path = confined(root/'components/model-catalog/snapshot', entry['path'])
            files[entry['path']] = checked(path.read_bytes(), entry)
    catalog = files['adaptations.json']
    if (catalog.get('kind') != 'AdaptationSourceCatalog' or catalog.get('schema_version') != 1
            or catalog.get('installable') is not False
            or not isinstance(catalog.get('models'), list)
            or set(catalog['models']) != set(files) - {'adaptations.json'}):
        raise ProtocolError('Invalid repository model catalog')
    models = [guide_model(files[path], path, lock['source']) for path in catalog['models']]
    if {item['id'] for item in models} != set(MODELS):
        raise ProtocolError('Pinned catalog does not contain the expected models')
    return dict(source=lock['source'], offline=offline, models=models,
                bundled_packages=bundled_packages(root) if include_bundles else [])
