"""Reviewed shared repairs using the existing Engine file transactions.

Each selected component is its own transaction. A partial operation remains
visible in Engine inventory and can be verified or restored from this screen.
"""
from pathlib import Path
import hashlib
import json

from protocol import compatible, validate_package
from protocol.manifest import load_json, confined
from protocol.model_catalog import ROOT, MODELS, bundled_model_package
from .engine import Engine, MaintenanceError, _read
from .adoption_blobs import observe, _directory, _target_user
from .updates import target_environment, _digest
from . import models


def prepare_component_dirs(engine, component):
    """Create only the declared Battery plugin containers before file writes."""
    for entry in component['files']:
        target = engine._target(entry['target'])
        user = _target_user(engine, target)
        if user:
            if component['id'] != 'x2-mini:battery' or not entry['target'].startswith('home/deck/homebrew/plugins/OXP3-battery/'):
                raise MaintenanceError('Undeclared user repair directory')
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
    metadata = value.get('shared_adaptations', {})
    ids = metadata.get('components', [])
    known = {part['id']: part for part in value['components']}
    if (value['id'] != identity or metadata.get('kind') != 'boiler-shared-static-v1'
            or not ids or len(set(ids)) != len(ids) or any(cid not in known for cid in ids)):
        raise MaintenanceError('Package does not declare reviewed shared repair components')
    if any(known[cid].get('hooks') for cid in ids):
        raise MaintenanceError('Shared repair components must use Engine static transactions')
    item['components'] = [known[cid] for cid in ids]
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
        capabilities.append(dict(id=component['id'], name=component['name'], category='repairs',
            available=available, default=component.get('default', False), dependencies=component.get('requires', []),
            reason='' if available else 'Actual system is incompatible with this component.',
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
