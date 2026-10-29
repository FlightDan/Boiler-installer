"""Materialize only selected, image-missing firmware from a bound guest plan."""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'components'))
from payload_lock import checked, relative, regular
from wireless import component_identity, selected, validate_metadata, validate_record_metadata
from integration import validate_options
from protocol.manifest import _filesystem_path
import graphics
from graphics_catalog import firmware_metadata as graphics_metadata

REVISION = '2ab4ce216003ee2c2396c69e19455e4379b0e1f0'
BASE = 'https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/' + REVISION + '/'
MIRROR = 'https://gitlab.com/kernel-firmware/linux-firmware/-/raw/' + REVISION + '/'
CORE = {'core:decky', 'core:maintenance'}


def source_url(item):
    """Validate one run's upstream snapshot; future baselines may change it."""
    source = relative(item['source']).as_posix()
    if not re.fullmatch(r'[A-Za-z0-9._/-]+', source): raise ValueError('Nonportable canonical firmware URL path')
    match = re.fullmatch(r'https://kernel\.googlesource\.com/pub/scm/linux/kernel/git/firmware/linux-firmware/\+/([0-9a-f]{40})/([A-Za-z0-9._/-]+)', str(item.get('url', '')))
    if match is None or match[2] != source: raise ValueError('Firmware URL differs from fixed upstream source')
    if not isinstance(item.get('git_blob'), str) or not re.fullmatch('[0-9a-f]{40}', item['git_blob']):
        raise ValueError('Canonical Git blob required')
    return source


def fetch_bytes(item):
    source = source_url(item)
    origin = item['url'][:-len(source)]
    revision = origin.rstrip('/').rsplit('/',1)[-1]
    mirror = 'https://gitlab.com/kernel-firmware/linux-firmware/-/raw/' + revision + '/'
    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            # A CDN/login redirect must never broaden this fixed-origin policy.
            raise ValueError('Firmware origin redirects are forbidden')
    opener = urllib.request.build_opener(Redirects())
    try:
        with opener.open(mirror + source, timeout=90) as response:
            return response.read(item['size'] + 1)
    except (OSError, TimeoutError):
        # Gitiles encodes content, so bound the encoded response too.
        with opener.open(origin + source + '?format=TEXT', timeout=90) as response:
            encoded = response.read(((item['size'] + 2) // 3) * 4 + 1)
        return base64.b64decode(encoded, validate=True)


def verified(data, item):
    if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
        raise ValueError('Downloaded firmware size or SHA256 differs: ' + item['source'])
    if hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != item['git_blob']:
        raise ValueError('Downloaded firmware Git blob differs: ' + item['source'])
    return data


def safe_directory(path):
    path = Path(path).absolute()
    for entry in (path, *path.parents):
        if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
            raise ValueError('Download path cannot traverse local links')
    return path


def cached_bytes(item, cache_root, fetch):
    source_url(item)
    cache_root = _filesystem_path(safe_directory(cache_root)); cache_root.mkdir(parents=True, exist_ok=True)
    path = cache_root / item['sha256']
    if path.exists() or path.is_symlink():
        try: return verified(regular(cache_root, path.name).read_bytes(), item)
        except (ValueError, FileNotFoundError):
            if path.is_symlink() or path.is_dir(): raise ValueError('Invalid firmware content cache entry')
            path.unlink()
    data = verified(fetch(item), item)
    descriptor, temporary = tempfile.mkstemp(prefix='.download-', dir=cache_root)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return data


def _catalog_records(identity, bundle, lock_root):
    transport, vendor = identity.split(':', 1)
    lock_path = ('bluetooth/' if transport == 'bluetooth' else '') + vendor + '.json'
    lock = json.loads(regular(lock_root, lock_path).read_text())
    validate_metadata(lock, vendor, transport)
    if lock.get('provenance', {}).get('revision') != REVISION or lock.get('provenance', {}).get('url') != BASE:
        raise ValueError('Canonical lock must use the fixed firmware revision')
    for key in ('schema', 'kind', 'vendor', 'version', 'provenance'):
        if bundle.get(key) != lock.get(key): raise ValueError('Catalog differs from canonical firmware lock: ' + identity)
    prefix = 'wireless/' + ('bluetooth/' if transport == 'bluetooth' else '') + vendor + '/'
    firmware, licenses = {}, {}
    for category, destination in (('files', firmware), ('licenses', licenses)):
        for original in lock.get(category, []):
            validate_record_metadata(original); source_url(original)
            if category == 'licenses' and not original['target'].startswith('licenses/'):
                raise ValueError('Invalid canonical license destination')
            source = prefix + ('firmware/' + original['target'].removeprefix('usr/lib/firmware/')
                               if category == 'files' else original['target'])
            expected = dict(original, source=source)
            destination[original['target']] = (expected, original)
    if len(bundle.get('files', [])) != len(firmware) or len(bundle.get('licenses', [])) != len(licenses):
        raise ValueError('Catalog firmware or notice list differs from canonical lock')
    for category, destination in (('files', firmware), ('licenses', licenses)):
        targets = [item.get('target') for item in bundle.get(category, [])]
        if len(set(targets)) != len(targets) or set(targets) != set(destination):
            raise ValueError('Catalog record destinations differ from canonical lock')
        for item in bundle.get(category, []):
            if item != destination.get(item.get('target'), (None,))[0]:
                raise ValueError('Catalog record differs from canonical lock')
    return firmware, licenses


def _graphics_catalog_records(identity, bundle):
    expected = graphics_metadata(ROOT).get(identity)
    if expected is None or bundle != expected:
        raise ValueError('Graphics catalogue differs from the current adjustable baseline')
    graphics.validate_metadata(bundle, identity)
    origin = bundle['provenance']['url']
    firmware, licenses = {}, {}
    for category, destination in (('files',firmware),('licenses',licenses)):
        for entry in bundle[category]:
            if not entry['url'].startswith(origin): raise ValueError('Graphics candidate origin changed')
            original = dict(entry,source=entry['url'][len(origin):])
            source_url(original)
            destination[entry['target']] = (entry,original)
    return firmware,licenses


def download(catalog_path, plan_path, output, core_payload, cache_root, lock_root=None, fetch=None):
    """Validate first, then atomically publish one complete private payload root."""
    catalog_data = _filesystem_path(Path(catalog_path)).read_bytes()
    catalog = json.loads(catalog_data); plan_data = _filesystem_path(Path(plan_path)).read_bytes(); plan = json.loads(plan_data)
    if catalog.get('schema') != 1 or not isinstance(catalog.get('components'), dict): raise ValueError('Invalid firmware catalog')
    if plan.get('schema') != 1 or plan.get('catalog_sha256') != hashlib.sha256(catalog_data).hexdigest():
        raise ValueError('Firmware plan is not bound to this catalog')
    options = plan.get('selection')
    if not isinstance(options, dict) or options.get('schema') != 1: raise ValueError('Invalid firmware plan selection')
    validate_options(options)
    chosen = {component_identity(value) for value in selected(options)}
    gpu = graphics.selected(options)
    if gpu: chosen.add(gpu)
    choices = options.get('repairs', []) + options.get('plugins', [])
    core = set(choices) & CORE
    slot_names = set(plan.get('slots', {}))
    if slot_names != {'A', 'B'} and (chosen or slot_names): raise ValueError('Both target slots are required')
    components, canonical = catalog['components'], {}
    for identity in chosen:
        if not isinstance(components.get(identity), dict): raise ValueError('Selected firmware catalog is unavailable')
        if components[identity].get('available') is False: raise ValueError('Selected firmware catalog is unavailable')
        if identity.startswith('graphics:'):
            canonical[identity] = _graphics_catalog_records(identity, components[identity])
        else:
            transport, vendor = identity.split(':', 1)
            validate_metadata(components[identity], vendor, transport)
            canonical[identity] = _catalog_records(identity, components[identity], lock_root or ROOT / 'components/locks')
    required, license_owners = {}, ({gpu} if gpu else set())
    for slot in plan['slots'].values():
        if not isinstance(slot, dict) or not isinstance(slot.get('records'), dict) or not isinstance(slot.get('plans'), dict):
            raise ValueError('Invalid firmware slot plan')
        if set(slot['plans']) != chosen: raise ValueError('Firmware plan includes unselected or missing components')
        expected = {}
        for identity, component_plan in slot['plans'].items():
            if not isinstance(component_plan, dict) or not isinstance(component_plan.get('files'), list):
                raise ValueError('Invalid component firmware plan')
            if identity.startswith('bluetooth:') and (component_plan.get('coverage') != 'runtime_dependent' or
                                                       component_plan.get('runtime') != 'not_validated'):
                raise ValueError('Bluetooth hardware coverage must remain runtime dependent')
            if identity.startswith('graphics:') and (component_plan.get('coverage') != 'runtime_dependent' or component_plan.get('runtime') != 'not_validated'):
                raise ValueError('Graphics hardware coverage must remain runtime dependent')
            if component_plan['files']: license_owners.add(identity)
            for target in component_plan['files']:
                entry = canonical[identity][0].get(target)
                if entry is None: raise ValueError('Unknown firmware target in plan: ' + str(target))
                if target in expected: raise ValueError('Duplicate firmware owner in slot plan')
                expected[target] = entry
        if set(expected) != set(slot['records']): raise ValueError('Slot records differ from component firmware plan')
        for target, (record, original) in expected.items():
            if slot['records'][target] != record: raise ValueError('Guest firmware record differs from host catalog')
            if target in required and required[target][0] != record: raise ValueError('Conflicting target slots')
            required[target] = (record, original)
    materialize = list(required.values())
    for identity in license_owners:
        entries = canonical[identity][1]
        if not entries: raise ValueError('Firmware redistribution notices missing')
        materialize.extend(entries.values())
    # Core sources are checked before networking; no unselected component bytes
    # are opened, and no firmware bytes are read from the development full cache.
    local = {}
    for identity in core:
        bundle = components.get(identity, {})
        if bundle.get('available') is not True: raise ValueError('Selected core component unavailable')
        for category in ('files', 'licenses', 'notices'):
            for entry in bundle.get(category, []): local[entry['source']] = checked(_filesystem_path(Path(core_payload)), entry)
    display_output = safe_directory(output)
    # Keep confinement and displayed paths unchanged; only Windows filesystem
    # I/O gets the extended prefix for deeply nested retained model resources.
    output = _filesystem_path(display_output)
    if output.exists(): raise FileExistsError('Download output must be a new private directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.' + output.name + '-', dir=output.parent))
    try:
        integration = temporary / 'integration'; integration.mkdir()
        for source, data in local.items():
            destination = integration / relative(source); destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(data)
        for record, original in materialize:
            data = cached_bytes(original, cache_root, fetch or fetch_bytes)
            destination = integration / relative(record['source'])
            destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(data)
        (integration / 'manifest.json').write_bytes(catalog_data)
        (temporary / 'firmware-plan.json').write_bytes(plan_data)
        os.rename(temporary, output)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return display_output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('catalog', 'plan', 'output', 'core-payload', 'cache'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--locks', type=Path)
    args = parser.parse_args()
    result = download(args.catalog, args.plan, args.output, args.core_payload, args.cache, args.locks)
    print(json.dumps(dict(schema=1, output=str(result), status='complete')))
