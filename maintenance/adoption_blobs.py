"""Bounded, private binary snapshots for explicit legacy adoption receipts v2.

Source bundles never execute code. All blobs and live files are preflighted
before ownership changes. Adoption writes journals/ownership only; legacy files
and their recovery records remain untouched. Failed batches resume explicitly.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import uuid

from protocol.manifest import confined, relative_path
from .engine import MaintenanceError, _json_write, _read, _same, _sync_dir, _overlapping_targets

MAX_FILE = 256*1024**2
MAX_TOTAL = 8*1024**3
MAX_OPERATIONS = 20000
HASH = re.compile(r'[0-9a-f]{64}\Z')
STORAGE = 'blob-v1'
PENDING = 'adoption-pending.json'
_HELD_LEGACY = contextvars.ContextVar('boiler_held_legacy_lock',default=None)


def _pairs(entries):
    value = {}
    for key, item in entries:
        if key in value:
            raise MaintenanceError('Duplicate adoption lifecycle key')
        value[key] = item
    return value


def _signature(info):
    return tuple(getattr(info, field) for field in
                 ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


@contextlib.contextmanager
def _directory(path, *, user_from=None, user_uid=None, create=False, create_mode=0o700):
    """Hold every parent through no-follow directory traversal (Linux runtime)."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise MaintenanceError('Unsafe adoption directory')
    if os.name == 'nt':
        for current in reversed([path, *path.parents]):
            if not current.exists():
                if create:
                    current.mkdir(mode=create_mode)
                else:
                    raise FileNotFoundError(current)
            if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                raise MaintenanceError('Unsafe adoption directory')
        yield None
        return
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        def check(fd, current):
            info = os.fstat(fd)
            allowed = (0, user_uid) if user_uid is not None and user_from is not None and current.is_relative_to(user_from) else (0,)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid not in allowed:
                raise MaintenanceError('Untrusted adoption directory')
            if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
                raise MaintenanceError('Writable adoption directory')
        current = Path(path.anchor)
        check(descriptor, current)
        for part in path.parts[1:]:
            current /= part
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, create_mode, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            try:
                check(child, current)
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def _file(path, *, private=False, user_from=None, user_uid=None):
    path = Path(path).absolute()
    with _directory(path.parent, user_from=user_from, user_uid=user_uid) as directory:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
        fd = os.open(path if os.name == 'nt' else path.name, flags, **({} if os.name == 'nt' else {'dir_fd': directory}))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise MaintenanceError('Adoption source must be a single-link regular file')
            if os.name != 'nt' and (info.st_uid not in (0, user_uid) or info.st_mode & 0o022):
                raise MaintenanceError('Adoption file ownership is untrusted')
            if private and os.name != 'nt' and (info.st_uid != 0 or info.st_mode & 0o077):
                raise MaintenanceError('Adoption blob must be root-owned and private')
            with os.fdopen(fd, 'rb') as stream:
                fd = None
                yield stream, info
                if _signature(os.fstat(stream.fileno())) != _signature(info):
                    raise MaintenanceError('Adoption source changed while reading')
        finally:
            if fd is not None:
                os.close(fd)


def _hash_file(path, expected, *, private=False, destination=None, limit=MAX_FILE, user_from=None, user_uid=None):
    if type(expected.get('size')) is not int or not 0 <= expected['size'] <= limit or not HASH.fullmatch(expected.get('sha256', '')):
        raise MaintenanceError('Invalid adoption blob digest or size')
    with _file(path, private=private, user_from=user_from, user_uid=user_uid) as (stream, info):
        if info.st_size != expected['size']:
            raise MaintenanceError('Adoption file size changed')
        digest, size = hashlib.sha256(), 0
        while True:
            block = stream.read(min(1024*1024, expected['size']+1-size))
            if not block:
                break
            size += len(block)
            if size > expected['size']:
                raise MaintenanceError('Adoption file grew while reading')
            digest.update(block)
            if destination is not None:
                destination.write(block)
        if size != expected['size'] or digest.hexdigest() != expected['sha256']:
            raise MaintenanceError('Adoption file digest changed')
        return info


def _target_user(engine, path):
    home = engine.root/'home/deck'
    if not path.is_relative_to(home):
        return {}
    passwd = confined(engine.root, 'etc/passwd')
    with _file(passwd) as (stream, info):
        if info.st_size > 65536:
            raise MaintenanceError('Invalid target account metadata')
        users = [line.split(':') for line in stream.read(65537).decode('utf-8').splitlines() if line.startswith('deck:')]
    if len(users) != 1 or not users[0][2].isdigit():
        raise MaintenanceError('Expected one target deck account')
    return dict(user_from=home, user_uid=int(users[0][2]))


def observe(engine, path):
    """Fingerprint a live target without retaining its bytes; absence is explicit."""
    path = Path(path)
    user = _target_user(engine, path)
    try:
        with _file(path, **user) as (stream, info):
            if info.st_size > MAX_FILE:
                raise MaintenanceError('Adopted file exceeds the explicit legacy blob limit')
            digest, size = hashlib.sha256(), 0
            while block := stream.read(1024*1024):
                size += len(block)
                if size > MAX_FILE:
                    raise MaintenanceError('Adopted file exceeds the explicit legacy blob limit')
                digest.update(block)
            return dict(exists=True, sha256=digest.hexdigest(), size=size,
                        mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
    except FileNotFoundError:
        # _directory traverses each existing parent without following links;
        # ENOENT therefore proves absence beneath a held protected ancestor.
        return {'exists': False}
    except OSError as error:
        raise MaintenanceError('Unsafe adopted target path') from error


def _snapshot(value, inventory):
    if not isinstance(value, dict) or type(value.get('exists')) is not bool:
        raise MaintenanceError('Invalid adoption snapshot')
    if not value['exists']:
        if value != {'exists': False}:
            raise MaintenanceError('Absent adoption snapshot has extra fields')
        return {'exists': False, 'storage': STORAGE}
    if set(value) != {'exists', 'blob', 'sha256', 'size', 'mode', 'uid', 'gid'}:
        raise MaintenanceError('Invalid binary adoption snapshot')
    name = value['blob']
    if not isinstance(name, str) or not HASH.fullmatch(name) or name != value['sha256'] or name not in inventory:
        raise MaintenanceError('Adoption snapshot is absent from blob inventory')
    if inventory[name]['size'] != value['size'] or type(value['size']) is not int:
        raise MaintenanceError('Adoption snapshot differs from blob inventory')
    if (any(type(value[key]) is not int or value[key] < 0 for key in ('mode', 'uid', 'gid')) or value['mode'] > 0o7777):
        raise MaintenanceError('Invalid adoption snapshot metadata')
    return {key: value[key] for key in ('exists', 'sha256', 'size', 'mode', 'uid', 'gid')} | {'storage': STORAGE}


def blob_cache(engine, *, create=False):
    """Fixed shared-home content store; slot ownership remains in state_dir.

    Objects are immutable SHA-addressed content only. No garbage collection is
    attempted: both installed root slots can still refer to these originals.
    """
    parent = engine.root/'home/.boiler'
    directory = parent/'maintenance-blobs'
    for path in (parent,directory):
        with _directory(path, create=create) as directory_fd:
            if os.name != 'nt' and os.fstat(directory_fd).st_mode & 0o077:
                raise MaintenanceError('Adoption CAS directories must be private')
    return directory


def _cache_blob(engine, source, entry, *, source_private=True, source_user=None):
    directory = blob_cache(engine, create=True)
    target = directory/entry['sha256']
    if target.exists() or target.is_symlink():
        _hash_file(target, entry, private=True)
        return
    fd, temporary = tempfile.mkstemp(prefix='.incoming-', dir=directory)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as output:
            _hash_file(source, entry, private=source_private, destination=output, **(source_user or {}))
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            _hash_file(target, entry, private=True)
        temporary.unlink()
        _sync_dir(directory)
    finally:
        temporary.unlink(missing_ok=True)


def capture(engine, target, snapshot=None):
    """Stream a current target into the same durable store used by adoption."""
    snapshot = observe(engine,target) if snapshot is None else snapshot
    if snapshot['exists']:
        _cache_blob(engine,target,snapshot,source_private=False,source_user=_target_user(engine,target))
    return snapshot | {'storage':STORAGE}


def payload_snapshot(engine, source, entry, before):
    if type(entry.get('size')) is not int or not 0 <= entry['size'] <= MAX_FILE:
        raise MaintenanceError('Component file exceeds the explicit binary transaction limit')
    _cache_blob(engine,source,entry,source_private=False)
    return dict(exists=True,storage=STORAGE,sha256=entry['sha256'],size=entry['size'],
                mode=entry.get('mode',0o644),uid=before.get('uid',0),gid=before.get('gid',0))


def restore_blob(engine, target, snapshot):
    """Restore an authenticated private CAS object with an atomic file replace."""
    source = blob_cache(engine)/snapshot['sha256']
    user = _target_user(engine, target)
    mode = 0o700 if target.is_relative_to(engine.root/'home/.boiler') else 0o755
    with _directory(target.parent, create=not bool(user), create_mode=mode, **user) as directory:
        name = '.boiler-restore-'+uuid.uuid4().hex
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(target.parent/name if os.name == 'nt' else name, flags, 0o600,
                     **({} if os.name == 'nt' else {'dir_fd': directory}))
        try:
            with os.fdopen(fd, 'wb') as output:
                fd = None
                _hash_file(source, snapshot, private=True, destination=output)
                output.flush()
                os.fsync(output.fileno())
                if os.name != 'nt':
                    os.fchown(output.fileno(), snapshot['uid'], snapshot['gid'])
                    os.fchmod(output.fileno(), snapshot['mode'])
                os.fsync(output.fileno())
            if os.name == 'nt':
                os.replace(target.parent/name, target)
            else:
                os.replace(name, target.name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
        finally:
            if fd is not None:
                os.close(fd)
            if os.name == 'nt':
                (target.parent/name).unlink(missing_ok=True)
            else:
                try:
                    os.unlink(name, dir_fd=directory)
                except FileNotFoundError:
                    pass


def remove_blob_target(engine, target):
    """Remove an explicitly absent original through its protected parent."""
    try:
        with _directory(target.parent, **_target_user(engine, target)) as directory:
            if os.name == 'nt':
                target.unlink(missing_ok=True)
            else:
                try:
                    os.unlink(target.name, dir_fd=directory)
                    os.fsync(directory)
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        pass


def validate_backups(engine, operations):
    """Authenticate every needed CAS original before any restore write."""
    checked = set()
    for operation in operations:
        snapshot = operation['before']
        if snapshot.get('storage') == STORAGE and snapshot.get('exists'):
            target = engine._target(operation['target'])
            user = _target_user(engine,target)
            if user:
                try:
                    with _directory(target.parent,**user):
                        pass
                except FileNotFoundError as error:
                    raise MaintenanceError('Restore requires an existing protected user directory') from error
            key = (snapshot['sha256'], snapshot['size'])
            if key not in checked:
                _hash_file(blob_cache(engine)/snapshot['sha256'], snapshot, private=True)
                checked.add(key)


@contextlib.contextmanager
def _legacy_lock(engine,*,blocking=True):
    path = engine.root/'home/.oxp3-maintenance/lock'
    with _file(path, private=True) as (stream, info):
        held=_HELD_LEGACY.get()
        if held is not None:
            expected=(os.getpid(),threading.get_ident(),str(engine.root),info.st_dev,info.st_ino)
            if held[:5] != expected or _signature(os.fstat(held[5])) != _signature(info):
                raise MaintenanceError('Nested legacy lock context differs from the protected store')
            yield
            return
        if os.name != 'nt':
            import fcntl
            try: fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as error:
                raise MaintenanceError('Legacy maintenance is active; retry after it finishes') from error
        token=_HELD_LEGACY.set((os.getpid(),threading.get_ident(),str(engine.root),info.st_dev,info.st_ino,stream.fileno()))
        try:
            yield
        finally:
            _HELD_LEGACY.reset(token)
            if os.name != 'nt':
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def adopt(engine, receipt_path, receipt, receipt_digest):
    from protocol import validate_manifest
    if (receipt.get('schema_version') != 2 or receipt.get('kind') != 'LegacyAdoptionReceipt' or
            receipt.get('device_id') != 'oxp3' or receipt.get('slot') not in ('A', 'B') or
            receipt.get('preserve_legacy') is not True or receipt.get('translation_complete') is not True):
        raise MaintenanceError('Unsupported binary adoption receipt')
    if (not isinstance(receipt.get('legacy_slot'), str) or
            not re.fullmatch(r'(?:partuuid-[a-z0-9-]{8,64}|[AB])', receipt['legacy_slot']) or
            receipt.get('source_format') not in ('oxp3-maintenance-format-1', 'oxp3-installer-logical-files-v1') or
            not isinstance(receipt.get('source_release'), dict) or receipt.get('migration_complete') is not False):
        raise MaintenanceError('Invalid binary adoption source identity')
    if len(__import__('json').dumps(receipt).encode('utf-8')) > 8*1024**2:
        raise MaintenanceError('Binary adoption receipt metadata exceeds size limit')
    public_key = Path(__file__).resolve().parents[1]/'protocol/trusted-public-key.pem'
    if receipt.get('trusted_public_key', {}).get('sha256') != hashlib.sha256(public_key.read_bytes()).hexdigest():
        raise MaintenanceError('Legacy signing anchor differs; implicit key replacement forbidden')
    evidence = receipt.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= MAX_OPERATIONS:
        raise MaintenanceError('Invalid adoption evidence')
    evidence_paths = set()
    for record in evidence:
        if (not isinstance(record, dict) or set(record) != {'path', 'size', 'sha256', 'role'} or
                not isinstance(record['role'], str) or not record['role'] or
                type(record['size']) is not int or not 0 <= record['size'] <= MAX_TOTAL or
                not isinstance(record['sha256'], str) or not HASH.fullmatch(record['sha256'])):
            raise MaintenanceError('Invalid adoption evidence descriptor')
        name = relative_path(record['path'])
        if name in evidence_paths:
            raise MaintenanceError('Duplicate adoption evidence path')
        evidence_paths.add(name)
    if receipt['trusted_public_key'] not in evidence or receipt['trusted_public_key']['role'] != 'trusted-public-key':
        raise MaintenanceError('Signing anchor is absent from adoption evidence')
    index_name = 'home/.oxp3-maintenance/index.json'
    if receipt['source_format'] == 'oxp3-maintenance-format-1' and not any(
            record['path'] == index_name and record['role'] == 'maintenance-index' for record in evidence):
        raise MaintenanceError('Maintenance index is absent from adoption evidence')
    receipt_path = Path(receipt_path).absolute()
    if receipt.get('blob_directory') != receipt_path.name+'.blobs':
        raise MaintenanceError('Invalid private adoption blob directory')
    bundle = confined(receipt_path.parent, receipt['blob_directory'])
    inventory = {}
    records = receipt.get('blobs')
    if not isinstance(records, list) or len(records) > 2*MAX_OPERATIONS:
        raise MaintenanceError('Invalid adoption blob inventory')
    for record in records:
        if (not isinstance(record, dict) or set(record) != {'size', 'sha256'} or
                type(record['size']) is not int or not 0 <= record['size'] <= MAX_FILE or
                not isinstance(record['sha256'], str) or not HASH.fullmatch(record['sha256']) or record['sha256'] in inventory):
            raise MaintenanceError('Invalid adoption blob inventory')
        inventory[record['sha256']] = record
    if sum(item['size'] for item in inventory.values()) > MAX_TOTAL:
        raise MaintenanceError('Adoption blob inventory exceeds total byte limit')
    with _directory(bundle) as directory:
        info = bundle.stat() if os.name == 'nt' else os.fstat(directory)
        if os.name != 'nt' and info.st_mode & 0o077:
            raise MaintenanceError('Adoption blob directory must be private')
        actual = set(os.listdir(bundle if os.name == 'nt' else directory))
        if actual != set(inventory):
            raise MaintenanceError('Adoption blob inventory differs from directory')
    entries = receipt.get('components')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise MaintenanceError('Invalid adoption components')
    prepared, targets, ids, used = [], set(), set(), set()
    referenced_bytes = 0
    for entry in entries:
        component = dict(schema_version=1, kind='ComponentManifest', id=entry['id'], version=entry['version'], files=[])
        validate_manifest(component, 'ComponentManifest')
        if component['id'] in ids:
            raise MaintenanceError('Duplicate adoption component')
        ids.add(component['id'])
        operations = []
        if not isinstance(entry.get('operations'), list) or not entry['operations']:
            raise MaintenanceError('Invalid adoption operations')
        for item in entry['operations']:
            target = relative_path(item['target'])
            if target.casefold() in targets:
                raise MaintenanceError('Duplicate adoption target')
            targets.add(target.casefold())
            if len(targets) > MAX_OPERATIONS:
                raise MaintenanceError('Adoption operation limit exceeded')
            before, after = _snapshot(item['before'], inventory), _snapshot(item['after'], inventory)
            for snapshot in (before, after):
                if snapshot['exists']:
                    used.add(snapshot['sha256'])
                    referenced_bytes += snapshot['size']
                    if referenced_bytes > MAX_TOTAL:
                        raise MaintenanceError('Adoption referenced byte limit exceeded')
            operations.append(dict(target=target, before=before, after=after))
        prepared.append((component, operations))
    if used != set(inventory):
        raise MaintenanceError('Adoption inventory contains unreferenced blobs')
    if _overlapping_targets(targets):
        raise MaintenanceError('Overlapping adoption target paths')
    with _directory(engine.state_dir):
        pass
    with engine._lock(), _legacy_lock(engine):
        pending_path = confined(engine.state_dir, PENDING)
        if os.path.lexists(pending_path) and _read(pending_path).get('receipt_sha256') != receipt_digest:
            raise MaintenanceError('Resume the existing adoption receipt before starting another migration')
        engine._recover(allow_adoption=True, legacy_locked=True)
        if os.path.lexists(engine.root/'home/.oxp3-maintenance/pending.json'):
            raise MaintenanceError('Legacy recovery must complete before adoption')
        for evidence in receipt['evidence']:
            path = engine._target(evidence['path'])
            # Index evidence can be large; only streaming fingerprints are kept.
            _hash_file(path, evidence, private=evidence.get('role') == 'maintenance-index', limit=MAX_TOTAL)
        lifecycle = engine.root/'home/.oxp3-maintenance/live-operation.json'
        if os.path.lexists(lifecycle):
            if 'home/.oxp3-maintenance/live-operation.json' not in evidence_paths:
                raise MaintenanceError('Legacy lifecycle appeared after adoption export')
            with _file(lifecycle, private=True) as (stream, info):
                if info.st_size > 8*1024**2:
                    raise MaintenanceError('Legacy lifecycle metadata exceeds limit')
                record = __import__('json').loads(stream.read(8*1024**2+1), object_pairs_hook=_pairs)
            if record.get('phase') != 'coherent' or record.get('slot') != receipt['legacy_slot']:
                raise MaintenanceError('Legacy lifecycle requires recovery')
        for name, record in inventory.items():
            _hash_file(confined(bundle, name), record, private=True)
            _cache_blob(engine, confined(bundle, name), record)
        state, existing = engine._state(), []
        for component, operations in prepared:
            cid = component['id']
            if cid in state['components']:
                journal = _read(engine._journal(state['components'][cid]['transaction']))
                if (journal.get('root') != engine.identity or journal.get('status') != 'committed' or journal.get('adoption_receipt') != receipt_digest or
                        journal.get('component') != component or journal.get('operations') != operations):
                    raise MaintenanceError('Component already belongs to another adoption receipt')
                existing.append(journal['id'])
            for operation in operations:
                path = engine._target(operation['target'])
                owner = state['owners'].get(operation['target'])
                if owner and owner['component'] != cid:
                    raise MaintenanceError('Adoption ownership conflict')
                if owner and cid not in state['components']:
                    raise MaintenanceError('Adoption ownership state is incomplete')
                folded = operation['target'].casefold()
                if any(name != operation['target'] and (name.casefold() == folded or
                       name.casefold().startswith(folded+'/') or folded.startswith(name.casefold()+'/'))
                       for name in state['owners']):
                    raise MaintenanceError('Adoption overlaps existing ownership')
                if not _same(observe(engine, path), operation['after']):
                    raise MaintenanceError('Legacy target changed after binary translation')
        progress = dict(schema_version=1, kind='AdoptionMigration', receipt_sha256=receipt_digest,
                        components=sorted(ids), transactions=list(existing), status='applying', migration_complete=False)
        _json_write(pending_path, progress)
        try:
            for component, operations in prepared:
                if component['id'] in state['components']:
                    continue
                for operation in operations:
                    if not _same(observe(engine, engine._target(operation['target'])), operation['after']):
                        raise MaintenanceError('Legacy target changed before adoption ownership commit')
                transaction = uuid.uuid4().hex
                previous = __import__('copy').deepcopy(state)
                journal = dict(schema_version=1, id=transaction, root=engine.identity, status='adopting',
                               component=component, operations=operations, before_state=previous,
                               adoption_evidence=receipt['evidence'], adoption_receipt=receipt_digest,
                               legacy_lock_required=True)
                path = engine._journal(transaction)
                _json_write(path, journal)
                for operation in operations:
                    state['owners'][operation['target']] = dict(component=component['id'], transaction=transaction,
                                                                sha256=operation['after'].get('sha256'))
                state['components'][component['id']] = dict(version=component['version'], transaction=transaction,
                                                            requires=[], conflicts=[])
                _json_write(engine.state_dir/'state.json', state)
                journal['status'] = 'committed'
                _json_write(path, journal)
                progress['transactions'].append(transaction)
                _json_write(pending_path, progress)
            for _, operations in prepared:
                for operation in operations:
                    if not _same(observe(engine, engine._target(operation['target'])), operation['after']):
                        raise MaintenanceError('Legacy target changed before migration completion')
            progress.update(status='committed', migration_complete=True)
            history = engine.state_dir/'adoptions'
            with _directory(history, create=True):
                pass
            _json_write(confined(history, receipt_digest+'.json'), progress)
            pending_path.unlink()
            _sync_dir(engine.state_dir)
        except BaseException:
            progress.update(status='failed-preserved', migration_complete=False)
            _json_write(pending_path, progress)
            raise
        return dict(migration_complete=True, preserve_legacy=True, scope='verified-binary-file-ownership',
                    transactions=progress['transactions'])
