"""Prepare one firmware-only bundle from explicit, content-locked local inputs."""
import argparse
import json
from pathlib import Path
import sys
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'components'))
from payload_lock import checked, record, relative
from wireless import validate_bundle, VENDORS

REVISION = '2ab4ce216003ee2c2396c69e19455e4379b0e1f0'
BASE = 'https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/' + REVISION + '/'


def download(url):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=90) as response: return response.read()
        except (OSError, TimeoutError):
            if attempt == 4: raise
            time.sleep(1 + attempt * 2)


def fetch_pinned(vendor, directory):
    """Resolve a fixed upstream tree into explicit SHA256-locked local inputs.

    The Git blob identity from the immutable tree is checked before deriving
    the package SHA256. Repeated reads can resume only matching Git objects.
    """
    if vendor not in VENDORS: raise ValueError('Unknown vendor')
    directory = Path(directory)
    trees = {'intel': ['intel/iwlwifi'], 'realtek': ['rtw89', 'rtw88', 'rtlwifi'],
             'mediatek': ['mediatek', 'mediatek/mt7925', 'mediatek/mt7927', 'mediatek/mt7996', 'mediatek/mt7987', 'mediatek/mt7988']}
    licenses = {'intel': ['LICENCE.iwlwifi_firmware', 'LICENSE.intel'],
                'realtek': ['LICENCE.rtlwifi_firmware.txt'],
                'mediatek': ['LICENCE.mediatek', 'LICENCE.ralink-firmware.txt', 'LICENCE.ralink_a_mediatek_company_firmware']}
    def listing(path):
        return json.loads(download(BASE + (path + '/' if path else '') + '?format=JSON')[4:])['entries']
    objects = []
    for tree in trees[vendor]:
        for entry in listing(tree):
            if entry['mode'] != 0o100644 or entry['name'] == 'README': continue
            source = tree + '/' + entry['name']
            target = entry['name'] if vendor == 'intel' else source
            objects.append((source, target, entry['id'], False))
            # Historic kernel MODULE_FIRMWARE names retain these root aliases.
            if vendor == 'mediatek' and tree == 'mediatek' and entry['name'].startswith('mt'):
                objects.append((source, entry['name'], entry['id'], False))
    for entry in listing('LICENSES'):
        if entry['name'] in licenses[vendor]:
            objects.append(('LICENSES/' + entry['name'], 'licenses/' + entry['name'], entry['id'], True))
    root_objects = listing('')
    whence = next(entry for entry in root_objects if entry['name'] == 'WHENCE')
    objects.append(('WHENCE', 'licenses/WHENCE', whence['id'], True))
    def fetch(item):
        source, target, object_id, license_file = item
        local = directory / source
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
        return license_file, record(source, data, target=target if license_file else 'usr/lib/firmware/' + target,
                                    git_blob=object_id, url=BASE + source)
    with ThreadPoolExecutor(max_workers=3) as pool: records = list(pool.map(fetch, objects))
    lock = dict(schema=1, kind='firmware-only', vendor=vendor, version='linux-firmware-' + REVISION[:12],
                provenance=dict(url=BASE, revision=REVISION, license=', '.join(licenses[vendor])),
                files=[entry for is_license, entry in records if not is_license],
                licenses=[entry for is_license, entry in records if is_license])
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / 'lock.json'
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + '\n')
    print('Pinned upstream resolved: ' + vendor + ' (' + str(len(lock['files'])) + ' firmware files)', flush=True)
    return lock_path


def prepare(lock_path, input_root, output):
    lock = json.loads(Path(lock_path).read_text())
    if lock.get('schema') != 1 or lock.get('vendor') not in VENDORS or lock.get('kind') != 'firmware-only':
        raise ValueError('Expected a firmware-only lock')
    provenance = lock.get('provenance', {})
    if not all(isinstance(provenance.get(key), str) and provenance[key] for key in ('url', 'revision', 'license')):
        raise ValueError('Pinned upstream URL, revision and license required')
    # Validate the full lock before creating an output directory.
    files = validate_bundle(lock, input_root, lock['vendor'])
    licenses = [(item, checked(input_root, item)) for item in lock.get('licenses', [])]
    if not licenses: raise ValueError('Redistribution license files required')
    output = Path(output)
    if output.exists(): raise FileExistsError('Output exists; use a fresh preparation directory')
    output.mkdir(parents=True)
    records = []
    for target, data in sorted(files.items()):
        name = 'firmware/' + target.removeprefix('usr/lib/firmware/')
        destination = output / name; destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        records.append(record(name, data, target=target))
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
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.fetch_pinned:
        if not args.vendor or args.lock: parser.error('--fetch-pinned requires --vendor and no --lock')
        args.lock = fetch_pinned(args.vendor, args.input)
    if not args.lock: parser.error('--lock or --fetch-pinned is required')
    result = prepare(args.lock, args.input, args.output)
    print(json.dumps(dict(vendor=result['vendor'], version=result['version'], files=len(result['files']), licenses=len(result['licenses']), output=str(args.output))))
