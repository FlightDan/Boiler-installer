"""Facade security and recovery tests with real files and a fake model adapter.

These portable fixtures do not exercise Bash, pkexec, a KDE session, or Linux
live-root protection. Root evidence, package authentication, durable Engine state
and inventory-only retention remain real; only authorization and the adapter
process boundary are replaced.
"""
import contextlib
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maintenance import Engine, MaintenanceError
from maintenance import models
from protocol.manifest import ProtocolError
from protocol.model_catalog import load_models
from protocol.model_options import validated_selection


MODEL = 'device:oxp3'
BASE = 'oxp3:base'
FAN = 'oxp3:fan'
PLUGIN = 'oxp3:decky'
REAL_RUN = models._run


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class MaintenanceModelsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='boiler-model-facade-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.root, self.runtime = self.work/'target', self.work/'runtime'
        (self.root/'etc').mkdir(parents=True)
        (self.root/'etc/os-release').write_text('ID=steamos\nVERSION_ID="3.10"\nBUILD_ID=fixture\n')
        binary = bytearray(20)
        binary[:6] = b'\x7fELF\x02\x01'
        binary[18:20] = (62).to_bytes(2, 'little')
        (self.root/'usr/bin').mkdir(parents=True)
        (self.root/'usr/bin/env').write_bytes(binary)
        (self.root/'usr/lib/modules/kernel-fixture').mkdir(parents=True)
        shutil.copytree(ROOT/'components/model-catalog', self.runtime/'components/model-catalog')
        for entry in json.loads((ROOT/'components/model-catalog/lock.json').read_bytes())['files']:
            target = self.runtime/'adaptations'/entry['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/'adaptations'/entry['path'], target)
        self.package = self.runtime/'cache/model-bundles/packages/oxp3'
        (self.package/'scripts').mkdir(parents=True)
        (self.package/'scripts/maintenance.sh').write_bytes(b'#!/bin/bash\n# adapter fixture; never executed\n')
        (self.package/'payload').mkdir()
        (self.package/'payload/data').write_bytes(b'authenticated fixture\n')
        self.capabilities = [
            dict(id=BASE, category='repairs', name={'zh': '基础', 'en': 'Base'},
                 available=True, default=True, dependencies=[]),
            dict(id=FAN, category='repairs', name={'zh': '风扇', 'en': 'Fan'},
                 available=True, default=False, dependencies=[BASE]),
            dict(id=PLUGIN, category='plugins', name={'zh': 'Decky', 'en': 'Decky'},
                 available=True, default=False, dependencies=[BASE])]
        self.manifest = dict(schema_version=1, kind='DevicePackage', id=MODEL, version='0.1.0-test.1',
                             source={'kind': 'local', 'path': 'fixture'},
                             compatibility={'architectures': ['x86_64'], 'os_ids': ['steamos'],
                                            'kernels': ['kernel-fixture']},
                             components=[], files=[{'path': path.relative_to(self.package).as_posix(),
                                                   'size': path.stat().st_size,
                                                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                                                  for path in sorted(self.package.rglob('*')) if path.is_file()],
                             selection_contract={'kind': 'oxp3-legacy-options-v1', 'component': 'oxp3:legacy-adaptation'},
                             maintenance_contract=copy.deepcopy(models.CONTRACT),
                             legacy_kde_languages=['system', 'en_US', 'zh_CN'],
                             legacy_capabilities=self.capabilities)
        self.write_package()
        self.state = self.root/'var/lib/boiler/maintenance'
        self.current, self.slot = None, 'A'
        self.fail_apply, self.verify_ok = False, True
        self.actions = []
        self.privileged = self.enterContext(patch.object(models, '_privileged'))
        self.adapter = self.enterContext(patch.object(models, '_run', side_effect=self.run_adapter))

    def write_package(self):
        raw = (json.dumps(self.manifest, ensure_ascii=False, sort_keys=True) + '\n').encode()
        (self.package/'manifest.json').write_bytes(raw)
        self.anchor = hashlib.sha256(raw).hexdigest()
        descriptor = {'kind': 'LocalTestModelBundles', 'schema_version': 1, 'packages': [
            {'id': MODEL, 'version': self.manifest['version'], 'directory': 'cache/model-bundles/packages/oxp3',
             'manifest_sha256': self.anchor}]}
        (self.runtime/'cache/model-bundles/manifest.json').write_text(json.dumps(descriptor))

    def run_adapter(self, package_directory, action, root, request, transaction='0'*32):
        self.actions.append(action)
        self.assertEqual(Path(root), self.root)
        if action == 'inspect':
            return {'executable': True, 'current': copy.deepcopy(self.current),
                    'capabilities': copy.deepcopy(self.capabilities), 'target': {'slot': self.slot}}
        if action == 'plan':
            return {'executable': True, 'plan_sha256': digest({'request': request, 'slot': self.slot}),
                    'summary': {'action': request['action'], 'slot': self.slot, 'file_count': 1},
                    'warnings': ['Test adapter only']}
        if action == 'apply':
            self.current = copy.deepcopy(request['selection'])
            if self.fail_apply:
                raise MaintenanceError('fixture adapter interrupted after partial apply')
            return {'ok': True, 'status': 'installed'}
        if action == 'verify':
            return {'ok': True, 'verified': self.verify_ok, 'status': 'verified' if self.verify_ok else 'modified',
                    'modified': [] if self.verify_ok else ['etc/changed.conf']}
        if action == 'restore':
            self.current = None
            return {'ok': True, 'status': 'restored'}
        raise AssertionError(action)

    def plan(self, repairs=None, plugins=None, **kwargs):
        return models.plan_model(self.root, MODEL, [BASE] if repairs is None else repairs,
                                 [] if plugins is None else plugins, trust_local_test=True,
                                 acknowledge_update_hold=True, runtime_root=self.runtime, **kwargs)

    def execute(self, plan, **kwargs):
        return models.execute_model(self.root, plan, reviewed_sha256=plan['review_sha256'],
                                    trust_local_test=True, runtime_root=self.runtime, **kwargs)

    def files(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def test_offline_catalog_matches_shared_two_model_source_without_executable_bundles(self):
        view = models.catalog(runtime_root=self.runtime, online=False)
        shared = load_models(root=self.runtime, online=False)
        self.assertEqual(view['source'], shared['source'])
        self.assertEqual(view['models'], shared['models'])
        self.assertEqual({row['id'] for row in view['models']}, {MODEL, 'device:x2-mini'})
        self.assertTrue(view['offline'])
        self.assertEqual(view['bundled_packages'], [])
        self.adapter.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_missing_session_trust_cannot_run_adapter_or_create_state(self):
        before = self.files()
        with self.assertRaisesRegex(MaintenanceError, 'Explicit session trust'):
            models.plan_model(self.root, MODEL, [BASE], [], runtime_root=self.runtime,
                              acknowledge_update_hold=True)
        self.adapter.assert_not_called()
        self.assertEqual(self.files(), before)
        self.assertFalse(self.state.exists())
        plan = self.plan()
        self.adapter.reset_mock()
        with self.assertRaisesRegex(MaintenanceError, 'Explicit session trust'):
            models.execute_model(self.root, plan, reviewed_sha256=plan['review_sha256'],
                                 runtime_root=self.runtime)
        self.adapter.assert_not_called()
        self.assertEqual(self.files(), before)
        self.assertFalse(self.state.exists())

    def test_missing_hold_and_dependencies_never_call_adapter_or_write_state(self):
        before = self.files()
        for repairs, hold, error in (([BASE], False, 'acknowledgement'), ([FAN], True, 'Missing model dependency')):
            with self.subTest(repairs=repairs, hold=hold):
                self.adapter.reset_mock()
                with self.assertRaisesRegex(ProtocolError, error):
                    models.plan_model(self.root, MODEL, repairs, [], runtime_root=self.runtime,
                                      trust_local_test=True, acknowledge_update_hold=hold)
                self.adapter.assert_not_called()
                self.assertEqual(self.files(), before)
                self.assertFalse(self.state.exists())

    def test_x2_guide_cannot_authorize_execution(self):
        before = self.files()
        view = models.options(self.root, 'device:x2-mini', runtime_root=self.runtime)
        self.assertFalse(view['executable'])
        self.assertTrue(view['model']['components'])
        with self.assertRaisesRegex((MaintenanceError, ProtocolError), '[Nn]o (bundled )?executable'):
            models.plan_model(self.root, 'device:x2-mini', [], [], runtime_root=self.runtime,
                              trust_local_test=True, acknowledge_update_hold=True)
        self.adapter.assert_not_called()
        self.assertEqual(self.files(), before)

    def test_exact_declared_maintenance_contract_and_inventory_are_required(self):
        cases = [None, {'kind': 'other', 'entry': 'scripts/maintenance.sh'},
                 dict(models.CONTRACT, extra='not allowed')]
        for contract in cases:
            with self.subTest(contract=contract):
                self.manifest['maintenance_contract'] = contract
                self.write_package()
                with self.assertRaisesRegex(MaintenanceError, 'does not declare'):
                    self.plan()
                self.adapter.assert_not_called()
                self.assertFalse(self.state.exists())
        self.manifest['maintenance_contract'] = copy.deepcopy(models.CONTRACT)
        self.manifest['files'] = [row for row in self.manifest['files'] if row['path'] != 'scripts/maintenance.sh']
        (self.package/'scripts/maintenance.sh').unlink()
        self.write_package()
        with self.assertRaisesRegex(MaintenanceError, 'outside its authenticated inventory'):
            self.plan()
        self.adapter.assert_not_called()

    def test_review_hash_rejects_tampered_selection_target_and_state_before_adapter_or_writes(self):
        plan = self.plan([BASE, FAN])
        self.assertFalse(self.state.exists())
        for field, value in (('selection', {'repairs': [BASE], 'plugins': []}),
                             ('target', {'slot': 'B'}), ('state_sha256', 'f'*64)):
            with self.subTest(field=field):
                changed = copy.deepcopy(plan)
                changed[field] = value
                before = self.files()
                self.adapter.reset_mock()
                with self.assertRaisesRegex(MaintenanceError, 'hash does not match'):
                    self.execute(changed)
                self.adapter.assert_not_called()
                self.assertEqual(self.files(), before)

    def test_replan_rejects_changed_target_or_private_state_before_execution_writes(self):
        for change in ('slot', 'environment', 'state'):
            with self.subTest(change=change):
                plan = self.plan()
                if change == 'slot':
                    self.slot = 'B'
                elif change == 'environment':
                    (self.root/'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.11\nBUILD_ID=fixture\n')
                else:
                    state = Engine(self.root, read_only=True)._state()
                    state['owners']['etc/external'] = 'external:owner'
                    self.state.mkdir(parents=True, mode=0o700, exist_ok=True)
                    (self.state/'state.json').write_text(json.dumps(state))
                before = self.files()
                self.actions.clear()
                with self.assertRaisesRegex(MaintenanceError, 'changed after review'):
                    self.execute(plan)
                self.assertEqual(self.files(), before)
                self.assertNotIn('apply', self.actions)
                self.assertFalse((self.state/'models').exists())

    def test_execution_retains_only_authenticated_inventory_and_survives_cache_removal(self):
        plan = self.plan([BASE, FAN], [PLUGIN])
        # A cache addition after validation must not enter the detached copy.
        item = models._package(MODEL, Engine(self.root, read_only=True), self.runtime, trust_local_test=True)
        (self.package/'unlisted-secret').write_text('never retain')
        models._retain(Engine(self.root), item)
        receipt = self.execute(plan)
        self.assertEqual(receipt['status'], 'verified')
        retained = self.state/'models/packages'/self.anchor
        inventory = {row['path'] for row in self.manifest['files']} | {'manifest.json'}
        self.assertEqual({path.relative_to(retained).as_posix() for path in retained.rglob('*') if path.is_file()}, inventory)
        self.assertEqual((retained/'manifest.json').read_bytes(), (self.package/'manifest.json').read_bytes())
        shutil.rmtree(self.runtime/'cache/model-bundles')
        view = models.options(self.root, MODEL, trust_local_test=True, runtime_root=self.runtime)
        self.assertEqual(view['current'], plan['selection'])
        self.assertEqual(view['package']['manifest_sha256'], self.anchor)
        (retained/'scripts/maintenance.sh').write_bytes(b'tampered executable\n')
        self.adapter.reset_mock()
        with self.assertRaises(ProtocolError):
            models.options(self.root, MODEL, trust_local_test=True, runtime_root=self.runtime)
        self.adapter.assert_not_called()

    def test_unlisted_cache_addition_before_execution_is_rejected_without_writes(self):
        plan = self.plan()
        (self.package/'unlisted-secret').write_text('not an authenticated file')
        before = self.files()
        self.adapter.reset_mock()
        with self.assertRaisesRegex(ProtocolError, 'inventory does not match'):
            self.execute(plan)
        self.adapter.assert_not_called()
        self.assertEqual(self.files(), before)
        self.assertFalse(self.state.exists())

    def test_false_verification_keeps_receipt_without_pending_barrier(self):
        self.current = validated_selection(self.manifest, [BASE], [], acknowledge_update_hold=True)
        plan = self.plan(action='verify')
        self.verify_ok = False
        receipt = self.execute(plan)
        self.assertEqual(receipt['status'], 'verification-failed')
        self.assertEqual(receipt['result']['modified'], ['etc/changed.conf'])
        self.assertFalse(receipt['restart_required'])
        self.assertEqual(json.loads(Path(receipt['receipt_path']).read_text())['status'], 'verification-failed')
        self.assertFalse((self.state/'models/pending.json').exists())
        self.assertEqual(Engine(self.root).recover(), [])

    def test_failed_apply_blocks_generic_recovery_until_explicit_restore(self):
        plan = self.plan([BASE, FAN])
        self.fail_apply = True
        with self.assertRaisesRegex(MaintenanceError, 'receipt preserved'):
            self.execute(plan)
        marker = self.state/'models/pending.json'
        pending = json.loads(marker.read_text())
        original = self.state/'models/actions'/(pending['id'] + '.json')
        self.assertEqual(json.loads(original.read_text())['status'], 'failed-preserved')
        with self.assertRaisesRegex(MaintenanceError, 'Interrupted model maintenance'):
            Engine(self.root).recover()
        with self.assertRaisesRegex(MaintenanceError, 'explicit Restore'):
            self.plan()
        restore = self.plan(action='restore')
        lock_modes, original_lock = [], Engine._lock
        @contextlib.contextmanager
        def observed_lock(engine, *, live_recover=False):
            lock_modes.append(live_recover)
            with original_lock(engine, live_recover=live_recover):
                yield
        with patch.object(Engine, '_lock', observed_lock):
            receipt = self.execute(restore)
        self.assertEqual(lock_modes, [True])
        self.assertEqual(receipt['previous_pending'], pending)
        self.assertEqual(receipt['result']['status'], 'restored')
        self.assertFalse(marker.exists())
        self.assertEqual(json.loads(original.read_text())['status'], 'failed-preserved')
        self.assertEqual(Engine(self.root).recover(), [])

    def test_actual_apply_timeout_preserves_receipt_and_recovery_barrier(self):
        plan = self.plan([BASE, FAN])
        def dispatch(package, action, root, request, transaction='0'*32):
            if action == 'apply':
                return REAL_RUN(package, action, root, request, transaction)
            return self.run_adapter(package, action, root, request, transaction)
        with patch.object(models, '_run', side_effect=dispatch), \
             patch.object(models.subprocess, 'run', side_effect=subprocess.TimeoutExpired('model-hook', 3600)) as process:
            with self.assertRaisesRegex(MaintenanceError, 'receipt preserved'):
                self.execute(plan)
        self.assertEqual(process.call_args.kwargs['timeout'], 3600)
        pending = json.loads((self.state/'models/pending.json').read_text())
        receipt = json.loads((self.state/'models/actions'/(pending['id']+'.json')).read_text())
        self.assertEqual(receipt['id'], pending['id'])
        self.assertEqual(receipt['status'], 'failed-preserved')
        self.assertIn('3600 seconds', receipt['error'])
        with self.assertRaisesRegex(MaintenanceError, 'Interrupted model maintenance'):
            Engine(self.root).recover()

    def test_inspect_timeout_after_completed_apply_preserves_state_and_receipt(self):
        receipt = self.execute(self.plan([BASE, FAN]))
        self.assertEqual(receipt['status'], 'verified')
        receipt_path = Path(receipt['receipt_path'])
        self.assertTrue(receipt_path.is_file())
        before = self.files()
        self.assertFalse((self.state/'models/pending.json').exists())
        with patch.object(models, '_run', REAL_RUN), \
             patch.object(models.subprocess, 'run', side_effect=subprocess.TimeoutExpired('model-hook', 3600)) as process:
            with self.assertRaises(subprocess.TimeoutExpired):
                models.options(self.root, MODEL, trust_local_test=True, runtime_root=self.runtime)
        process.assert_called_once()
        self.assertEqual(process.call_args.args[0][2], 'inspect')
        self.assertEqual(process.call_args.kwargs['timeout'], 3600)
        self.assertEqual(self.files(), before)
        self.assertFalse((self.state/'models/pending.json').exists())

    def test_real_runner_uses_one_hour_timeout_for_all_actions(self):
        class Result:
            returncode = 0
            stdout = b'BOILER_MODEL_RESULT={"ok":true}\n'
            stderr = b''
        for action in ('inspect', 'plan', 'apply', 'verify', 'restore'):
            with self.subTest(action=action), patch.object(models.subprocess, 'run', return_value=Result()) as process:
                REAL_RUN(self.package, action, self.root, {})
                self.assertEqual(process.call_args.kwargs['timeout'], 3600)

    def test_declared_underscore_and_hyphen_locales_are_accepted_without_broadening(self):
        for locale in ('system', 'zh_CN', 'zh-CN', 'en_US', 'en-US'):
            with self.subTest(locale=locale):
                plan = self.plan(kde_language=locale)
                self.assertIn(plan['selection']['kde_language'].replace('-', '_'),
                              self.manifest['legacy_kde_languages'])
        self.adapter.reset_mock()
        with self.assertRaisesRegex(ProtocolError, 'Unsupported model language'):
            self.plan(kde_language='unlisted_LOCALE')
        self.adapter.assert_not_called()
        self.assertFalse(self.state.exists())


if __name__ == '__main__':
    unittest.main()
