"""Build a deterministic unsigned device package from a hash-locked public asset.

No model script is executed by this preparer. The archive whitelist is the only
source for legacy bytes; the local workspace and research trees are never read.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.0-dev.1'
REPOSITORY = 'FlightDan/Boiler-installer'
SOURCE_SUBTREE = 'adaptations/brands/onexplayer/oxp3/bridge'
SOURCE_FILES = ('.gitignore', 'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'adapter.py', 'maintenance_adapter.py',
                'scripts/apply.sh', 'scripts/build.py', 'scripts/fetch-input.py', 'scripts/restore.sh',
                'scripts/verify.sh', 'selection.example.json', 'source-files.txt', 'tests/test_adapter.py', 'upstream-lock.json',
                'maintenance-static-lock.json', 'scripts/build-maintenance.py', 'tests/test_maintenance_package.py',
                'docs/MAINTENANCE_PACKAGE.md', 'scripts/maintenance.sh', 'tests/test_maintenance_adapter.py',
                'opti-offline-lock.json', 'tests/test_opti_updates.py',
                'scripts/build-shared-adaptations.py', 'scripts/prepare-shared-inputs.py', 'tests/test_shared_adaptations.py')
COMPATIBILITY = {'architectures': ['x86_64'], 'os_ids': ['steamos'], 'device_ids': ['oxp3'], 'os_versions': ['3.10'],
                 'kernels': ['7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9']}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            result.update(block)
    return result.hexdigest()


def stream_digest(stream):
    stream.seek(0)
    result = hashlib.sha256()
    for block in iter(lambda: stream.read(1024*1024), b''):
        result.update(block)
    stream.seek(0)
    return result.hexdigest()


@contextmanager
def public_input(path, expected):
    """Keep the authenticated regular file open through all archive reads."""
    if path.is_symlink():
        raise ValueError('Public input must be a regular file')
    if os.name == 'nt':
        # Deny concurrent writes/deletion while preserving ordinary readers.
        import ctypes
        from ctypes import wintypes
        import msvcrt
        create = ctypes.WinDLL('kernel32', use_last_error=True).CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        handle = create(str(path.absolute()), 0x80000000, 1, None, 3, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            close = ctypes.WinDLL('kernel32', use_last_error=True).CloseHandle
            close.argtypes = (wintypes.HANDLE,)
            close(handle)
            raise
    else:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream, ExitStack() as stack:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected['size']:
            raise ValueError('Public input does not match the locked size and SHA-256')
        authenticated = stream
        if os.name != 'nt':
            # POSIX permits in-place writers despite a held reader descriptor.
            # Authenticate an unlinked private snapshot, then parse only that
            # snapshot; mutate-and-restore of the source cannot influence it.
            authenticated = stack.enter_context(tempfile.TemporaryFile('w+b'))
            os.fchmod(authenticated.fileno(), 0o600)
            remaining = expected['size'] + 1
            while remaining:
                block = stream.read(min(1024*1024, remaining))
                if not block:
                    break
                authenticated.write(block)
                remaining -= len(block)
            if authenticated.tell() != expected['size']:
                raise ValueError('Public input does not match the locked size and SHA-256')
        if stream_digest(authenticated) != expected['sha256']:
            raise ValueError('Public input does not match the locked size and SHA-256')
        yield authenticated
        if os.fstat(authenticated.fileno()).st_size != expected['size'] or stream_digest(authenticated) != expected['sha256']:
            raise ValueError('Public input changed during extraction')


def safe_path(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or name.startswith('/') or any(part in ('', '.', '..') for part in name.split('/')):
        raise ValueError('Unsafe archive path')
    if any(ord(char) < 32 for char in name):
        raise ValueError('Unsafe archive path')
    return name


def read_selection(value, catalog, adapter_source=None):
    if adapter_source is not None:
        adapter = types.ModuleType('boiler_oxp3_adapter')
        adapter.__file__ = str(ROOT/'adapter.py')
        exec(compile(adapter_source, adapter.__file__, 'exec'), adapter.__dict__)
        adapter.selection(value, catalog)
        return value
    spec = importlib.util.spec_from_file_location('boiler_oxp3_adapter', ROOT/'adapter.py')
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    adapter.selection(value, catalog)
    return value


def committed_model(root, source_commit):
    """Freeze exactly the public model subtree; never read a signing identity."""
    root = Path(root).resolve()
    if not re.fullmatch(r'[0-9a-f]{40}', source_commit) or len(root.parents) < 5:
        raise ValueError('Release source requires the public Boiler bridge subtree and exact commit')
    repository_root = root.parents[4]
    if root != repository_root/SOURCE_SUBTREE:
        raise ValueError('Release source requires the reviewed Boiler bridge subtree')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repository_root), *args], stderr=subprocess.PIPE)
    if Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve() != repository_root:
        raise ValueError('Release source must be the independent public repository model subtree')
    if git('rev-parse', 'HEAD').decode().strip() != source_commit:
        raise ValueError('Release source commit must match the public checkout HEAD')
    origin = git('remote', 'get-url', 'origin').decode().strip()
    if origin not in ('https://github.com/'+REPOSITORY, 'https://github.com/'+REPOSITORY+'.git',
                      'git@github.com:'+REPOSITORY+'.git'):
        raise ValueError('Release source must use the existing public Boiler Installer repository origin')
    if git('status', '--porcelain=v1', '--untracked-files=all', '--', SOURCE_SUBTREE):
        raise ValueError('Public model source must be clean')
    whitelist = (root/'source-files.txt').read_text(encoding='utf-8').splitlines()
    if len(whitelist) != len(SOURCE_FILES) or set(whitelist) != set(SOURCE_FILES):
        raise ValueError('Public model source whitelist must contain exactly the reviewed source files')
    tracked = {}
    for entry in git('ls-tree', '-rz', source_commit, '--', SOURCE_SUBTREE).split(b'\x00'):
        if not entry:
            continue
        metadata, path = entry.split(b'\t', 1)
        mode, kind, _ = metadata.decode().split()
        name = path.decode('utf-8').removeprefix(SOURCE_SUBTREE+'/')
        if mode not in ('100644', '100755') or kind != 'blob':
            raise ValueError('Public model source links and submodules are forbidden')
        tracked[name] = mode
    if set(tracked) != set(SOURCE_FILES):
        raise ValueError('Committed model subtree differs from its reviewed source whitelist')
    contents = {}
    for name in SOURCE_FILES:
        data = git('show', source_commit+':'+SOURCE_SUBTREE+'/'+name)
        path = root/name
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()) or path.read_bytes() != data:
            raise ValueError('Working model bytes differ from committed source: '+name)
        contents[name] = data
    return contents


def selected_path(name, lock):
    if name in lock['export_files']:
        return 'payload/legacy/' + name
    if not any(name.startswith(prefix) for prefix in lock['export_prefixes']):
        return None
    if name.startswith('cache/components/waydroid/'):
        return 'payload/legacy/waydroid/' + name.removeprefix('cache/components/waydroid/')
    if name.startswith('cache/'):
        return 'payload/legacy/' + name.removeprefix('cache/')
    return 'payload/legacy/' + name


def deterministic_zip(directory, destination):
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(directory.rglob('*')):
            if not path.is_file():
                continue
            name = path.relative_to(directory).as_posix()
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | (0o755 if name.startswith('scripts/') else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with path.open('rb') as source, archive.open(info, 'w', force_zip64=True) as target:
                shutil.copyfileobj(source, target, 1024*1024)


def legacy_option_fields(item, category, selected=()):
    available = item.get('available', True)
    reason = {'zh': item.get('unavailable_zh', ''), 'en': item.get('unavailable_en', '')}
    if item['id'] == 'gm7-resume':
        # The install-once bridge does not carry a reviewed target SSD context.
        # Preserve the pinned legacy implementation, but do not offer this path.
        available = False
        reason = {'zh': '旧机型桥不提供 GM7 修复；此修复由已复核目标 SSD 的通用构件负责。',
                  'en': 'The legacy model bridge does not provide GM7 repair; the reviewed target SSD common component owns it.'}
    return dict(id='oxp3:'+item['id'], category=category, name={'zh': item['zh'], 'en': item['en']},
                available=available, default=available and 'oxp3:'+item['id'] in selected,
                reason=reason, dependencies=['oxp3:'+name for name in item['dependencies']])


def opti_lock(raw):
    """Only the explicitly approved published layouts may extend the base."""
    value = json.loads(raw)
    plugin, release = value['plugin_archive'], value['plugin_release']
    archives = value['optiscaler_release']['archives']
    expected = {'0.9.4': '575cb4df866116093df75af607e37fd70e10f5163e0f23fd5c804142e80ef0ad',
                '0.7.9': '70103bf3746140d368179773edeafb02ec9718efa130276b8da833c3156fbfe3',
                '10.0.0-pre1_20261025': '6bb2e9cd6ac0204c76c974df1c202afb1fc7a765844a9ccf608103557aa595fe'}
    if (value.get('schema_version') != 1 or value.get('kind') != 'PublishedOptiOfflineInputs'
            or plugin.get('version') != '0.3.0' or plugin.get('filename') != 'opti2xmx-0.3.0.zip'
            or plugin.get('size_bytes') != 1479143
            or plugin.get('sha256') != 'c317e3df3eec2c2249cb83c37fdb04c04191ec99aebef0f870715d8332120e0f'
            or release.get('plugin_sha256') != plugin['sha256'] or release.get('plugin_version') != plugin['version']
            or release.get('source_commit') != '771a55e8e1202bb4b6aa601d9942bb03704aef02'
            or len(archives) != 3 or {item['version']: item['sha256'] for item in archives} != expected
            or hashlib.sha256(json.dumps(release['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest() != release['files_sha256']):
        raise ValueError('Unapproved Opti offline input lock')
    for name in release['files']:
        safe_path(name)
    return value


def read_opti_inputs(plugin_path, archive_paths, pins):
    """Authenticate bytes only; downloaded plugin code is never imported."""
    plugin = pins['plugin_archive']
    with public_input(Path(plugin_path), dict(size=plugin['size_bytes'], sha256=plugin['sha256'])) as incoming:
        with zipfile.ZipFile(incoming) as archive:
            expected = pins['plugin_release']['files']
            if len(archive.infolist()) != len(expected):
                raise ValueError('Published Opti ZIP membership differs from the locked layout')
            records = {}
            for item in archive.infolist():
                safe_path(item.filename)
                relative = item.filename.removeprefix('opti2xmx/')
                if (not item.filename.startswith('opti2xmx/') or relative not in expected or relative in records
                        or item.is_dir() or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
                        or item.file_size > 2*1024*1024):
                    raise ValueError('Unsafe or unexpected published Opti ZIP member')
                data = archive.read(item)
                if hashlib.sha256(data).hexdigest() != expected[relative]:
                    raise ValueError('Published Opti member hash differs: '+relative)
                records[relative] = data
    if (json.loads(records['package.json'])['version'] != plugin['version']
            or json.loads(records['plugin.json'])['name'] != 'Opti2XMX'
            or json.loads(records['runtime-source.json']) != pins['plugin_release']['runtime']):
        raise ValueError('Published Opti plugin or retained runtime identity differs')
    paths = {Path(path).name: Path(path) for path in archive_paths}
    archives = pins['optiscaler_release']['archives']
    if len(paths) != len(archive_paths) or set(paths) != {item['filename'] for item in archives}:
        raise ValueError('Provide exactly all three pinned original OptiScaler archives')
    offline = {}
    for item in archives:
        with public_input(paths[item['filename']], dict(size=item['size_bytes'], sha256=item['sha256'])) as incoming:
            data = incoming.read(item['size_bytes']+1)
        if len(data) != item['size_bytes']:
            raise ValueError('OptiScaler archive size changed')
        offline[item['tag']+'/'+item['filename']] = data
    return records, offline


def apply_opti_update(output, resources, records, offline, pins):
    """Rebuild one legacy file-map; retain every unrelated component verbatim."""
    output = Path(output).resolve()
    legacy = output/'payload/legacy'
    integration = legacy/'integration'
    previous = resources['opti2xmx']
    runtime = integration/'opti2xmx/runtime.tar.gz'
    runtime_pin = pins['plugin_release']['runtime']
    if runtime.stat().st_size != runtime_pin['size_bytes'] or digest(runtime) != runtime_pin['sha256']:
        raise ValueError('The pinned public base does not carry the unchanged published Opti runtime')
    for entry in previous['files']:
        source = safe_path(entry['source'])
        if not source.startswith('opti2xmx/') or not source.endswith('.bin'):
            raise ValueError('Unexpected old Opti blob target')
        (integration/source).unlink()
    # Both are fixed descendants of a fresh link-free extraction, never a
    # caller-selected workspace tree. Check the final path before removal.
    for relative in ('components/opti2xmx', 'integration/sources/opti2xmx'):
        tree = legacy/relative
        if tree.is_symlink() or not tree.resolve().is_relative_to(output) or tree.resolve() == output:
            raise ValueError('Unsafe Opti source mirror destination')
        if tree.exists():
            shutil.rmtree(tree)
    report = dict(schema=1, plugin_version=pins['plugin_archive']['version'],
                  plugin_sha256=pins['plugin_archive']['sha256'], contains_game_state=False,
                  runtime=runtime_pin, files=pins['plugin_release']['files'])
    for relative in ('components/opti2xmx', 'integration/sources/opti2xmx'):
        tree = legacy/relative
        for name, data in records.items():
            target = tree/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (tree/('deployment.json' if relative.startswith('components/') else 'PROVENANCE.json')).write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    for name, value in (('opti2xmx-release.json', pins['plugin_release']), ('optiscaler-release.json', pins['optiscaler_release'])):
        (legacy/'components'/name).write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')
    targets = {'home/deck/homebrew/plugins/opti2xmx/'+name: (data, False) for name, data in records.items()}
    targets['home/deck/.local/share/opti2xmx/runtime.json'] = ((json.dumps({
        'package': '/opt/oxp3-opti2xmx/20261005/runtime', 'sr': '/opt/oxp3-opti2xmx/20261005/sdk/libxess.dll'})+'\n').encode(), True)
    targets.update({'home/deck/.local/share/opti2xmx/downloads/'+name: (data, True) for name, data in offline.items()})
    files = []
    for index, (target, (data, user)) in enumerate(targets.items()):
        source = 'opti2xmx/'+f'{index:03d}.bin'
        (integration/source).write_bytes(data)
        files.append(dict(target=target, source=source, mode=0o644, user=user))
    resources['opti2xmx'] = dict(previous, files=files, runtime_archive='opti2xmx/runtime.tar.gz', missing=[],
                                version=pins['plugin_archive']['version'], offline_optiscaler_versions=[item['version'] for item in pins['optiscaler_release']['archives']])
    (integration/'manifest.json').write_text(json.dumps(resources, indent=2)+'\n', encoding='utf-8')
    return dict(kind='published-opti-offline-v1', plugin=pins['plugin_archive'],
                optiscaler=pins['optiscaler_release']['archives'], retained_runtime=runtime_pin,
                file_count=len(files), contains_game_state=False)


def build(archive_path, output, choice, *, lock_path=ROOT/'upstream-lock.json', source_commit=None, version=None,
          opti2xmx_plugin=None, optiscaler_archives=None):
    archive_path, output = Path(archive_path), Path(output)
    if source_commit and not version:
        raise ValueError('Release mode requires an explicit model version')
    version = version or VERSION
    if not isinstance(version, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?', version):
        raise ValueError('Explicit model version must be a canonical three-part version')
    contents = committed_model(ROOT, source_commit) if source_commit else None
    if contents is not None:
        if Path(lock_path).resolve() != (ROOT/'upstream-lock.json').resolve():
            raise ValueError('Release builds use only the committed upstream lock')
        if choice != json.loads(contents['selection.example.json']):
            raise ValueError('Release selection differs from the committed frozen selection')
    lock = json.loads(contents['upstream-lock.json'] if contents is not None else Path(lock_path).read_bytes())
    addons = None
    if opti2xmx_plugin is not None or optiscaler_archives:
        if opti2xmx_plugin is None or not optiscaler_archives:
            raise ValueError('Opti offline update requires the published plugin ZIP and all three original OptiScaler archives')
        pins = opti_lock(contents['opti-offline-lock.json'] if contents is not None else (ROOT/'opti-offline-lock.json').read_bytes())
        addons = read_opti_inputs(opti2xmx_plugin, optiscaler_archives, pins)
    expected = lock['asset']
    if output.exists() or output.is_symlink():
        raise ValueError('Output must be a new directory')
    with public_input(archive_path, expected) as input_stream, zipfile.ZipFile(input_stream) as source:
        seen, selected = set(), []
        for info in source.infolist():
            name = info.filename.rstrip('/')
            safe_path(name)
            if name.casefold() in seen:
                raise ValueError('Duplicate archive path')
            seen.add(name.casefold())
            mode = info.external_attr >> 16
            if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError('Archive links and special files are forbidden')
            target = selected_path(name, lock)
            if target and not info.is_dir():
                selected.append((info, target))
        if sum(info.file_size for info, _ in selected) > 4*1024**3:
            raise ValueError('Model export exceeds size limit')
        build_info = json.loads(source.read('build-info.json'))
        if build_info['source_commit'] != lock['source_commit'] or build_info['package_channel'] != 'public-release':
            raise ValueError('Public source/build identity mismatch')
        catalog = json.loads(source.read('components/options.json'))
        public_catalog = {item['id']: item for item in json.loads(source.read('components/catalog.json'))['components']}
        resources = json.loads(source.read('cache/integration/manifest.json'))
        read_selection(choice, catalog, contents['adapter.py'] if contents is not None else None)
        output.mkdir(parents=True)
        for info, name in selected:
            target = output/name
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as original, target.open('xb') as destination:
                shutil.copyfileobj(original, destination, 1024*1024)
    for name in ('adapter.py', 'maintenance_adapter.py', 'scripts/apply.sh', 'scripts/verify.sh',
                 'scripts/restore.sh', 'scripts/maintenance.sh', 'upstream-lock.json'):
        if contents is not None and name not in contents:
            continue
        target = output/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents[name] if contents is not None else (ROOT/name).read_bytes())
    (output/'selection.json').write_text(json.dumps(choice, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    payload_update = apply_opti_update(output, resources, *addons, pins) if addons is not None else None
    inventory = [{'path': path.relative_to(output).as_posix(), 'size': path.stat().st_size, 'sha256': digest(path)}
                 for path in sorted(output.rglob('*')) if path.is_file()]
    manifest = {
        'schema_version': 1, 'kind': 'DevicePackage', 'id': 'device:oxp3', 'version': version,
        'minimum_engine_version': '0.1.0',
        'name': {'en': 'ONEXPLAYER 3 legacy adaptation bridge', 'zh': 'ONEXPLAYER 3 兼容适配桥'},
        'source': {'kind': 'github', 'repository': REPOSITORY, 'commit': source_commit, 'subtree': SOURCE_SUBTREE} if contents is not None else
                  {'kind': 'local', 'path': ROOT.name, 'repository': lock['repository'], 'base_commit': lock['source_commit']},
        'upstream_source': {'repository': lock['repository'], 'commit': lock['source_commit'],
                            'release_tag': lock['release_tag'], 'asset': lock['asset']},
        'compatibility': COMPATIBILITY,
        'hardware_matching': {
            'mode': 'manual', 'automatic_selection': False,
            'dmi': {'sys_vendor': 'ONE-NETBOOK', 'product_name': 'ONEXPLAYER 3'},
            'evidence': 'https://github.com/FlightDan/OneXPlayer3-SteamOS/blob/'+lock['source_commit']+'/windows-installer/components/controls/profile.json',
            'note': 'Published model identity is advisory; this development bridge requires explicit manual model selection and does not claim automatic DMI validation.'
        },
        'trust': {'status': 'index-signature-required' if contents is not None else 'unsigned-development', 'official_release': False},
        'legacy_kde_languages': catalog['kde_languages'],
        'components': [{
            'schema_version': 1, 'kind': 'ComponentManifest', 'id': 'oxp3:legacy-adaptation', 'version': version,
            'minimum_engine_version': '0.1.0',
            'name': {'en': 'Explicit OXP3 device adaptation', 'zh': '显式 OXP3 设备适配'},
            'description': {'en': 'Runs the pinned public OXP3 integration once using the package selection, including its explicitly acknowledged OS-image update hold.', 'zh': '按包内显式选择执行一次已公开 OXP3 集成，包含已确认的系统镜像更新暂停。'},
            'optional': True, 'default': False, 'requires': [], 'conflicts': [],
            'compatibility': COMPATIBILITY, 'files': [],
            'hooks': {'apply': 'scripts/apply.sh', 'verify': 'scripts/verify.sh', 'restore': 'scripts/restore.sh'}
        }],
        'migration_status': 'experimental-new-stack-lifecycle-not-accepted',
        'legacy_capabilities': [dict(item, **legacy_option_fields(item, category, choice[category]),
                                     migration_status='packaged-existing-public-policy-new-stack-acceptance-pending',
                                     legacy_acceptance=public_catalog.get(item['id'], {}).get('state', 'see-upstream-release'),
                                     legacy_notes=public_catalog.get(item['id'], {}).get('reason', ''),
                                     targets=[entry['target'] for entry in resources.get(item['id'], {}).get('files', [])],
                                     services=resources.get(item['id'], {}).get('units', []),
                                     generated_targets='See pinned payload/legacy/components/integration.py for dynamic policy and transaction targets')
                                for category in ('repairs', 'plugins') for item in catalog[category]],
        'files': inventory
    }
    # Rebuilding an older committed adapter must not advertise a new runtime ABI.
    adapter_source = contents['adapter.py'] if contents is not None else (ROOT/'adapter.py').read_bytes()
    if b'def runtime_selection(' in adapter_source:
        manifest['selection_contract'] = {'kind': 'oxp3-legacy-options-v1', 'component': 'oxp3:legacy-adaptation'}
    if b'def model_efi_interception(' in adapter_source:
        manifest['minimum_engine_version'] = '0.1.3'
        manifest['components'][0]['minimum_engine_version'] = '0.1.3'
        manifest['oxp3_efi_contract'] = {'kind': 'oxp3-split-lock-efi-v1', 'component': 'oxp3:legacy-adaptation'}
    if (output/'maintenance_adapter.py').is_file() and (output/'scripts/maintenance.sh').is_file():
        manifest['maintenance_contract'] = {'kind': 'boiler-model-maintenance-v1', 'entry': 'scripts/maintenance.sh'}
    if payload_update is not None:
        manifest['payload_updates'] = [payload_update]
        for capability in manifest['legacy_capabilities']:
            if capability['id'] == 'oxp3:opti2xmx':
                capability.update(payload_version=payload_update['plugin']['version'],
                                  offline_optiscaler_versions=[item['version'] for item in payload_update['optiscaler']])
    if contents is not None:
        manifest['model_source'] = {'root': SOURCE_SUBTREE, 'source_whitelist_sha256': hashlib.sha256(contents['source-files.txt']).hexdigest(),
                                    'files': [{'path': SOURCE_SUBTREE+'/'+name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                                              for name, data in sorted(contents.items())]}
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-installer', type=Path, required=True)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--zip', type=Path)
    parser.add_argument('--source-commit', help='Exact clean public model commit; enables index-signature-required release candidate mode')
    parser.add_argument('--version', help='Independent model version; mandatory with --source-commit')
    parser.add_argument('--opti2xmx-plugin', type=Path, help='Exact published Opti2XMX v0.3.0 ZIP; enables the pinned additive offline update')
    parser.add_argument('--optiscaler-archive', action='append', type=Path, help='Original pinned OptiScaler archive; supply all three versions')
    args = parser.parse_args()
    manifest = build(args.public_installer, args.output, json.loads(args.selection.read_text(encoding='utf-8')),
                     source_commit=args.source_commit, version=args.version,
                     opti2xmx_plugin=args.opti2xmx_plugin, optiscaler_archives=args.optiscaler_archive)
    report = {'id': manifest['id'], 'version': manifest['version'], 'status': manifest['trust']['status'],
              'manifest_sha256': digest(args.output/'manifest.json'), 'manifest_size': (args.output/'manifest.json').stat().st_size,
              'files': len(manifest['files']), 'source': manifest['source']}
    if args.zip:
        if args.zip.exists():
            raise SystemExit('Archive output already exists')
        deterministic_zip(args.output, args.zip)
        report['archive'] = {'name': args.zip.name, 'size': args.zip.stat().st_size, 'sha256': digest(args.zip)}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
