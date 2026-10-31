"""Fixed onexplayer fan private configuration lifecycle.

The caller holds the outer operation lock and persists each callback before it
returns. This module does not authorize or start the fan service.
"""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from maintenance.adoption_blobs import _directory, _file
from maintenance.fan_schema import IDENTITY, TARGET, DEFAULT, validate
from maintenance.engine import MaintenanceError

MAX_BYTES = 16384
DEFAULT_SHA = hashlib.sha256(DEFAULT).hexdigest()
GEN = re.compile(r'[0-9a-f]{32}\Z')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise MaintenanceError('Duplicate fan configuration key')
        result[key] = value
    return result


def _observe(root):
    path = Path(root) / TARGET
    try:
        with _file(path, private=True) as (stream, info):
            if info.st_mode & 0o7777 != 0o600 or info.st_gid != 0 or info.st_size > MAX_BYTES:
                raise MaintenanceError('Fan configuration requires root:root mode 0600')
            raw = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return {'exists': False}
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
        validate(value)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise MaintenanceError('Invalid fan configuration') from exc
    return {'exists': True, 'sha256': hashlib.sha256(raw).hexdigest(),
            'mode': 0o600, 'uid': 0, 'gid': 0, 'configuration': value, 'bytes_b64':base64.b64encode(raw).decode('ascii')}


def _check(state, generation):
    if not isinstance(generation, str) or not GEN.fullmatch(generation): raise MaintenanceError('Invalid fan configuration generation')
    if state is None: return
    if (not isinstance(state, dict) or state.get('kind') != 'fixed-fan-config-v1'
        or state.get('identity') != IDENTITY or state.get('target') != TARGET
        or state.get('generation') != generation or state.get('phase') not in
        ('prepared-existing', 'prepared-create', 'retained', 'created', 'restored')
        or type(state.get('original_exists')) is not bool
        or set(state) - {'kind','identity','target','generation','phase','original_exists','original_sha256',
                         'init_sha256','original_bytes_b64','original_mode','original_uid','original_gid','restore_pending','remove','preserved'}
        or state.get('init_sha256') != DEFAULT_SHA
        or (state['original_exists'] and (not isinstance(state.get('original_sha256'), str) or
            not re.fullmatch(r'[0-9a-f]{64}', state['original_sha256'])))
        or (not state['original_exists'] and state.get('original_sha256') is not None)
        or any(type(state[k]) is not bool for k in ('restore_pending','remove','preserved') if k in state)):
        raise MaintenanceError('Invalid fan configuration lifecycle state')
    if state['original_exists']:
        try: raw=base64.b64decode(state.get('original_bytes_b64',''),validate=True)
        except (ValueError,TypeError): raise MaintenanceError('Invalid original fan configuration snapshot')
        if (len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=state['original_sha256'] or
            (state.get('original_mode'),state.get('original_uid'),state.get('original_gid'))!=(0o600,0,0) or
            any(type(state.get(k)) is not int for k in ('original_mode','original_uid','original_gid'))):
            raise MaintenanceError('Invalid original fan configuration snapshot')
    elif any(state.get(k) is not None for k in ('original_bytes_b64','original_mode','original_uid','original_gid')):
        raise MaintenanceError('Invalid absent original fan configuration')
    if (state['phase'] in ('prepared-create', 'created') and state['original_exists']) or (
        state['phase'] in ('prepared-existing', 'retained') and not state['original_exists']):
        raise MaintenanceError('Inconsistent fan configuration lifecycle state')


def plan(root, *, generation, state=None):
    _check(state, generation)
    current = _observe(root)
    if state and (state['phase'] == 'restored' or state.get('restore_pending')):
        raise MaintenanceError('Fan generation is restoring or already restored')
    if state and state['phase'] in ('created', 'retained') and not current['exists']:
        raise MaintenanceError('Fan configuration was removed; do not recreate it automatically')
    if state and state['phase'] == 'prepared-create' and current['exists'] and current['sha256'] != DEFAULT_SHA:
        raise MaintenanceError('Fan configuration changed during interrupted initialization')
    return current


def _ensure_parents(root):
    current = Path(root).absolute()
    for part in ('var', 'lib', 'oxp3-fan'):
        parent = current
        current = current / part
        with _directory(current, create=True, create_mode=0o700) as child:
            os.fsync(child)
        with _directory(parent) as descriptor:
            os.fsync(descriptor)


def _clean_temporary(root, generation):
    path = Path(root) / TARGET
    try:
        with _directory(path.parent) as directory:
            name = '.boiler-config-' + generation
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or
                info.st_mode & 0o7777 != 0o600 or info.st_nlink not in (1, 2)):
                raise MaintenanceError('Unsafe interrupted fan temporary file')
            if info.st_nlink == 2:
                try: target = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError: raise MaintenanceError('Unexpected fan temporary links')
                if (target.st_dev, target.st_ino) != (info.st_dev, info.st_ino):
                    raise MaintenanceError('Unexpected fan temporary links')
            os.unlink(name, dir_fd=directory); os.fsync(directory)
    except FileNotFoundError:
        return


def ensure(root, *, generation, state, record_before_write):
    if not callable(record_before_write): raise MaintenanceError('A durable fan recorder is required')
    _check(state, generation)
    if state and state['phase'] == 'prepared-create': _clean_temporary(root, generation)
    current = plan(root, generation=generation, state=state)
    if state is None:
        state = {'kind':'fixed-fan-config-v1', 'identity':IDENTITY, 'target':TARGET,
                 'generation':generation, 'phase':'prepared-existing' if current['exists'] else 'prepared-create',
                 'original_exists':current['exists'], 'original_sha256':current.get('sha256'),
                 'original_bytes_b64':current.get('bytes_b64'), 'original_mode':current.get('mode'),
                 'original_uid':current.get('uid'), 'original_gid':current.get('gid'), 'init_sha256':DEFAULT_SHA}
        record_before_write(copy.deepcopy(state))
    else:
        state = copy.deepcopy(state)
    if state['phase'] == 'prepared-existing':
        if not current['exists']: raise MaintenanceError('Existing fan configuration disappeared')
        state['phase'] = 'retained'
        record_before_write(copy.deepcopy(state))
    elif state['phase'] == 'prepared-create':
        if not current['exists']:
            path = Path(root) / TARGET
            _ensure_parents(root)
            with _directory(path.parent) as directory:
                temporary = '.boiler-config-' + generation
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
                try:
                    os.fchmod(fd, 0o600)
                    with os.fdopen(fd, 'wb') as stream:
                        fd = None; stream.write(DEFAULT); stream.flush(); os.fsync(stream.fileno())
                finally:
                    if fd is not None: os.close(fd)
                os.link(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
        current = _observe(root)
        if not current['exists'] or current['sha256'] != DEFAULT_SHA:
            raise MaintenanceError('Fan initialization readback differs')
        state['phase'] = 'created'
        record_before_write(copy.deepcopy(state))
    return state


def verify(root, *, generation, state):
    current = plan(root, generation=generation, state=state)
    if state is None or state['phase'] not in ('created', 'retained'):
        raise MaintenanceError('Fan initialization is not committed')
    return {'identity': IDENTITY, 'configuration': current['configuration'], 'valid': True}


def restore(root, *, generation, state, record_before_write):
    _check(state, generation)
    if not callable(record_before_write) or state is None or state['phase'] not in ('created', 'retained'):
        raise MaintenanceError('Fan restoration requires a committed generation and durable recorder')
    current = _observe(root); state = copy.deepcopy(state)
    remove = state['phase'] == 'created' and current.get('sha256') == DEFAULT_SHA
    record_before_write(dict(state, restore_pending=True, remove=remove))
    if remove:
        path=Path(root)/TARGET
        with _directory(path.parent) as directory:
            # Re-observe under the caller-held operation lock immediately before unlink.
            if _observe(root).get('sha256') != DEFAULT_SHA:
                raise MaintenanceError('Fan config changed during restore')
            os.unlink(path.name, dir_fd=directory); os.fsync(directory)
    state.pop('restore_pending', None); state.pop('remove', None)
    state['phase']='restored'; state['preserved']=not remove and current['exists']
    record_before_write(copy.deepcopy(state))
    return state





