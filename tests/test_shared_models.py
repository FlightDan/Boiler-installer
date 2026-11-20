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
        versions = [{'id': 'x2-mini:battery', 'version': '0.2.0'}]
        install = self.make_plan()
        self.assertEqual(install['component_versions'], versions)
        self.assertEqual(self.execute(install)['component_versions'], versions)
        self.assertEqual(target.read_bytes(), b'system-default-no-sleep\n')
        verify = self.make_plan('verify')
        verify.pop('component_versions')
        verify.pop('review_sha256')
        verify['review_sha256'] = shared._digest(verify)
        receipt = self.execute(verify)
        self.assertTrue(receipt['result']['verified'])
        self.assertEqual(receipt['component_versions'], versions)
        self.assertEqual(self.execute(self.make_plan('restore'))['component_versions'], versions)
        self.assertEqual(target.read_bytes(), b'original\n')
        self.assertIsNone(shared.options(self.root, 'device:x2-mini', **self.kw)['current'])

    def test_verify_and_restore_label_the_installed_version(self):
        engine = Engine(self.root)
        item = shared.package('device:x2-mini', engine, self.runtime, True)
        installed = dict(item['components'][0], version='0.1.9')
        (self.root/'etc').mkdir()
        engine.apply(installed, item['directory'])
        for action in ('verify', 'restore'):
            with self.subTest(action=action):
                plan = self.make_plan(action)
                self.assertEqual(plan['component_versions'], [{'id': installed['id'], 'version': '0.1.9'}])

    def test_changed_target_invalidates_review(self):
        plan = self.make_plan()
        target = self.root/'etc/shared-test.conf'; target.parent.mkdir(); target.write_bytes(b'changed')
        with self.assertRaisesRegex(MaintenanceError, 'changed'):
            self.execute(plan)

    def test_additive_microcode_keeps_existing_model_and_restores_boot_bytes(self):
        from components import graphics_boot
        from maintenance import onexplayer_microcode as microcode
        self.execute(self.make_plan())
        Engine(self.root).apply(dict(schema_version=1, kind='ComponentManifest', id='core:maintenance',
                                     version='0.2.0+maintenance.29', files=[]), self.root)
        engine = Engine(self.root, read_only=True)
        model_pointer = engine.state_dir/'models/package-x2-mini.json'
        shared_pointer = engine.state_dir/'models/shared-x2-mini.json'
        anchors = [path.read_bytes() for path in (model_pointer, shared_pointer)]
        original_battery = engine._state()['components']['x2-mini:battery']
        package = self.runtime/'cache/model-bundles/packages/x2-mini'
        value = json.loads((package/'manifest.json').read_bytes())
        value.update(version='0.1.0-localtest.2', minimum_engine_version='0.1.7')
        sources = Path(__file__).resolve().parents[1]/'adaptations/common/cpu/intel/microcode'
        files = []
        for source, target in (('inputs/06-cc-02', microcode.BLOB_TARGET),
                               ('licenses/Intel-microcode.txt', microcode.LICENSE_TARGET)):
            path = package/source
            path.parent.mkdir(parents=True, exist_ok=True)
            data = (sources/source).read_bytes()
            path.write_bytes(data)
            size, digest = microcode.INPUTS[target]
            files.append(dict(source=source, target=target, size=size, sha256=digest))
            value['files'].append(dict(path=source, size=size, sha256=digest))
        value['components'].append(dict(schema_version=1, kind='ComponentManifest', id=microcode.IDENTITY,
            version='0.1.0', minimum_engine_version='0.1.7', optional=True, default=False,
            microcode_boot_policy={'kind': 'onexplayer-intel-microcode-v1'}, files=files))
        value['shared_adaptations']['components'].append(microcode.IDENTITY)
        raw = (json.dumps(value)+'\n').encode()
        (package/'manifest.json').write_bytes(raw)
        descriptor = self.runtime/'cache/model-bundles/manifest.json'
        index = json.loads(descriptor.read_bytes())
        index['packages'][0].update(version=value['version'], manifest_sha256=hashlib.sha256(raw).hexdigest())
        descriptor.write_text(json.dumps(index))
        boot = self.root/'boot/initramfs-stock.img'
        boot.parent.mkdir()
        original = graphics_boot._fragment({'init': b'fixture'}, {})
        boot.write_bytes(original)

        def plan(action, **kwargs):
            return shared.plan(self.root, 'device:x2-mini', [microcode.IDENTITY], action=action, **kwargs)

        self.execute(plan('apply', **self.kw))
        self.assertNotEqual(original, boot.read_bytes())
        sidecar = next((engine.state_dir/'models/microcode-packages').iterdir())
        self.assertEqual({'manifest.json', 'inputs/06-cc-02', 'licenses/Intel-microcode.txt'},
                         {p.relative_to(sidecar).as_posix() for p in sidecar.rglob('*') if p.is_file()})
        retained_only = dict(self.kw, runtime_root=self.runtime/'no-bundle')
        for action in ('verify', 'restore'):
            reviewed = plan(action, **retained_only)
            result = shared.execute(self.root, reviewed, reviewed_sha256=reviewed['review_sha256'], **retained_only)
            self.assertTrue(result['result']['verified'])
        self.assertEqual(original, boot.read_bytes())
        self.assertEqual(anchors, [path.read_bytes() for path in (model_pointer, shared_pointer)])
        self.assertEqual(original_battery, engine._state()['components']['x2-mini:battery'])

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

    def test_unavailable_component_is_not_selectable_for_maintenance(self):
        view=shared.options(self.root,'device:x2-mini',**self.kw)
        self.assertTrue(view['capabilities'][0]['available'])
        package=self.runtime/'cache/model-bundles/packages/x2-mini/manifest.json'
        value=json.loads(package.read_bytes());value['components'][0]['available']=False
        raw=(json.dumps(value)+'\n').encode();package.write_bytes(raw)
        descriptor=self.runtime/'cache/model-bundles/manifest.json'
        index=json.loads(descriptor.read_bytes());index['packages'][0]['manifest_sha256']=hashlib.sha256(raw).hexdigest()
        descriptor.write_text(json.dumps(index))
        view=shared.options(self.root,'device:x2-mini',**self.kw)
        self.assertFalse(view['capabilities'][0]['available'])
        with self.assertRaisesRegex(MaintenanceError,'unavailable'):
            self.make_plan()

    def test_unavailable_installed_component_can_still_verify_and_restore(self):
        self.execute(self.make_plan())
        original=shared.package
        def unavailable(*args,**kwargs):
            item=original(*args,**kwargs)
            for component in item['components']:
                if component['id']=='x2-mini:battery': component['available']=False
            return item
        with patch.object(shared,'package',side_effect=unavailable):
            view=shared.options(self.root,'device:x2-mini',**self.kw)
            self.assertFalse(view['capabilities'][0]['available'])
            self.assertTrue(self.execute(self.make_plan('verify'))['result']['verified'])
            self.execute(self.make_plan('restore'))

    def test_fingerprint_action_is_not_exposed(self):
        self.assertFalse(hasattr(shared, 'fingerprint'))

    def test_fingerprint_keyboard_targets_are_optional_and_atomic(self):
        targets = shared.FINGERPRINT_FIXED | {shared.FINGERPRINT_RULES['device:oxp3'],
            shared.FINGERPRINT_NATIVE + 'libexec/fprintd',
            *(shared.FINGERPRINT_NATIVE + 'bin/fprintd-' + name for name in ('verify', 'enroll', 'list', 'delete'))}
        historical = [dict(target=target) for target in sorted(targets)]
        keyboard = [dict(target=target, mode=mode) for target, mode in shared.FINGERPRINT_KEYBOARD.items()]
        component = dict(id=shared.FINGERPRINT_COMPONENT, optional=True, default=True)
        engine = Engine(self.root)
        shared.check_fingerprint_component(engine, dict(component, files=historical))
        shared.check_fingerprint_component(engine, dict(component, files=historical + keyboard))
        with self.assertRaisesRegex(MaintenanceError, 'Incomplete fingerprint keyboard runtime'):
            shared.check_fingerprint_component(engine, dict(component, files=historical + keyboard[:1]))

    def test_pending_before_first_commit_exposes_restore(self):
        with patch('maintenance.live_root.pending', return_value={'id': 'interrupted-root-window'}):
            view = shared.options(self.root, 'device:x2-mini', **self.kw)
            self.assertIsNone(view['current'])
            self.assertIsNotNone(view['pending'])
            with self.assertRaisesRegex(MaintenanceError, 'recovery first'):
                self.make_plan()
            with self.assertRaisesRegex(MaintenanceError, 'known shared'):
                shared.plan(self.root,'device:x2-mini',['x2-mini:unknown'],action='restore',**self.kw)
            with self.assertRaisesRegex(MaintenanceError, 'known shared'):
                shared.plan(self.root,'device:x2-mini',['x2-mini:battery']*2,action='restore',**self.kw)
            with self.assertRaisesRegex(MaintenanceError, 'installed'):
                self.make_plan('verify')
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


class MicrocodeModelIdentity(unittest.TestCase):
    def test_existing_manual_package_dmi_is_still_compared_exactly(self):
        from types import SimpleNamespace
        engine = SimpleNamespace(root=Path('/'))
        package = dict(hardware_matching=dict(mode='manual', automatic_selection=False,
                       dmi=dict(sys_vendor='ONE-NETBOOK', product_name='ONEXPLAYER 3')))
        with patch.object(Path, 'read_text', side_effect=['ONE-NETBOOK\n', 'ONEXPLAYER 3\n']):
            shared._check_live_microcode_model(engine, package)
        with patch.object(Path, 'read_text', side_effect=['ONE-NETBOOK\n', 'ONEXPLAYER X2 Mini\n']):
            with self.assertRaisesRegex(MaintenanceError, 'Running model does not match'):
                shared._check_live_microcode_model(engine, package)


if __name__ == '__main__':
    unittest.main()
