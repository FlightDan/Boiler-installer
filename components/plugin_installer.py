"""Pinned offline plugin file deployment for virtual integration testing.

This module does not enable services or initialize Steam. A live installer must
separately preserve service state and validate the Steam session before using it.
Existing installations outside our managed code store are deliberately refused.
"""
import base64
import json
import os
from pathlib import Path, PurePosixPath
import stat
import uuid
import zipfile
from payload_lock import checked
from file_transaction import FileTransaction, atomic_write, snapshot, directory_fd, serialized
from directory_transaction import DirectoryTransaction

PLUGINS = {
    'steamgriddb': ('SteamGridDB', '1.8.1', 'decky-steamgriddb'),
    'protondb': ('ProtonDB Badges', '1.3.4', 'protondb-decky'),
}
UNIT = b'''# Boiler managed offline Decky files; activation is a separate step.
[Unit]
Description=Decky Loader (Boiler managed)
After=network.target
[Service]
Type=simple
User=root
Restart=on-failure
KillMode=control-group
TimeoutStopSec=15
ExecStart=/var/lib/boiler/decky/runtime/services/PluginLoader
WorkingDirectory=/var/lib/boiler/decky/runtime/services
Environment=PRIVILEGED_PATH=/var/lib/boiler/decky/runtime
Environment=UNPRIVILEGED_PATH=/home/deck/homebrew
Environment=UNPRIVILEGED_USER=deck
Environment=CHOWN_PLUGIN_PATH=0
Environment=KEEP_SYSTEMD_SERVICE=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=LOG_LEVEL=INFO
[Install]
WantedBy=multi-user.target
'''


def validate_decky(root, resources, manifest):
    if manifest.get('available') is not True or manifest.get('architecture') != 'x86_64':
        raise RuntimeError('Pinned x86_64 Decky payload required')
    files = manifest.get('files', [])
    if len(files) != 1 or files[0].get('source') != 'decky/PluginLoader':
        raise RuntimeError('Unexpected Decky payload manifest')
    binary = checked(resources, files[0])
    if not manifest.get('notices'): raise RuntimeError('Decky license and source records missing')
    for entry in manifest['notices']: checked(resources, entry)
    # ELF64 little-endian x86_64, ET_EXEC or ET_DYN. Runtime boot remains a
    # separate acceptance gate; packaging cannot prove live Steam compatibility.
    if (len(binary) < 1024 * 1024 or binary[:6] != b'\x7fELF\x02\x01' or
            int.from_bytes(binary[18:20], 'little') != 62 or
            int.from_bytes(binary[16:18], 'little') not in (2, 3)):
        raise RuntimeError('Pinned Decky binary is not Linux ELF64 x86_64')
    unit = Path(root) / 'etc/systemd/system/plugin_loader.service'
    if unit.is_symlink() or (unit.exists() and not unit.read_bytes().startswith(b'# Boiler managed offline Decky')):
        raise RuntimeError('Existing Decky installation requires an explicit migration')
    return binary


def prepare_user_directories(root):
    """Keep user-writable Decky state apart from privileged executable code."""
    root = Path(root)
    rows = [line.split(':') for line in (root / 'etc/passwd').read_text().splitlines() if line.startswith('deck:')]
    if len(rows) != 1 or rows[0][5] != '/home/deck': raise RuntimeError('Expected deck user')
    uid, gid = int(rows[0][2]), int(rows[0][3])
    if uid <= 0 or gid <= 0: raise RuntimeError('Deck account cannot be privileged')
    for relative in ('home/deck/homebrew', 'home/deck/.local', 'home/deck/.local/share', 'home/deck/.local/share/Steam'):
        path = root / relative
        descriptor = directory_fd(path.parent); os.close(descriptor)
        if path.is_symlink(): raise RuntimeError('User state directory cannot be a link')
        if not path.exists(): path.mkdir(mode=0o755); os.chown(path, uid, gid)
        if not path.is_dir(): raise RuntimeError('User state directory is not a directory')
    return uid, gid


class ActivationTransaction(FileTransaction):
    def __init__(self, journal, allowed, marker, owner):
        self.marker, self.owner = str(marker), owner
        super().__init__(journal, allowed)

    def installed_metadata(self, path, original):
        metadata = super().installed_metadata(path, original)
        if path == self.marker:
            metadata.update(uid=self.owner[0], gid=self.owner[1])
        return metadata


def patch_animation_changer(files, prefix):
    """Prepare the supported upstream backend without touching its cached ZIP.

    Decky can initialize before Steam creates ~/.steam/root. Creating movies
    below that future symlink makes it a directory, breaking Steam's ln -sfn.
    Resolve an existing root (including custom/dangling symlinks) each time;
    before Steam creates it, use Steam's canonical data directory.
    The caller deploys these bytes through its existing file transaction.
    Original backend: Justin Marentette / SteamDeckHomebrew, BSD-3-Clause,
    https://github.com/TheLogicMaster/SDH-AnimationChanger. Preserve its LICENSE
    and metadata. The maintenance adapter carries the full original notice.
    """
    try:
        plugin = json.loads(files[prefix + 'plugin.json']['data'])
        package = json.loads(files[prefix + 'package.json']['data'])
        source = files[prefix + 'main.py']['data']
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError('Missing Animation Changer compatibility patch input') from error
    if (not isinstance(plugin, dict) or not isinstance(package, dict) or
            plugin.get('name') != 'Animation Changer' or
            package.get('name') != 'decky-animation-changer' or package.get('version') != '1.3.2'):
        raise RuntimeError('Unsupported Animation Changer identity or version for compatibility patch')
    original = b"OVERRIDE_PATH = os.path.expanduser('~/.steam/root/config/uioverrides/movies')"
    canonical = b"OVERRIDE_PATH = os.path.expanduser('~/.local/share/Steam/config/uioverrides/movies')"
    replacement = b'''\n\ndef get_override_path():
    steam_root = os.path.expanduser('~/.steam/root')
    # Preserve existing installations, including custom and dangling symlinks.
    # Never create the compatibility root as a directory before Steam does.
    if os.path.lexists(steam_root):
        steam_root = os.path.realpath(steam_root)
    else:
        steam_root = os.path.expanduser('~/.local/share/Steam')
    return os.path.join(steam_root, 'config', 'uioverrides', 'movies')'''
    assignment = original if isinstance(source, bytes) and original in source else canonical
    if (not isinstance(source, bytes) or source.splitlines().count(assignment) != 1
            or source.count(assignment) != 1 or b'get_override_path' in source):
        raise RuntimeError('Unsupported Animation Changer override path for compatibility patch')
    apply = b"    override_path = f'{OVERRIDE_PATH}/{video}'"
    startup = b'            os.makedirs(OVERRIDE_PATH, exist_ok=True)'
    if source.count(apply) != 1 or source.count(startup) != 1 or source.count(b'OVERRIDE_PATH') != 3:
        raise RuntimeError('Unsupported Animation Changer override path uses for compatibility patch')
    result = source.replace(assignment, replacement, 1).replace(apply, b'''    override_directory = get_override_path()
    os.makedirs(override_directory, exist_ok=True)
    override_path = os.path.join(override_directory, video)''', 1).replace(
        startup, b'            os.makedirs(get_override_path(), exist_ok=True)', 1)
    changed = dict(files)
    changed[prefix + 'main.py'] = dict(files[prefix + 'main.py'], data=result)
    return changed


def extract_plugin(archive, destination, plugin_id):
    name, version, folder = PLUGINS[plugin_id]
    destination = Path(destination)
    destination.mkdir(mode=0o700)
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 1000 or sum(entry.file_size for entry in entries) > 64 * 1024 * 1024:
            raise RuntimeError('Plugin archive exceeds supported resource limits')
        seen = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if ('\\' in entry.filename or path.is_absolute() or '..' in path.parts or not path.parts or
                path.parts[0] != folder or entry.filename in seen or stat.S_ISLNK(mode)):
                raise RuntimeError('Unsafe or unexpected plugin archive entry')
            seen.add(entry.filename)
            target = destination.joinpath(*path.parts)
            if entry.is_dir(): target.mkdir(mode=0o755, parents=True, exist_ok=True)
            else:
                if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG): raise RuntimeError('Special plugin file refused')
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                atomic_write(target, package.read(entry), 0o644)
    plugin = destination / folder
    metadata = json.loads((plugin / 'plugin.json').read_text())
    package_metadata = json.loads((plugin / 'package.json').read_text())
    if metadata['name'] != name or package_metadata['version'] != version:
        raise RuntimeError('Pinned plugin metadata does not match the selected component')
    required_files = ('main.py', 'dist/index.js')
    if plugin_id == 'protondb': required_files += ('settings.py',)
    for relative in required_files:
        if not (plugin / relative).is_file(): raise RuntimeError('Missing plugin runtime file: ' + relative)
    return plugin


class PluginInstaller:
    def __init__(self, root, resources, slot=None):
        self.root = Path(os.path.abspath(root))
        self.resources = Path(resources)
        if slot not in (None, 'A', 'B'): raise ValueError('Unknown Decky installation slot')
        self.slot = slot
        self.code = self.root / 'var/lib/boiler/decky/runtime'
        self.directory = self.root / 'var/lib/boiler/transactions/plugins'
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError('Private plugin operation directory required')
        descriptor = directory_fd(self.directory); os.close(descriptor)
        self.unit = self.root / 'etc/systemd/system/plugin_loader.service'
        self.unit.parent.mkdir(mode=0o755, parents=True, exist_ok=True)

    def shared_references(self):
        directory = self.root / 'home/.boiler/decky/slots'
        if not directory.exists() and not directory.is_symlink(): return {}
        descriptor = directory_fd(directory); os.close(descriptor)
        info = directory.lstat()
        if info.st_uid != 0 or info.st_mode & 0o077: raise RuntimeError('Untrusted shared Decky registry')
        references = {}
        for path in directory.iterdir():
            if path.name not in ('A.json', 'B.json'): raise RuntimeError('Unexpected shared Decky registry record')
            saved = snapshot(path)
            if saved['uid'] != 0 or saved['mode'] != 0o600: raise RuntimeError('Untrusted shared Decky slot reference')
            value = json.loads(base64.b64decode(saved['data']))
            slot = path.stem
            if (value.get('schema') != 1 or value.get('slot') != slot or
                    type(value.get('owns_marker')) is not bool or
                    value.get('marker') != 'home/deck/.local/share/Steam/.cef-enable-remote-debugging' or
                    not isinstance(value.get('epoch'), str) or uuid.UUID(value['epoch']).hex != value['epoch']):
                raise RuntimeError('Invalid shared Decky slot reference')
            references[slot] = value
        return references

    def register_slot(self, epoch, owns_marker):
        if self.slot is None: return  # Explicit legacy file-fixture API only.
        directory = self.root / 'home/.boiler/decky/slots'
        for path in (self.root / 'home/.boiler', directory.parent, directory):
            if path.is_symlink(): raise RuntimeError('Shared Decky registry cannot be a link')
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = directory_fd(path); os.close(descriptor)
            info = path.lstat()
            if info.st_uid != 0 or info.st_mode & 0o077: raise RuntimeError('Shared Decky registry must be private and root-owned')
        value = dict(schema=1, slot=self.slot, epoch=epoch, owns_marker=owns_marker,
                     marker='home/deck/.local/share/Steam/.cef-enable-remote-debugging')
        references = self.shared_references()
        if self.slot in references and references[self.slot] != value:
            raise RuntimeError('Shared Decky slot ownership differs; explicit recovery required')
        atomic_write(directory / (self.slot + '.json'), (json.dumps(value, sort_keys=True) + '\n').encode(), 0o600, 0, 0)

    def restore_reference(self, epoch):
        references = self.shared_references()
        if not references:
            if self.slot is not None: raise RuntimeError('Shared Decky slot ownership is missing')
            return None
        matches = [slot for slot, item in references.items() if item['epoch'] == epoch]
        if len(matches) != 1 or (self.slot is not None and matches != [self.slot]):
            raise RuntimeError('Shared Decky slot ownership is missing or ambiguous')
        slot = matches[0]
        if references[slot]['owns_marker'] and any(other != slot for other in references):
            raise RuntimeError('Restore the sibling Decky slot before removing its shared debug marker')
        return self.root / 'home/.boiler/decky/slots' / (slot + '.json')

    def epoch(self, plugin_id, create):
        if plugin_id not in ('decky', *PLUGINS): raise ValueError('Unsupported plugin id')
        pointer = self.directory / (plugin_id + '.json')
        value = None
        if pointer.exists():
            content = snapshot(pointer)
            value = json.loads(base64.b64decode(content['data']))
            if content['uid'] != os.geteuid() or content['mode'] & 0o077 or not uuid.UUID(value['epoch']).hex == value['epoch']:
                raise RuntimeError('Invalid private plugin operation record')
        if create and (value is None or value['phase'] == 'rolled_back'):
            value = {'id': plugin_id, 'epoch': uuid.uuid4().hex, 'phase': 'prepared'}
            if plugin_id == 'decky' and self.slot is not None: value['slot'] = self.slot
            atomic_write(pointer, (json.dumps(value) + '\n').encode(), 0o600)
        if value is None: raise RuntimeError('No previous plugin operation')
        if plugin_id == 'decky' and self.slot is not None and value.get('slot') not in (None, self.slot):
            raise RuntimeError('Decky slot journal differs from selected slot')
        directory = self.directory / value['epoch']; directory.mkdir(mode=0o700, exist_ok=True)
        return pointer, value, directory

    @serialized
    def apply(self, plugin_id):
        if plugin_id != 'decky':
            _, dependency, _ = self.epoch('decky', False)
            if dependency['phase'] != 'files_installed': raise RuntimeError('Decky dependency has not been deployed')
        pointer, value, journal = self.epoch(plugin_id, True)
        if plugin_id == 'decky' and self.slot is not None:
            references = self.shared_references()
            current = references.get(self.slot)
            if (current and current['epoch'] != value['epoch']) or (value['phase'] == 'files_installed' and not current):
                raise RuntimeError('Shared Decky slot ownership differs or is missing')
        staging = journal / ('staging-' + uuid.uuid4().hex)
        try:
            if plugin_id == 'decky':
                current = snapshot(self.unit)
                if current['exists'] and not base64.b64decode(current['data']).startswith(b'# Boiler managed offline Decky'):
                    raise RuntimeError('Existing Decky installation requires a separately validated migration')
                staging.mkdir(mode=0o755)
                manifest = json.loads((self.resources / 'manifest.json').read_text())['components']['core:decky']
                binary = validate_decky(self.root, self.resources, manifest)
                atomic_write(staging / 'PluginLoader', binary, 0o755)
                atomic_write(staging / 'VERSION', (manifest['version'] + '\n').encode(), 0o644)
                DirectoryTransaction(journal / 'code', self.code / 'services', self.code).apply(staging)
                FileTransaction(journal / 'unit', [self.unit]).apply({str(self.unit): UNIT})
                uid, gid = prepare_user_directories(self.root)
                marker = self.root / 'home/deck/.local/share/Steam/.cef-enable-remote-debugging'
                enable = self.root / 'etc/systemd/system/multi-user.target.d/50-boiler-decky.conf'
                enable.parent.mkdir(mode=0o755, exist_ok=True)
                files = {str(enable): b'[Unit]\nWants=plugin_loader.service\n'}
                activation_state = journal / 'activation/state.json'
                marker_owned = activation_state.exists() and str(marker) in json.loads(activation_state.read_text())['allowed']
                if not marker.exists() or marker_owned: files[str(marker)] = b''
                ActivationTransaction(journal / 'activation', list(files), marker, (uid, gid)).apply(files)
                self.register_slot(value['epoch'], str(marker) in files)
            else:
                name, version, folder = PLUGINS[plugin_id]
                archive = self.resources / plugin_id / (plugin_id + '-' + version + '.zip')
                plugin = extract_plugin(archive, staging, plugin_id)
                DirectoryTransaction(journal / 'code', self.code / 'plugins' / folder, self.code).apply(plugin)
            value['phase'] = 'files_installed'; value['runtime'] = 'not_validated'; value.pop('error', None)
            atomic_write(pointer, (json.dumps(value, indent=2) + '\n').encode(), 0o600)
            return value
        except Exception as error:
            value['phase'] = 'failed'; value['error'] = str(error)
            atomic_write(pointer, (json.dumps(value, indent=2) + '\n').encode(), 0o600)
            raise

    @serialized
    def rollback(self, plugin_id):
        if plugin_id == 'decky':
            for dependent in PLUGINS:
                try: _, status, _ = self.epoch(dependent, False)
                except RuntimeError: continue
                if status['phase'] != 'rolled_back': raise RuntimeError('Restore dependent plugin before Decky')
        pointer, value, journal = self.epoch(plugin_id, False)
        if plugin_id == 'decky' and self.slot is None and value.get('slot') in ('A', 'B'):
            self.slot = value['slot']
        if value['phase'] == 'rolled_back':
            # Retire a reference if power was lost after the durable restored
            # pointer but before unlinking this last registry record.
            if plugin_id == 'decky':
                for slot, item in self.shared_references().items():
                    if item['epoch'] == value['epoch']:
                        from file_transaction import remove_file
                        remove_file(self.root / 'home/.boiler/decky/slots' / (slot + '.json'))
            return value
        reference = self.restore_reference(value['epoch']) if plugin_id == 'decky' else None
        if plugin_id == 'decky':
            activation_state = journal / 'activation/state.json'
            if activation_state.exists():
                state = json.loads(activation_state.read_text())
                targets = state['allowed']
                owner = prepare_user_directories(self.root)
                marker = self.root / 'home/deck/.local/share/Steam/.cef-enable-remote-debugging'
                errors = ActivationTransaction(journal / 'activation', targets, marker, owner).rollback()
                if errors: raise RuntimeError('Decky activation restoration conflicts: ' + json.dumps(errors))
            unit_state = journal / 'unit/state.json'
            if unit_state.exists():
                errors = FileTransaction(journal / 'unit', [self.unit]).rollback()
                if errors: raise RuntimeError('Service unit restoration has conflicts: ' + json.dumps(errors))
            destination = self.code / 'services'
        else: destination = self.code / 'plugins' / PLUGINS[plugin_id][2]
        if (journal / 'code/state.json').exists(): DirectoryTransaction(journal / 'code', destination, self.code).rollback()
        value['phase'] = 'rolled_back'; value.pop('error', None)
        atomic_write(pointer, (json.dumps(value, indent=2) + '\n').encode(), 0o600)
        if reference is not None:
            from file_transaction import remove_file
            remove_file(reference)
        return value
