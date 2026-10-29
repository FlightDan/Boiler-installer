"""Prepare one firmware-only bundle from explicit, content-locked local inputs."""
import argparse
import json
from pathlib import Path, PurePosixPath
import sys
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import time
import re
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'components'))
from payload_lock import checked, record, relative
from wireless import validate_bundle, VENDORS, TRANSPORTS, bluetooth_path

REVISION = '2ab4ce216003ee2c2396c69e19455e4379b0e1f0'
BASE = 'https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/' + REVISION + '/'


def bluetooth_aliases(vendor, whence, regular_ids, tree_links=None):
    """Resolve pinned link declarations inside this Bluetooth filename family."""
    links = {}
    def add(alias, target, evidence):
        if not bluetooth_path(vendor, alias): return
        relative(alias)
        if not target or '\\' in target or ':' in target or target.startswith('/'):
            raise ValueError('Unsafe Bluetooth alias target: ' + alias)
        parts = list(PurePosixPath(alias).parent.parts)
        for part in target.split('/'):
            if part in ('', '.'): continue
            if part == '..':
                if not parts: raise ValueError('Bluetooth alias escapes firmware namespace: ' + alias)
                parts.pop()
            else: parts.append(part)
        resolved = relative('/'.join(parts)).as_posix()
        if not bluetooth_path(vendor, resolved):
            raise ValueError('Bluetooth alias leaves its driver filename family: ' + alias)
        if alias in links:
            if links[alias][0] != resolved: raise ValueError('Conflicting Bluetooth alias: ' + alias)
            links[alias][1].append(evidence)
        else: links[alias] = (resolved, [evidence])
    for number, line in enumerate(whence.splitlines(), 1):
        match = re.fullmatch(r'Link:\s+(\S+)\s+->\s+(\S+)\s*', line)
        if match: add(*match.groups(), dict(source='WHENCE', line=number, declaration=line))
    for alias, (target, object_id) in (tree_links or {}).items():
        add(alias, target, dict(source=alias, git_blob=object_id, kind='git-symlink'))
    result = {}
    for alias in sorted(links):
        current, visited, evidence = alias, set(), []
        while current in links:
            if current in visited or len(visited) >= 40: raise ValueError('Bluetooth alias cycle: ' + alias)
            visited.add(current)
            current, declarations = links[current]
            evidence.extend(declarations)
        if current not in regular_ids: raise ValueError('Missing pinned Bluetooth alias destination: ' + current)
        if alias in regular_ids and regular_ids[alias] != regular_ids[current]:
            raise ValueError('Bluetooth alias conflicts with a regular Git object: ' + alias)
        result[alias] = dict(source=current, git_blob=regular_ids[current], evidence=evidence)
    return result


def download(url):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=90) as response: return response.read()
        except (OSError, TimeoutError):
            if attempt == 4: raise
            time.sleep(1 + attempt * 2)


def fetch_pinned(vendor, directory, transport='wifi'):
    """Resolve a fixed upstream tree into explicit SHA256-locked local inputs.

    The Git blob identity from the immutable tree is checked before deriving
    the package SHA256. Repeated reads can resume only matching Git objects.
    """
    if vendor not in VENDORS or transport not in TRANSPORTS: raise ValueError('Unknown vendor or transport')
    directory = Path(directory)
    trees = {'intel': ['intel/iwlwifi'], 'realtek': ['rtw89', 'rtw88', 'rtlwifi'],
             'mediatek': ['mediatek', 'mediatek/mt7925', 'mediatek/mt7927', 'mediatek/mt7996', 'mediatek/mt7987', 'mediatek/mt7988']}
    licenses = {'intel': ['LICENCE.iwlwifi_firmware', 'LICENSE.intel'],
                'realtek': ['LICENCE.rtlwifi_firmware.txt'],
                'mediatek': ['LICENCE.mediatek', 'LICENCE.ralink-firmware.txt', 'LICENCE.ralink_a_mediatek_company_firmware']}
    if transport == 'bluetooth':
        trees = {'intel': ['intel'], 'realtek': ['rtl_bt'], 'mediatek': ['mediatek', 'mediatek/mt7925']}
        licenses = {'intel': ['LICENCE.ibt_firmware'], 'realtek': ['LICENCE.rtlwifi_firmware.txt'],
                    'mediatek': ['LICENCE.mediatek']}
    def listing(path):
        return json.loads(download(BASE + (path + '/' if path else '') + '?format=JSON')[4:])['entries']
    objects = []
    regular_ids, symlink_ids = {}, {}
    for tree in trees[vendor]:
        for entry in listing(tree):
            if entry['mode'] not in (0o100644, 0o120000) or entry['name'] == 'README': continue
            source = tree + '/' + entry['name']
            if transport == 'bluetooth' and not bluetooth_path(vendor, source): continue
            if entry['mode'] == 0o120000:
                if transport == 'bluetooth': symlink_ids[source] = entry['id']
                continue
            regular_ids[source] = entry['id']
            target = entry['name'] if vendor == 'intel' and transport == 'wifi' else source
            objects.append((source, target, entry['id'], False))
            # Historic kernel MODULE_FIRMWARE names retain these root aliases.
            if transport == 'wifi' and vendor == 'mediatek' and tree == 'mediatek' and entry['name'].startswith('mt'):
                objects.append((source, entry['name'], entry['id'], False))
    found_licenses = set()
    for entry in listing('LICENSES'):
        if entry['name'] in licenses[vendor]:
            found_licenses.add(entry['name'])
            objects.append(('LICENSES/' + entry['name'], 'licenses/' + entry['name'], entry['id'], True))
    # Older Wi-Fi manifests historically carried only the notices present in
    # this upstream revision. New Bluetooth bundles require every scoped notice.
    if transport == 'bluetooth' and found_licenses != set(licenses[vendor]):
        raise ValueError('Pinned Bluetooth redistribution notice is missing')
    if not any(not item[3] for item in objects): raise ValueError('Pinned upstream contains no matching firmware')
    root_objects = listing('')
    whence = next(entry for entry in root_objects if entry['name'] == 'WHENCE')
    objects.append(('WHENCE', 'licenses/WHENCE', whence['id'], True))
    def read_blob(source, object_id):
        local = directory
        for part in ('', *relative(source).parts):
            if part: local = local / part
            if local.is_symlink() or (hasattr(local, 'is_junction') and local.is_junction()):
                raise ValueError('Pinned input path must not contain local links: ' + source)
        data = local.read_bytes() if local.is_file() and not local.is_symlink() else None
        def valid(value):
            return value is not None and hashlib.sha1(b'blob ' + str(len(value)).encode() + b'\0' + value).hexdigest() == object_id
        if not valid(data):
            # Both origins use the same immutable commit. Independently verify
            # the Git object to avoid trusting mirror content by URL alone.
            mirror = 'https://gitlab.com/kernel-firmware/linux-firmware/-/raw/' + REVISION + '/' + source
            try: data = download(mirror)
            except OSError: data = base64.b64decode(download(BASE + source + '?format=TEXT'), validate=True)
            if not valid(data): raise ValueError('Pinned Git blob mismatch: ' + source)
            local.parent.mkdir(parents=True, exist_ok=True); local.write_bytes(data)
        return data
    def fetch(item):
        source, target, object_id, license_file = item
        data = read_blob(source, object_id)
        return license_file, record(source, data, target=target if license_file else 'usr/lib/firmware/' + target,
                                    git_blob=object_id, url=BASE + source)
    with ThreadPoolExecutor(max_workers=3) as pool: records = list(pool.map(fetch, objects))
    if transport == 'bluetooth':
        # WHENCE and actual tree symlink text are Git-blob verified first. Alias
        # lock entries point to the final regular source bytes; preparation then
        # materializes every firmware destination as a regular file, never a link.
        whence_data = read_blob('WHENCE', whence['id']).decode('utf-8')
        tree_links = {source: (read_blob(source, object_id).decode('utf-8'), object_id)
                      for source, object_id in symlink_ids.items()}
        aliases = bluetooth_aliases(vendor, whence_data, regular_ids, tree_links)
        for alias, entry in aliases.items():
            if alias in regular_ids: continue
            source, object_id = entry['source'], entry['git_blob']
            data = read_blob(source, object_id)
            records.append((False, record(source, data, target='usr/lib/firmware/' + alias,
                git_blob=object_id, url=BASE + source, alias_of=source,
                alias_evidence=entry['evidence'],
                whence=dict(source='WHENCE', git_blob=whence['id'], url=BASE + 'WHENCE'))))
    lock = dict(schema=1, kind='firmware-only', vendor=vendor, transport=transport, version='linux-firmware-' + REVISION[:12],
                provenance=dict(url=BASE, revision=REVISION, license=', '.join(licenses[vendor])),
                files=[entry for is_license, entry in records if not is_license],
                licenses=[entry for is_license, entry in records if is_license])
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / 'lock.json'
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + '\n')
    print('Pinned upstream resolved: ' + transport + ':' + vendor + ' (' + str(len(lock['files'])) + ' firmware files)', flush=True)
    return lock_path


def prepare(lock_path, input_root, output):
    lock = json.loads(Path(lock_path).read_text())
    if lock.get('schema') != 1 or lock.get('vendor') not in VENDORS or lock.get('kind') != 'firmware-only':
        raise ValueError('Expected a firmware-only lock')
    provenance = lock.get('provenance', {})
    if not all(isinstance(provenance.get(key), str) and provenance[key] for key in ('url', 'revision', 'license')):
        raise ValueError('Pinned upstream URL, revision and license required')
    # Validate the full lock before creating an output directory.
    files = validate_bundle(lock, input_root, lock['vendor'], lock.get('transport', 'wifi'))
    licenses = [(item, checked(input_root, item)) for item in lock.get('licenses', [])]
    if not licenses: raise ValueError('Redistribution license files required')
    output = Path(output)
    if output.exists(): raise FileExistsError('Output exists; use a fresh preparation directory')
    output.mkdir(parents=True)
    records = []
    original_records = {item['target']: item for item in lock['files']}
    for target, data in sorted(files.items()):
        name = 'firmware/' + target.removeprefix('usr/lib/firmware/')
        destination = output / name; destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        original = original_records[target]
        evidence = {key: original[key] for key in ('git_blob', 'url', 'alias_of', 'alias_evidence', 'whence') if key in original}
        records.append(record(name, data, target=target, **evidence))
    license_records = []
    for item, data in licenses:
        name = relative(item['target']).as_posix()
        if not name.startswith('licenses/'): raise ValueError('Invalid license destination')
        destination = output / name; destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(data)
        license_records.append(record(name, data, target=name))
    bundle = dict(lock, files=records, licenses=license_records)
    (output / 'manifest.json').write_text(json.dumps(bundle, indent=2, sort_keys=True) + '\n')
    return bundle


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path)
    parser.add_argument('--fetch-pinned', action='store_true')
    parser.add_argument('--vendor', choices=VENDORS)
    parser.add_argument('--transport', choices=TRANSPORTS, default='wifi', help='Firmware family for --fetch-pinned (default: wifi)')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.fetch_pinned:
        if not args.vendor or args.lock: parser.error('--fetch-pinned requires --vendor and no --lock')
        args.lock = fetch_pinned(args.vendor, args.input, args.transport)
    if not args.lock: parser.error('--lock or --fetch-pinned is required')
    result = prepare(args.lock, args.input, args.output)
    print(json.dumps(dict(vendor=result['vendor'], version=result['version'], files=len(result['files']), licenses=len(result['licenses']), output=str(args.output))))
