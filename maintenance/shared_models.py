"""Reviewed shared repairs using the existing Engine file transactions.

Each selected component is its own transaction. A partial operation remains
visible in Engine inventory and can be verified or restored from this screen.
"""
from pathlib import Path
import hashlib
import json
import os

from protocol import compatible, validate_package
from protocol.manifest import load_json, confined
from protocol.model_catalog import ROOT, MODELS, bundled_model_package
from .engine import Engine, MaintenanceError, _read
from .adoption_blobs import observe, _directory, _target_user, _file
from .updates import target_environment, _digest, _guest_path
from . import models


GAMESCOPE_COMPONENT = 'x2-mini:gamescope-env'
GAMESCOPE_TARGET = 'home/deck/.config/systemd/user/gamescope-session.service.d/90-oxp3-desktop-env-isolation.conf'
GAMESCOPE_SHA256 = '9616729dac8be729b56bd201b8cfbbb1fcd0eb2fb7163944638306856e85a13b'
GAMESCOPE_KERNEL = '7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
SLOT_SWITCH_COMPONENT = 'x2-mini:slot-switch'
SLOT_SWITCH_TOOLS = ('python3', 'pkexec', 'kdialog', 'steamos-bootconf', 'systemctl', 'busctl')
SLOT_SWITCH_FILES = {'home/.oxp3-slot-switch/boot.py': (20555, '94d98f74d6025d79d2f6af07d59657117c6a80316e591e235d4e4e9ab75dcf85', 420), 'home/.oxp3-slot-switch/desktop.py': (9643, 'ebfc65ff86e4dcf33011d1884e487fee00b072c134fb1fc0815d7d841cf0294d', 420), 'home/.oxp3-slot-switch/oxp3-switch-slot': (75, '4d58916cc7d5d31405d0d189e2ea7963c8d8a29776242bf8dd967e38c421dd46', 493), 'etc/polkit-1/rules.d/49-oxp3-slot-switch.rules': (298, 'f95d37f73f87532e6827a2e50cd03f86703a2def50fe036f9e54a280f155c317', 420), 'home/deck/Desktop/oxp3-slot-switch.desktop': (236, '05c6039b3d12cf633c846da62c9a28f5cae98b179ca2e0985960451520822b4a', 493), 'home/deck/.local/share/applications/oxp3-slot-switch.desktop': (236, '05c6039b3d12cf633c846da62c9a28f5cae98b179ca2e0985960451520822b4a', 493), 'usr/share/licenses/boiler-x2-slot-switch/LICENSE-MIT': (1070, '1959942e9e263938024cbd4d8c63bba342647877777dd48056c467672dcb915a', 420)}
X2_STATIC_ADDITIONS = frozenset((GAMESCOPE_COMPONENT, SLOT_SWITCH_COMPONENT))
X2_KEYBOARD_COMPONENT = 'x2-mini:keyboard'
X2_ADDITIVE_ADDITIONS = X2_STATIC_ADDITIONS | {X2_KEYBOARD_COMPONENT}
X2_ADDITIVE_BASES = frozenset((
    '874ee0c4bf0f46ec5af94f7452ebe0d656b50bb2b5c673a42392c8ba9191db6c',
    '4765603630661f26e925be214d5da684bae81109240a23aa306333a2d99459e6',
))


def check_gamescope_component_shape(component):
    rows = component.get('files', [])
    if (component.get('id') != GAMESCOPE_COMPONENT or component.get('hooks') or component.get('default') is not False
            or component.get('selection_policy') != 'explicit-opt-in' or len(rows) != 1
            or rows[0].get('target') != GAMESCOPE_TARGET
            or rows[0].get('sha256') != GAMESCOPE_SHA256 or rows[0].get('size') != 222 or rows[0].get('mode') != 0o644):
        raise MaintenanceError('Unexpected X2 gamescope environment component')


def check_gamescope_component(engine, component):
    """An exact static option, not permission to install arbitrary user files."""
    if component['id'] != GAMESCOPE_COMPONENT:
        return
    check_gamescope_component_shape(component)
    env = target_environment(engine.root)
    if (env.get('architecture') != 'x86_64' or env.get('os_id') != 'steamos'
            or env.get('os_version') != '3.10' or env.get('os_build') != '20260924.1000'
            or env.get('kernels') != [GAMESCOPE_KERNEL]):
        raise MaintenanceError('X2 gamescope environment requires the exact accepted SteamOS baseline')
    # These are image-owned paths; never follow a host or guest redirect here.
    for relative in ('usr/lib/systemd/user/gamescope-session.service', 'usr/bin/systemctl'):
        path = confined(engine.root, relative)
        if not path.is_file() or path.stat().st_size == 0:
            raise MaintenanceError('X2 gamescope environment dependency missing: '+relative)
    target = engine._target(GAMESCOPE_TARGET)
    user = _target_user(engine, target)
    # Validate the existing account/home and every existing target ancestor
    # before any installer component or maintenance transaction writes.
    with _directory(engine.root/'home/deck', **user):
        pass
    observe(engine, target)
    return True


def check_slot_switch_shape(component):
    if (component.get('id') != SLOT_SWITCH_COMPONENT or component.get('hooks')
            or component.get('default') is not False
            or component.get('selection_policy') != 'explicit-opt-in'):
        raise MaintenanceError('Unexpected X2 slot switch component')
    rows = component.get('files', [])
    actual = {row['target']: (row['size'], row['sha256'], row.get('mode', 0o644)) for row in rows}
    if len(rows) != len(actual) or actual != SLOT_SWITCH_FILES:
        raise MaintenanceError('Unexpected X2 slot switch payload')


def check_slot_switch_component(engine, component):
    if component['id'] != SLOT_SWITCH_COMPONENT:
        return
    check_slot_switch_shape(component)
    env = target_environment(engine.root)
    if (env.get('architecture') != 'x86_64' or env.get('os_id') != 'steamos'
            or env.get('os_version') != '3.10' or env.get('os_build') != '20260924.1000'
            or env.get('kernels') != [GAMESCOPE_KERNEL]):
        raise MaintenanceError('X2 slot switch requires the exact accepted SteamOS baseline')
    # Resolve links in the image namespace, then use the existing root-owned,
    # no-follow regular-file reader. Never execute guest binaries during staging.
    for tool in SLOT_SWITCH_TOOLS:
        path = _guest_path(engine.root, 'usr/bin/'+tool)
        try:
            with _file(path) as (_, info):
                if info.st_size == 0 or os.name != 'nt' and not info.st_mode & 0o111:
                    raise MaintenanceError('Slot switch dependency is not executable: '+tool)
        except FileNotFoundError as error:
            raise MaintenanceError('Slot switch dependency missing: '+tool) from error
    for row in component['files']:
        target = engine._target(row['target'])
        user = _target_user(engine, target)
        if user:
            with _directory(engine.root/'home/deck', **user):
                pass
        observed = observe(engine, target)
        if os.name != 'nt' and not user and observed['exists'] and observed['mode'] != row.get('mode', 0o644):
            raise MaintenanceError('Unexpected existing slot switch file permissions: '+row['target'])
    return True


def check_static_component(engine, component):
    check_gamescope_component(engine, component)
    check_slot_switch_component(engine, component)


def _fixed_user_dirs(engine, user, parts):
    """Create only the fixed user hierarchy; never re-own an existing directory."""
    parent = engine.root/'home/deck'
    with _directory(parent, **user):
        pass
    for name in parts:
        path = parent/name
        if os.name == 'nt':
            with _directory(path, create=True, create_mode=0o755, **user):
                pass
        else:
            with _directory(parent, **user) as directory:
                created = False
                try:
                    os.mkdir(name, 0o755, dir_fd=directory)
                    created = True
                except FileExistsError:
                    pass
                with _directory(path, **user) as child:
                    if created:
                        os.fchown(child, user['user_uid'], -1)
                        os.fchmod(child, 0o755)
                        os.fsync(child)
                        os.fsync(directory)
        parent = path


def _gamescope_user_dirs(engine, user):
    _fixed_user_dirs(engine, user, ('.config', 'systemd', 'user', 'gamescope-session.service.d'))


def prepare_component_dirs(engine, component):
    """Create only explicitly reviewed Battery, gamescope and slot-tool containers."""
    check_static_component(engine, component)
    for entry in component['files']:
        target = engine._target(entry['target'])
        user = _target_user(engine, target)
        if user:
            battery = component['id'] == 'x2-mini:battery' and entry['target'].startswith('home/deck/homebrew/plugins/OXP3-battery/')
            gamescope = component['id'] == GAMESCOPE_COMPONENT and entry['target'] == GAMESCOPE_TARGET
            slot_switch = component['id'] == SLOT_SWITCH_COMPONENT and entry['target'] in SLOT_SWITCH_FILES
            if not (battery or gamescope or slot_switch):
                raise MaintenanceError('Undeclared user repair directory')
            if gamescope:
                _gamescope_user_dirs(engine, user)
                continue
            if slot_switch:
                parts = target.parent.relative_to(engine.root/'home/deck').parts
                _fixed_user_dirs(engine, user, parts)
                continue
            with _directory(target.parent, create=True, create_mode=0o755, **user):
                pass


def package(identity, engine, runtime_root, trust):
    if not trust:
        raise MaintenanceError('Explicit session trust is required for local test repairs')
    if identity not in MODELS:
        raise MaintenanceError('Unknown shared repair model')
    pointer = engine.state_dir/'models'/('shared-' + identity.removeprefix('device:') + '.json')
    if pointer.exists():
        saved = models._private_json(pointer)
        import re
        if not re.fullmatch('[0-9a-f]{64}', saved.get('manifest_sha256', '')):
            raise MaintenanceError('Invalid retained shared package anchor')
        directory = confined(engine.state_dir, 'models/packages/' + saved['manifest_sha256'])
        value = validate_package(directory, expected_manifest_sha256=saved['manifest_sha256'])
        item = dict(directory=str(directory), manifest_sha256=saved['manifest_sha256'], package=value)
        if not any(cid in engine._state()['components'] for cid in value.get('shared_adaptations', {}).get('components', [])):
            item = bundled_model_package(identity, trust_local_test=True, root=runtime_root)
    else:
        item = bundled_model_package(identity, trust_local_test=True, root=runtime_root)
    value = item['package']
    # Only the reviewed additive X2 migration is eligible. Old payload and
    # original component journals remain retained for verification/recovery.
    if identity == 'device:x2-mini' and item['manifest_sha256'] in X2_ADDITIVE_BASES:
        # Discovery of an optional new package must never gate recovery of
        # an already validated retained package and its original journals.
        try:
            candidate = bundled_model_package(identity, trust_local_test=True, root=runtime_root)
            upgrade = candidate['package']
            policy = upgrade.get('shared_adaptations', {})
            if not isinstance(policy, dict):
                raise MaintenanceError('Invalid optional X2 additive upgrade metadata')
            anchors = policy.get('additive_upgrade_from', [])
            offered = policy.get('components', [])
            warnings = policy.get('warnings', [])
            if (policy.get('kind') not in ('boiler-shared-static-v1','boiler-shared-host-v1')
                    or not isinstance(anchors, list) or any(not isinstance(anchor, str) for anchor in anchors)
                    or not isinstance(offered, list) or any(not isinstance(cid, str) for cid in offered)
                    or len(offered) != len(set(offered))
                    or not isinstance(warnings, list) or any(not isinstance(warning, str) for warning in warnings)):
                raise MaintenanceError('Invalid optional X2 additive upgrade metadata')
            if item['manifest_sha256'] in anchors:
                previous = {part['id']: part for part in value['components']}
                proposed = {part['id']: part for part in upgrade['components']}
                added = set(proposed) - set(previous)
                if (not added or not added <= X2_ADDITIVE_ADDITIONS or set(previous) - set(proposed)
                        or any(proposed.get(cid) != part for cid, part in previous.items())
                        or any(part.get('hooks') for part in proposed.values())
                        or set(offered) != set(proposed)):
                    raise MaintenanceError('X2 additive upgrade changes retained component definitions')
                # A known host capability may accompany the additive static
                # repairs, but its data contract must be checked inside this
                # optional-discovery boundary. Invalid/new code contracts must
                # fall back to the retained package before final dispatch.
                if any(part.get('dynamic_handler') for cid,part in proposed.items() if cid != X2_KEYBOARD_COMPONENT):
                    raise MaintenanceError('Unknown dynamic X2 additive component')
                if X2_KEYBOARD_COMPONENT in proposed:
                    if policy.get('kind') != 'boiler-shared-host-v1':
                        raise MaintenanceError('Keyboard requires the fixed host package contract')
                    from .keyboard_contract import check
                    check(proposed[X2_KEYBOARD_COMPONENT])
                elif policy.get('kind') == 'boiler-shared-host-v1':
                    raise MaintenanceError('Host X2 package is missing its fixed keyboard contract')
                if GAMESCOPE_COMPONENT in added:
                    check_gamescope_component_shape(proposed[GAMESCOPE_COMPONENT])
                if SLOT_SWITCH_COMPONENT in added:
                    check_slot_switch_shape(proposed[SLOT_SWITCH_COMPONENT])
                item, value = candidate, upgrade
        except (MaintenanceError, ValueError, OSError, ImportError):
            pass
    metadata = value.get('shared_adaptations', {})
    ids = metadata.get('components', [])
    known = {part['id']: part for part in value['components']}
    if (value['id'] != identity or metadata.get('kind') not in ('boiler-shared-static-v1','boiler-shared-host-v1')
            or not ids or len(set(ids)) != len(ids) or any(cid not in known for cid in ids)):
        raise MaintenanceError('Package does not declare reviewed shared repair components')
    if any(known[cid].get('hooks') for cid in ids):
        raise MaintenanceError('Shared repair components must use Engine static transactions')
    # Dynamic keyboard has a separate fixed review/recovery screen and ledger.
    # Never feed its empty file list through the generic static Engine path.
    for cid in ids:
        if known[cid].get('dynamic_handler') or cid == 'x2-mini:keyboard':
            from .keyboard_contract import check
            check(known[cid])
    item['components'] = [known[cid] for cid in ids if cid != 'x2-mini:keyboard']
    return item


def options(root, identity, *, trust_local_test=False, runtime_root=ROOT, state_dir=None):
    models._privileged()
    engine = Engine(root, state_dir, read_only=True)
    item = package(identity, engine, runtime_root, trust_local_test)
    environment = target_environment(root, device_id=identity.removeprefix('device:'))
    state = engine._state()
    capabilities = []
    for component in item['components']:
        available = compatible(component, environment)
        reason = ''
        if available and component['id'] in X2_STATIC_ADDITIONS:
            try:
                check_static_component(engine, component)
            except (MaintenanceError, ValueError, OSError) as error:
                available = False
                reason = str(error)
        capabilities.append(dict(id=component['id'], name=component['name'], category='repairs',
            available=available, default=component.get('default', False), dependencies=component.get('requires', []),
            reason='' if available else reason or 'Actual system is incompatible with this component.',
            notes=component.get('description', '')))
    installed = [part['id'] for part in item['components'] if part['id'] in state['components']]
    from .live_root import pending as root_pending
    interrupted = root_pending(root, state_dir)
    if interrupted is None:
        for journal in engine.journals.glob('*.json'):
            if journal.is_symlink():
                raise MaintenanceError('Unsafe transaction journal')
            value = _read(journal)
            if value.get('status') not in ('committed', 'restored', 'rolled_back'):
                interrupted = dict(source='engine-journal', transaction=value.get('id'))
                break
    install_available = compatible(item['package'], environment)
    return dict(shared_adaptations=True, model={'id': identity}, capabilities=capabilities,
        executable=install_available or bool(installed) or interrupted is not None,
        install_available=install_available, reasons=[],
        current=dict(repairs=installed, plugins=[]) if installed else None,
        package=dict(id=identity, version=item['package']['version'], manifest_sha256=item['manifest_sha256'], trust='local-test'),
        target=dict(root=str(Path(root).resolve())), environment=environment, pending=interrupted,
        scope=models.SCOPE, warnings=item['package']['shared_adaptations'].get('warnings', []))


def plan(root, identity, repairs, plugins=(), *, action='apply', trust_local_test=False, runtime_root=ROOT,
         state_dir=None, **unused):
    current = options(root, identity, trust_local_test=trust_local_test, runtime_root=runtime_root, state_dir=state_dir)
    if action not in ('apply', 'verify', 'restore') or not current['executable']:
        raise MaintenanceError('Unavailable shared repair action')
    if action == 'apply' and (not current['install_available'] or current['pending'] is not None):
        raise MaintenanceError('Shared repair installation is incompatible or requires recovery first')
    engine = Engine(root, state_dir, read_only=True)
    item = package(identity, engine, runtime_root, trust_local_test)
    known = {part['id']: part for part in item['components']}
    state = engine._state()
    if action == 'apply':
        if plugins or not isinstance(repairs, list) or len(set(repairs)) != len(repairs) or not repairs:
            raise MaintenanceError('Choose unique shared repair components')
        if set(repairs) - set(known):
            raise MaintenanceError('Unknown shared repair component')
        selected = [cid for cid in known if cid in repairs]
    else:
        selected = [cid for cid in known if cid in state['components']]
        if not selected and not (action == 'restore' and current['pending'] is not None):
            raise MaintenanceError('No installed shared repair transaction')
    available = {row['id'] for row in current['capabilities'] if row['available']}
    if action == 'apply' and set(selected) - available:
        raise MaintenanceError('Shared repair is unavailable on this target')
    paths = {}
    for cid in selected:
        component = known[cid]
        for entry in component['files']:
            owner = state['owners'].get(entry['target'])
            if action == 'apply' and owner and owner['component'] != cid:
                raise MaintenanceError('Target belongs to another component: ' + entry['target'])
            paths[entry['target']] = observe(engine, engine._target(entry['target']))
    info = Path(root).resolve().stat()
    body = dict(schema_version=1, kind='ReviewedSharedModelPlan', model_id=identity, action=action,
        shared_adaptations=True, package=current['package'], scope=models.SCOPE,
        selection=dict(repairs=selected, plugins=[]), environment=current['environment'], target=current['target'],
        root=dict(path=str(Path(root).resolve()), device=info.st_dev, inode=info.st_ino),
        state_sha256=_digest(state), target_files=paths,
        pending=current['pending'],
        summary=dict(file_count=len(paths), added=selected if action == 'apply' else [],
                     removed=selected if action == 'restore' else []),
        warnings=current['warnings'] + ['Each component is a separate recoverable file transaction. Changes activate after restart; no sleep is initiated.'])
    body['review_sha256'] = _digest(body)
    return body


def execute(root, reviewed, *, reviewed_sha256, trust_local_test=False, runtime_root=ROOT, state_dir=None):
    models._privileged()
    reviewed = load_json(reviewed) if isinstance(reviewed, (str, Path)) else reviewed
    body = {key: value for key, value in reviewed.items() if key != 'review_sha256'}
    if (reviewed.get('kind') != 'ReviewedSharedModelPlan' or reviewed.get('review_sha256') != reviewed_sha256
            or _digest(body) != reviewed_sha256):
        raise MaintenanceError('Reviewed shared repair plan does not match')
    fresh = plan(root, reviewed['model_id'], reviewed['selection']['repairs'], action=reviewed['action'],
        trust_local_test=trust_local_test, runtime_root=runtime_root, state_dir=state_dir)
    if fresh != reviewed:
        raise MaintenanceError('Target or installed state changed; review shared repairs again')
    engine = Engine(root, state_dir)
    item = package(reviewed['model_id'], engine, runtime_root, trust_local_test)
    components = {part['id']: part for part in item['components']}
    selected = reviewed['selection']['repairs']
    results = []
    # Reuse the normal global Engine lock, recovery and root protection window.
    with engine._lock(live_recover=reviewed['action'] == 'restore'):
        info = Path(root).resolve().stat()
        if dict(path=str(Path(root).resolve()), device=info.st_dev, inode=info.st_ino) != reviewed['root']:
            raise MaintenanceError('Target root changed while waiting for the lock')
        environment = target_environment(root, device_id=reviewed['model_id'].removeprefix('device:'))
        if environment != reviewed['environment']:
            raise MaintenanceError('Target environment changed while waiting for the lock')
        engine._recover()
        if _digest(engine._state()) != reviewed['state_sha256']:
            raise MaintenanceError('Installed state changed while waiting for the lock')
        for name, expected in reviewed['target_files'].items():
            if observe(engine, engine._target(name)) != expected:
                raise MaintenanceError('Shared repair target changed after review: ' + name)
        payload = models._retain(engine, item)
        from .engine import _json_write
        _json_write(engine.state_dir/'models'/('shared-' + reviewed['model_id'].removeprefix('device:') + '.json'),
                    dict(manifest_sha256=item['manifest_sha256']))
        if reviewed['action'] == 'apply':
            import uuid
            for cid in selected:
                prepare_component_dirs(engine, components[cid])
                results.append(engine._apply_locked(components[cid], payload, {}, engine._state(), uuid.uuid4().hex, None, False))
        elif reviewed['action'] == 'restore':
            for cid in reversed(selected):
                transaction = engine._state()['components'][cid]['transaction']
                path = engine._journal(transaction)
                results.append(engine._restore_locked(transaction, engine._state(), path, _read(path)))
        else:
            reader = Engine(root, state_dir, read_only=True)
            results = [reader.verify_readonly(cid) for cid in selected]
    verified = all(result.get('ok', True) for result in results)
    return dict(status='verified' if verified else 'verification-failed',
        result=dict(verified=verified, components=results), restart_required=reviewed['action'] != 'verify')
