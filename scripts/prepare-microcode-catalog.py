"""Generate metadata from explicitly pinned official CPU microcode sources.

No CPU code is executed or loaded. Binary bytes remain in an explicit cache;
only their locked inventory is written to the installer source tree.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
INTEL_REPOSITORY = 'intel/Intel-Linux-Processor-Microcode-Data-Files'
AMD_REPOSITORY = 'kernel-firmware/linux-firmware'
GITILES = 'https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/'
MAX_BLOB = 16 * 1024 * 1024


def git_blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Boiler-microcode-preparation'})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read(MAX_BLOB * 2 + 1)
    if len(raw) > MAX_BLOB * 2:
        raise ValueError('Microcode source response exceeds preparation bound')
    return raw


def refresh(root=ROOT, cache=None, download=fetch):
    root = Path(root)
    spec_bytes = (root / 'components/microcode-sources.json').read_bytes()
    spec = json.loads(spec_bytes)
    if (spec.get('schema') != 1 or spec.get('kind') != 'CpuMicrocodeSourceSpec'
            or set(spec.get('sources', {})) != {'intel', 'amd'} or not spec.get('version')):
        raise ValueError('Invalid CPU microcode source specification')
    cache = Path(cache) if cache is not None else None
    if cache is None:
        raise ValueError('An explicit retained microcode cache is required')
    for path in (cache, *cache.parents):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Microcode cache cannot be redirected')
    cache.mkdir(parents=True, exist_ok=True)
    components = {}
    for vendor in ('intel', 'amd'):
        origin = spec['sources'][vendor]
        revision = origin.get('revision', '')
        expected_repository = INTEL_REPOSITORY if vendor == 'intel' else AMD_REPOSITORY
        if origin.get('repository') != expected_repository or not re.fullmatch('[0-9a-f]{40}', revision):
            raise ValueError('CPU microcode requires an immutable official source')
        if vendor == 'intel':
            url = 'https://raw.githubusercontent.com/' + INTEL_REPOSITORY + '/' + revision + '/'
            api = 'https://api.github.com/repos/' + INTEL_REPOSITORY + '/contents/'
            entries = json.loads(download(api + 'intel-ucode?ref=' + revision))
            files = {}
            for entry in entries:
                if entry.get('type') != 'file' or not re.fullmatch('[0-9a-f]{2}-[0-9a-f]{2}-[0-9a-f]{2}', entry.get('name', '')):
                    raise ValueError('Unexpected Intel microcode source member')
                files['intel-ucode/' + entry['name']] = entry['sha']
            notices = {}
            for source in origin['licenses']:
                if source != 'license':
                    raise ValueError('Unexpected Intel redistribution notice')
                notices[source] = json.loads(download(api + source + '?ref=' + revision))['sha']
        else:
            url = GITILES + revision + '/'

            def listing(directory):
                raw = download(url + (directory + '/' if directory else '') + '?format=JSON')
                return json.loads(raw[4:])['entries']

            files = {}
            for entry in listing('amd-ucode'):
                name = entry.get('name', '')
                if re.fullmatch(r'microcode_amd(?:_fam[0-9a-f]{2}h)?\.bin', name):
                    if entry.get('mode') != 0o100644:
                        raise ValueError('AMD microcode source cannot be redirected')
                    files['amd-ucode/' + name] = entry['id']
            notices = {}
            for directory in ('', 'LICENSES'):
                for entry in listing(directory):
                    source = (directory + '/' if directory else '') + entry['name']
                    if source in origin['licenses']:
                        if entry.get('mode') != 0o100644:
                            raise ValueError('AMD notice cannot be redirected')
                        notices[source] = entry['id']
            if set(notices) != set(origin['licenses']):
                raise ValueError('AMD redistribution notices are incomplete')
        if not 1 <= len(files) <= 512:
            raise ValueError('Missing or excessive CPU microcode files')

        def materialize(item):
            source, object_id = item
            if not re.fullmatch('[0-9a-f]{40}', object_id):
                raise ValueError('Invalid official microcode Git object')
            path = cache / object_id
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('Microcode cache object cannot be redirected')
            if path.is_file() and path.stat().st_size > MAX_BLOB:
                raise ValueError('Cached microcode exceeds preparation bound')
            data = path.read_bytes() if path.is_file() else None
            if data is None or git_blob(data) != object_id:
                if vendor == 'intel':
                    data = download(url + source)
                else:
                    try:
                        data = base64.b64decode(download(url + source + '?format=TEXT'), validate=True)
                    except OSError:
                        data = download('https://gitlab.com/' + AMD_REPOSITORY + '/-/raw/' + revision + '/' + source)
                if len(data) > MAX_BLOB or git_blob(data) != object_id:
                    raise ValueError('Official microcode Git object mismatch: ' + source)
                path.write_bytes(data)
            target = 'usr/lib/firmware/' + source if source in files else 'licenses/' + Path(source).name
            return dict(source='microcode/' + vendor + '/' + source, original_source=source,
                        target=target, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                        git_blob=object_id, url=url + source)

        with ThreadPoolExecutor(max_workers=6) as pool:
            records = list(pool.map(materialize, sorted({**files, **notices}.items())))
        provenance = dict(repository=expected_repository, revision=revision, url=url)
        if origin.get('tag'):
            provenance['tag'] = origin['tag']
        identity = 'microcode:' + vendor
        components[identity] = dict(schema=1, kind='CpuMicrocodeBundle', id=identity, vendor=vendor,
            version=origin['version'], available=True, delivery='on-demand', runtime='not_validated',
            files=[record for record in records if record['original_source'] in files],
            licenses=[record for record in records if record['original_source'] in notices], provenance=provenance)
    result = dict(schema=1, kind='CpuMicrocodeCatalogue', version=spec['version'],
                  spec_sha256=hashlib.sha256(spec_bytes).hexdigest(), components=components)
    destination = root / 'components/microcode-firmware.json'
    if destination.is_symlink() or (hasattr(destination, 'is_junction') and destination.is_junction()):
        raise ValueError('CPU microcode metadata cannot be redirected')
    destination.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    args = parser.parse_args()
    result = refresh(cache=args.cache)
    print(json.dumps({identity: dict(files=len(bundle['files']), bytes=sum(x['size'] for x in bundle['files']))
                      for identity, bundle in result['components'].items()}))


if __name__ == '__main__':
    main()
