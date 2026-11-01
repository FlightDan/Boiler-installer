"""Local source checks; all public materializer bytes come from the pinned bundle."""
import copy
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT.parents[4]/'cache/model-bundles/packages/oxp3'
spec = importlib.util.spec_from_file_location('maintenance_adapter_under_test', ROOT/'maintenance_adapter.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


@unittest.skipUnless(BUNDLE.is_dir(), 'Prepared public model bundle is required')
class MaintenanceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.package, self.root = self.work/'package', self.work/'target'
        self.root.mkdir()
        self.package.mkdir()
        shutil.copy2(ROOT/'adapter.py', self.package/'adapter.py')
        components = self.package/'payload/legacy/components'
        components.mkdir(parents=True)
        for name in ('options.json', 'split-lock/UPSTREAM_LICENSE'):
            destination = components/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BUNDLE/'payload/legacy/components'/name, destination)
        resources = json.loads((BUNDLE/'payload/legacy/integration/manifest.json').read_text())
        self.defaults = ['input', 'multifunction', 'gyro', 'xe-dsb', 'split-lock']
        integration = self.package/'payload/legacy/integration'
        integration.mkdir()
        (integration/'manifest.json').write_text(json.dumps(resources))
        for name in self.defaults:
            for entry in resources.get(name, {}).get('files', []):
                destination = integration/entry['source']
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(BUNDLE/'payload/legacy/integration'/entry['source'], destination)
        manifest = json.loads((BUNDLE/'manifest.json').read_text())
        manifest['maintenance_contract'] = adapter.CONTRACT
        (self.package/'manifest.json').write_text(json.dumps(manifest))
        shutil.copy2(ROOT/'selection.example.json', self.package/'selection.json')
        self.api = adapter.modules(BUNDLE)
        self.system = dict(os=dict(self.api['compatibility'].BASE),
            kernel=self.api['compatibility'].KERNEL, uid=1000, gid=1000, dmi={}, bios='',
            packages={'inputplumber': '0.78.0-1'},
            slot=dict(id='partuuid-11111111-1111-1111-1111-111111111111', label='A',
                      partuuid='11111111-1111-1111-1111-111111111111', other_slots=[],
                      target_disk=dict(bus='NVMe', model='other-drive')),
            target_disk=dict(bus='NVMe', model='other-drive'),
            shared_compatible=True, shared_os_compatible=True)
        for name in self.defaults:
            self.system['packages'].update(resources.get(name, {}).get('packages', {}))
        for relative, data in {
            'etc/os-release': 'ID=steamos\nVERSION_ID=3.10\nBUILD_ID=20260924.1000\n',
            'etc/passwd': 'deck:x:1000:1000::/home/deck:/bin/bash\n',
            'efi/EFI/steamos/grub.cfg': 'linux /boot/vmlinuz-test quiet\n',
        }.items():
            target = self.root/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data)
        (self.root/'home/deck').mkdir(parents=True)
        choice = json.loads((ROOT/'selection.example.json').read_text())
        choice.pop('slot')
        choice['repairs'] = ['oxp3:xe-dsb']
        self.request = dict(schema_version=1, selection=choice, scope='active-root-and-shared-home')

    def call(self, action, request=None):
        with mock.patch.object(adapter, '_hold_status', return_value=(True, '')):
            return adapter.execute(action, self.root, request or self.request,
                package=self.package, api=self.api, system=copy.deepcopy(self.system), enforce_owner=False)

    def test_plan_is_readonly_and_exposes_all_catalog_rows(self):
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        plan = self.call('plan')
        self.assertTrue(plan['can_apply'])
        catalog = json.loads((self.package/'payload/legacy/components/options.json').read_text())
        self.assertEqual(len(plan['capabilities']), len(catalog['repairs'])+len(catalog['plugins']))
        self.assertFalse((self.root/adapter.STATE).exists())
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()})
        for identity in ('oxp3:refind', 'oxp3:waydroid', 'oxp3:gm7-resume', 'oxp3:split-lock'):
            row = next(row for row in plan['capabilities'] if row['id']==identity)
            self.assertFalse(row['available'])
            self.assertTrue(row['reason'])

    def test_supported_four_materialize_and_efi_boot_setting_is_blocked(self):
        request = copy.deepcopy(self.request)
        request['selection']['repairs'] = ['oxp3:'+name for name in self.defaults if name != 'split-lock']
        plan = self.call('plan', request)
        self.assertTrue(plan['can_apply'], plan['components'])
        self.assertNotIn('efi/EFI/steamos/grub.cfg', plan['files'])
        self.assertIn(adapter.GUARD, plan['files'])
        self.assertIn(adapter.ENABLE, plan['files'])
        self.assertFalse(any(path.startswith('usr/local/lib/oxp3-maintenance/') for path in plan['files']))
        self.assertFalse((self.root/'home/.oxp3-installer').exists())
        before = (self.root/'efi/EFI/steamos/grub.cfg').read_bytes()
        request['selection']['repairs'].append('oxp3:split-lock')
        blocked = self.call('plan', request)
        self.assertFalse(blocked['executable'])
        self.assertIn('EFI/VFAT', blocked['components']['split-lock']['reason'])
        self.assertNotIn('efi/EFI/steamos/grub.cfg', blocked['files'])
        self.assertEqual(before, (self.root/'efi/EFI/steamos/grub.cfg').read_bytes())

    def test_hold_scope_dependencies_and_unknown_choices_rejected_before_writes(self):
        for mutate in (
            lambda value: value['selection'].update(acknowledge_legacy_update_hold=False),
            lambda value: value.update(scope='unacknowledged'),
            lambda value: value['selection'].update(repairs=['oxp3:multifunction']),
            lambda value: value['selection'].update(repairs=['oxp3:unknown']),
        ):
            request = copy.deepcopy(self.request)
            mutate(request)
            with self.assertRaises(ValueError):
                self.call('plan', request)
            self.assertFalse((self.root/adapter.STATE).exists())

    def test_exact_manifest_contract_required(self):
        manifest = json.loads((self.package/'manifest.json').read_text())
        manifest['maintenance_contract'] = dict(kind='engine-hooks-v1')
        (self.package/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'contract'):
            self.call('plan')

    def test_changed_package_invalidates_review_before_state_creation(self):
        request = copy.deepcopy(self.request)
        request['expected_plan_sha256'] = self.call('plan')['plan_sha256']
        manifest = json.loads((self.package/'manifest.json').read_text())
        manifest['version'] = '0.1.0-new-test'
        (self.package/'manifest.json').write_text(json.dumps(manifest))
        with mock.patch.object(adapter, '_require_root'), self.assertRaisesRegex(RuntimeError, 'changed'):
            self.call('apply', request)
        self.assertFalse((self.root/adapter.STATE).exists())

    def test_file_apply_verify_change_refusal_and_restore(self):
        plan = self.call('plan')
        request = dict(self.request, expected_plan_sha256=plan['plan_sha256'])
        with mock.patch.object(adapter, '_require_root'):
            result = self.call('apply', request)
        self.assertEqual(result['status'], 'files-installed')
        self.assertTrue(self.call('verify')['verified'])
        target = self.root/'etc/modprobe.d/xe-oxp3.conf'
        installed = target.read_bytes()
        target.write_bytes(b'user edit\n')
        with mock.patch.object(adapter, '_require_root'), self.assertRaises(Exception):
            self.call('restore')
        self.assertEqual(target.read_bytes(), b'user edit\n')
        target.write_bytes(installed)
        with mock.patch.object(adapter, '_require_root'):
            result = self.call('restore')
        self.assertEqual(result['status'], 'restored')
        self.assertFalse(target.exists())
        self.assertFalse(self.call('verify')['verified'])

    def test_selection_change_requires_explicit_restore(self):
        request = dict(self.request, expected_plan_sha256=self.call('plan')['plan_sha256'])
        with mock.patch.object(adapter, '_require_root'):
            self.call('apply', request)
        request = copy.deepcopy(self.request)
        request['selection']['repairs'] = ['oxp3:input', 'oxp3:xe-dsb']
        plan = self.call('plan', request)
        self.assertFalse(plan['can_apply'])
        self.assertTrue(plan['replacement_requires_restore'])
        with mock.patch.object(adapter, '_require_root'):
            self.call('restore')
        self.assertTrue(self.call('plan', request)['can_apply'])

    def test_shared_probe_failure_blocks_shared_setting_without_faking_compatibility(self):
        request = copy.deepcopy(self.request)
        request['selection']['repairs'] = ['oxp3:keyboard']
        target = self.root/'usr/share/applications/org.kde.plasma.keyboard.desktop'
        target.parent.mkdir(parents=True)
        target.write_text('[Desktop Entry]\n')
        with mock.patch.object(self.api['probe'], 'verify_shared', return_value=False), mock.patch.object(self.api['probe'], 'verify_shared_os', return_value=False):
            plan = self.call('plan', request)
        self.assertFalse(plan['can_apply'])
        self.assertFalse(plan['shared_compatibility_verified'])
        self.assertIn('Shared', plan['components']['keyboard']['reason'])

    def test_absent_hold_blocks_maintenance_without_installing_policy(self):
        with mock.patch.object(adapter, '_hold_status', return_value=(False, 'Existing update hold is required')):
            report = adapter.execute('plan', self.root, self.request, package=self.package,
                api=self.api, system=self.system, enforce_owner=False)
        self.assertFalse(report['executable'])
        self.assertEqual(report['reasons'], ['Existing update hold is required'])
        self.assertFalse((self.root/adapter.STATE).exists())

    def test_inspect_without_selection_returns_current_catalog_contract(self):
        report = self.call('inspect', dict(schema_version=1, scope='active-root-and-shared-home'))
        self.assertTrue(report['ok'])
        self.assertTrue(report['executable'])
        self.assertIsNone(report['current'])
        self.assertIn('partuuid', report['target'])

    def test_restore_wrong_partition_does_not_touch_other_slot(self):
        request = dict(self.request, expected_plan_sha256=self.call('plan')['plan_sha256'])
        with mock.patch.object(adapter, '_require_root'):
            self.call('apply', request)
        self.system['slot'].update(id='partuuid-22222222-2222-2222-2222-222222222222', partuuid='22222222-2222-2222-2222-222222222222')
        with mock.patch.object(adapter, '_require_root'), self.assertRaisesRegex(RuntimeError, 'another root partition'):
            self.call('restore')
        self.assertTrue((self.root/'etc/modprobe.d/xe-oxp3.conf').exists())

    def test_pending_first_apply_can_be_inspected_planned_and_restored(self):
        request = dict(self.request, expected_plan_sha256=self.call('plan')['plan_sha256'])
        store_class = self.api['transactions'].Store
        with mock.patch.object(adapter, '_require_root'), mock.patch.object(store_class, '_after_write', side_effect=RuntimeError('simulated interruption')):
            with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
                self.call('apply', request)
        report = self.call('inspect', dict(schema_version=1, scope='active-root-and-shared-home'))
        self.assertTrue(report['executable'])
        self.assertEqual(report['current'], self.request['selection'])
        restore_request = dict(self.request, action='restore')
        plan = self.call('plan', restore_request)
        self.assertTrue(plan['executable'])
        restore_request['expected_plan_sha256'] = plan['plan_sha256']
        with mock.patch.object(adapter, '_require_root'):
            result = self.call('restore', restore_request)
        self.assertTrue(result['recovery']['recovered'])
        self.assertFalse((self.root/'etc/modprobe.d/xe-oxp3.conf').exists())

    def test_interrupted_restore_uses_pending_before_after_images(self):
        request = dict(self.request, expected_plan_sha256=self.call('plan')['plan_sha256'])
        with mock.patch.object(adapter, '_require_root'):
            self.call('apply', request)
        store_class = self.api['transactions'].Store
        with mock.patch.object(adapter, '_require_root'), mock.patch.object(store_class, '_after_write', side_effect=RuntimeError('restore interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'restore interrupted'):
                self.call('restore')
        self.assertFalse((self.root/'etc/modprobe.d/xe-oxp3.conf').exists())
        restore_request = dict(self.request, action='restore')
        plan = self.call('plan', restore_request)
        self.assertTrue(plan['executable'], plan)
        self.assertEqual(plan['modified'], [])
        restore_request['expected_plan_sha256'] = plan['plan_sha256']
        with mock.patch.object(adapter, '_require_root'):
            result = self.call('restore', restore_request)
        self.assertTrue(result['recovery']['recovered'])
        self.assertFalse((self.root/adapter.STATE/'pending.json').exists())
        self.assertFalse(self.call('verify')['verified'])

    def test_pending_restore_still_refuses_unknown_external_bytes(self):
        request = dict(self.request, expected_plan_sha256=self.call('plan')['plan_sha256'])
        with mock.patch.object(adapter, '_require_root'):
            self.call('apply', request)
        with mock.patch.object(adapter, '_require_root'), mock.patch.object(self.api['transactions'].Store, '_after_write', side_effect=RuntimeError('restore interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'restore interrupted'):
                self.call('restore')
        target = self.root/'etc/modprobe.d/xe-oxp3.conf'
        target.write_bytes(b'new administrator contents\n')
        plan = self.call('plan', dict(self.request, action='restore'))
        self.assertFalse(plan['executable'])
        self.assertIn('etc/modprobe.d/xe-oxp3.conf', plan['modified'])
        with mock.patch.object(adapter, '_require_root'), self.assertRaisesRegex(RuntimeError, 'changed model files'):
            self.call('restore')
        self.assertEqual(target.read_bytes(), b'new administrator contents\n')

    def test_slotless_wire_and_language_normalization_do_not_change_guest_guards(self):
        request = copy.deepcopy(self.request)
        request['selection']['kde_language'] = 'zh-CN'
        plan = self.call('plan', request)
        self.assertNotIn('slot', plan['selection'])
        self.assertEqual(plan['selection']['kde_language'], 'zh-CN')
        spec = importlib.util.spec_from_file_location('unchanged_guest_validator', ROOT/'adapter.py')
        guest = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guest)
        catalog = json.loads((self.package/'payload/legacy/components/options.json').read_text())
        with self.assertRaises(ValueError):
            guest.selection(request['selection'], catalog)

    def test_inactive_legacy_owner_blocks_shared_setting_without_blocking_other_root_files(self):
        keyboard = self.root/'usr/share/applications/org.kde.plasma.keyboard.desktop'
        keyboard.parent.mkdir(parents=True)
        keyboard.write_text('[Desktop Entry]\n')
        shared = self.root/'home/deck/.config/kwinrc'
        shared.parent.mkdir(parents=True)
        shared.write_bytes(b'[Wayland]\nVirtualKeyboardMode=0\n')
        state = self.root/'home/.oxp3-maintenance'
        old = self.api['transactions'].Store(self.root, state, uid=1000, gid=1000, enforce_owner=False)
        inactive = 'partuuid-22222222-2222-2222-2222-222222222222'
        old.apply({'home/deck/.config/kwinrc': dict(data=shared.read_bytes(), user=True, component='keyboard'),
                   'etc/modprobe.d/xe-oxp3.conf': dict(data=b'other root only\n', component='xe-dsb')},
                  {'version': 'fixture'}, ['keyboard', 'xe-dsb'], inactive)
        request = copy.deepcopy(self.request)
        request['selection']['repairs'] = ['oxp3:keyboard']
        # The old Store Windows fixtures have no POSIX permissions. Preserve
        # real ownership checking in production and isolate that fixture seam.
        with mock.patch.object(adapter, '_check_state'):
            plan = self.call('plan', request)
            self.assertFalse(plan['can_apply'])
            self.assertEqual(plan['legacy_path_conflicts'][0]['slot'], inactive)
            self.assertIn('kwinrc', plan['components']['keyboard']['reason'])
            self.assertTrue(self.call('plan')['can_apply'])
        self.assertEqual(shared.read_bytes(), b'[Wayland]\nVirtualKeyboardMode=0\n')

    def test_inspect_shows_completed_legacy_choices_and_preserves_both_journals(self):
        directory = self.root/'home/.oxp3-installer/integration/A'
        directory.mkdir(parents=True)
        options = dict(schema=1, repairs=['xe-dsb', 'input'], plugins=[],
                       installer_language='zh-CN', kde_language='zh_CN')
        request = directory/'request.json'
        result = directory/'result.json'
        request.write_text(json.dumps(options))
        result.write_text(json.dumps(dict(schema=1, slot='A', options=options,
            components={'xe-dsb': {'status': 'files_installed'}, 'input': {'status': 'not_installed'}})))
        before = request.read_bytes(), result.read_bytes()
        with mock.patch.object(self.api['adoption'], '_trusted'):
            report = self.call('inspect', dict(schema_version=1, scope='active-root-and-shared-home'))
            self.assertEqual(report['current']['repairs'], ['oxp3:xe-dsb'])
            self.assertNotIn('slot', report['current'])
            self.assertEqual(report['current']['kde_language'], 'zh-CN')
            self.assertEqual(report['current_source'], 'legacy-installer')
            with self.assertRaisesRegex(RuntimeError, 'not Boiler-owned'):
                self.call('plan', dict(self.request, action='restore'))
        self.assertEqual(before, (request.read_bytes(), result.read_bytes()))

    def test_inspect_materializes_authenticated_nondefault_legacy_selection_readonly(self):
        directory = self.root/'home/.oxp3-installer/integration/A'
        directory.mkdir(parents=True)
        options = dict(schema=1, repairs=['slot-switch', 'partition-mount', 'opti2xmx'],
                       plugins=['decky'], installer_language='en-US', kde_language='system')
        (directory/'request.json').write_text(json.dumps(options))
        (directory/'result.json').write_text(json.dumps(dict(schema=1, slot='A', options=options,
            components={name: {'status': 'files_installed'} for name in options['repairs']+options['plugins']})))
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        with mock.patch.object(self.api['adoption'], '_trusted'), mock.patch.object(adapter, '_hold_status', return_value=(True, '')):
            report = adapter.execute('inspect', self.root, dict(schema_version=1, scope='active-root-and-shared-home'),
                package=BUNDLE, api=self.api, system=copy.deepcopy(self.system), enforce_owner=False)
            direct = adapter.execute('plan', self.root, dict(schema_version=1, scope='active-root-and-shared-home', selection=report['current']),
                package=BUNDLE, api=self.api, system=copy.deepcopy(self.system), enforce_owner=False)
        self.assertEqual(report['current_source'], 'legacy-installer')
        self.assertEqual(report['current']['repairs'], ['oxp3:slot-switch', 'oxp3:partition-mount', 'oxp3:opti2xmx'])
        self.assertEqual(report['current']['plugins'], ['oxp3:decky'])
        rows = {row['id']: row for row in report['capabilities']}
        planned = {row['id']: row for row in direct['capabilities']}
        for name in ('slot-switch', 'partition-mount', 'opti2xmx', 'decky'):
            self.assertTrue(rows['oxp3:'+name]['selected'])
            self.assertIn('status', rows['oxp3:'+name])
            self.assertEqual({key: rows['oxp3:'+name].get(key) for key in ('status','reason','available')},
                             {key: planned['oxp3:'+name].get(key) for key in ('status','reason','available')})
        self.assertIn('oxp3:decky', rows['oxp3:opti2xmx']['dependencies'])
        self.assertFalse((self.root/adapter.STATE).exists())
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_inspect_rejects_inconsistent_legacy_selection_without_writes(self):
        directory = self.root/'home/.oxp3-installer/integration/A'
        directory.mkdir(parents=True)
        options = dict(schema=1, repairs=['xe-dsb'], plugins=[], installer_language='en-US', kde_language='system')
        (directory/'request.json').write_text(json.dumps(options))
        changed = dict(options, repairs=['input'])
        (directory/'result.json').write_text(json.dumps(dict(schema=1, slot='A', options=changed,
            components={'xe-dsb': {'status': 'files_installed'}})))
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        with mock.patch.object(self.api['adoption'], '_trusted'), self.assertRaisesRegex(RuntimeError, 'selection proof is incomplete'):
            self.call('inspect', dict(schema_version=1, scope='active-root-and-shared-home'))
        self.assertFalse((self.root/adapter.STATE).exists())
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_inspect_prioritizes_saved_choice_over_legacy(self):
        request = dict(self.request, expected_plan_sha256=self.call('plan')['plan_sha256'])
        with mock.patch.object(adapter, '_require_root'):
            self.call('apply', request)
        directory = self.root/'home/.oxp3-installer/integration/A'
        directory.mkdir(parents=True)
        options = dict(schema=1, repairs=['input'], plugins=[], installer_language='en-US', kde_language='system')
        (directory/'request.json').write_text(json.dumps(options))
        (directory/'result.json').write_text(json.dumps(dict(schema=1, slot='A', options=options,
            components={'input': {'status': 'files_installed'}})))
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        with mock.patch.object(self.api['adoption'], '_trusted'):
            report = self.call('inspect', dict(schema_version=1, scope='active-root-and-shared-home'))
        self.assertEqual(report['current_source'], 'boiler-model')
        self.assertEqual(report['current'], self.request['selection'])
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_explicit_outer_intent_recovery_before_first_store_write_is_readonly(self):
        directory = self.root/'home/.oxp3-installer/integration/A'
        directory.mkdir(parents=True)
        options = dict(schema=1, repairs=['xe-dsb'], plugins=[], installer_language='en-US', kde_language='system')
        (directory/'request.json').write_text(json.dumps(options))
        (directory/'result.json').write_text(json.dumps(dict(schema=1, slot='A', options=options,
            components={'xe-dsb': {'status': 'files_installed'}})))
        before = {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        request = dict(self.request, action='restore', recovery_requested=True)
        plan = self.call('plan', request)
        self.assertTrue(plan['executable'])
        self.assertTrue(plan['no_file_transaction'])
        self.assertEqual(plan['summary']['files'], 0)
        self.assertFalse((self.root/adapter.STATE).exists())
        request['expected_plan_sha256'] = plan['plan_sha256']
        with mock.patch.object(adapter, '_require_root'):
            result = self.call('restore', request)
        self.assertEqual(result['status'], 'not-managed')
        self.assertEqual(result['recovery'], 'no-file-transaction-to-restore')
        self.assertFalse((self.root/adapter.STATE).exists())
        self.assertEqual(before, {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()})
        with mock.patch.object(self.api['adoption'], '_trusted'), self.assertRaisesRegex(RuntimeError, 'not Boiler-owned'):
            self.call('plan', dict(self.request, action='restore'))

    def test_verified_installer_original_survives_model_adoption_and_restore(self):
        target = self.root/'etc/modprobe.d/xe-oxp3.conf'
        target.parent.mkdir(parents=True)
        desired = (self.package/'payload/legacy/integration/xe-dsb/000.bin').read_bytes()
        target.write_bytes(desired)
        original = dict(exists=True, data=base64.b64encode(b'original vendor config\n').decode(),
                        mode=target.stat().st_mode & 0o7777, uid=0, gid=0)
        relative = target.relative_to(self.root).as_posix()
        state = self.root/'home/.oxp3-installer/integration/A/xe-dsb/state.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps(dict(phase='installed', allowed=[relative], original={relative: original},
                                        applied={relative: base64.b64encode(desired).decode()}, metadata={relative: {}})))
        old_bytes = state.read_bytes()
        # The Windows fixture has no root-owned POSIX journal permissions; the
        # production validator remains intact and native checks cover those.
        with mock.patch.object(self.api['adoption'], '_trusted'):
            request = dict(self.request, expected_plan_sha256=self.call('plan')['plan_sha256'])
            with mock.patch.object(adapter, '_require_root'):
                self.call('apply', request)
                self.call('restore')
            self.assertEqual(target.read_bytes(), b'original vendor config\n')
            request = dict(self.request, expected_plan_sha256=self.call('plan')['plan_sha256'])
            with mock.patch.object(adapter, '_require_root'):
                self.call('apply', request)
                self.call('restore')
        self.assertEqual(target.read_bytes(), b'original vendor config\n')
        self.assertEqual(state.read_bytes(), old_bytes)

    def test_language_is_transactional_and_restore_preserves_previous_settings(self):
        request = copy.deepcopy(self.request)
        request['selection']['kde_language'] = 'ja_JP'
        original = b'LANG=en_US.UTF-8\nLC_TIME=de_DE.UTF-8\n'
        (self.root/'etc/locale.conf').write_bytes(original)
        request['expected_plan_sha256'] = self.call('plan', request)['plan_sha256']
        with mock.patch.object(adapter, '_require_root'):
            self.call('apply', request)
            self.assertIn(b'LANG=ja_JP.UTF-8', (self.root/'etc/locale.conf').read_bytes())
            self.assertIn(b'LC_TIME=de_DE.UTF-8', (self.root/'etc/locale.conf').read_bytes())
            self.call('restore')
        self.assertEqual((self.root/'etc/locale.conf').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
