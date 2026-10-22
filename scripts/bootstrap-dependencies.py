"""Prepare exact public dependencies without a sibling checkout or running setup.

Only immutable, size/hash-locked archives are inputs. Existing output files are
verified and reused; differing bytes are never silently replaced. Executable
downloads are treated as bytes, never launched during preparation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.manifest import relative_path, confined


def hash_file(path, algorithm='sha256'):
    digest = hashlib.new(algorithm)
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify(path, item):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != item['size'] or hash_file(path) != item['sha256']:
        raise ValueError('Dependency size/SHA-256 mismatch: ' + item.get('name', item.get('target', 'file')))
    if item.get('sha512') and hash_file(path, 'sha512') != item['sha512']:
        raise ValueError('Official dependency SHA-512 mismatch')


def source_file(item, cache, supplied, offline):
    path = Path(supplied) if supplied else cache/item['name']
    if path.exists() or path.is_symlink():
        verify(path, item)
        return path
    if supplied or offline:
        raise ValueError('Missing locked input: '+item['name'])
    if not item['url'].startswith('https://'):
        raise ValueError('Dependency downloads require HTTPS')
    cache.mkdir(parents=True, exist_ok=True)
    temporary = confined(cache, item['name']+'.partial')
    with urllib.request.urlopen(item['url'], timeout=60) as response, temporary.open('xb') as output:
        total = 0
        while block := response.read(1024*1024):
            total += len(block)
            if total > item['size']:
                raise ValueError('Dependency exceeded locked size; partial retained')
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    verify(temporary, item)
    temporary.rename(path)
    return path


def archive_members(archive):
    entries = {}
    for info in archive.infolist():
        name = info.filename.rstrip('/')
        relative_path(name)
        if name.casefold() in entries:
            raise ValueError('Archive contains duplicate paths')
        mode = info.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError('Dependency archive contains a link or special file')
        entries[name.casefold()] = info
    return entries


def install_member(archive, info, root, entry, check_only):
    target = confined(root, entry['target'])
    if target.exists() or target.is_symlink():
        verify(target, entry)
        return False
    if check_only:
        raise ValueError('Prepared dependency is missing: '+entry['target'])
    target.parent.mkdir(parents=True, exist_ok=True)
    confined(root, entry['target'])
    descriptor, name = tempfile.mkstemp(prefix='.boiler-dependency-', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as output, archive.open(info) as source:
            digest, size = hashlib.sha256(), 0
            while block := source.read(1024*1024):
                size += len(block)
                if size > entry['size']:
                    raise ValueError('Dependency archive member exceeded locked size')
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if size != entry['size'] or digest.hexdigest() != entry['sha256']:
            raise ValueError('Dependency archive member hash mismatch')
        if target.exists() or target.is_symlink():
            raise ValueError('Dependency destination appeared during preparation')
        temporary.rename(target)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def prepare(root, profile, *, public_installer=None, dotnet_sdk=None, cache=None, offline=False, check_only=False, lock_path=None):
    root = Path(root).resolve()
    if check_only:
        if not root.is_dir(): raise ValueError('Prepared dependency root is missing')
    else: root.mkdir(parents=True, exist_ok=True)
    lock_path = Path(lock_path) if lock_path else Path(__file__).with_name('runtime-dependencies.lock.json')
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    if lock.get('schema_version') != 1 or lock.get('kind') != 'BoilerDependencyLock':
        raise ValueError('Unsupported dependency lock')
    groups = set(lock['profiles'][profile])
    cache = Path(cache) if cache else root/'cache/bootstrap'
    supplied = {'audited-public-runtime': public_installer, 'dotnet-sdk': dotnet_sdk}
    report = {'schema_version': 1, 'profile': profile, 'lock_sha256': hash_file(lock_path), 'versions': lock['versions'],
              'mode': 'verified-only' if check_only else 'prepared', 'files': [], 'created': 0, 'archives': []}
    for item in lock['archives']:
        members = [entry for entry in item.get('members', []) if entry['group'] in groups]
        sdk = item.get('group') == 'dotnet-sdk' and 'dotnet-sdk' in groups
        if not members and not sdk:
            continue
        source = source_file(item, cache, supplied.get(item['id']), offline or check_only)
        report['archives'].append({key: item[key] for key in ('id', 'name', 'url', 'size', 'sha256')})
        if item.get('format') == 'raw':
            if len(members) != 1 or any(members[0][key] != item[key] for key in ('size', 'sha256')):
                raise ValueError('Raw dependency must have one identical locked output')
            entry = members[0]
            relative_path(entry['target'])
            class RawSource:
                def open(self, unused):
                    return source.open('rb')
            report['created'] += install_member(RawSource(), None, root, entry, check_only)
            report['files'].append({key: entry[key] for key in ('target', 'size', 'sha256', 'group')})
            continue
        with zipfile.ZipFile(source) as archive:
            entries = archive_members(archive)
            if sdk:
                # The SDK archive itself is authenticated by both official
                # SHA-512 and the committed SHA-256. Hash every output member
                # rather than assuming an installed SDK tree is authoritative.
                members = []
                for name, info in entries.items():
                    if info.is_dir():
                        continue
                    digest = hashlib.sha256()
                    with archive.open(info) as stream:
                        for block in iter(lambda: stream.read(1024*1024), b''):
                            digest.update(block)
                    members.append({'member': info.filename, 'target': item['target_prefix']+info.filename,
                                    'size': info.file_size, 'sha256': digest.hexdigest(), 'group': 'dotnet-sdk'})
            if len(members) > 20000 or sum(entry['size'] for entry in members) > 4*1024**3:
                raise ValueError('Dependency extraction exceeds limits')
            for entry in sorted(members, key=lambda entry: entry['target']):
                relative_path(entry['member']); relative_path(entry['target'])
                info = entries.get(entry['member'].casefold())
                if info is None or info.is_dir() or info.filename != entry['member'] or info.file_size != entry['size']:
                    raise ValueError('Locked dependency member is absent or ambiguous')
                report['created'] += install_member(archive, info, root, entry, check_only)
                report['files'].append({key: entry[key] for key in ('target', 'size', 'sha256', 'group')})
    # SDK and runtime archives intentionally overlap. Record one output path
    # once; both archive anchors remain in provenance above.
    unique = {}
    for entry in report['files']:
        previous = unique.get(entry['target'])
        if previous and (previous['size'], previous['sha256']) != (entry['size'], entry['sha256']):
            raise ValueError('Dependency archives disagree about a shared output')
        unique.setdefault(entry['target'], entry)
    report['files'] = [unique[name] for name in sorted(unique)]
    # This report is evidence, not a substitute for the immutable source lock.
    if not check_only:
        directory = root/'runtime/dependencies'
        directory.mkdir(parents=True, exist_ok=True)
        target = confined(root, 'runtime/dependencies/preparation-'+profile+'.json')
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--profile', choices=('runtime', 'build', 'all', 'guest', 'refind'), default='all')
    parser.add_argument('--public-installer', type=Path)
    parser.add_argument('--dotnet-sdk', type=Path)
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    value = prepare(args.root, args.profile, public_installer=args.public_installer, dotnet_sdk=args.dotnet_sdk,
                    cache=args.cache, offline=args.offline, check_only=args.check_only)
    print(json.dumps({'profile': value['profile'], 'mode': value['mode'], 'files': len(value['files']), 'created': value['created'], 'lock_sha256': value['lock_sha256']}, indent=2))


if __name__ == '__main__':
    main()
