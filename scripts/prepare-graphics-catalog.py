"""Prepare metadata only, or explicitly refresh an editable graphics baseline."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'components'))
from graphics_catalog import load_spec, metadata, firmware_metadata, GATES, _relative


def prepare(output, root=ROOT):
    """Write a metadata-only manifest; this path never performs network I/O."""
    result = metadata(root)
    output = Path(output)
    if output.exists(): raise FileExistsError('Use a fresh graphics metadata directory')
    output.mkdir(parents=True)
    (output / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return result


def download(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as response: return response.read()
        except (OSError, TimeoutError):
            if attempt == 3: raise
            time.sleep(attempt + 1)


def git_blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()


def resolve_alias(alias, links, regular, allowed):
    """Resolve only selected aliases and keep every hop in the selected family."""
    current, seen, evidence = alias, set(), []
    while current in links:
        if current in seen or len(seen) >= 40: raise ValueError('Graphics alias cycle: ' + alias)
        seen.add(current)
        target, declarations = links[current]
        if not target or target.startswith('/') or '\\' in target or ':' in target:
            raise ValueError('Unsafe graphics alias: ' + alias)
        parts = list(PurePosixPath(current).parent.parts)
        for part in target.split('/'):
            if part in ('', '.'): continue
            if part == '..':
                if not parts: raise ValueError('Graphics alias escapes source namespace')
                parts.pop()
            else: parts.append(part)
        current = '/'.join(parts)
        _relative(current)
        if not allowed(current): raise ValueError('Graphics alias leaves selected family: ' + alias)
        evidence.extend(declarations)
    if current not in regular: raise ValueError('Missing graphics alias destination: ' + current)
    return current, evidence


def refresh(root=ROOT, cache=None, snapshot=None, fetch=download):
    """Generate byte hashes from editable family patterns and immutable Git blobs.

    Cached firmware bytes are reused only after verifying the current tree's Git
    blob identity. Trees and WHENCE are read from the requested revision, rather
    than treating the previous generated snapshot as an approval lock.
    """
    root = Path(root)
    spec, digest = load_spec(root)
    base, revision = spec['upstream_url'], spec['revision']
    cache = Path(cache or root / 'cache/graphics-baseline')
    for part in [cache, *cache.parents]:
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Graphics cache must not contain redirected directories')
    cache.mkdir(parents=True, exist_ok=True)

    def listing(directory):
        raw = fetch(base + (directory + '/' if directory else '') + '?format=JSON')
        result = json.loads(raw[4:])
        entries = result.get('entries', [])
        for entry in entries:
            if (not isinstance(entry.get('name'), str) or '/' in entry['name']
                    or not re.fullmatch(r'[0-9a-f]{40}', entry.get('id', ''))):
                raise ValueError('Invalid graphics source tree entry')
        return entries

    directories = {str(PurePosixPath(path).parent) for p in spec['profiles'].values()
                   for path in p['patterns'] + p.get('extra_paths', [])}
    regular, symlinks = {}, {}
    root_entries = listing('')
    for directory in sorted(directories | {'LICENSES'}):
        for entry in listing(directory):
            source = directory + '/' + entry['name']
            if entry.get('mode') in (0o100644, 0o100755): regular[source] = entry['id']
            elif entry.get('mode') == 0o120000: symlinks[source] = entry['id']
    whence_entry = next((e for e in root_entries if e['name'] == 'WHENCE' and e.get('mode') in (0o100644, 0o100755)), None)
    if not whence_entry: raise ValueError('Graphics source is missing WHENCE')
    regular['WHENCE'] = whence_entry['id']

    def read_blob(source, object_id):
        local = cache / object_id
        if local.is_symlink() or (hasattr(local, 'is_junction') and local.is_junction()):
            raise ValueError('Graphics cached object cannot be redirected')
        data = local.read_bytes() if local.is_file() else None
        if data is None or git_blob(data) != object_id:
            try:
                raw = fetch(base + source + '?format=TEXT')
                data = base64.b64decode(raw, validate=True)
            except (OSError, ValueError):
                data = fetch('https://gitlab.com/kernel-firmware/linux-firmware/-/raw/' + revision + '/' + source)
            if git_blob(data) != object_id: raise ValueError('Graphics Git blob mismatch: ' + source)
            local.write_bytes(data)
        return data

    whence = read_blob('WHENCE', regular['WHENCE']).decode('utf-8')
    links, notices = {}, {}
    for section in re.split(r'\n-{5,}\s*\n', whence):
        names = re.findall(r'^(?:File|Link):\s+(\S+)', section, flags=re.MULTILINE)
        license_names = sorted(set(re.findall(r'\bLICEN[CS]E\.[A-Za-z0-9._-]+', section)))
        for source in names: notices[source] = license_names
    for number, line in enumerate(whence.splitlines(), 1):
        match = re.fullmatch(r'Link:\s+(\S+)\s+->\s+(\S+)\s*', line)
        if match:
            alias, target = match.groups()
            links[alias] = (target, [dict(source='WHENCE', line=number, declaration=line)])

    def read_symlink(item):
        source, object_id = item
        return source, read_blob(source, object_id).decode('utf-8'), object_id
    # Read only symlinks that may be selected, then resolve referenced links on demand.
    def family_patterns(profile): return profile['patterns'] + profile.get('extra_paths', [])
    all_patterns = [p for profile in spec['profiles'].values() for p in family_patterns(profile)]
    selected_links = [(p, oid) for p, oid in symlinks.items() if any(fnmatch.fnmatchcase(p, pat) for pat in all_patterns)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for source, target, object_id in pool.map(read_symlink, selected_links):
            evidence = dict(source=source, git_blob=object_id, kind='git-symlink')
            if source in links and links[source][0] != target: raise ValueError('Conflicting graphics alias: ' + source)
            links.setdefault(source, (target, []))[1].append(evidence)

    components, needed, selections = {}, {'WHENCE'}, {}
    for identity, profile in spec['profiles'].items():
        patterns = family_patterns(profile)
        allowed = lambda p: any(fnmatch.fnmatchcase(p, pattern) for pattern in patterns)
        names = sorted(p for p in set(regular) | set(links) if allowed(p))
        if not names: raise ValueError('Graphics profile matched no files: ' + identity)
        for pattern in patterns:
            if not any(fnmatch.fnmatchcase(p, pattern) for p in names): raise ValueError('Graphics pattern matched no source: ' + pattern)
        selected, licenses = [], {'WHENCE'}
        for target in names:
            canonical, evidence = resolve_alias(target, links, regular, allowed)
            if target in regular and regular[target] != regular[canonical]: raise ValueError('Graphics alias conflicts with a regular source')
            selected.append((target, canonical, evidence))
            needed.add(canonical)
            declared = notices.get(canonical)
            if not declared: raise ValueError('Missing graphics redistribution notice: ' + canonical)
            for filename in declared:
                notice = 'LICENSES/' + filename
                if notice not in regular: raise ValueError('Missing graphics license source: ' + filename)
                licenses.add(notice)
                needed.add(notice)
        selections[identity] = selected, sorted(licenses)

    def get(source): return source, read_blob(source, regular[source])
    with ThreadPoolExecutor(max_workers=4) as pool: blobs = dict(pool.map(get, sorted(needed)))

    def record(source, target, canonical, evidence):
        data = blobs[canonical]
        result = dict(source=source, target=target, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                      git_blob=regular[canonical], url=base + canonical)
        if evidence: result.update(alias_of=canonical, alias_evidence=evidence)
        return result
    baseline = dict(spec_sha256=digest, version=spec['version'], revision=revision)
    for identity, (selected, licenses) in selections.items():
        prefix = 'graphics/' + identity.split(':')[1] + '/'
        files = [record(prefix + 'firmware/' + target, 'usr/lib/firmware/' + target, canonical, evidence)
                 for target, canonical, evidence in selected]
        license_records = [record(prefix + 'licenses/' + PurePosixPath(p).name,
                                  'licenses/' + PurePosixPath(p).name, p, []) for p in licenses]
        profile = spec['profiles'][identity]
        components[identity] = dict(schema=1, kind='GraphicsFirmwareBundle', id=identity, version=spec['version'],
            drivers=profile['drivers'], available=True, metadata_available=True, payload_cached=False,
            delivery='on-demand', runtime='not_validated', status='experimental-baseline',
            provenance=dict(revision=revision, url=base), baseline=baseline,
            files=files, licenses=license_records, evidence=profile['evidence'], note=profile['note'])
    result = dict(schema=1, kind='GraphicsFirmwareBaseline', spec_sha256=digest,
                  version=spec['version'], revision=revision, components=components)
    destination = Path(snapshot) if snapshot else root / 'components/graphics-firmware.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    # Synchronize only baseline references and availability; preserve hardware identity prose.
    catalog_path = root / 'components/graphics-support.json'
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    catalog['firmware_origin'] = dict(url=base, revision=revision, scope='editable-test-baseline')
    for profile in catalog['profiles']:
        profile.update(install_available=True, status='experimental-baseline', gates=GATES,
                       candidate_baseline=dict(version=spec['version'], revision=revision))
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if destination == root / 'components/graphics-firmware.json': firmware_metadata(root)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Fresh metadata-only output directory; never downloads binaries')
    parser.add_argument('--refresh', action='store_true', help='Regenerate baseline hashes from the current editable spec')
    parser.add_argument('--cache', type=Path, help='Verified Git blob cache used by --refresh')
    parser.add_argument('--snapshot', type=Path, help='Generated baseline JSON destination used by --refresh')
    args = parser.parse_args()
    if args.refresh:
        result = refresh(cache=args.cache, snapshot=args.snapshot)
        print(json.dumps(dict(profiles=len(result['components']), version=result['version'], revision=result['revision'], spec_sha256=result['spec_sha256'])))
    if args.output:
        result = prepare(args.output)
        print(json.dumps(dict(output=str(args.output), profiles=len(result['catalogue']['profiles']),
                             firmware_binary_count=0, install_available=True, sha256=result['sha256'])))
    if not args.refresh and not args.output: parser.error('Specify --output for metadata or --refresh to regenerate')
