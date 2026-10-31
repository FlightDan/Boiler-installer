# SPDX-License-Identifier: AGPL-3.0-only
"""Actual CLI subprocesses and Engine transactions in private temporary trees."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.resource_pool import decoder
from maintenance.resource_runtime import ResourcePool, ResourceSession
from maintenance import resource_cli as api
from maintenance.engine import MaintenanceError, _json_write, runtime_files

IDENTITY = 'resource:sample'


def pool_fixture(root):
    root.mkdir(); toon = decoder(); inventory = []
    def write(name, raw):
        path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        entry = dict(path=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest()); inventory.append(entry)
        return entry
    write('license', b'fixture only\n')
    resources = []
    for identity, targets in ((IDENTITY, ['etc/sample', 'home/deck/sample']), ('resource:other', ['etc/other'])):
        payload, files = [], []
        for n, target in enumerate(targets):
            entry = write('payload/'+identity.split(':')[1]+str(n), (identity+target).encode()); payload.append(entry)
            files.append(dict(source=entry['path'], target=target, mode=420,
                              scope='shared-home' if target.startswith('home/') else 'root-slots'))
        resource = dict(schema_version=1, kind='BoilerResource', id=identity, version='1.0.0',
            dependencies=[], hardware_requires={}, compatibility={}, payload=dict(files=payload),
            install=dict(kind='files-v1', files=files), verify=dict(kind='sha256-v1'),
            restore=dict(kind='transaction-backup-v1'), license_files=['license'])
        name = 'resources/'+identity.split(':')[1]+'.toon'; entry = write(name, (toon.dumps(resource)+'\n').encode())
        resources.append(dict(id=identity, version=resource['version'], path=name, sha256=entry['sha256']))
    model = dict(schema_version=1, kind='BoilerResourceModel', id='device:fixture', complete=False,
                 resources=[dict(resource=r['id'], enabled=False, parameters={}) for r in resources])
    entry = write('model.toon', (toon.dumps(model)+'\n').encode())
    catalog = dict(schema_version=1, kind='BoilerResourceCatalog', trust='unsigned-development', official_release=False,
        resources=resources, models=[dict(id=model['id'], path='model.toon', sha256=entry['sha256'])], files=copy.deepcopy(inventory))
    write('catalog.toon', (toon.dumps(catalog)+'\n').encode()); write('catalog.json', json.dumps(catalog).encode())
    return root


class ResourceCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.work = Path(self.temp.name)
        self.pool = pool_fixture(self.work/'pool'); self.catalog_sha = hashlib.sha256((self.pool/'catalog.toon').read_bytes()).hexdigest()
        fixture_id = uuid.uuid4().hex; self.base = self.work/(api.FIXTURE_PREFIX+fixture_id); self.base.mkdir(mode=0o700)
        self.slots = {}; marker = json.dumps(dict(kind=api.FIXTURE_KIND, id=fixture_id)).encode()
        for path in (self.base, self.base/'A', self.base/'B', self.base/'home'):
            path.mkdir(mode=0o700, exist_ok=True); mark = path/'.boiler-resource-fixture.json'; mark.write_bytes(marker); mark.chmod(0o600)
        (self.base/'operation.lock').write_bytes(b'0'); (self.base/'operation.lock').chmod(0o600)
        for slot in ('A', 'B'):
            root = self.base/slot; (root/'etc').mkdir(); (root/'etc/passwd').write_text('deck:x:1000:1000:deck:/home/deck:/bin/bash\n')
            self.slots[slot] = dict(root=root, environment={}, hardware={})
        self.home = self.base/'home'; self.pool_object = ResourcePool.for_local_test(self.pool, trust_local_test=True)
        self.refs = self.pool_object.catalog['models'][0]['resources']
        self.session = ResourceSession(self.pool_object, self.slots, self.home)
        self.receipt = self.session.install(self.refs, [IDENTITY])

    def snapshot(self): return {p.relative_to(self.base).as_posix(): p.read_bytes() for p in self.base.rglob('*') if p.is_file()}

    def common(self):
        return dict(fixture=self.base, root=self.base/'A', trust_local_test=True)

    def plan(self, scope='A', group=None, selected=None):
        return api.plan_restore(self.pool, self.catalog_sha, 'device:fixture', selected or [IDENTITY],
                                group_id=group or self.receipt['id'], scope=scope, **self.common())

    def execute(self, review, scope='A', **changes):
        args = dict(plan=review['plan'], reviewed_sha256=review['reviewed_sha256'],
                    group_id=self.receipt['id'], scope=scope, confirm_fixture_restore=True, **self.common())
        args.update(changes)
        return api.execute_restore(self.pool, self.catalog_sha, 'device:fixture', [IDENTITY], **args)

    def command(self, action, *extra, trusted=True, runtime_root=ROOT, pool_root=None):
        args = ['--root', str(self.base/'A'), 'resources', action, '--pool', str(pool_root or self.pool)]
        if action != 'preview':
            args += ['--catalog-sha256', self.catalog_sha, '--model', 'device:fixture', '--component', IDENTITY, '--fixture', str(self.base)]
            if trusted: args.append('--trust-local-test')
        args += list(extra)
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
        code = 'import sys; sys.path.insert(0,sys.argv.pop(1)); from maintenance.__main__ import main; raise SystemExit(main())'
        result = subprocess.run([sys.executable, '-B', '-c', code, str(runtime_root), *args], capture_output=True, text=True, env=env)
        if not result.stdout: self.fail('CLI produced no JSON: '+result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_actual_cli_preview_grants_no_trust_and_does_not_touch_target(self):
        before = self.snapshot(); code, response = self.command('preview')
        self.assertEqual(code, 0); result = response['result']
        self.assertFalse(result['production_trust']); self.assertFalse(result['deployment_available']); self.assertFalse(result['target_opened'])
        self.assertEqual(result['catalog_sha256'], self.catalog_sha); self.assertEqual(before, self.snapshot())

    def test_actual_cli_verify_readonly_and_requires_fresh_explicit_trust(self):
        before = self.snapshot(); code, response = self.command('verify', trusted=False)
        self.assertEqual(code, 2); self.assertIn('Explicit unsigned', response['error'])
        code, response = self.command('verify'); self.assertEqual(code, 0, response)
        self.assertFalse(response['result']['recovery_performed']); self.assertEqual(before, self.snapshot())

    def test_actual_cli_review_and_confirmation_then_exact_scope_restore(self):
        plan_path = self.work/'review.json'; before = self.snapshot()
        code, response = self.command('plan-restore', '--group', self.receipt['id'], '--scope', 'A', '--plan-output', str(plan_path))
        self.assertEqual(code, 0, response); self.assertFalse(response['result']['execution_authorized']); self.assertEqual(before, self.snapshot())
        args = ['--group', self.receipt['id'], '--scope', 'A', '--plan', str(plan_path), '--reviewed-sha256', response['result']['reviewed_sha256']]
        code, result = self.command('restore', *args); self.assertEqual(code, 2); self.assertIn('Explicit confirmation', result['error'])
        self.assertEqual(before, self.snapshot())
        code, result = self.command('restore', *args, '--confirm-fixture-restore'); self.assertEqual(code, 0, result)
        self.assertFalse((self.base/'A/etc/sample').exists()); self.assertTrue((self.base/'B/etc/sample').exists())
        self.assertTrue((self.home/'deck/sample').exists()); self.assertFalse((self.base/'A/etc/other').exists())

    def test_plan_bytes_changes_and_saved_plan_never_authorize_by_themselves(self):
        review = self.plan(); before = self.snapshot(); changed = copy.deepcopy(review)
        self.assertEqual(review['plan']['changes'][0]['files'][0]['target'], 'etc/sample')
        self.assertEqual(review['plan']['changes'][0]['files'][0]['action'], 'remove')
        changed['plan']['transactions'][0]['component'] = 'resource:other'
        with self.assertRaisesRegex(MaintenanceError, 'reviewed SHA'): self.execute(changed)
        changed['reviewed_sha256'] = api.plan_digest(api._json(changed['plan']))
        with self.assertRaisesRegex(MaintenanceError, 'changed after review'): self.execute(changed)
        with self.assertRaisesRegex(MaintenanceError, 'Explicit confirmation'): self.execute(review, confirm_fixture_restore=False)
        self.assertEqual(before, self.snapshot())

    def test_journal_edit_after_review_is_rejected_before_restore(self):
        review = self.plan()
        row = next(r for r in self.receipt['transactions'] if r['scope'] == 'A')
        journal = self.session.engines['A']._journal(row['transaction'])
        value = json.loads(journal.read_bytes()); value['review_note'] = 'changed after review'; _json_write(journal, value)
        before = self.snapshot()
        with self.assertRaisesRegex(MaintenanceError, 'changed after review'): self.execute(review)
        self.assertEqual(before, self.snapshot())

    def test_group_crash_after_engine_commit_does_not_implicitly_reconcile(self):
        review = self.plan()
        path = self.session.shared.state_dir/'resource-actions'/(self.receipt['id']+'.json')
        for status in ('prepared', 'applying', 'partial'):
            record = json.loads(path.read_bytes()); record['status'] = status; _json_write(path, record)
            before = self.snapshot()
            with self.assertRaisesRegex(MaintenanceError, 'Pending resource group'): self.plan()
            with self.assertRaisesRegex(MaintenanceError, 'Pending resource group'): self.execute(review)
            self.assertEqual(before, self.snapshot())

    def test_legitimate_other_group_and_raw_transactions_cannot_extend_selection(self):
        other = self.session.install(self.refs, ['resource:other']); before = self.snapshot()
        with self.assertRaisesRegex(MaintenanceError, 'selection differs'): self.plan(group=other['id'])
        review = self.plan(); review['plan']['group_id'] = other['id']; review['reviewed_sha256'] = api.plan_digest(api._json(review['plan']))
        with self.assertRaises(MaintenanceError): self.execute(review, group_id=other['id'])
        self.assertEqual(before, self.snapshot()); self.assertTrue((self.base/'A/etc/other').exists())

    def test_new_generation_pool_or_fixture_replacement_invalidates_review(self):
        review = self.plan(); self.session.install(self.refs, [IDENTITY]); before = self.snapshot()
        with self.assertRaisesRegex(MaintenanceError, 'current selected transaction'): self.execute(review)
        self.assertEqual(before, self.snapshot())
        current = self.session.install(self.refs, [IDENTITY]); self.receipt = current; review = self.plan()
        old = self.base/'old-A'; (self.base/'A').rename(old); shutil.copytree(old, self.base/'A')
        with self.assertRaisesRegex(MaintenanceError, 'changed after review'): self.execute(review)
        (self.pool/'catalog.toon').write_bytes((self.pool/'catalog.toon').read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError, 'Catalog hash mismatch'): api.verify(self.pool, self.catalog_sha, 'device:fixture', [IDENTITY], **self.common())

    def test_modified_file_and_pending_state_never_trigger_automatic_recovery(self):
        target = self.base/'A/etc/sample'; target.write_bytes(b'user change'); before = self.snapshot()
        with self.assertRaises(MaintenanceError): self.plan()
        self.assertEqual(before, self.snapshot())
        # Restore exact bytes first; only the owned fixture journal is changed.
        payload = self.pool_object.resource(IDENTITY)['payload']['files'][0]
        target.write_bytes((self.pool/payload['path']).read_bytes())
        journal = self.session.engines['B']._journal(next(r['transaction'] for r in self.receipt['transactions'] if r['scope']=='B'))
        record = json.loads(journal.read_bytes()); record['status'] = 'applying'; _json_write(journal, record); before = self.snapshot()
        with self.assertRaisesRegex(MaintenanceError, 'Pending transaction'): self.plan()
        with self.assertRaises(MaintenanceError): api.verify(self.pool, self.catalog_sha, 'device:fixture', [IDENTITY], **self.common())
        self.assertEqual(before, self.snapshot())

    def test_shared_restoration_requires_both_prior_slot_generations(self):
        with self.assertRaisesRegex(MaintenanceError, 'both slot copies'): self.plan(scope='shared-home')
        for scope in ('A', 'B'): self.execute(self.plan(scope), scope)
        review = self.plan('shared-home'); self.execute(review, 'shared-home')
        self.assertFalse((self.home/'deck/sample').exists())

    def test_existing_fixture_lease_is_exclusive_for_real_cli_process(self):
        before = self.snapshot()
        with api.fixture_binding(self.base, self.base/'A'):
            code, result = self.command('verify')
        self.assertEqual(code, 2); self.assertIn('lease is busy', result['error']); self.assertEqual(before, self.snapshot())

    def test_production_root_and_unmarked_or_redirected_fixture_are_not_accepted(self):
        before = self.snapshot()
        with self.assertRaisesRegex(MaintenanceError, 'Production resource targets'):
            api.verify(self.pool, self.catalog_sha, 'device:fixture', [IDENTITY], fixture=self.work, root=self.base/'A', trust_local_test=True)
        mark = self.base/'B/.boiler-resource-fixture.json'; mark.write_bytes(b'{}')
        with self.assertRaisesRegex(MaintenanceError, 'marker identity'): api.verify(self.pool, self.catalog_sha, 'device:fixture', [IDENTITY], **self.common())
        self.assertTrue((self.base/'A/etc/sample').is_file())

    @unittest.skipIf(os.name == 'nt', 'Native Linux ownership and no-follow verification')
    def test_linux_private_domains_and_no_follow_are_required(self):
        domain = self.base/'B'; domain.chmod(0o755); before = self.snapshot()
        with self.assertRaisesRegex(MaintenanceError, 'private root-owned'): self.plan()
        self.assertEqual(before, self.snapshot()); domain.chmod(0o700)
        original = self.base/'real-B'; domain.rename(original); domain.symlink_to(original, target_is_directory=True)
        with self.assertRaises((MaintenanceError, ValueError, OSError)): self.plan()
        self.assertTrue((original/'etc/sample').is_file())

    @unittest.skipIf(os.name == 'nt', 'Native Linux private request verification')
    def test_linux_saved_plan_must_be_private_regular_file(self):
        review = self.plan(); plan = self.work/'review.json'; api.write_plan(plan, review['plan'], self.base)
        plan.chmod(0o644); before = self.snapshot()
        with self.assertRaisesRegex(MaintenanceError, 'root-owned and private'): self.execute(review, plan=plan)
        self.assertEqual(before, self.snapshot())

    def test_ordinary_model_cli_branch_and_retained_runtime_are_preserved(self):
        from maintenance.__main__ import main
        with patch('maintenance.models.catalog', return_value={'models': []}) as old, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--root', str(self.base/'A'), 'models', 'list', '--offline']), 0)
            old.assert_called_once_with(online=False)
        self.assertTrue(json.loads(output.getvalue())['ok'])
        self.assertIn(ROOT/'maintenance/resource_cli.py', runtime_files(ROOT))

    def test_exact_retained_runtime_reopens_cli_without_source_tree(self):
        retained = self.work/'retained'
        for source in runtime_files(ROOT):
            target = retained/source.relative_to(ROOT); target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
        pool = retained/'cache/resource-pool'; shutil.copytree(self.pool, pool)
        before = self.snapshot()
        for action in ('preview', 'verify'):
            code, result = self.command(action, runtime_root=retained, pool_root=pool)
            self.assertEqual(code, 0, result)
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__': unittest.main()
