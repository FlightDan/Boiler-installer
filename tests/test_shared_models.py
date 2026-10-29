import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import contextlib
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance import shared_models as shared
from maintenance.engine import Engine, MaintenanceError
from maintenance.model_ui import ModelUI
from test_model_ui import Dialog, FakeBackend, MODEL, CATALOG, OPTIONS, PLAN


class SharedLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'root'; self.root.mkdir()
        self.runtime = Path(self.temp.name)/'runtime'
        package = self.runtime/'cache/model-bundles/packages/x2-mini'; package.mkdir(parents=True)
        data = b'system-default-no-sleep\n'
        (package/'payload.bin').write_bytes(data)
        record = dict(source='payload.bin', target='etc/shared-test.conf', size=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=0o644)
        component = dict(schema_version=1, kind='ComponentManifest', id='x2-mini:battery', version='0.2.0',
                         name='Battery', files=[record], requires=[], conflicts=[])
        value = dict(schema_version=1, kind='DevicePackage', id='device:x2-mini', version='0.1.0-localtest.1',
            source=dict(kind='local', path='fixture'), components=[component],
            shared_adaptations=dict(kind='boiler-shared-static-v1', components=['x2-mini:battery']),
            files=[dict(path='payload.bin', size=len(data), sha256=record['sha256'])])
        raw = (json.dumps(value)+'\n').encode(); (package/'manifest.json').write_bytes(raw)
        descriptor = dict(schema_version=1, kind='LocalTestModelBundles', packages=[dict(id=value['id'],version=value['version'],
            directory='cache/model-bundles/packages/x2-mini', manifest_sha256=hashlib.sha256(raw).hexdigest())])
        (self.runtime/'cache/model-bundles/manifest.json').write_text(json.dumps(descriptor))
        self.kw = dict(trust_local_test=True, runtime_root=self.runtime)
        for target, replacement in [('maintenance.models._privileged', lambda: None),
            ('maintenance.shared_models.target_environment', lambda *a, **k: dict(architecture='x86_64', os_id='steamos', os_version='3.10'))]:
            patcher = patch(target, replacement); patcher.start(); self.addCleanup(patcher.stop)

    def make_plan(self, action='apply'):
        return shared.plan(self.root, 'device:x2-mini', ['x2-mini:battery'], action=action, **self.kw)

    def execute(self, plan):
        return shared.execute(self.root, plan, reviewed_sha256=plan['review_sha256'], **self.kw)

    def test_apply_verify_restore_preserves_original(self):
        target = self.root/'etc/shared-test.conf'; target.parent.mkdir(); target.write_bytes(b'original\n')
        self.execute(self.make_plan())
        self.assertEqual(target.read_bytes(), b'system-default-no-sleep\n')
        self.assertTrue(self.execute(self.make_plan('verify'))['result']['verified'])
        self.execute(self.make_plan('restore'))
        self.assertEqual(target.read_bytes(), b'original\n')
        self.assertIsNone(shared.options(self.root, 'device:x2-mini', **self.kw)['current'])

    def test_changed_target_invalidates_review(self):
        plan = self.make_plan()
        target = self.root/'etc/shared-test.conf'; target.parent.mkdir(); target.write_bytes(b'changed')
        with self.assertRaisesRegex(MaintenanceError, 'changed'):
            self.execute(plan)

    def test_modified_install_verify_fails_restore_refuses(self):
        self.execute(self.make_plan())
        (self.root/'etc/shared-test.conf').write_bytes(b'user edit')
        self.assertFalse(self.execute(self.make_plan('verify'))['result']['verified'])
        with self.assertRaisesRegex(MaintenanceError, 'modified target'):
            self.execute(self.make_plan('restore'))

    def test_unknown_component_and_missing_trust_refused(self):
        with self.assertRaises(MaintenanceError):
            shared.options(self.root, 'device:x2-mini', runtime_root=self.runtime)
        with self.assertRaisesRegex(MaintenanceError, 'Unknown shared'):
            shared.plan(self.root, 'device:x2-mini', ['x2-mini:fan-driver'], **self.kw)

    def test_fingerprint_action_is_not_exposed(self):
        self.assertFalse(hasattr(shared, 'fingerprint'))

    def test_pending_before_first_commit_exposes_restore(self):
        with patch('maintenance.live_root.pending', return_value={'id': 'interrupted-root-window'}):
            view = shared.options(self.root, 'device:x2-mini', **self.kw)
            self.assertIsNone(view['current'])
            self.assertIsNotNone(view['pending'])
            with self.assertRaisesRegex(MaintenanceError, 'recovery first'):
                self.make_plan()
            self.assertTrue(self.execute(self.make_plan('restore'))['result']['verified'])

    def test_changed_os_allows_verify_restore_but_blocks_install(self):
        self.execute(self.make_plan())
        with patch('maintenance.shared_models.target_environment', return_value=dict(architecture='x86_64', os_id='steamos', os_version='3.11')):
            # The fixture has no version restriction; add the package restriction
            # through the same compatibility decision used by real packages.
            original = shared.compatible
            with patch('maintenance.shared_models.compatible', side_effect=lambda component, env: False):
                with self.assertRaisesRegex(MaintenanceError, 'incompatible'):
                    self.make_plan()
                self.assertTrue(self.execute(self.make_plan('verify'))['result']['verified'])
                self.execute(self.make_plan('restore'))

    def test_environment_rechecked_under_lock(self):
        plan = self.make_plan()
        original_lock = Engine._lock
        @contextlib.contextmanager
        def changed(engine, **kwargs):
            with original_lock(engine, **kwargs):
                with patch('maintenance.shared_models.target_environment', return_value=dict(architecture='x86_64', os_id='steamos', os_version='3.11')):
                    yield
        with patch.object(Engine, '_lock', changed):
            with self.assertRaisesRegex(MaintenanceError, 'environment changed'):
                self.execute(plan)
        self.assertFalse((self.root/'etc/shared-test.conf').exists())


class SharedUI(unittest.TestCase):
    def test_shared_menu_executes_without_legacy_hold_consent(self):
        class Backend(FakeBackend):
            def call(self, *args):
                if args[0].startswith('shared-'):
                    return super().call(args[0].removeprefix('shared-'), *args[1:])
                return super().call(*args)
        dialog = Dialog(['device:oxp3', 'shared', 'apply', 'back'],
                        [{'base'}, set()], [True, True])
        backend = Backend(dict(OPTIONS, shared_adaptations=True))
        self.assertEqual(ModelUI(dialog, backend, offline=True).run(), 0)
        self.assertEqual(backend.saved_plan, PLAN)
        self.assertEqual(len(dialog.checklist_rows), 2)
        args = next(args for args in backend.calls if args[0] == 'plan')
        self.assertNotIn('--acknowledge-update-hold', args)
        self.assertNotIn('--installer-language', args)


if __name__ == '__main__':
    unittest.main()
