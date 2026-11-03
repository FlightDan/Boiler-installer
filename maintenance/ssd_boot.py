"""Fixed, byte-only SteamOS ESP repair with durable shared-home recovery.

The host adapter authenticates installer context. Review digests bind a review to
observed state; they are not an authorization credential. No firmware variables,
bootloader binaries or rEFInd files are modified.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from components.ssd_boot import classify_reviewed_device, render_grub_config
from protocol.manifest import relative_path, _decode_json
from .engine import Engine, MaintenanceError, _read
from .adoption_blobs import _directory, _file, _legacy_lock
from .keyboard_runtime.vendor import probe
from .keyboard_runtime.vendor.transactions import Store
from .keyboard_target import run

TARGET = 'efi/EFI/steamos/grub.cfg'
DROPIN = 'etc/default/grub.d/oxp3-nvme.cfg'
DROPIN_DATA = (b'# Selected destination: Predator GM7; reviewed boot workaround; device validation pending.\n'
               b'GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX} nvme.noacpi=1"\n')
STATE = 'home/.boiler/ssd-boot-v1'
ESP_TYPE = 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'
_PART = re.compile(r'[a-zA-Z0-9-]{8,64}\Z')
_MAX = 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _encode(value):
    return base64.b64encode(value).decode('ascii')


def _decode(value):
    if not isinstance(value, str) or len(value) > (_MAX + 2) // 3 * 4:
        raise MaintenanceError('Invalid SSD repair snapshot')
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise MaintenanceError('Invalid SSD repair snapshot') from error
    if not result or len(result) > _MAX:
        raise MaintenanceError('Invalid SSD repair snapshot size')
    return result


def _mount(path, *, efi_device=None):
    mounts = json.loads(run('findmnt', '--json', '--target', str(path), '--output',
                            'TARGET,SOURCE,MAJ:MIN,FSTYPE,UUID'))['filesystems']
    if not isinstance(mounts,list) or any(row.get('target') != str(path) for row in mounts):
        raise MaintenanceError('SSD repair requires explicit root, home and EFI mounts')
    if efi_device is None:
        if len(mounts) != 1: raise MaintenanceError('SSD repair requires a unique mount')
        return mounts[0]
    # systemd automount leaves its autofs layer visible under the triggered VFAT.
    # Accept only that exact pair, and bind the chosen row to the opened file.
    if len(mounts) == 2:
        auto=[row for row in mounts if row.get('fstype') == 'autofs']
        fat=[row for row in mounts if row.get('fstype') == 'vfat']
        if len(auto) != 1 or len(fat) != 1: raise MaintenanceError('Unexpected EFI mount stack')
        selected=fat[0]
    elif len(mounts) == 1:
        selected=mounts[0]
    else:
        raise MaintenanceError('Unexpected EFI mount stack')
    if selected.get('fstype') != 'vfat' or selected.get('maj:min') != efi_device:
        raise MaintenanceError('Opened EFI file differs from the actual VFAT mount')
    return selected


def _activate_efi(root):
    """Trigger an existing EFI automount by a bounded read-only traversal."""
    with _directory(root) as descriptor:
        descriptors=[]
        try:
            current=descriptor
            for name in ('efi','EFI','steamos'):
                current=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=current)
                descriptors.append(current)
            handle=os.open('grub.cfg',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=current)
            try:
                info=os.fstat(handle)
                if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > _MAX:
                    raise MaintenanceError('EFI configuration is not a supported regular file')
                return str(os.major(info.st_dev))+':'+str(os.minor(info.st_dev))
            finally: os.close(handle)
        finally:
            for handle in reversed(descriptors): os.close(handle)


def binding(root, *, installer_context=None):
    """Observe the actual root/ESP parent and persistent partition identities."""
    root = Path(os.path.abspath(root))
    for path in (root, root/'home'):
        with _directory(path):
            pass
    efi_device = _activate_efi(root)
    mounted, home, esp = (_mount(root), _mount(root/'home'), _mount(root/'efi',efi_device=efi_device))
    devices = json.loads(run('lsblk', '--json', '--tree', '--paths', '--output',
        'PATH,TYPE,MAJ:MIN,PARTUUID,PARTLABEL,PARTTYPE,TRAN,MODEL,SERIAL'))['blockdevices']
    verified = None
    if mounted.get('fstype') == 'btrfs':
        verified = probe._single_btrfs_device(mounted.get('uuid'), probe._canonical_block(mounted.get('source')))
    topology = probe.identify_root(mounted, devices, verified_device=verified)
    if topology.get('label') not in ('A', 'B'):
        raise MaintenanceError('SSD repair requires an identified SteamOS A/B root')
    matches = []
    for disk in devices:
        for part in disk.get('children', []):
            if part.get('maj:min') == esp.get('maj:min'):
                matches.append((disk, part))
    if len(matches) != 1:
        raise MaintenanceError('Cannot uniquely bind the EFI partition')
    disk, part = matches[0]
    partuuid = part.get('partuuid')
    if (disk.get('type') != 'disk' or disk.get('path') != topology['disk']
            or part.get('type') != 'part' or part.get('partlabel') != 'efi-' + topology['label']
            or str(part.get('parttype', '')).lower() != ESP_TYPE
            or not isinstance(partuuid, str) or not _PART.fullmatch(partuuid)
            or esp.get('fstype') not in ('vfat', 'fat', 'msdos')
            or home.get('maj:min') in (mounted.get('maj:min'), esp.get('maj:min'))
            or not isinstance(home.get('uuid'), str) or not home['uuid'].strip()):
        raise MaintenanceError('EFI partition, parent disk, filesystem or shared home is unsupported')
    physical = dict(bus={'nvme':'NVMe','sata':'SATA','usb':'USB','sd':'SD','mmc':'MMC'}.get(str(disk.get('tran', '')).lower()),
                    model=(disk.get('model') or '').strip(), serial=(disk.get('serial') or '').strip())
    if installer_context is not None:
        if not isinstance(installer_context, dict):
            raise MaintenanceError('Invalid installer SSD context')
        if installer_context == {'kind': 'nonphysical'}:
            disposition = 'not_applicable'
        else:
            if (set(installer_context) != {'kind', 'bus', 'model', 'serial', 'disposition'}
                    or installer_context.get('kind') != 'physical'
                    or not isinstance(installer_context.get('serial'), str)
                    or not installer_context['serial'].strip() or len(installer_context['serial']) > 128
                    or any(ord(c) < 32 or ord(c) > 126 for c in installer_context['serial'])):
                raise MaintenanceError('Invalid reviewed physical SSD context')
            disposition = classify_reviewed_device(installer_context['bus'], installer_context['model'])
            if installer_context['disposition'] != disposition:
                raise MaintenanceError('Reviewed SSD disposition disagrees with physical facts')
    else:
        if not physical['serial'] or len(physical['serial']) > 128 or any(ord(c) < 32 or ord(c) > 126 for c in physical['serial']):
            raise MaintenanceError('Physical SSD serial is unavailable or invalid')
        disposition = classify_reviewed_device(physical['bus'], physical['model'])
    return dict(root_partuuid=topology['partuuid'].lower(), slot=topology['label'],
        esp_partuuid=partuuid.lower(), esp_type=ESP_TYPE, home_uuid=home['uuid'].lower(),
        root_device=verified['maj:min'] if verified is not None else mounted.get('maj:min'), esp_device=esp.get('maj:min'),
        disk=disk.get('path'), disposition=disposition,
        physical=physical if installer_context is None else installer_context)


def _durable(bound):
    physical = bound['physical']
    if physical.get('kind') == 'physical':
        physical = {key: physical[key] for key in ('bus', 'model', 'serial')}
    return {**{key: bound[key] for key in ('root_partuuid', 'slot', 'esp_partuuid', 'esp_type', 'home_uuid')},
            'physical': {key: ' '.join(physical[key].split()).casefold() for key in ('bus', 'model', 'serial')}}


@contextlib.contextmanager
def _efi_directory(root, bound):
    """Traverse the mounted VFAT without interpreting synthetic Unix ownership."""
    with _directory(root) as root_fd:
        fd = os.open('efi', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            device = os.fstat(fd).st_dev
            if str(os.major(device)) + ':' + str(os.minor(device)) != bound['esp_device']:
                raise MaintenanceError('Opened EFI mount differs from reviewed partition')
            for name in ('EFI', 'steamos'):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                if os.fstat(child).st_dev != device:
                    os.close(child)
                    raise MaintenanceError('Nested EFI mount is unsupported')
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)


def _efi_read(directory):
    fd = os.open('grub.cfg', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise MaintenanceError('EFI configuration must be a regular file')
        data = stream.read(_MAX + 1)
        if not data or len(data) > _MAX:
            raise MaintenanceError('Invalid EFI boot configuration size')
        return data


def _target(root, bound):
    with _efi_directory(root, bound) as directory:
        return _efi_read(directory)


def _dropin(root):
    path = Path(root)/DROPIN
    try:
        with _file(path) as (stream, info):
            data = stream.read(_MAX + 1)
            if len(data) > _MAX:
                raise MaintenanceError('NVMe drop-in exceeds size limit')
            return dict(data=_encode(data), mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
    except FileNotFoundError:
        with _directory(path.parent):
            pass
        return None


def _snapshot(root, bound):
    return dict(efi=_encode(_target(root, bound)), dropin=_dropin(root))


def _valid_dropin(value):
    if value is None:
        return
    if (not isinstance(value, dict) or set(value) != {'data','mode','uid','gid'}
            or any(type(value[key]) is not int or value[key] < 0 for key in ('mode','uid','gid'))
            or value['mode'] > 0o7777):
        raise MaintenanceError('Invalid NVMe drop-in snapshot')
    _decode(value['data'])


def _desired(original):
    if not isinstance(original, dict) or set(original) != {'efi','dropin'}:
        raise MaintenanceError('Invalid SSD repair snapshot')
    _valid_dropin(original['dropin'])
    dropin = original['dropin']
    if dropin is not None and _decode(dropin['data']) != DROPIN_DATA:
        raise MaintenanceError('Existing NVMe drop-in differs; preserve it for inspection')
    if dropin is None:
        dropin = dict(data=_encode(DROPIN_DATA),mode=0o644,uid=0,gid=0)
    return dict(efi=_encode(render_grub_config(_decode(original['efi']))), dropin=dropin)


def _replace_efi(root, before, after, bound):
    with _efi_directory(root, bound) as directory:
        if _efi_read(directory) != before:
            raise MaintenanceError('EFI configuration changed before replacement')
        temporary = '.boiler-' + uuid.uuid4().hex
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(after)
                stream.flush()
                os.fsync(stream.fileno())
            if _efi_read(directory) != before:
                raise MaintenanceError('EFI configuration changed before replacement')
            os.replace(temporary, 'grub.cfg', src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try: os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError: pass


def _replace_dropin(root, before, after):
    path = Path(root)/DROPIN
    with _directory(path.parent) as directory:
        if _dropin(root) != before:
            raise MaintenanceError('NVMe drop-in changed before replacement')
        if after is None:
            os.unlink(path.name, dir_fd=directory)
            os.fsync(directory)
            return
        _valid_dropin(after)
        temporary = '.boiler-' + uuid.uuid4().hex
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(_decode(after['data']))
                stream.flush()
                os.fchmod(stream.fileno(), after['mode'])
                os.fchown(stream.fileno(), after['uid'], after['gid'])
                os.fsync(stream.fileno())
            if _dropin(root) != before:
                raise MaintenanceError('NVMe drop-in changed before replacement')
            os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try: os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError: pass


def _replace(root, before, after, bound, context):
    # All files were preflighted before the intent was persisted. Recheck the
    # mount/partition topology and each expected snapshot before each write.
    expected = dict(before)
    for key in ('dropin','efi'):
        if _snapshot(root, bound) != expected:
            raise MaintenanceError('SSD repair files changed before write')
        if before[key] == after[key]:
            continue
        if binding(root, installer_context=context) != bound:
            raise MaintenanceError('SSD target topology changed before write')
        if key == 'efi':
            _replace_efi(root, _decode(before[key]), _decode(after[key]), bound)
        else:
            _replace_dropin(root, before[key], after[key])
        expected[key] = after[key]
    if _snapshot(root, bound) != after or binding(root, installer_context=context) != bound:
        raise MaintenanceError('SSD repair files or topology changed during write')


def _provider_owner(value):
    if (not isinstance(value,dict) or set(value) != {'component_id','manifest_sha256'}
            or not isinstance(value['component_id'],str)
            or not re.fullmatch(r'[a-z][a-z0-9_-]*:[a-z0-9][a-z0-9_.-]*',value['component_id'])
            or not isinstance(value['manifest_sha256'],str)
            or not re.fullmatch(r'[0-9a-f]{64}',value['manifest_sha256'])):
        raise MaintenanceError('Invalid authenticated SSD provider owner')
    return dict(value)


def _record(value, bound):
    keys = {'version', 'target', 'original', 'applied', 'status'}
    if not isinstance(value,dict) or value.get('version') not in (2,3,4):
        raise MaintenanceError('SSD repair state is corrupt or belongs to another target')
    version=value['version']
    expected=keys | ({'handoff'} if version == 3 else set()) | ({'provider_owner'} if version == 4 else set())
    if version == 4 and 'handoff' in value: expected.add('handoff')
    if (set(value) != expected or value['target'] != _durable(bound)
            or value['status'] not in ('installed', 'restored')):
        raise MaintenanceError('SSD repair state is corrupt or belongs to another target')
    original, applied = value['original'], value['applied']
    if _desired(original) != applied:
        raise MaintenanceError('SSD repair state is inconsistent')
    if 'handoff' in value and (not isinstance(value['handoff'],dict)
            or set(value['handoff']) != {'preflight','model_proof'}):
        raise MaintenanceError('Invalid SSD model handoff state')
    if version == 4:
        _provider_owner(value['provider_owner'])
    return original, applied


def _overlaps_owned(paths):
    """Compare ownership at case-insensitive path-component boundaries."""
    targets = tuple(path.casefold() for path in (TARGET, DROPIN))
    for path in paths:
        owned = relative_path(path).casefold()
        if any(owned == target or owned.startswith(target + '/') or target.startswith(owned + '/')
               for target in targets):
            return True
    return False


def reject_conflicting_component(engine, component):
    """Guard generic writes while the caller holds the Engine transaction lock."""
    files = component.get('files', [])
    if not isinstance(files, list) or any(not isinstance(item, dict) or 'target' not in item for item in files):
        raise MaintenanceError('Invalid component targets')
    hooks = component.get('hooks', {})
    if not isinstance(hooks, dict):
        raise MaintenanceError('Invalid component hooks')
    if not _overlaps_owned(item['target'] for item in files):
        return
    state_dir = engine.home_root/'.boiler/ssd-boot-v1'
    if not os.path.lexists(state_dir):
        return
    with _directory(state_dir) as directory:
        if os.name != 'nt' and os.fstat(directory).st_mode & 0o077:
            raise MaintenanceError('SSD ownership state directory must be private')
        names = os.listdir(state_dir if os.name == 'nt' else directory)
        for name in names:
            if name == 'pending.json':
                raise MaintenanceError('Interrupted SSD repair owns boot paths; recover it first')
            if name == 'lock':
                with _file(state_dir/name, private=True):
                    pass
                continue
            if not re.fullmatch(r'esp-[a-z0-9-]{8,64}\.json', name):
                raise MaintenanceError('Unknown SSD ownership state; preserve it for inspection')
            try:
                with _file(state_dir/name, private=True) as (stream, info):
                    if info.st_size > 8 * 1024 * 1024:
                        raise MaintenanceError('SSD ownership state is oversized')
                    value = _decode_json(stream.read(8 * 1024 * 1024 + 1))
                target = value['target']
                physical = target['physical']
                if (set(target) != {'root_partuuid','slot','esp_partuuid','esp_type','home_uuid','physical'}
                        or target['slot'] not in ('A','B') or target['esp_type'] != ESP_TYPE
                        or not isinstance(target['root_partuuid'], str) or not _PART.fullmatch(target['root_partuuid'])
                        or not isinstance(target['esp_partuuid'], str) or not _PART.fullmatch(target['esp_partuuid'])
                        or name != 'esp-' + target['esp_partuuid'] + '.json'
                        or not isinstance(target['home_uuid'], str) or not target['home_uuid']
                        or not isinstance(physical, dict) or set(physical) != {'bus','model','serial'}
                        or physical['bus'] != 'nvme' or not isinstance(physical['serial'], str)
                        or not physical['serial'].strip() or len(physical['serial']) > 128
                        or classify_reviewed_device('NVMe', physical['model']) != 'match'):
                    raise MaintenanceError('Invalid SSD ownership target')
                _record(value, target)
            except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as error:
                raise MaintenanceError('Invalid SSD ownership state; preserve it for inspection') from error
            if value['status'] == 'installed':
                raise MaintenanceError('SSD repair owns boot paths; restore it before applying this component')


def _legacy_conflicts(root, engine):
    # Historical installer journals and adapter records have independent boot
    # ownership. No self-declared digest or selected model authorizes layering.
    for path in (Path(root)/'home/.oxp3-installer/integration',
                 Path(root)/'home/.boiler/oxp3-adapter'):
        if os.path.lexists(path):
            raise MaintenanceError('Legacy installer or adapter ownership requires an authenticated handoff')
    _legacy_noninstaller_conflicts(root, engine)


def _legacy_noninstaller_conflicts(root, engine, *, allowed_parent=None):
    state = engine._state()
    if _overlaps_owned(state['owners']):
        raise MaintenanceError('EFI configuration is owned by another maintenance component')
    if os.path.lexists(engine.state_dir/'adoption-pending.json'):
        raise MaintenanceError('Pending adoption must be recovered first')
    if engine.journals.exists():
        for journal in engine.journals.glob('*.json'):
            if journal.is_symlink():
                raise MaintenanceError('Unsafe generic maintenance journal')
            value=_read(journal)
            if value.get('status') not in ('committed', 'restored', 'rolled_back'):
                if allowed_parent is None or not allowed_parent(journal,value):
                    raise MaintenanceError('Pending generic maintenance must be recovered first')
    legacy = Path(root)/'home/.oxp3-maintenance'
    if not os.path.lexists(legacy):
        return
    with _directory(legacy):
        pass
    if os.path.lexists(legacy/'pending.json'):
        raise MaintenanceError('Pending legacy maintenance must be recovered first')
    index_path = legacy/'index.json'
    if os.path.lexists(index_path):
        with _file(index_path, private=True) as (stream, info):
            if info.st_size > 128 * 1024 * 1024:
                raise MaintenanceError('Legacy maintenance index is oversized')
            value = json.load(stream)
        if not isinstance(value, dict) or not isinstance(value.get('slots'), dict):
            raise MaintenanceError('Invalid legacy maintenance index')
        for record in value['slots'].values():
            if not isinstance(record, dict):
                raise MaintenanceError('Invalid legacy maintenance slot')
            for key in ('current', 'previous'):
                generation = record.get(key)
                if generation is not None:
                    if not isinstance(generation, dict) or not isinstance(generation.get('paths'), dict):
                        raise MaintenanceError('Invalid legacy maintenance generation')
                    if _overlaps_owned(generation['paths']):
                        raise MaintenanceError('Legacy maintenance owns EFI boot settings; explicit migration is required')


def _activation(root, installer_context):
    """Report the running kernel parameter, never physical-device acceptance."""
    if installer_context is not None or os.path.abspath(root) != os.path.abspath(os.sep):
        return 'first_boot_required'
    try:
        with open('/sys/module/nvme/parameters/noacpi', 'r', encoding='ascii') as stream:
            value = stream.read(32).strip().lower()
    except (OSError, UnicodeError):
        return 'unknown'
    if value in ('y', '1'):
        return 'active'
    if value in ('n', '0'):
        return 'inactive'
    return 'unknown'


def _result(root, context, action, changed):
    activation = _activation(root, context)
    status = ('verified' if action == 'verify' else 'recovered' if action == 'recover'
              else 'restored' if action == 'restore' else 'configured' if changed else 'already_configured')
    restart_required = None
    if action in ('apply', 'verify') and activation in ('active', 'inactive'):
        restart_required = activation == 'inactive'
    return dict(ok=True, action=action, changed=changed, status=status,
                activation=activation, restart_required=restart_required)


class SSD:
    def __init__(self, root, *, installer_context=None, model_preflight=None, provider_owner=None):
        self.provider_owner = _provider_owner(provider_owner) if provider_owner is not None else None
        self.model_preflight = model_preflight
        self.root = Path(os.path.abspath(root))
        self.context = installer_context
        self.store = Store(self.root, self.root/STATE)

    def _name(self, bound):
        return 'esp-' + bound['esp_partuuid'] + '.json'

    def _handoff(self, bound, record, pending):
        saved = record.get('handoff') if isinstance(record,dict) and record.get('version') in (3,4) else None
        if saved is None and isinstance(pending,dict):
            new=pending.get('new')
            if isinstance(new,dict) and new.get('version') in (3,4):
                saved=new.get('handoff')
        preview=self.model_preflight if self.model_preflight is not None else (saved or {}).get('preflight')
        if self.model_preflight is not None and record is not None and saved is None:
            raise MaintenanceError('Existing SSD ownership cannot be adopted into a model handoff')
        if saved is not None and self.model_preflight is not None and saved.get('preflight') != self.model_preflight:
            raise MaintenanceError('Reviewed model-to-SSD preflight changed')
        if preview is None:
            return None
        from .model_efi import validate_model_handoff
        observed, proof, model_bytes=validate_model_handoff(self.root,self.context,preview,
            expected_proof=saved.get('model_proof') if saved else None)
        if observed != bound:
            raise MaintenanceError('Model-to-SSD topology changed')
        return dict(preflight=preview,model_proof=proof),model_bytes

    def _inspect(self, action):
        if action not in ('apply', 'verify', 'restore', 'recover'):
            raise MaintenanceError('Unknown SSD repair action')
        bound = binding(self.root, installer_context=self.context)
        if bound['disposition'] != 'match':
            raise MaintenanceError('Reviewed SSD is not the supported GM7 model')
        engine = Engine(self.root, read_only=True)
        if os.path.lexists(self.store.state):
            with _directory(self.store.state) as fd:
                if os.name != 'nt' and os.fstat(fd).st_mode & 0o077:
                    raise MaintenanceError('SSD repair state directory must be private')
        record = self.store._state_read(self._name(bound))
        pending = self.store._state_read('pending.json')
        saved=record if record is not None else (pending.get('new') if isinstance(pending,dict) else None)
        saved_owner=_provider_owner(saved['provider_owner']) if isinstance(saved,dict) and saved.get('version') == 4 else None
        requested=getattr(self,'provider_owner',None)
        if requested is not None and saved is not None and saved_owner != requested:
            raise MaintenanceError('Existing SSD ownership is not this authenticated provider')
        owner=saved_owner if saved is not None else requested
        layered=self._handoff(bound,record,pending)
        if layered is None:
            _legacy_conflicts(self.root,engine)
        else:
            _legacy_noninstaller_conflicts(self.root,engine)
        current = _snapshot(self.root, bound)
        if pending is not None:
            if action != 'recover':
                raise MaintenanceError('Interrupted SSD repair requires explicit recovery')
            if not isinstance(pending, dict) or pending.get('target') != _durable(bound):
                raise MaintenanceError('Interrupted SSD repair belongs to a different root or EFI partition')
        elif action == 'recover':
            raise MaintenanceError('No interrupted SSD repair')
        if record is not None:
            original, applied = _record(record, bound)
            expected = applied if record['status'] == 'installed' else original
            if pending is None and current != expected:
                raise MaintenanceError('Managed EFI or drop-in bytes changed; preserve them for inspection')
        if layered is not None:
            handoff,model_bytes=layered
            baseline=record['original'] if record is not None else (pending['new']['original'] if pending else current)
            preview=handoff['preflight']
            if (baseline['efi'] != _encode(model_bytes)
                    or digest(baseline['dropin']) != preview['dropin_before_sha256']
                    or digest(_desired(baseline)['dropin']) != preview['dropin_after_sha256']
                    or hashlib.sha256(_decode(_desired(baseline)['efi'])).hexdigest() != preview['ssd_efi_sha256']):
                raise MaintenanceError('SSD originals or intended bytes differ from the reviewed model handoff')
        if action == 'apply':
            _desired(current if record is None else original)
        if action in ('verify', 'restore') and (record is None or record['status'] != 'installed'):
            raise MaintenanceError('No installed SSD repair on this EFI partition')
        body = dict(kind='ReviewedSsdBootPlan', version=2, action=action, target=bound,
            current_sha256=digest(current), record_sha256=digest(record), pending_sha256=digest(pending))
        if layered is not None:
            body['model_handoff']=layered[0]
        if owner is not None:
            body['provider_owner']=owner
        body['review_sha256'] = digest(body)
        return body, current, record, pending

    def plan(self, action):
        return self._inspect(action)[0]

    def inspect(self):
        bound = binding(self.root, installer_context=self.context)
        if bound['disposition'] != 'match':
            return dict(target=bound, available=False, reason='not_applicable')
        engine = Engine(self.root, read_only=True)
        if os.path.lexists(self.store.state):
            with _directory(self.store.state) as fd:
                if os.name != 'nt' and os.fstat(fd).st_mode & 0o077:
                    raise MaintenanceError('SSD repair state directory must be private')
        record = self.store._state_read(self._name(bound))
        pending = self.store._state_read('pending.json')
        saved=record if record is not None else (pending.get('new') if isinstance(pending,dict) else None)
        saved_owner=_provider_owner(saved['provider_owner']) if isinstance(saved,dict) and saved.get('version') == 4 else None
        requested=getattr(self,'provider_owner',None)
        if requested is not None and saved is not None and saved_owner != requested:
            raise MaintenanceError('Existing SSD ownership is not this authenticated provider')
        owner=saved_owner if saved is not None else requested
        layered=self._handoff(bound,record,pending)
        if layered is None:
            _legacy_conflicts(self.root,engine)
        else:
            _legacy_noninstaller_conflicts(self.root,engine)
        if record is not None:
            original, applied = _record(record, bound)
            if pending is None and _snapshot(self.root, bound) != (applied if record['status']=='installed' else original):
                raise MaintenanceError('Managed EFI or drop-in bytes changed; preserve them for inspection')
        return dict(target=bound, available=True, installed=record is not None and record['status']=='installed',
                    pending=pending is not None,provider_owner=owner)

    def execute(self, reviewed, *, reviewed_sha256):
        if os.name != 'posix' or os.geteuid() != 0:
            raise MaintenanceError('SSD repair requires Linux root')
        if not isinstance(reviewed, dict) or reviewed.get('review_sha256') != reviewed_sha256:
            raise MaintenanceError('SSD repair review digest differs')
        body = {k: v for k, v in reviewed.items() if k != 'review_sha256'}
        if digest(body) != reviewed_sha256:
            raise MaintenanceError('SSD repair review is corrupt')
        if reviewed.get('action') == 'verify':
            with Store(self.root,self.root/'home/.boiler/boot-ownership-v1').lock():
                fresh, _, _, _ = self._inspect('verify')
                if fresh != reviewed:
                    raise MaintenanceError('SSD repair target or state changed; review again')
                return _result(self.root, self.context, 'verify', False)
        engine = Engine(self.root)
        legacy = self.root/'home/.oxp3-maintenance'
        with engine._lock():
            if os.path.lexists(legacy) and not os.path.lexists(legacy/'lock'):
                raise MaintenanceError('Existing legacy maintenance has no lock')
            with _legacy_lock(engine, blocking=False) if os.path.lexists(legacy) else contextlib.nullcontext():
                with Store(self.root,self.root/'home/.boiler/boot-ownership-v1').lock():
                    with self.store.lock():
                        fresh, current, record, pending = self._inspect(reviewed.get('action'))
                        if fresh != reviewed:
                            raise MaintenanceError('SSD repair target or state changed; review again')
                        bound = fresh['target']
                        action = fresh['action']
                        if action == 'recover':
                            return self._recover(bound, current, record, pending)
                        if action == 'apply':
                            original = current if record is None else record['original']
                            after = _desired(original)
                            if record is not None and record['status'] == 'installed' and current == after:
                                return _result(self.root, self.context, action, False)
                            new = dict(version=2, target=_durable(bound), original=original,
                                       applied=after, status='installed')
                            if fresh.get('model_handoff') is not None:
                                new.update(version=3,handoff=fresh['model_handoff'])
                            if fresh.get('provider_owner') is not None:
                                new.update(version=4,provider_owner=fresh['provider_owner'])
                        else:
                            after = record['original']
                            new = dict(record, status='restored')
                        intent = dict(version=2, target=_durable(bound), before=current,
                                      after=after, old=record, new=new)
                        self.store._state_write('pending.json', intent)
                        _replace(self.root, current, after, bound, self.context)
                        self.store._state_write(self._name(bound), new)
                        self._remove_pending()
                        return _result(self.root, self.context, action, current != after)

    def _remove_pending(self):
        (self.store.state/'pending.json').unlink()
        self.store._sync_state()

    def _recover(self, bound, current, record, pending):
        if (set(pending) != {'version','target','before','after','old','new'} or pending['version'] != 2):
            raise MaintenanceError('Invalid SSD repair recovery intent')
        before, after = pending['before'], pending['after']
        new_original, new_applied = _record(pending['new'], bound)
        old = pending['old']
        if old is not None:
            old_original, old_applied = _record(old, bound)
            if (old_original != new_original or old_applied != new_applied
                    or old.get('handoff') != pending['new'].get('handoff')
                    or old.get('provider_owner') != pending['new'].get('provider_owner')):
                raise MaintenanceError('Recovery intent changed the first originals or model handoff')
            expected_before = old_applied if old['status'] == 'installed' else old_original
        else:
            expected_before = new_original
        expected_after = new_applied if pending['new']['status'] == 'installed' else new_original
        if before != expected_before or after != expected_after or record not in (old, pending['new']):
            raise MaintenanceError('Recovery intent is inconsistent')
        if not isinstance(current, dict) or set(current) != {'efi','dropin'} or any(
                current[key] not in (before[key], after[key]) for key in ('efi','dropin')):
            raise MaintenanceError('EFI or drop-in changed during interrupted repair')
        _replace(self.root, current, before, bound, self.context)
        if old is None:
            path = self.store.state/self._name(bound)
            if path.exists():
                path.unlink()
                self.store._sync_state()
        else:
            self.store._state_write(self._name(bound), old)
        self._remove_pending()
        return _result(self.root, self.context, 'recover', current != before)


def installer_preflight_model_ssd(root, host_context, model_plan, *, provider_owner=None):
    """Read-only preview of this one fixed model-then-SSD sequence.

    The outer installer authenticates the source and selection independently;
    this review digest is not an authentication credential.
    """
    if not isinstance(model_plan,dict) or model_plan.get('kind') != 'ReviewedModelEfiPlan' or model_plan.get('action') != 'apply':
        raise MaintenanceError('A reviewed model EFI apply plan is required')
    review=model_plan.get('review_sha256')
    if not isinstance(review,str) or digest({k:v for k,v in model_plan.items() if k != 'review_sha256'}) != review:
        raise MaintenanceError('Model EFI plan review differs')
    root=Path(os.path.abspath(root))
    bound=binding(root,installer_context=host_context)
    if bound['disposition'] != 'match' or model_plan.get('target') != bound:
        raise MaintenanceError('SSD preflight target differs or is not the supported model')
    if os.path.lexists(root/STATE):
        raise MaintenanceError('Fresh model-to-SSD preflight requires absent SSD ownership state')
    engine=Engine(root,read_only=True)
    _legacy_conflicts(root,engine)
    observed=_snapshot(root,bound)
    original=_decode(observed['efi'])
    from .model_efi import _render
    expected=_render(original)
    if (model_plan.get('current_sha256') != hashlib.sha256(original).hexdigest()
            or model_plan.get('expected_efi') != _encode(expected)
            or model_plan.get('expected_efi_sha256') != hashlib.sha256(expected).hexdigest()):
        raise MaintenanceError('Reviewed model bytes or original EFI configuration changed')
    projected=dict(observed,efi=_encode(expected))
    final=_desired(projected)
    body=dict(kind='ReviewedModelSsdPreflight',version=1,target=_durable(bound),
              model_plan_sha256=review,source=model_plan.get('source'),
              original_efi_sha256=hashlib.sha256(original).hexdigest(),
              model_efi_sha256=hashlib.sha256(expected).hexdigest(),
              ssd_efi_sha256=hashlib.sha256(_decode(final['efi'])).hexdigest(),
              dropin_before_sha256=digest(observed['dropin']),dropin_after_sha256=digest(final['dropin']))
    if provider_owner is not None:
        body['provider_owner']=_provider_owner(provider_owner)
    body['review_sha256']=digest(body)
    return body


def installer_model_ssd_plan(root, host_context, model_preflight, *, provider_owner=None):
    if model_preflight.get('provider_owner') != provider_owner:
        raise MaintenanceError('Model SSD preflight provider differs')
    return SSD(root,installer_context=host_context,model_preflight=model_preflight,provider_owner=provider_owner).plan('apply')


def installer_model_ssd_execute(root, host_context, model_preflight, reviewed, *, provider_owner=None):
    if not isinstance(reviewed,dict) or reviewed.get('action') != 'apply':
        raise MaintenanceError('Installer requires a reviewed model-to-SSD apply plan')
    if model_preflight.get('provider_owner') != provider_owner:
        raise MaintenanceError('Model SSD preflight provider differs')
    return SSD(root,installer_context=host_context,model_preflight=model_preflight,provider_owner=provider_owner).execute(
        reviewed,reviewed_sha256=reviewed.get('review_sha256'))


def installer_plans(root_a, root_b, host_context, *, provider_owner=None):
    """Review both explicitly mounted slots before either EFI write.

    This API is only for the managed installer adapter after it has received its
    physical context over the authenticated private guest channel.
    """
    plans = [SSD(root_a, installer_context=host_context, provider_owner=provider_owner).plan('apply'),
             SSD(root_b, installer_context=host_context, provider_owner=provider_owner).plan('apply')]
    a, b = (item['target'] for item in plans)
    if (a['slot'] != 'A' or b['slot'] != 'B' or a['disk'] != b['disk']
            or a['home_uuid'] != b['home_uuid']
            or a['root_partuuid'] == b['root_partuuid']
            or a['esp_partuuid'] == b['esp_partuuid']):
        raise MaintenanceError('Installer slots do not bind distinct partitions on the same disk and shared home')
    return plans


def installer_execute(root, host_context, reviewed, *, provider_owner=None):
    if not isinstance(reviewed, dict) or reviewed.get('action') != 'apply':
        raise MaintenanceError('Installer requires a reviewed SSD apply plan')
    return SSD(root, installer_context=host_context, provider_owner=provider_owner).execute(
        reviewed, reviewed_sha256=reviewed.get('review_sha256'))
