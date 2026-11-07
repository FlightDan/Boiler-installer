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
REPOSITORY = 'FlightDan/Boiler-adaptations'
SOURCE_SUBTREE = ''
LEGACY_SOURCE = ('FlightDan/Boiler-installer', 'adaptations')
MAX_MODELS = 128
MAX_CATALOG_BYTES = 32 * 1024 * 1024
# lock.json only records hashes; the guide bytes are the exact-commit files of the
# generated adaptations snapshot, so no second copy is stored or shipped.
CATALOG_SOURCE = 'adaptations'
# Compatibility for immutable catalogs predating TOON runtime metadata only.
# New catalogs are completely data-driven and never extend this legacy table.
_LEGACY_MODELS = {
    'device:oxp3': ('guide:oxp3', 'ONEXPLAYER 3', ['ONEXPLAYER 3']),
    'device:x2-mini': ('guide:x2-mini', 'ONEXPLAYER X2 Mini / Mini PRO',
                       ['ONEXPLAYER X2 Mini', 'ONEXPLAYER X2 Mini PRO']),
}
# Compatibility API consumed by the existing legacy maintenance facades. This
# stays an executable-facade capability limit, never the display catalog. Adding
# a TOON runtime record cannot authorize a new model maintenance implementation.
MODELS = _LEGACY_MODELS
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
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ProtocolError('Invalid model JSON constant')))
    except (UnicodeError,json.JSONDecodeError,RecursionError) as exc:
        raise ProtocolError('Invalid model metadata JSON') from exc


def model_path(path):
    relative_path(path)
    if not re.fullmatch(r'brands/[a-z][a-z0-9-]*/[a-z][a-z0-9-]*/manifest\.json',path):
        raise ProtocolError('Invalid canonical model manifest path')
    return path


def _display_text(value, maximum=512):
    return (isinstance(value,str) and 0<len(value)<=maximum and value.strip()
            and not any(ord(c)<32 or 127<=ord(c)<=159 for c in value))


def runtime_record(value):
    """Validate display metadata only; no executable/trust fields are accepted."""
    if (not isinstance(value,dict) or set(value)!={'id','guide_id','manifest','name','hardware_matching'}
            or not isinstance(value['id'],str) or not re.fullmatch('device:[a-z][a-z0-9-]*',value['id'])
            or value['guide_id']!='guide:'+value['id'].split(':',1)[1]):
        raise ProtocolError('Invalid runtime model identity')
    path=model_path(value['manifest'])
    _, brand, model, _ = path.split('/')
    branded = 'device:' + brand + '-' + model
    legacy = {
        'brands/onexplayer/oxp3/manifest.json': 'device:oxp3',
        'brands/onexplayer/x2-mini/manifest.json': 'device:x2-mini',
    }
    if value['id'] != branded and value['id'] != legacy.get(path):
        raise ProtocolError('Runtime identity and model path differ')
    if not isinstance(value['name'],dict) or set(value['name'])!={'en','zh'} or not all(_display_text(v) for v in value['name'].values()):
        raise ProtocolError('Invalid bilingual model display name')
    hardware=value['hardware_matching']
    if (not isinstance(hardware,dict) or set(hardware)!={'mode','pairs'} or hardware['mode']!='advisory-exact-dmi-v1'
            or not isinstance(hardware['pairs'],list) or not 1<=len(hardware['pairs'])<=32):
        raise ProtocolError('Invalid advisory model hardware metadata')
    seen=set()
    for pair in hardware['pairs']:
        if (not isinstance(pair,dict) or set(pair)!={'manufacturer','model'}
                or not all(_display_text(v,256) for v in pair.values())):
            raise ProtocolError('Invalid advisory DMI pair')
        identity=(pair['manufacturer'],pair['model'])
        if identity in seen: raise ProtocolError('Duplicate advisory DMI pair')
        seen.add(identity)
    return value


def catalog_records(catalog):
    """One-to-one guide/display records from pinned bytes, with old-lock support."""
    if (not isinstance(catalog,dict) or catalog.get('kind')!='AdaptationSourceCatalog'
            or type(catalog.get('schema_version')) is not int or catalog['schema_version']!=1
            or catalog.get('installable') is not False or not isinstance(catalog.get('models'),list)
            or not 1<=len(catalog['models'])<=MAX_MODELS):
        raise ProtocolError('Invalid repository model catalog')
    paths=[model_path(path) for path in catalog['models']]
    if len({path.casefold() for path in paths})!=len(paths):
        raise ProtocolError('Duplicate model manifest path')
    records=catalog.get('runtime_models')
    if 'runtime_models' not in catalog:
        expected={'brands/onexplayer/'+identity.split(':',1)[1]+'/manifest.json' for identity in _LEGACY_MODELS}
        if set(paths)!=expected:
            raise ProtocolError('New model catalogs require TOON-derived runtime metadata')
        records=[]
        for identity,(guide,name,products) in _LEGACY_MODELS.items():
            records.append(dict(id=identity,guide_id=guide,manifest='brands/onexplayer/'+identity.split(':',1)[1]+'/manifest.json',
                name={'en':name,'zh':name},hardware_matching=dict(mode='advisory-exact-dmi-v1',
                    pairs=[dict(manufacturer='ONE-NETBOOK',model=product) for product in products])))
    if not isinstance(records,list) or len(records)!=len(paths):
        raise ProtocolError('Runtime metadata must match every model guide')
    identities,manifests=set(),set()
    for row in records:
        runtime_record(row)
        if row['id'] in identities or row['manifest'].casefold() in manifests:
            raise ProtocolError('Duplicate runtime model identity or path')
        identities.add(row['id']); manifests.add(row['manifest'].casefold())
    if manifests!={path.casefold() for path in paths}:
        raise ProtocolError('Runtime metadata refers to an unknown model guide')
    by_path={row['manifest']:row for row in records}
    return [by_path[path] for path in paths]


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
    if not isinstance(value,dict): raise ProtocolError('Invalid model catalog lock object')
    source = value.get('source', {})
    if (value.get('kind') != 'ModelCatalogLock' or value.get('schema_version') != 1
            or type(value.get('schema_version')) is not int or not isinstance(source,dict)
            or (source.get('repository'),source.get('subtree')) not in ((REPOSITORY,SOURCE_SUBTREE),LEGACY_SOURCE)
            or not isinstance(source.get('commit'),str) or not re.fullmatch('[0-9a-f]{40}', source['commit'])):
        raise ProtocolError('Invalid pinned model catalog lock')
    if source.get('subtree'): relative_path(source['subtree'])
    files=value.get('files')
    if not isinstance(files,list) or not 2<=len(files)<=MAX_MODELS+1 or any(not isinstance(row,dict) for row in files):
        raise ProtocolError('Unexpected model catalog source inventory')
    paths=[entry.get('path') for entry in files]
    if any(not isinstance(path,str) for path in paths) or len({path.casefold() for path in paths})!=len(paths) or paths[0]!='adaptations.json':
        raise ProtocolError('Unexpected model catalog source inventory')
    for entry in files:
        if (type(entry.get('size')) is not int or not 0 < entry['size'] <= 2 * 1024 * 1024
                or not isinstance(entry.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
            raise ProtocolError('Invalid model source digest')
        if entry['path']!='adaptations.json': model_path(entry['path'])
        confined(root/CATALOG_SOURCE, entry['path'])
    if sum(row['size'] for row in files)>MAX_CATALOG_BYTES:
        raise ProtocolError('Model catalog inventory exceeds total size limit')
    return value


def source_member(source, path):
    """Resolve a pinned member inside the explicitly recorded public subtree."""
    subtree=source['subtree']
    return (relative_path(subtree)+'/' if subtree else '') + relative_path(path)


def guide_model(value, path, source, metadata=None):
    model_path(path)
    if metadata is None:
        legacy=dict(schema_version=1,kind='AdaptationSourceCatalog',installable=False,
            models=['brands/onexplayer/'+identity.split(':',1)[1]+'/manifest.json' for identity in _LEGACY_MODELS])
        metadata=next((row for row in catalog_records(legacy) if row['manifest']==path),None)
    if metadata is not None: runtime_record(metadata)
    if (not isinstance(value,dict) or metadata is None or metadata['manifest']!=path or metadata['guide_id']!=value.get('id')
            or value.get('kind') != 'ModelInstallGuide' or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('installable') is not False):
        raise ProtocolError('Model guide cannot authorize an executable package')
    identity=metadata['id']
    if value.get('guide')!=path.removesuffix('manifest.json')+'model.md':
        raise ProtocolError('Model guide documentation path differs from its canonical model path')
    items = value.get('components')
    if not isinstance(items, list) or not 1 <= len(items) <= 128:
        raise ProtocolError('Invalid model component list')
    if any(not isinstance(item,dict) for item in items): raise ProtocolError('Invalid model component metadata')
    names=[item.get('id') for item in items]
    if any(not isinstance(name,str) or not re.fullmatch('[a-z][a-z0-9-]*',name) for name in names):
        raise ProtocolError('Invalid guide component identity')
    ids=set(names)
    if len(ids) != len(items):
        raise ProtocolError('Invalid or duplicate guide component identity')
    components = []
    for item in items:
        fingerprint_default = (item.get('default') is True and item['id'] == 'fingerprint-unlock'
            and (value['id'], path) in {
                ('guide:oxp3', 'brands/onexplayer/oxp3/manifest.json'),
                ('guide:x2-mini', 'brands/onexplayer/x2-mini/manifest.json')})
        if (not (item.get('default') is False or fingerprint_default) or item.get('recommendation') not in
                {'required', 'recommended', 'optional', 'conditional', 'pending', 'excluded'}
                or not isinstance(item.get('dependencies'), list) or any(not isinstance(dep,str) or dep not in ids for dep in item['dependencies'])
                or not isinstance(item.get('name'), str) or not isinstance(item.get('notes'), str)):
            raise ProtocolError('Invalid model option metadata')
        english_note = ('Not offered for this model.' if item['recommendation'] == 'excluded'
                        else 'Implementation is not available.' if item['recommendation'] == 'pending'
                        else 'Check hardware, software and runtime requirements in the model guide.')
        components.append(dict(item, name={'zh': item['name'], 'en': ENGLISH.get(item['id'], item['name'])},
                               notes={'zh': item['notes'], 'en': english_note}))
    guide_url = ('https://github.com/' + source['repository'] + '/blob/' + source['commit']
                 + '/' + source_member(source, value['guide']))
    return dict(id=identity, guide_id=value['id'], name=metadata['name'], guide_url=guide_url,
                components=components, installable=False,
                hardware_matching=metadata['hardware_matching'])


def _snapshot_files(root, lock):
    return {entry['path']:checked(confined(root/CATALOG_SOURCE,entry['path']).read_bytes(),entry)
            for entry in lock['files']}


def snapshot_records(root):
    root=Path(root).resolve()
    lock=read_lock(root); files=_snapshot_files(root,lock)
    records=catalog_records(files['adaptations.json'])
    if {row['manifest'] for row in records}!=set(files)-{'adaptations.json'}:
        raise ProtocolError('Catalog snapshot inventory differs from listed guides')
    for row in records: guide_model(files[row['manifest']],row['manifest'],lock['source'],row)
    return records


def model_identities(root):
    """Known display IDs constrain local-test descriptors, never authorize them.

    Retain the original two-model developer behavior for legacy retained test
    runtimes without a guide snapshot. New identities require the pinned
    snapshot; a guide ID alone cannot grant package trust.
    """
    root=Path(root).resolve()
    if not (root/'components/model-catalog/lock.json').exists():
        return set(_LEGACY_MODELS)
    return {row['id'] for row in snapshot_records(root)}


def bundled_packages(root):
    root=Path(root).resolve()
    descriptor = root/'cache/model-bundles/manifest.json'
    if not descriptor.exists():
        return []
    allowed=model_identities(root)
    value = document(confined(root, 'cache/model-bundles/manifest.json').read_bytes())
    kind = value.get('kind')
    if kind not in ('LocalTestModelBundles', 'LocalTestModelBrandBundle') or value.get('schema_version') != 1:
        raise ProtocolError('Invalid local test bundle descriptor')
    archive = None
    if kind == 'LocalTestModelBrandBundle':
        archive = value.get('archive')
        if (not isinstance(archive, dict) or archive.get('path') != 'cache/model-bundles/onexplayer.zip'
                or type(archive.get('size')) is not int or archive['size'] <= 0
                or not isinstance(archive.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', archive['sha256'])):
            raise ProtocolError('Invalid bundled brand archive anchor')
        archive = dict(archive, path=str(confined(root, archive['path'])))
        if not Path(archive['path']).is_file():
            raise ProtocolError('Bundled brand archive is missing')
    items = value.get('packages')
    if not isinstance(items, list) or len(items) > len(allowed):
        raise ProtocolError('Invalid bundled model list')
    result, identities = [], set()
    for item in items:
        if not isinstance(item,dict): raise ProtocolError('Invalid bundled model record')
        identity = item.get('id')
        if (not isinstance(identity,str) or identity not in allowed or identity in identities
                or not isinstance(item.get('manifest_sha256'),str) or not re.fullmatch('[0-9a-f]{64}', item['manifest_sha256'])
                or not isinstance(item.get('version'), str)):
            raise ProtocolError('Invalid bundled model identity or anchor')
        identities.add(identity)
        if archive is not None:
            if (type(item.get('manifest_size')) is not int or item['manifest_size'] <= 0
                    or type(item.get('files')) is not int or item['files'] < 0
                    or item.get('official_release') is not False):
                raise ProtocolError('Invalid bundled brand package metadata')
            result.append(dict(id=identity, version=item['version'], manifest_sha256=item['manifest_sha256'],
                               manifest_size=item['manifest_size'], files=item['files'],
                               official_release=False, archive=archive, trust='local-test'))
            continue
        expected_path = 'cache/model-bundles/packages/' + identity.removeprefix('device:')
        if item.get('directory') != expected_path:
            raise ProtocolError('Bundled model path differs from its fixed identity')
        directory = confined(root, expected_path)
        if not directory.is_dir():
            raise ProtocolError('Bundled model payload is missing: ' + identity)
        result.append(dict(id=identity, directory=str(directory), version=item['version'],
                           manifest_sha256=item['manifest_sha256'], trust='local-test'))
    return result


def bundled_model_package(identity, *, trust_local_test=False, root=ROOT, destination=None):
    if not trust_local_test:
        raise ProtocolError('Explicit session trust is required for a local test package')
    root = Path(root).resolve()
    item = next((item for item in bundled_packages(root) if item['id'] == identity), None)
    if item is None:
        raise ProtocolError('No bundled executable package is available for this model')
    if 'archive' in item:
        if destination is None:
            raise ProtocolError('A fresh package destination is required')
        if Path(destination).exists():
            directory = str(destination)
            package = validate_package(directory, expected_manifest_sha256=item['manifest_sha256'])
        else:
            from .brand_bundle import import_bundled_brand
            result = import_bundled_brand(item['archive'], destination, identity, item['manifest_sha256'],
                                          item['manifest_size'], item['files'], item['version'])
            package = result['package']
            directory = result['directory']
    else:
        directory = item['directory']
        package = validate_package(directory, expected_manifest_sha256=item['manifest_sha256'])
    if package['id'] != identity or package['version'] != item['version'] or package.get('purpose') == 'maintenance':
        raise ProtocolError('Bundled package does not match its model or installation purpose')
    return dict(directory=directory, manifest_sha256=item['manifest_sha256'], package=package)


def load_models(*, root=ROOT, online=True, reader=None, include_bundles=True):
    root = Path(root).resolve()
    lock = read_lock(root)
    reader = reader or remote_bytes
    files, offline = {}, not online
    if online:
        try:
            for entry in lock['files']:
                url = ('https://raw.githubusercontent.com/' + lock['source']['repository'] + '/' + lock['source']['commit']
                       + '/' + source_member(lock['source'], entry['path']))
                files[entry['path']] = checked(reader(url, entry['size']), entry)
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            if isinstance(error, ProtocolError):
                raise
            offline = True
            files.clear()
    if offline:
        files=_snapshot_files(root,lock)
    catalog = files['adaptations.json']
    records=catalog_records(catalog)
    if {row['manifest'] for row in records} != set(files) - {'adaptations.json'}:
        raise ProtocolError('Invalid repository model catalog')
    models = [guide_model(files[row['manifest']],row['manifest'],lock['source'],row) for row in records]
    return dict(source=lock['source'], offline=offline, models=models,
                bundled_packages=bundled_packages(root) if include_bundles else [])
