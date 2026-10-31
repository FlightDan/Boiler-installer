"""One optional KWin setting, using the unchanged public OXP3 mutable Store.

Install and maintenance must both call plan/execute. No service or guest binary
is invoked. The host must authenticate this runtime before calling it; a review
digest binds a user review to target/state, it is not package authentication.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path

from maintenance.engine import Engine, MaintenanceError, _read
from maintenance.adoption_blobs import _target_user, _directory, _file, observe
from maintenance.updates import target_environment
from maintenance import models
from protocol.manifest import confined
from maintenance.keyboard_target import binding

from .vendor.transactions import Store as PublicStore, TransactionError
from .merge import merge as _merge_group

IDENTITY = 'x2-mini:keyboard'
VERSION = '0.1.0-localtest.20261031.keyboard.1'
TARGET = 'home/deck/.config/kwinrc'
DESKTOP = 'usr/share/applications/org.kde.plasma.keyboard.desktop'
STATE = 'home/.boiler/keyboard-v1'
VALUES = {'InputMethod': '/' + DESKTOP, 'VirtualKeyboardMode': '1'}
BASELINE = {'os_id': 'steamos'}
NOTICE = {'zh-CN': '所选机型可能不匹配或无法识别；由您决定是否继续。',
          'en-US': 'The selected model may differ or be unidentified. You can choose whether to continue.'}
MUTABLE_NOTICE = {'zh-CN': '若安装后修改过 kwinrc，升级和恢复会保留整个现文件并明确报告；键盘设置可能保留。未修改时恢复真实原文件。',
                  'en-US': 'If kwinrc changes after installation, upgrade and restore preserve the entire edited file and report it; keyboard settings may remain. Unedited files restore to their true original.'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


class Store(PublicStore):
    """Keep the public engine unchanged; bind its journal to the reviewed merge.

The first generation otherwise captures its original after the host has formed
the merged bytes. A desktop edit in that interval must never become a new
original followed by an overwrite using the earlier merge.
"""
    def _commit(self, index, slot, generation, operations, **kwargs):
        expected = getattr(self, '_keyboard_expected', None)
        if (expected is None or self._read(TARGET, user=True) != expected
                or any(op.get('path') != TARGET or op.get('before') != expected for op in operations)):
            raise TransactionError('Keyboard target changed before journal commit; review again')
        return super()._commit(index, slot, generation, operations, **kwargs)


class Keyboard:
    """The False ownership mode exists only for portable synthetic tests.

    Production entry adapters never expose an ownership override. The original
    Store rejects production execution outside POSIX and keeps its native lock,
    durable snapshots, shared-home originals and pending-operation recovery.
    """
    def __init__(self, root, *, enforce_owner=True):
        self.root = Path(os.path.abspath(root))
        self.enforce_owner = enforce_owner
        # Constructor checks the supplied root without resolving redirects.
        Store(self.root, self.root / STATE, enforce_owner=enforce_owner)
        self.engine = Engine(self.root, read_only=True)

    def _preflight(self, apply=False):
        if self.enforce_owner:
            models._privileged()
        user = _target_user(self.engine, self.engine._target(TARGET))
        with _directory(self.root / 'home/deck', **user):
            pass
        target = observe(self.engine, self.engine._target(TARGET))
        if target.get('size', 0) > 4 * 1024 * 1024:
            raise MaintenanceError('Keyboard configuration exceeds the candidate size limit')
        state = self.engine._state()
        models.check_pending(self.engine)
        if os.path.lexists(self.engine.state_dir / 'adoption-pending.json'):
            raise MaintenanceError('Pending generic adoption requires its own recovery first')
        for path in self.engine.journals.glob('*.json'):
            if path.is_symlink() or _read(path).get('status') not in ('committed', 'restored', 'rolled_back'):
                raise MaintenanceError('Pending generic transaction requires its own recovery first')
        owner = state['owners'].get(TARGET)
        if owner:
            raise MaintenanceError('Keyboard target already belongs to a generic component; explicit migration required')
        # Do not silently acquire a still-managed legacy keyboard configuration.
        legacy = self.root / 'home/.oxp3-maintenance/index.json'
        if os.path.lexists(legacy):
            with _file(legacy, private=True) as (stream, info):
                if info.st_size > 128 * 1024 * 1024:
                    raise MaintenanceError('Legacy ownership state is oversized')
                value = json.load(stream)
            if any(TARGET in (record.get('current') or {}).get('paths', {})
                   for record in value.get('slots', {}).values()):
                raise MaintenanceError('Keyboard target is owned by legacy maintenance; explicit migration required')
        environment = target_environment(self.root) if apply else None
        if apply:
            if any(environment.get(key) != value for key, value in BASELINE.items()):
                raise MaintenanceError('Keyboard requires a SteamOS target')
            with _file(confined(self.root, DESKTOP)) as (stream, info):
                if not 0 < info.st_size <= 65536:
                    raise MaintenanceError('Keyboard desktop dependency is missing or oversized')
                # Presence is the established public dependency contract. Do
                # not infer that arbitrary Exec content is usable or activate it.
        store = Store(self.root, self.root / STATE, uid=user.get('user_uid', 0),
                      gid=self._gid(user), enforce_owner=self.enforce_owner)
        return store, environment, target

    def _gid(self, user):
        # _target_user already authenticated and validated passwd using this
        # same no-follow root-owned reader. Repeat under the outer Engine lock.
        with _file(confined(self.root, 'etc/passwd')) as (stream, info):
            if info.st_size > 65536:
                raise MaintenanceError('Target account metadata is oversized')
            rows = [line.split(':') for line in stream.read().decode().splitlines()
                    if line.startswith('deck:')]
        if (len(rows) != 1 or len(rows[0]) != 7 or not rows[0][2].isdigit()
                or not rows[0][3].isdigit() or int(rows[0][2]) != user['user_uid']
                or int(rows[0][2]) < 1000 or int(rows[0][3]) < 1000
                or rows[0][5] != '/home/deck'):
            raise MaintenanceError('Missing or ambiguous deck account')
        return int(rows[0][3])

    @classmethod
    def _state(cls, store):
        index = store._index()
        for scope in index.get('originals', {}):
            if scope != 'shared:' + TARGET:
                raise TransactionError('Keyboard state contains another target original')
        for slot, record in index.get('slots', {}).items():
            if not re.fullmatch(r'partuuid-[a-z0-9-]{8,64}', slot):
                raise TransactionError('Keyboard slot requires a stable partition identity')
            for key in ('current', 'previous'):
                generation = record.get(key)
                if generation is None:
                    continue
                if generation.get('runtime_directories'):
                    raise TransactionError('Keyboard state contains unrelated runtime directories')
                if set(generation.get('selected', [])) - {IDENTITY}:
                    raise TransactionError('Keyboard state contains another component')
                for path, entry in generation.get('paths', {}).items():
                    if (path != TARGET or entry.get('component') != IDENTITY
                            or not entry.get('user') or not entry.get('mutable')):
                        raise TransactionError('Keyboard state contains another target or contract')
        pending = store._state_read('pending.json')
        if pending is not None:
            if any(key.startswith('runtime_directory_') and value for key, value in pending.items()):
                raise TransactionError('Keyboard pending operation contains unrelated runtime directories')
            if any(op.get('path') != TARGET or not op.get('user') for op in pending.get('operations', [])):
                raise TransactionError('Keyboard pending operation contains another target')
            # Validate index_after with the same restrictions before any recovery.
            after = pending.get('index_after')
            if not isinstance(after, dict):
                raise TransactionError('Invalid keyboard pending index')
            cls._validate_pending_index(after)
        return index, pending

    @staticmethod
    def _validate_pending_index(index):
        if set(index.get('originals', {})) - {'shared:' + TARGET}:
            raise TransactionError('Keyboard pending index contains another original')
        for slot,record in index.get('slots', {}).items():
            if not re.fullmatch(r'partuuid-[a-z0-9-]{8,64}', slot):
                raise TransactionError('Keyboard pending slot requires a stable partition identity')
            for key in ('current', 'previous'):
                generation = record.get(key) or {}
                if generation.get('runtime_directories'):
                    raise TransactionError('Keyboard pending index contains unrelated runtime directories')
                if set(generation.get('selected', [])) - {IDENTITY}:
                    raise TransactionError('Keyboard pending index contains another component')
                for path, entry in generation.get('paths', {}).items():
                    if (path != TARGET or entry.get('component') != IDENTITY
                            or not entry.get('user') or not entry.get('mutable')):
                        raise TransactionError('Keyboard pending index contains another target')

    def options(self):
        available, error = True, None
        try:
            self._preflight(apply=True)
        except (ValueError, RuntimeError, OSError) as exc:
            available, error = False, str(exc)
        installed_slots, pending, pending_slot, lifecycle_error = [], False, None, None
        try:
            store, _, _ = self._preflight()
            if os.path.lexists(store.state):
                with store.lock():
                    index, queued = self._state(store)
                installed_slots = sorted(name for name, record in index['slots'].items()
                    if TARGET in (record.get('current') or {}).get('paths', {}))
                pending = queued is not None
                if pending:
                    pending_slot=queued.get('slot')
                    if not isinstance(pending_slot,str) or not re.fullmatch(r'partuuid-[a-z0-9-]{8,64}',pending_slot):
                        raise TransactionError('Invalid interrupted keyboard partition identity')
            if pending:
                available, error = False, 'Interrupted keyboard transaction; review Recover first'
        except (ValueError, RuntimeError, OSError) as exc:
            lifecycle_error = str(exc)
            available, error = False, str(exc)
        return dict(id=IDENTITY, default=False, selection_policy='explicit-opt-in',
                    available=available, reason=error, compatibility_notice=NOTICE,
                    mutable_notice=MUTABLE_NOTICE, hardware_accepted=False,
                    installed_slots=installed_slots, pending=pending, pending_slot=pending_slot,
                    verify_available=bool(installed_slots) and not pending,
                    restore_available=bool(installed_slots) and not pending,
                    recover_available=pending, lifecycle_reason=lifecycle_error)

    def _plan_locked(self, store, action, bound, environment):
        slot = bound['slot']
        index, pending = self._state(store)
        if action == 'recover' and pending is not None and pending.get('slot') != slot:
            raise TransactionError('Interrupted keyboard transaction belongs to original slot '+str(pending.get('slot'))+'; return to that slot to recover')
        if action != 'recover' and pending is not None:
            raise TransactionError('Interrupted keyboard transaction; review Recover first')
        snapshot = store._read(TARGET, user=True)
        if action == 'apply':
            # Validate the exact merge before writing any transaction journal.
            raw = base64.b64decode(snapshot['data']) if snapshot['exists'] else None
            if raw is not None and len(raw) > 4 * 1024 * 1024:
                raise MaintenanceError('Keyboard configuration exceeds the candidate size limit')
            _merge_group(raw, 'Wayland', VALUES)
            if self.enforce_owner and snapshot['exists'] and snapshot['mode'] & 0o7022:
                raise MaintenanceError('Keyboard configuration has unsafe permissions')
        info = self.root.stat()
        body = dict(kind='ReviewedKeyboardPlan', schema_version=1, component=IDENTITY,
                    version=VERSION, action=action, slot=slot, target_binding=bound, environment=environment,
                    account=dict(uid=store.uid, gid=store.gid),
                    root=dict(path=str(self.root), device=info.st_dev, inode=info.st_ino),
                    target_sha256=digest(snapshot), state_sha256=digest(index),
                    pending_sha256=digest(pending), compatibility_notice=NOTICE,
                    mutable_notice=MUTABLE_NOTICE)
        body['review_sha256'] = digest(body)
        return body

    def plan(self, action, slot):
        if action not in ('apply', 'restore', 'verify', 'recover') or slot not in ('A', 'B'):
            raise ValueError('Choose a known keyboard action and authoritative A/B slot')
        bound = binding(self.root, expected_label=slot)
        store, environment, _ = self._preflight(apply=action == 'apply')
        with store.lock():
            return self._plan_locked(store, action, bound, environment)

    def execute(self, reviewed, *, reviewed_sha256):
        body = {key: value for key, value in reviewed.items() if key != 'review_sha256'}
        if reviewed.get('review_sha256') != reviewed_sha256 or digest(body) != reviewed_sha256:
            raise MaintenanceError('Keyboard review digest does not match')
        action, slot = reviewed['action'], reviewed['slot']
        if action not in ('apply', 'restore', 'verify', 'recover') or not re.fullmatch(r'partuuid-[a-z0-9-]{8,64}', slot):
            raise MaintenanceError('Unknown keyboard action or slot')
        # Existing global Engine lock protects generic ownership checks; the
        # unchanged original Store lock protects this shared-home generation.
        # Refuse missing account/home/dependencies before creating writable state.
        self._preflight(apply=action == 'apply')
        engine = Engine(self.root)
        with engine._state_lock():
            store, environment, _ = self._preflight(apply=action == 'apply')
            with store.lock():
                bound = binding(self.root, expected_label=reviewed.get('target_binding', {}).get('label'))
                if self._plan_locked(store, action, bound, environment) != reviewed:
                    raise MaintenanceError('Keyboard target or state changed; review again')
                before = store._read(TARGET, user=True)
                if digest(before) != reviewed['target_sha256']:
                    raise MaintenanceError('Keyboard target changed after snapshot; review again')
                store._keyboard_expected = before
                if action == 'apply':
                    raw = base64.b64decode(before['data']) if before['exists'] else None
                    mode = before['mode'] if before['exists'] else 0o600
                    if os.name != 'posix' and not self.enforce_owner:
                        mode &= ~0o022  # Original Store maps this back to native fixture metadata.
                    files = {TARGET: dict(data=_merge_group(raw, 'Wayland', VALUES),
                        component=IDENTITY, mode=mode,
                        user=True, mutable=True)}
                    result = store.apply(files, {'version': VERSION}, [IDENTITY], slot)
                elif action == 'restore':
                    result = store.restore(slot, [IDENTITY])
                elif action == 'recover':
                    result = store.recover(slot)
                else:
                    index, _ = self._state(store)
                    current = index['slots'].get(slot, {}).get('current') or {'paths': {}}
                    actual, edited = store._live_checked(current)
                    result = dict(action='verify', installed=TARGET in current['paths'],
                                  ok=TARGET in current['paths'] and not edited,
                                  preserved=sorted(edited), paths=sorted(actual), changed=False)
                index, _ = self._state(store)
                managed_slots = sorted(name for name, record in index['slots'].items()
                    if TARGET in (record.get('current') or {}).get('paths', {}))
                return dict(component=IDENTITY, action=action, operation=result,
                            managed_slots=managed_slots,
                            configuration_restored=action == 'restore' and bool(result.get('changed')) and not managed_slots and not result.get('preserved'),
                            restart_required=action != 'verify' and bool(result.get('paths')),
                            user_edits_preserved=bool(result.get('preserved')), mutable_notice=MUTABLE_NOTICE,
                            compatibility_notice=NOTICE, runtime_validated=False, dependency_scope='Fixed desktop path presence only; activation and Exec are not validated')


    def verify_readonly(self, label):
        """No mkdir, journal, writable-open or implicit recovery on a readonly image."""
        bound = binding(self.root, expected_label=label)
        store, _, _ = self._preflight()
        with _file(store.state/'lock', private=True) as (stream, info):
            if os.name == 'posix':
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            try:
                index, pending = self._state(store)
                if pending is not None:
                    raise TransactionError('Interrupted keyboard transaction requires explicit Recover')
                current = index['slots'].get(bound['slot'], {}).get('current') or {'paths': {}}
                actual, edited = store._live_checked(current)
                after, queued = self._state(store)
                if digest(index) != digest(after) or queued is not None:
                    raise TransactionError('Keyboard state changed during readonly verification')
                return dict(ok=TARGET in current['paths'] and not edited,
                    installed=TARGET in current['paths'],preserved=sorted(edited),changed=False)
            finally:
                if os.name == 'posix':
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def installer_apply(root, *, selected=False, slot, reviewed=None, reviewed_sha256=None):
    """Explicit opt-in; installer must present plan before invoking this adapter."""
    if type(selected) is not bool:
        raise ValueError('Keyboard selection must be boolean')
    if not selected:
        return dict(component=IDENTITY, status='not-selected', changed=False)
    if reviewed is None or reviewed.get('action') != 'apply' or reviewed.get('target_binding', {}).get('label') != slot:
        raise ValueError('Installer keyboard operation requires its reviewed apply plan')
    return Keyboard(root).execute(reviewed, reviewed_sha256=reviewed_sha256)


def maintenance_execute(root, reviewed, *, reviewed_sha256):
    """Maintenance uses the exact same lifecycle implementation as new install."""
    return Keyboard(root).execute(reviewed, reviewed_sha256=reviewed_sha256)
