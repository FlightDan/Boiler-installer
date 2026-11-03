"""Build a local index or an unsigned, immutable release index candidate."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE = 8*1024**3
IDENTITY = re.compile(r'[a-z][a-z0-9_-]*:[a-z0-9][a-z0-9_.-]*\Z')
VERSION = re.compile(r'[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.+-]+)?\Z')


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()


def hardware_advice(value):
    if (not isinstance(value, dict) or set(value) != {'mode', 'pairs'}
            or value['mode'] != 'advisory-exact-dmi-v1' or not isinstance(value['pairs'], list)
            or not 1 <= len(value['pairs']) <= 32): raise ValueError('Invalid advisory hardware matching')
    for pair in value['pairs']:
        if not isinstance(pair, dict) or set(pair) != {'manufacturer', 'model'}: raise ValueError('Invalid advisory DMI pair')
        for field in pair.values():
            if (not isinstance(field, str) or not 1 <= len(field) <= 256 or not field.strip()
                    or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in field)):
                raise ValueError('Invalid advisory DMI value')
    return value


def document(raw):
    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8') if isinstance(raw,bytes) else raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))


def relative(name):
    if (not isinstance(name, str) or not name or len(name) > 4096 or '\\' in name or ':' in name or name.startswith('/')
            or any(part in ('', '.', '..') or part.endswith(('.', ' ')) for part in name.split('/')) or any(ord(c) < 32 for c in name)):
        raise ValueError('Unsafe inventory path')
    reserved = {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}
    if any(part.split('.')[0].upper() in reserved for part in name.split('/')):
        raise ValueError('Reserved inventory path')
    return name


@contextmanager
def regular_file(path):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Package input must be a regular file')
        yield stream


def verify_record(path, expected):
    with regular_file(path) as stream:
        if os.fstat(stream.fileno()).st_size != expected['size']:
            raise ValueError('Package inventory mismatch')
        checksum, count = hashlib.sha256(), 0
        while block := stream.read(min(1024*1024, expected['size'] + 1 - count)):
            count += len(block); checksum.update(block)
            if count > expected['size']:
                raise ValueError('Package inventory mismatch')
        if count != expected['size'] or checksum.hexdigest() != expected['sha256']:
            raise ValueError('Package inventory mismatch')


def validate_contract(manifest):
    """Standalone v1 preflight matching the public package intake contract."""
    def header(value, kind):
        if (not isinstance(value,dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1
                or value.get('kind') != kind or not isinstance(value.get('id'),str) or not IDENTITY.fullmatch(value['id'])
                or not isinstance(value.get('version'),str) or not VERSION.fullmatch(value['version'])):
            raise ValueError('Unsupported manifest identity or schema')
        compatibility = value.get('compatibility',{})
        if not isinstance(compatibility,dict): raise ValueError('Invalid compatibility')
        for key, entries in compatibility.items():
            if (key not in ('architectures','os_ids','device_ids','os_versions','kernels') or not isinstance(entries,list)
                    or not entries or any(not isinstance(s,str) or not s for s in entries)):
                raise ValueError('Invalid compatibility')
        if 'minimum_engine_version' in value and not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',str(value['minimum_engine_version'])):
            raise ValueError('Invalid minimum engine version')
    def entries(value, name):
        result = value.get(name,[])
        if not isinstance(result,list) or len(result)>100000: raise ValueError('Invalid manifest list')
        return result
    def record(value):
        if (not isinstance(value,dict) or type(value.get('size')) is not int or not 0 <= value['size'] <= MAX_ARCHIVE
                or not isinstance(value.get('sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',value['sha256'])):
            raise ValueError('Invalid file inventory')
    header(manifest,'DevicePackage')
    if 'files' not in manifest or 'components' not in manifest: raise ValueError('Missing package inventory')
    inventory, seen = {}, set()
    for item in entries(manifest,'files'):
        record(item); name = relative(item.get('path'))
        if name.casefold() in seen: raise ValueError('Duplicate inventory path')
        seen.add(name.casefold()); inventory[name]=item
    if sum(item['size'] for item in inventory.values()) > MAX_ARCHIVE: raise ValueError('Package inventory exceeds size limit')
    maintenance = manifest.get('maintenance_entry')
    if 'maintenance_entry' in manifest:
        if isinstance(maintenance,str):
            relative(maintenance)
            if not maintenance.startswith('scripts/') or not maintenance.endswith('.sh') or maintenance not in inventory:
                raise ValueError('Legacy maintenance entry absent or unsupported')
        elif maintenance != {'kind':'engine-hooks-v1'}: raise ValueError('Unsupported maintenance entry')
    ids = set()
    for component in entries(manifest,'components'):
        header(component,'ComponentManifest')
        if component['id'] in ids: raise ValueError('Duplicate component')
        ids.add(component['id'])
        for key in ('requires','conflicts'):
            values = entries(component,key)
            if any(not isinstance(v,str) or not IDENTITY.fullmatch(v) for v in values) or len(set(values)) != len(values):
                raise ValueError('Invalid component dependency')
        targets=set()
        for item in entries(component,'files'):
            record(item); source=relative(item.get('source')); target=relative(item.get('target'))
            if target.casefold() in targets or type(item.get('mode',420)) is not int or not 0 <= item.get('mode',420) <= 0o777:
                raise ValueError('Duplicate target or invalid file mode')
            targets.add(target.casefold())
            if source not in inventory or any(item[key] != inventory[source][key] for key in ('size','sha256')):
                raise ValueError('Component source absent or differs from inventory')
        hooks=component.get('hooks',{})
        if not isinstance(hooks,dict): raise ValueError('Invalid hooks')
        for phase,path in hooks.items():
            relative(path)
            if phase not in ('apply','verify','restore') or not path.startswith('scripts/') or not path.endswith('.sh') or path not in inventory:
                raise ValueError('Hook absent from inventory or unsupported')
    source=manifest.get('source',{})
    if not isinstance(source,dict): raise ValueError('Invalid package source')
    if source.get('kind') == 'github':
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',source.get('repository','')) or not re.fullmatch(r'[0-9a-f]{40}',source.get('commit','')):
            raise ValueError('GitHub source requires a pinned full commit')
    elif source.get('kind') == 'local':
        if not isinstance(source.get('path',source.get('repository')),str) or not source.get('path',source.get('repository')):
            raise ValueError('Invalid local source')
        if 'base_commit' in source and not re.fullmatch(r'[0-9a-f]{40}',source['base_commit']): raise ValueError('Invalid local source commit')
    elif source.get('kind') == 'https':
        record(source)
        if not source.get('url','').startswith('https://'): raise ValueError('Invalid HTTPS source')
    else: raise ValueError('Unsupported package source')
    return inventory


def committed_records():
    """Release metadata comes from exact committed public registry bytes."""
    def git(*args):
        return subprocess.check_output(['git', '-C', str(ROOT), *args], stderr=subprocess.PIPE)
    if Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve() != ROOT.resolve():
        raise ValueError('Release candidates require an independent public registry checkout')
    origin = git('remote', 'get-url', 'origin').decode().strip()
    if origin not in ('https://github.com/FlightDan/Boiler-devices', 'https://github.com/FlightDan/Boiler-devices.git',
                      'git@github.com:FlightDan/Boiler-devices.git'):
        raise ValueError('Unexpected public registry origin')
    commit = git('rev-parse', 'HEAD').decode().strip()
    records = []
    for name in ('device-index.json', 'devices/oxp3.json'):
        raw = git('show', commit+':'+name)
        path = ROOT/name
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError('Registry release metadata differs from committed bytes')
        records.append(document(raw))
    return records


def asset_source(url, repository):
    if (not isinstance(url, str) or not url.startswith('https://github.com/')
            or any(ord(c) <= 32 or ord(c) >= 127 for c in url) or re.search(r'%(?![0-9A-Fa-f]{2})', url)):
        raise ValueError('Release asset URL must be canonical encoded HTTPS')
    parsed = urlsplit(url)
    parts = parsed.path.split('/')
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.username or parsed.password
            or parsed.query or parsed.fragment or len(parts) != 7 or parts[1:3] != repository.split('/')
            or parts[3:5] != ['releases', 'download']):
        raise ValueError('Use a credential-free fixed GitHub release asset URL in the model repository')
    for value in parts[5:]:
        decoded = unquote(value)
        if decoded in ('', '.', '..', 'latest') or '/' in decoded or '\\' in decoded or any(ord(c) < 32 for c in decoded):
            raise ValueError('Release asset URL must use an explicit tag and filename')
    if not unquote(parts[-1]).endswith('.zip'):
        raise ValueError('The model release asset must be a ZIP')
    return {'kind':'https', 'url':url, 'repository':repository}


def archive_record(path, raw_manifest, inventory):
    """Freeze the bytes that are hashed and inspected; never parse a reopened path."""
    if path.is_symlink():
        raise ValueError('Archive links are forbidden')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as original, tempfile.TemporaryFile('w+b') as snapshot:
        info = os.fstat(original.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ARCHIVE:
            raise ValueError('Invalid release archive')
        sha, size = hashlib.sha256(), 0
        while block := original.read(min(1024*1024, MAX_ARCHIVE + 1 - size)):
            size += len(block)
            if size > MAX_ARCHIVE:
                raise ValueError('Release archive exceeds size limit')
            sha.update(block); snapshot.write(block)
        if size != info.st_size:
            raise ValueError('Release archive changed while copied')
        snapshot.seek(0)
        expected = dict(inventory, **{'manifest.json':{'size':len(raw_manifest), 'sha256':hashlib.sha256(raw_manifest).hexdigest()}})
        with zipfile.ZipFile(snapshot) as archive:
            seen, actual = set(), set()
            if len(archive.infolist()) > 100000:
                raise ValueError('ZIP inventory exceeds file count limit')
            for item in archive.infolist():
                name = relative(item.filename.rstrip('/'))
                mode = item.external_attr >> 16
                if name.casefold() in seen or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise ValueError('Duplicate archive entry or archive link')
                seen.add(name.casefold())
                if item.is_dir():
                    continue
                if name not in expected or item.file_size != expected[name]['size']:
                    raise ValueError('ZIP inventory differs from the package')
                checksum, count = hashlib.sha256(), 0
                with archive.open(item) as stream:
                    while block := stream.read(1024*1024):
                        count += len(block)
                        if count > expected[name]['size']:
                            raise ValueError('ZIP content exceeds its inventory')
                        checksum.update(block)
                if count != expected[name]['size'] or checksum.hexdigest() != expected[name]['sha256']:
                    raise ValueError('ZIP content differs from the package')
                actual.add(name)
            if actual != set(expected):
                raise ValueError('ZIP inventory differs from the package')
        return {'size':size, 'sha256':sha.hexdigest()}


def build(package, output, *, archive=None, asset_url=None, sequence=None, previous_index=None):
    package, output = Path(package).resolve(), Path(output)
    if output.exists():
        raise ValueError('Index destination already exists')
    manifest_path = package/'manifest.json'
    if manifest_path.is_symlink():
        raise ValueError('Manifest cannot be a symlink')
    with regular_file(manifest_path) as stream:
        raw_manifest = stream.read(8*1024**2+1)
    if len(raw_manifest) > 8*1024**2:
        raise ValueError('Manifest exceeds size limit')
    manifest = document(raw_manifest)
    inventory = validate_contract(manifest)
    actual = set()
    for path in package.rglob('*'):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Package links are forbidden')
        if path.is_file():
            actual.add(path.relative_to(package).as_posix())
    for name,entry in inventory.items():
        verify_record(package/name,entry)
    if actual != {'manifest.json', *inventory}:
        raise ValueError('Uninventoried package content')
    release = archive is not None or asset_url is not None or sequence is not None or previous_index is not None
    if release:
        if archive is None or asset_url is None or type(sequence) is not int or sequence <= 0:
            raise ValueError('Release candidate requires ZIP, fixed asset URL and positive explicit sequence')
        index, device = committed_records()
        baseline = index['sequence']
        if previous_index is not None:
            previous = document(Path(previous_index).read_bytes())
            if (previous.get('kind') != 'DeviceIndex' or previous.get('id') != index['id']
                    or type(previous.get('sequence')) is not int or previous['sequence'] < baseline):
                raise ValueError('Invalid previous verified index baseline')
            baseline = previous['sequence']
            if not set(previous.get('revocations', [])).issubset(index.get('revocations', [])):
                raise ValueError('Committed registry omits previous revocations')
        if sequence <= baseline:
            raise ValueError('Index sequence must increase monotonically')
        if hashlib.sha256(raw_manifest).hexdigest() in index.get('revocations', []):
            raise ValueError('Revoked package cannot enter a release candidate')
        source = manifest.get('source', {})
        if (source.get('kind') != 'github' or source.get('repository') != device['repository']
                or not re.fullmatch(r'[0-9a-f]{40}', source.get('commit', ''))):
            raise ValueError('Release package requires the actual pinned GitHub model source commit')
        if manifest.get('id') != device['id'] or device.get('package_status') != 'experimental':
            raise ValueError('Only the recorded experimental model package can enter this candidate')
        transport = asset_source(asset_url, source['repository'])
        transport.update(archive_record(Path(archive), raw_manifest, {item['path']:item for item in manifest['files']}))
        index['sequence'] = sequence
        index['trust'] = {'status':'index-signature-required', 'official_release':False}
        index['note'] = 'Unsigned release candidate; authenticate these exact bytes only after asset publication and required acceptance gates.'
    else:
        index = document((ROOT/'device-index.json').read_bytes())
    index['generated_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    index['packages'] = [{
        'id': manifest['id'], 'version': manifest['version'],
        'source': {'kind': 'local', 'path': Path(os.path.relpath(package, output.parent.resolve())).as_posix(), 'repository': manifest['source'].get('repository', '')},
        'sha256': hashlib.sha256(raw_manifest).hexdigest(), 'size': len(raw_manifest),
        'compatibility': manifest['compatibility'], 'status': 'experimental',
        'notes': ['Unsigned local snapshot; requires an explicit development trust grant.']
    }]
    if release:
        entry = index['packages'][0]
        entry.update(source=transport, name=device['name'], status='experimental', known_issues=device.get('known_issues', []),
                     notes=device.get('notes', []) + ['Artifact inventory verification only; this unsigned candidate does not certify first boot, hardware or live model maintenance.'],
                     compatibility_records=device['compatibility_records'], verified_at=index['generated_at'],
                     verification_scope='local-manifest-and-archive-inventory',
                     source_url='https://github.com/'+source['repository']+'/tree/'+source['commit']+'/boiler-device',
                     declared_source=source)
        if 'hardware_matching' in device:
            entry['hardware_matching'] = hardware_advice(device['hardware_matching'])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(index, ensure_ascii=False, indent=2)+'\n')
    return index


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--archive', type=Path, help='Exact model release ZIP; enables unsigned release-candidate mode')
    parser.add_argument('--asset-url', help='Fixed credential-free GitHub model release asset URL')
    parser.add_argument('--sequence', type=int, help='Explicit positive sequence greater than the last verified index')
    parser.add_argument('--previous-index', type=Path, help='Optional previously verified public index; release operator verifies its signature separately')
    args = parser.parse_args()
    value = build(args.package, args.output, archive=args.archive, asset_url=args.asset_url, sequence=args.sequence, previous_index=args.previous_index)
    print(json.dumps({'status': value['trust']['status'], 'packages': len(value['packages']),
                      'sequence':value['sequence'], 'sha256':digest(args.output), 'size':args.output.stat().st_size}))
