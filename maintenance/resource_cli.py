# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit unsigned-resource preview and marked-fixture maintenance.

No production target provider is implemented here. The only write path uses an
explicitly confirmed, private, tool-owned fixture with fixed A/B/home domains.
Pool data and saved plans cannot grant trust or select a new target topology.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re

from .engine import MaintenanceError
from .resource_runtime import ResourcePool, ResourceSession, resolve
from .adoption_blobs import _file, _directory
from protocol.local import _safe_path
from protocol.manifest import load_json
from protocol.resource_pool import validate_pool, json_document

PURPOSE = 'BoilerResourceMaintenanceRestore:v1'
FIXTURE_KIND = 'BoilerResourceMaintenanceFixture'
FIXTURE_PREFIX = 'boiler-resource-maintenance-fixture-'
HEX = re.compile('[0-9a-f]{64}\\Z')


class ResourceMaintenanceError(MaintenanceError): pass


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode()


def plan_digest(raw): return hashlib.sha256((PURPOSE+'\0').encode()+raw).hexdigest()


def preview(pool_root):
    """Read and describe a pool; this never creates a ResourceSession."""
    root = _safe_path(pool_root)
    catalog = validate_pool(root)
    return dict(kind='UnsignedResourcePreview', trust='unsigned-development', production_trust=False,
        deployment_available=False, target_opened=False,
        catalog_sha256=hashlib.sha256((root/'catalog.toon').read_bytes()).hexdigest(),
        models=[dict(id=m['id'], resources=[r['resource'] for r in m['resources']],
                     complete=m.get('complete', False)) for m in catalog['models']],
        resources=[dict(id=r['id'], version=r['version'], handler=r['install']['kind'],
                        runtime='not_validated') for r in catalog['resources']],
        note='Preview grants no execution trust. Production target authentication/lease is unavailable; maintenance actions below are marked-fixture-only.')


def _pool(pool_root, catalog_sha256, trust_local_test):
    if trust_local_test is not True:
        raise ResourceMaintenanceError('Explicit unsigned local-test trust is required; preview does not authorize maintenance')
    if not isinstance(catalog_sha256, str) or not HEX.fullmatch(catalog_sha256):
        raise ResourceMaintenanceError('The reviewed catalog SHA-256 is required')
    root = _safe_path(pool_root)
    return ResourcePool(root, validate_pool(root, expected_catalog_sha256=catalog_sha256), local_test=True)


def _selection(pool, model_id, selected):
    models = [m for m in pool.catalog['models'] if m['id'] == model_id]
    if len(models) != 1 or not isinstance(selected, list) or not selected:
        raise ResourceMaintenanceError('An explicit model and nonempty resource selection are required')
    from .hdr_resources import bind
    effective, bound_references = bind(pool, models[0]['resources'], selected)
    identities = [r['id'] for r in resolve(effective, bound_references, selected)]
    if set(identities) != set(selected):
        raise ResourceMaintenanceError('Select the complete dependency closure explicitly')
    return models[0]['resources'], sorted(identities), effective


def _read_private(path, maximum):
    with _file(path, private=True) as (stream, info):
        if not 0 < info.st_size <= maximum: raise ResourceMaintenanceError('Private request exceeds its size limit')
        raw = stream.read(maximum+1)
    if not 0 < len(raw) <= maximum: raise ResourceMaintenanceError('Private request exceeds its size limit')
    return raw


@contextlib.contextmanager
def fixture_binding(fixture, requested_root):
    """An existing fixture-only lease, not an inferred physical disk lease."""
    base = _safe_path(fixture)
    match = re.fullmatch(re.escape(FIXTURE_PREFIX)+'([0-9a-f]{32})', base.name)
    if match is None or base == Path(base.anchor):
        raise ResourceMaintenanceError('Production resource targets are blocked; supply an explicitly owned named fixture')
    slots = {s: base/s for s in ('A', 'B')}; home = base/'home'
    if _safe_path(requested_root) != slots['A']:
        raise ResourceMaintenanceError('--root must match the fixed fixture A domain')
    with contextlib.ExitStack() as stack:
        for path in (base, *slots.values(), home):
            descriptor = stack.enter_context(_directory(path))
            if os.name != 'nt' and os.fstat(descriptor).st_mode & 0o077:
                raise ResourceMaintenanceError('Fixture domains must be private root-owned directories')
            value = json_document(_read_private(path/'.boiler-resource-fixture.json', 4096))
            if value != dict(kind=FIXTURE_KIND, id=match[1]):
                raise ResourceMaintenanceError('Fixture marker identity changed')
        lease, info = stack.enter_context(_file(base/'operation.lock', private=True))
        lease.seek(0)
        if info.st_size != 1: raise ResourceMaintenanceError('The existing fixture lease must have one byte')
        if os.name == 'nt':
            import msvcrt
            try: msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc: raise ResourceMaintenanceError('Fixture operation lease is busy') from exc
            stack.callback(lambda: (lease.seek(0), msvcrt.locking(lease.fileno(), msvcrt.LK_UNLCK, 1)))
        else:
            import fcntl
            try: fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc: raise ResourceMaintenanceError('Fixture operation lease is busy') from exc
            stack.callback(lambda: fcntl.flock(lease.fileno(), fcntl.LOCK_UN))
        identity = dict(fixture_id=match[1], fixture=str(base), domains={})
        for name, path in [*slots.items(), ('shared-home', home)]:
            stat = path.stat()
            identity['domains'][name] = dict(path=str(path), device=stat.st_dev, inode=stat.st_ino)
        yield dict(identity=identity, slots={s: dict(root=p, environment={}, hardware={}) for s,p in slots.items()}, home=home)


def _session(pool, binding, *, read_only):
    return ResourceSession(pool, binding['slots'], binding['home'], read_only=read_only)


def verify(pool_root, catalog_sha256, model_id, selected, *, fixture, root, trust_local_test=False):
    pool = _pool(pool_root, catalog_sha256, trust_local_test)
    _, identities, effective = _selection(pool, model_id, selected)
    with fixture_binding(fixture, root) as binding:
        session = _session(effective, binding, read_only=True)
        proof = session.verify(identities)
        if not proof or any(not row['ok'] for row in proof):
            raise ResourceMaintenanceError('Installed resource readonly verification failed: '+json.dumps(proof))
        return dict(kind='ResourceReadonlyVerification', fixture_only=True, trust='local-test',
                    runtime='not_validated', recovery_performed=False, verification=proof)


def _restore_plan(pool, binding, model_id, selected, group_id, scope):
    references, identities, effective = _selection(pool, model_id, selected)
    from protocol.cpu_contract import IDENTITY as CPU_ID
    blocked = {CPU_ID, 'resource:onexplayer-input', 'resource:bosch-bmi260-gyro'}
    if set(identities) & blocked:
        raise ResourceMaintenanceError('CPU/input CLI restore is blocked until trusted safety facts and a fixed mutable-configuration plan renderer are available')
    session = _session(effective, binding, read_only=True)
    receipt = session._bound_receipt(group_id)
    if receipt.get('status') not in ('committed', 'reconciled'):
        raise ResourceMaintenanceError('Pending resource group requires separate explicit recovery')
    if set(receipt['resources']) != set(identities):
        raise ResourceMaintenanceError('Restore selection differs from the durable resource group')
    if 'resource:gamma22-hdr-game' in identities:
        from .hdr_resources import check_restore
        check_restore(session,receipt)
    if scope not in {*session.engines, 'shared-home'}: raise ResourceMaintenanceError('Unknown restore scope')
    rows = [row for row in receipt['transactions'] if row['scope'] == scope]
    if not rows: raise ResourceMaintenanceError('The durable group has no transaction in the requested scope')
    states = {}
    for name, engine in [*session.engines.items(), ('shared-home', session.shared)]:
        if os.path.lexists(engine.state_dir/'adoption-pending.json'):
            raise ResourceMaintenanceError('Pending adoption requires separate explicit recovery')
        for path in engine.journals.glob('*.json'):
            if path.is_symlink() or load_json(path).get('status') not in ('committed', 'restored', 'rolled_back'):
                raise ResourceMaintenanceError('Pending transaction requires separate explicit recovery')
        state = engine._state(); states[name] = hashlib.sha256(_json(state)).hexdigest()
        # Verification is intentionally readonly and rejects pending recovery.
        for identity in identities:
            if identity in state['components']:
                if not engine.verify_readonly(identity)['ok']:
                    raise ResourceMaintenanceError('A managed file changed after installation; preserve it before recovery')
    changes = []
    for row in rows:
        engine = session.shared if scope == 'shared-home' else session.engines[scope]
        installed = engine._state()['components'].get(row['component'])
        if not installed or installed['transaction'] != row['transaction']:
            raise ResourceMaintenanceError('Restore group is not the current selected transaction generation')
        journal_raw = engine._journal(row['transaction']).read_bytes()
        journal = json_document(journal_raw)
        changes.append(dict(component=row['component'], transaction=row['transaction'],
                            journal_sha256=hashlib.sha256(journal_raw).hexdigest(),
                            files=[dict(target=op['target'], action='restore' if op['before']['exists'] else 'remove',
                                        current=copy.deepcopy(op['after']), restored=copy.deepcopy(op['before']))
                                   for op in journal['operations']]))
        if scope == 'shared-home':
            for slot, slot_engine in session.engines.items():
                expected = receipt.get('before_slots', {}).get(slot, {})
                if row['component'] not in expected or slot_engine._state()['components'].get(row['component']) != expected[row['component']]:
                    raise ResourceMaintenanceError('Restore both slot copies before reviewing shared-home restoration')
    raw = (session.shared.state_dir/'resource-actions'/(group_id+'.json')).read_bytes()
    return dict(schema_version=1, kind='ResourceMaintenanceRestorePlan', purpose=PURPOSE,
        fixture_only=True, trust='local-test', model_id=model_id, selected=identities,
        catalog_sha256=hashlib.sha256((pool.root/'catalog.toon').read_bytes()).hexdigest(),
        target=binding['identity'], group_id=group_id, scope=scope,
        group_sha256=hashlib.sha256(raw).hexdigest(), state_sha256=states,
        transactions=copy.deepcopy(rows), changes=changes, runtime='not_validated',
        note='Restore only this scope through the existing Engine. This plan is data, not authorization or a production signature.')


def plan_restore(pool_root, catalog_sha256, model_id, selected, *, fixture, root, group_id, scope, trust_local_test=False):
    pool = _pool(pool_root, catalog_sha256, trust_local_test)
    with fixture_binding(fixture, root) as binding:
        plan = _restore_plan(pool, binding, model_id, selected, group_id, scope)
    raw = _json(plan)
    return dict(plan=plan, reviewed_sha256=plan_digest(raw))


def execute_restore(pool_root, catalog_sha256, model_id, selected, *, fixture, root, group_id, scope,
                    plan, reviewed_sha256, trust_local_test=False, confirm_fixture_restore=False):
    if confirm_fixture_restore is not True:
        raise ResourceMaintenanceError('Explicit confirmation is required; a saved plan grants no restore authority')
    pool = _pool(pool_root, catalog_sha256, trust_local_test)
    raw = _read_private(Path(plan), 1024*1024) if isinstance(plan, (str, Path)) else _json(plan)
    if not isinstance(reviewed_sha256, str) or not HEX.fullmatch(reviewed_sha256) or not hmac.compare_digest(plan_digest(raw), reviewed_sha256):
        raise ResourceMaintenanceError('Restore plan differs from the explicitly reviewed SHA-256')
    reviewed = json_document(raw)
    with fixture_binding(fixture, root) as binding:
        actual = _restore_plan(pool, binding, model_id, selected, group_id, scope)
        if reviewed != actual:
            raise ResourceMaintenanceError('Resource selection, target, pool or transaction generation changed after review')
        # All review happened through readonly Engines under the same fixture
        # lease. No package hook is permitted by these fixed resource handlers.
        from .hdr_resources import action as maintenance_action
        references, _, _ = _selection(pool, model_id, selected)
        result = maintenance_action(pool, references, selected, slots=binding['slots'], shared_home=binding['home'],
                                    action='restore', receipt={'id': group_id}, scope=scope)
        return dict(kind='ResourceFixtureRestoreResult', fixture_only=True, trust='local-test',
                    group_id=group_id, scope=scope, restored=result, runtime='not_validated')


def write_plan(path, value, fixture):
    path = _safe_path(path); base = _safe_path(fixture)
    if any(parent in path.parents for parent in (base/'A', base/'B', base/'home')):
        raise ResourceMaintenanceError('Save review plans outside all target domains')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(_json(value)); stream.flush(); os.fsync(stream.fileno())


def add_parser(commands):
    resources = commands.add_parser('resources', help='Unsigned pool preview and explicitly confirmed fixture-only resource maintenance')
    actions = resources.add_subparsers(dest='resource_command', required=True)
    command = actions.add_parser('preview'); command.add_argument('--pool', required=True)
    for name in ('verify', 'plan-restore', 'restore'):
        command = actions.add_parser(name)
        for flag in ('pool', 'catalog-sha256', 'model', 'fixture'):
            command.add_argument('--'+flag, required=True)
        command.add_argument('--component', action='append', required=True)
        command.add_argument('--trust-local-test', action='store_true')
        if name != 'verify':
            command.add_argument('--group', required=True); command.add_argument('--scope', choices=('A', 'B', 'shared-home'), required=True)
        if name == 'plan-restore': command.add_argument('--plan-output', required=True)
        if name == 'restore':
            command.add_argument('--plan', required=True); command.add_argument('--reviewed-sha256', required=True)
            command.add_argument('--confirm-fixture-restore', action='store_true')


def dispatch(args):
    if args.resource_command == 'preview': return preview(args.pool)
    if args.state_dir is not None: raise ResourceMaintenanceError('Fixture resource state directories are fixed, not supplied by options')
    common = dict(fixture=args.fixture, root=args.root, trust_local_test=args.trust_local_test)
    if args.resource_command == 'verify': return verify(args.pool, args.catalog_sha256, args.model, args.component, **common)
    if args.resource_command == 'plan-restore':
        result = plan_restore(args.pool, args.catalog_sha256, args.model, args.component, group_id=args.group, scope=args.scope, **common)
        write_plan(args.plan_output, result['plan'], args.fixture)
        return dict(kind='ResourceFixtureRestoreReview', plan=result['plan'], plan_path=args.plan_output,
                    reviewed_sha256=result['reviewed_sha256'], execution_authorized=False)
    return execute_restore(args.pool, args.catalog_sha256, args.model, args.component, group_id=args.group, scope=args.scope,
                           plan=args.plan, reviewed_sha256=args.reviewed_sha256,
                           confirm_fixture_restore=args.confirm_fixture_restore, **common)
