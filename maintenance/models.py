"""Explicit, reviewed model maintenance; guides never authorize execution.

Local test bundles remain a separate, session-authorized path. Normal signed
updates retain their existing trust rules. A model package owns its own durable
file generations; Engine supplies serialization and the live root write window.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from protocol import compatible, validate_package
from protocol.manifest import confined, load_json
from protocol.model_catalog import ROOT, MODELS, bundled_model_package, load_models
from protocol.model_options import validated_selection
from .engine import Engine, MaintenanceError, _json_write, _sync_dir, build_hook_environment
from .updates import _authenticated_payload, _digest, target_environment
from .adoption_blobs import _directory, _file

CONTRACT = {'kind': 'boiler-model-maintenance-v1', 'entry': 'scripts/maintenance.sh'}
SCOPE = 'active-root-and-shared-home'


def _privileged():
    if os.name != 'posix' or os.geteuid() != 0:
        raise MaintenanceError('Model target inspection and maintenance require Linux root authorization')


def _private_json(path):
    with _file(path, private=True) as (stream, info):
        if info.st_size > 2*1024*1024:
            raise MaintenanceError('Model receipt exceeds size limit')
        from protocol.model_catalog import document
        return document(stream.read())


def pending(engine):
    path = engine.state_dir/'models/pending.json'
    if not os.path.lexists(path):
        return None
    value = _private_json(path)
    if (value.get('kind') != 'PendingModelAction' or value.get('model_id') not in MODELS
            or not re.fullmatch('[0-9a-f]{32}', value.get('id', ''))):
        raise MaintenanceError('Invalid pending model action')
    return value


def check_pending(engine, allowed=None):
    value = pending(engine)
    if value and value['id'] != allowed:
        raise MaintenanceError('Interrupted model maintenance requires explicit model verification/recovery: ' + value['id'])


def catalog(*, runtime_root=ROOT, online=True):
    return load_models(root=runtime_root, online=online, include_bundles=False)


def _package(identity, engine, runtime_root, *, trust_local_test):
    if not trust_local_test:
        raise MaintenanceError('Explicit session trust is required for the local test model package')
    if identity not in MODELS:
        raise MaintenanceError('Unknown model identity')
    pointer = engine.state_dir/'models'/('package-' + identity.removeprefix('device:') + '.json')
    if os.path.lexists(pointer):
        saved = _private_json(pointer)
        digest = saved.get('manifest_sha256', '')
        if saved.get('model_id') != identity or not re.fullmatch('[0-9a-f]{64}', digest):
            raise MaintenanceError('Invalid retained model package identity')
        directory = confined(engine.state_dir, 'models/packages/' + digest)
        package = validate_package(directory, expected_manifest_sha256=digest)
        item = dict(directory=str(directory), manifest_sha256=digest, package=package)
    else:
        item = bundled_model_package(identity, trust_local_test=True, root=runtime_root)
    package = item['package']
    if package.get('id') != identity or package.get('maintenance_contract') != CONTRACT:
        raise MaintenanceError('This package does not declare an on-device maintenance entry')
    if CONTRACT['entry'] not in {entry['path'] for entry in package['files']}:
        raise MaintenanceError('Model maintenance entry is outside its authenticated inventory')
    return item


def _run(package_directory, action, root, request, transaction='0'*32):
    if action not in ('inspect', 'plan', 'apply', 'verify', 'restore'):
        raise MaintenanceError('Unsupported model action')
    with tempfile.TemporaryDirectory(prefix='boiler-model-request-') as temporary:
        request_path = Path(temporary)/'request.json'
        _json_write(request_path, request)
        result = subprocess.run(['/bin/bash', str(confined(package_directory, CONTRACT['entry'])),
                                 action, str(root), str(request_path)],
                                cwd=package_directory, env=build_hook_environment(root, transaction),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=900, check=False)
    rows = [line[len('BOILER_MODEL_RESULT='):] for line in result.stdout.decode('utf-8').splitlines()
            if line.startswith('BOILER_MODEL_RESULT=')]
    if result.returncode or len(rows) != 1:
        error = result.stderr.decode('utf-8', errors='replace')[-3000:]
        if len(rows) == 1:
            try:
                error = str(json.loads(rows[0]).get('error', error))
            except (ValueError, TypeError):
                pass
        raise MaintenanceError('Model ' + action + ' failed; originals and receipts preserved. ' + error)
    from protocol.model_catalog import document
    value = document(rows[0].encode())
    if not isinstance(value, dict) or value.get('ok') is False:
        raise MaintenanceError('Model action returned an unsuccessful result')
    return value


def options(root, identity, *, trust_local_test=False, runtime_root=ROOT, state_dir=None, online=False):
    _privileged()
    data = catalog(runtime_root=runtime_root, online=online)
    model = next((item for item in data['models'] if item['id'] == identity), None)
    if model is None:
        raise MaintenanceError('Unknown model identity')
    view = dict(model=model, capabilities=[], current=None, executable=False,
                reasons=[], source=data['source'], offline=data['offline'], scope=SCOPE)
    if identity == 'device:x2-mini':
        view['reasons'] = ['X2 Mini currently has a source guide but no executable model package.']
        return view
    engine = Engine(root, state_dir, read_only=True)
    item = _package(identity, engine, runtime_root, trust_local_test=trust_local_test)
    environment = target_environment(root, device_id=identity.removeprefix('device:'))
    if not compatible(item['package'], environment):
        view['reasons'] = ['The actual OS, architecture or kernel is incompatible with this model package.']
        view['capabilities'] = [dict(row, available=False, reason=view['reasons'][0]) for row in item['package']['legacy_capabilities']]
        return view
    report = _run(item['directory'], 'inspect', Path(root), dict(schema_version=1, scope=SCOPE))
    view.update(report)
    view.update(model=model, package=dict(id=identity, version=item['package']['version'],
                manifest_sha256=item['manifest_sha256'], trust='local-test'), environment=environment,
                pending=pending(engine), scope=SCOPE)
    if 'capabilities' not in report or 'target' not in report:
        raise MaintenanceError('Model inspection omitted its target or capabilities')
    return view


def plan_model(root, identity, repairs, plugins, *, action='apply', trust_local_test=False,
               acknowledge_update_hold=False, installer_language='en-US', kde_language='system',
               runtime_root=ROOT, state_dir=None):
    if action not in ('apply', 'verify', 'restore'):
        raise MaintenanceError('Unsupported model plan action')
    _privileged()
    if action == 'apply':
        # Consent, choice whitelist and dependencies are data-only gates. Do
        # not invoke even the model's inspection code before these pass.
        initial = _package(identity, Engine(root, state_dir, read_only=True), runtime_root,
                           trust_local_test=trust_local_test)
        validated_selection(initial['package'], repairs, plugins, installer_language=installer_language,
                            kde_language=kde_language, acknowledge_update_hold=acknowledge_update_hold)
    current = options(root, identity, trust_local_test=trust_local_test, runtime_root=runtime_root, state_dir=state_dir)
    if not current.get('executable'):
        raise MaintenanceError('; '.join(current.get('reasons', [])) or 'No executable model maintenance is available')
    engine = Engine(root, state_dir, read_only=True)
    item = _package(identity, engine, runtime_root, trust_local_test=trust_local_test)
    if current.get('pending') and action != 'restore':
        raise MaintenanceError('Interrupted model action requires an explicit Restore plan')
    choice = validated_selection(item['package'], repairs, plugins, installer_language=installer_language,
                                 kde_language=kde_language, acknowledge_update_hold=acknowledge_update_hold) if action == 'apply' else current.get('current')
    if action == 'apply':
        available = {row['id']: row for row in current['capabilities']}
        for identity_option in choice['repairs'] + choice['plugins']:
            row = available.get(identity_option, {})
            if row.get('available') is not True:
                raise MaintenanceError('Model option is unavailable on this target: ' + identity_option + ': ' + str(row.get('reason', '')))
    request = dict(schema_version=1, action=action, selection=choice, scope=SCOPE,
                   recovery_requested=current.get('pending') is not None)
    backend = _run(item['directory'], 'plan', Path(root), request)
    if backend.get('executable') is not True:
        if backend.get('replacement_requires_restore'):
            raise MaintenanceError('replacement_requires_restore: Restore the installed adaptations before installing a different selection')
        raise MaintenanceError('Model plan is blocked: ' + str(backend.get('reason', backend.get('reasons', ''))))
    info = Path(root).resolve().stat()
    body = dict(schema_version=1, kind='ReviewedModelPlan', model_id=identity, action=action, scope=SCOPE,
                selection=choice, package=current['package'], target=current['target'], environment=current['environment'],
                root=dict(path=str(Path(root).resolve()), device=info.st_dev, inode=info.st_ino),
                state_sha256=_digest(engine._state()), pending=current.get('pending'),
                backend=backend, summary=backend.get('summary', {}), warnings=backend.get('warnings', []))
    body['review_sha256'] = _digest(body)
    return body


def _retain(engine, item):
    parent = engine.state_dir/'models/packages'
    with _directory(parent, create=True):
        pass
    destination = parent/item['manifest_sha256']
    if not destination.exists():
        with _authenticated_payload(item['directory'], item['manifest_sha256'], parent) as snapshot:
            snapshot.rename(destination)
            # TemporaryDirectory removes only the original name after the rename.
    validate_package(destination, expected_manifest_sha256=item['manifest_sha256'])
    _json_write(engine.state_dir/'models'/('package-' + item['package']['id'].removeprefix('device:') + '.json'),
                dict(kind='RetainedModelPackage', model_id=item['package']['id'], manifest_sha256=item['manifest_sha256']))
    return destination


def execute_model(root, plan, *, reviewed_sha256, trust_local_test=False, runtime_root=ROOT, state_dir=None):
    _privileged()
    if not trust_local_test:
        raise MaintenanceError('Explicit session trust is required for local test execution')
    plan = load_json(plan) if isinstance(plan, (str, Path)) else plan
    if not isinstance(plan, dict):
        raise MaintenanceError('A reviewed model plan is required')
    body = {key: value for key, value in plan.items() if key != 'review_sha256'}
    if (not re.fullmatch('[0-9a-f]{64}', reviewed_sha256 or '') or plan.get('review_sha256') != reviewed_sha256
            or _digest(body) != reviewed_sha256 or plan.get('kind') != 'ReviewedModelPlan'):
        raise MaintenanceError('The reviewed model plan hash does not match')
    choice = plan.get('selection') or {}
    fresh = plan_model(root, plan['model_id'], choice.get('repairs', []), choice.get('plugins', []),
                       action=plan['action'], trust_local_test=True, runtime_root=runtime_root, state_dir=state_dir,
                       acknowledge_update_hold=choice.get('acknowledge_legacy_update_hold') is True,
                       installer_language=choice.get('installer_language', 'en-US'), kde_language=choice.get('kde_language', 'system'))
    if fresh != plan:
        raise MaintenanceError('Target, selected options or installed state changed after review; review again')
    engine = Engine(root, state_dir)
    previous = pending(engine)
    # An explicitly reviewed Restore is also the model UI's interrupted
    # protection-window recovery route; the existing identity checks apply.
    with engine._lock(live_recover=plan['action']=='restore'):
        engine._recover(allow_model_action=previous['id'] if previous else None)
        if _digest(engine._state()) != plan['state_sha256'] or pending(engine) != previous:
            raise MaintenanceError('Maintenance state changed while waiting for the model lock')
        item = _package(plan['model_id'], engine, runtime_root, trust_local_test=True)
        payload = _retain(engine, item)
        # Recheck the model's own journal/target fingerprint under the write lock.
        request = dict(schema_version=1, action=plan['action'], selection=plan['selection'], scope=SCOPE,
                       recovery_requested=previous is not None)
        if _run(payload, 'plan', Path(root), request) != plan['backend']:
            raise MaintenanceError('Model files or target changed after review; review again')
        identity = uuid.uuid4().hex
        directory = engine.state_dir/'models/actions'
        with _directory(directory, create=True):
            pass
        receipt_path = directory/(identity + '.json')
        receipt = dict(schema_version=1, kind='ModelActionReceipt', id=identity, model_id=plan['model_id'],
                       action=plan['action'], package=plan['package'], plan_sha256=reviewed_sha256,
                       status='prepared', scope=SCOPE, previous_pending=previous)
        _json_write(receipt_path, receipt)
        marker = engine.state_dir/'models/pending.json'
        _json_write(marker, dict(kind='PendingModelAction', id=identity, model_id=plan['model_id']))
        try:
            request['expected_plan_sha256'] = plan['backend'].get('plan_sha256')
            result = _run(payload, plan['action'], Path(root), request, identity)
            if result.get('ok') is not True:
                raise MaintenanceError('Model action did not report verified success')
            status = 'verification-failed' if plan['action']=='verify' and result.get('verified') is not True else 'verified'
            receipt.update(status=status, result=result)
            _json_write(receipt_path, receipt)
            marker.unlink(); _sync_dir(marker.parent)
            return dict(receipt, receipt_path=str(receipt_path),
                        restart_required=plan['action'] != 'verify' and result.get('status') != 'not-managed')
        except BaseException as error:
            receipt.update(status='failed-preserved', error=str(error))
            _json_write(receipt_path, receipt)
            raise MaintenanceError('Model operation failed; original evidence and recovery receipt preserved at ' + str(receipt_path)) from error
