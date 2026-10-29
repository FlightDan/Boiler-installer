"""Synthetic candidate planning and immutable native-inventory checks."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'components'), str(ROOT / 'guest')]
import graphics
from integration import merged_firmware_plan, stage, validate_options, verify_device_components
from payload_lock import record

PROFILE = 'graphics:amd-780m'


class GraphicsFirmwareChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'root'; self.root.mkdir()
        self.payload = self.base / 'payload'; self.payload.mkdir()
        (self.root / 'etc').mkdir()
        (self.root / 'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.10\nBUILD_ID=fixture\n')
        self.module = self.root / 'usr/lib/modules/fixture/kernel/drivers/gpu/drm/amd/amdgpu/amdgpu.ko.zst'
        self.module.parent.mkdir(parents=True); self.module.write_bytes(b'target-driver')
        import graphics_boot
        boot = self.root / 'boot'; boot.mkdir()
        self.original_initramfs = b''.join((graphics_boot._entry('lib', b'usr/lib', 0o120777, 1),
            graphics_boot._entry('init', b'fixture-init', 0o100755, 2), graphics_boot._entry('TRAILER!!!', b'', 0, 3)))
        self.initramfs = boot / 'initramfs-linux.img'; self.initramfs.write_bytes(self.original_initramfs)
        self.bundle = dict(schema=1, kind='GraphicsFirmwareBundle', id=PROFILE, version='0.1.0-baseline.1',
                           drivers=['amdgpu'], files=[], licenses=[], available=True, delivery='on-demand',
                           provenance=dict(revision='a' * 40, url='https://example.invalid/firmware/'),
                           baseline=dict(revision='1', id='fixture'), runtime='not_validated', status='experimental-baseline')
        self.manifest = {PROFILE: self.bundle}
        self.candidate('amdgpu/selected.bin')
        notice = b'Fixture notice'
        (self.payload / 'NOTICE').write_bytes(notice)
        self.bundle['licenses'] = [record('NOTICE', notice, target='licenses/NOTICE')]

    def candidate(self, name):
        data = ('candidate-' + name).encode()
        source = 'graphics/amd-780m/firmware/' + name
        path = self.payload / source; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        self.bundle['files'].append(record(source, data, target='usr/lib/firmware/' + name))

    def native(self, name, data=b'native'):
        path = self.root / 'usr/lib/firmware' / name
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return path

    def options(self, **extra):
        return dict(schema=1, repairs=[], plugins=[], graphics_profile=PROFILE, **extra)

    def plan(self):
        return graphics.plan_required(self.root, self.manifest, PROFILE)

    def bound_payload(self, options):
        integration = self.payload / 'integration'; integration.mkdir(exist_ok=True)
        for item in self.bundle['files'] + self.bundle['licenses']:
            path = integration / item['source']; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((self.payload / item['source']).read_bytes())
        catalog = json.dumps(dict(schema=1, components=self.manifest)).encode()
        (integration / 'manifest.json').write_bytes(catalog)
        records, plans = merged_firmware_plan(self.root, self.manifest, options)
        plan = dict(schema=1, selection=options, catalog_sha256=hashlib.sha256(catalog).hexdigest(),
                    slots={slot: dict(records=records, plans=plans) for slot in ('A', 'B')})
        (self.payload / 'firmware-plan.json').write_text(json.dumps(plan))

    def test_only_explicit_profile_candidates_are_resolved(self):
        other = dict(self.bundle, id='graphics:amd-z1', files=[])
        self.manifest['graphics:amd-z1'] = other
        calls = []
        records, plans = graphics.plan_required(self.root, self.manifest, PROFILE, lambda path: calls.append(path))
        self.assertEqual({'usr/lib/firmware/amdgpu/selected.bin'}, set(records))
        self.assertEqual([self.module], calls)
        self.assertEqual({PROFILE}, set(plans))
        plan = plans[PROFILE]
        self.assertEqual('runtime_dependent', plan['coverage'])
        self.assertEqual('not_validated', plan['runtime'])
        self.assertEqual('not_validated', plan['early_boot'])
        self.assertEqual(self.bundle['baseline'], plan['baseline'])
        self.assertEqual(hashlib.sha256(b'target-driver').hexdigest(), next(iter(plan['module_fingerprints'].values()))['sha256'])

    def test_native_and_compressed_candidates_are_preserved_and_fingerprinted(self):
        for suffix in ('', '.xz', '.zst', '.gz'):
            with self.subTest(suffix=suffix):
                path = self.native('amdgpu/selected.bin' + suffix)
                records, plans = self.plan()
                self.assertEqual({}, records)
                self.assertEqual([path.relative_to(self.root).as_posix()], plans[PROFILE]['preserved'])
                self.assertTrue(graphics.verify_inventory(self.root, plans[PROFILE]))
                path.unlink()

    def test_native_firmware_changed_after_planning_is_detected(self):
        path = self.native('amdgpu/selected.bin')
        _, plans = self.plan()
        path.write_bytes(b'modified-native')
        with self.assertRaisesRegex(ValueError, 'inventory changed'):
            graphics.verify_inventory(self.root, plans[PROFILE])

    def test_missing_driver_is_refused_even_if_all_candidates_exist(self):
        self.native('amdgpu/selected.bin')
        self.module.unlink()
        with self.assertRaisesRegex(ValueError, 'no existing target driver'):
            self.plan()

    def test_other_driver_or_similar_module_does_not_establish_gpu_driver(self):
        self.module.rename(self.module.with_name('amdgpu_test.ko'))
        with self.assertRaisesRegex(ValueError, 'no existing target driver'):
            self.plan()

    def test_payload_content_is_verified_only_for_missing_candidates(self):
        item = self.bundle['files'][0]
        (self.payload / item['source']).write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            graphics.resolve(self.root, self.payload, self.manifest, PROFILE)
        self.native('amdgpu/selected.bin')
        self.assertEqual({}, graphics.resolve(self.root, self.payload, self.manifest, PROFILE)[0])

    def test_native_aliases_are_preserved_and_dangling_aliases_refused(self):
        native = self.native('amdgpu/real.bin')
        alias = native.with_name('selected.bin')
        try: alias.symlink_to('/usr/lib/firmware/amdgpu/real.bin')
        except OSError: self.skipTest('Native symlink creation unavailable')
        self.assertEqual({}, self.plan()[0])
        native.unlink()
        with self.assertRaisesRegex(ValueError, 'dangling native firmware alias'):
            self.plan()

    def test_native_directory_alias_and_escape_are_fail_closed(self):
        directory = self.root / 'usr/lib/firmware'; directory.mkdir()
        alias = directory / 'amdgpu'
        try: alias.symlink_to('/missing', target_is_directory=True)
        except OSError: self.skipTest('Native symlink creation unavailable')
        with self.assertRaisesRegex(ValueError, 'dangling native directory alias'):
            self.plan()
        alias.unlink(); alias.symlink_to('../../../../outside', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.plan()

    def test_existing_directory_leaf_is_not_treated_as_missing_firmware(self):
        (self.root / 'usr/lib/firmware/amdgpu/selected.bin').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'existing directory'):
            self.plan()

    def test_inventory_cannot_add_modules_wildcards_or_other_vendor(self):
        for target in ('usr/lib/firmware/amdgpu/driver.ko.zst', 'usr/lib/firmware/amdgpu/*',
                       'usr/lib/firmware/xe/other.bin', 'etc/modprobe.d/policy.conf'):
            with self.subTest(target=target):
                self.bundle['files'][0]['target'] = target
                with self.assertRaises(ValueError): self.plan()

    def test_duplicate_candidates_and_missing_licenses_fail(self):
        self.bundle['files'].append(dict(self.bundle['files'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'): self.plan()
        self.bundle['files'].pop(); self.bundle['licenses'] = []
        with self.assertRaisesRegex(ValueError, 'notices'): self.plan()

    def test_selection_requires_known_profile_and_generic_context(self):
        for mode in ('custom', 'vanilla'):
            validate_options(self.options(install_mode=mode))
        for mode in ('imported', 'local'):
            with self.assertRaises(ValueError): validate_options(self.options(install_mode=mode))
        with self.assertRaisesRegex(ValueError, 'without a device package'):
            validate_options(self.options(device_package='fixture', device_manifest_sha256='a' * 64, device_trust='developer-session'))
        with self.assertRaisesRegex(ValueError, 'Unknown graphics'):
            validate_options(dict(self.options(), graphics_profile='graphics:amd-unknown'))

    def test_gpu_outcome_is_generic_but_unselected_gpu_outcome_is_rejected(self):
        self.assertEqual(set(), verify_device_components(self.root, self.payload, self.options(), 'A', {PROFILE: {}}))
        with self.assertRaisesRegex(ValueError, 'explicitly trusted package'):
            verify_device_components(self.root, self.payload, self.options(), 'A', {'graphics:amd-z1': {}})

    def test_merged_plan_refuses_duplicate_ownership_before_materialization(self):
        target = self.bundle['files'][0]['target']
        with patch('integration.plan_required', return_value=({target: self.bundle['files'][0]}, {'wifi:intel': {}})):
            with self.assertRaisesRegex(ValueError, 'Conflicting firmware component ownership'):
                merged_firmware_plan(self.root, self.manifest, self.options())

    def test_stale_module_native_and_baseline_state_are_rejected_before_any_write(self):
        self.native('amdgpu/selected.bin')
        options = self.options()
        self.bound_payload(options)
        self.module.write_bytes(b'changed-target-driver')
        with self.assertRaisesRegex(ValueError, 'state changed'):
            stage(self.root, self.payload, options, 'A')
        self.assertFalse((self.root / 'var').exists())
        self.assertFalse((self.root / 'home').exists())

    def test_changed_preserved_file_is_rejected_even_when_candidate_names_match(self):
        native = self.native('amdgpu/selected.bin')
        options = self.options()
        self.bound_payload(options)
        native.write_bytes(b'replaced-native-content')
        with self.assertRaisesRegex(ValueError, 'state changed'):
            stage(self.root, self.payload, options, 'B')
        self.assertFalse((self.root / 'var').exists())

    def test_catalog_baseline_change_invalidates_the_bound_download_plan(self):
        options = self.options()
        self.bound_payload(options)
        catalog_path = self.payload / 'integration/manifest.json'
        catalog = json.loads(catalog_path.read_text())
        catalog['components'][PROFILE]['baseline']['revision'] = 'adjusted-baseline'
        catalog_path.write_text(json.dumps(catalog))
        with self.assertRaisesRegex(ValueError, 'changed after download planning'):
            stage(self.root, self.payload, options, 'A')
        self.assertFalse((self.root / 'var').exists())

    def test_boot_candidates_include_preserved_compression_and_missing_candidates(self):
        import graphics_boot
        self.candidate('amdgpu/native.bin')
        self.native('amdgpu/native.bin.zst', b'preserved-compressed')
        writes, plans = graphics.resolve(self.root, self.payload, self.manifest, PROFILE)
        candidates = graphics.boot_candidates(self.root, plans[PROFILE], writes)
        self.assertEqual({'usr/lib/firmware/amdgpu/selected.bin', 'usr/lib/firmware/amdgpu/native.bin.zst'}, set(candidates))
        boot_writes, proof = graphics_boot.prepare(self.root, PROFILE, candidates)
        updated = boot_writes['boot/initramfs-linux.img']
        self.assertTrue(updated.startswith(self.original_initramfs))
        entries = graphics_boot.inspect(updated)
        self.assertEqual(hashlib.sha256(b'preserved-compressed').hexdigest(),
                         entries['usr/lib/firmware/amdgpu/native.bin.zst'][0]['sha256'])
        self.assertEqual(self.original_initramfs, self.initramfs.read_bytes())
        self.assertEqual('firmware-appended-original-preserved', proof['status'])

    @unittest.skipIf(sys.platform == 'win32', 'Maintenance file transactions require POSIX fcntl')
    def test_fresh_replan_retains_owned_files_and_restores_each_generation(self):
        from maintenance import Engine
        import graphics_boot
        options = self.options()
        self.bound_payload(options)
        first = stage(self.root, self.payload, options, 'A')['components'][PROFILE]
        added = self.root / 'usr/lib/firmware/amdgpu/selected.bin'
        first_firmware = added.read_bytes(); first_boot = self.initramfs.read_bytes()
        self.bound_payload(options)
        records, _ = merged_firmware_plan(self.root, self.manifest, options)
        self.assertEqual({}, records)
        repeated = stage(self.root, self.payload, options, 'A')
        second = repeated['components'][PROFILE]
        self.assertTrue(repeated['install_plan']['components'][0]['retain_owned_paths'])
        self.assertEqual('existing-initramfs-coverage-verified', second['early_boot'])
        self.assertTrue(graphics_boot.verify(self.root, second['early_boot_proof']))
        self.assertTrue(Engine(self.root, read_only=True).verify_readonly(PROFILE)['ok'])
        for target in (added, self.initramfs):
            before = target.read_bytes()
            target.write_bytes(before + b'\0\0\0\0')
            self.bound_payload(options)
            with self.assertRaisesRegex(RuntimeError, 'Retained managed file has changed'):
                stage(self.root, self.payload, options, 'A')
            self.assertEqual(before + b'\0\0\0\0', target.read_bytes())
            target.write_bytes(before)
        engine = Engine(self.root)
        engine.restore(second['operation']['transaction'])
        self.assertEqual(first_firmware, added.read_bytes()); self.assertEqual(first_boot, self.initramfs.read_bytes())
        self.assertTrue(Engine(self.root, read_only=True).verify_readonly(PROFILE)['ok'])
        engine.restore(first['operation']['transaction'])
        self.assertFalse(added.exists()); self.assertEqual(self.original_initramfs, self.initramfs.read_bytes())

    def test_bad_initramfs_is_rejected_before_engine_or_core_component_writes(self):
        options = self.options()
        options['plugins'] = ['core:maintenance']
        self.manifest['core:maintenance'] = dict(available=True, files=[], component=dict(id='core:maintenance', files=[]))
        self.bound_payload(options)
        self.initramfs.write_bytes(b'not-an-initramfs')
        with patch('integration.maintenance_api') as engine_api:
            with self.assertRaises(ValueError): stage(self.root, self.payload, options, 'A')
        engine_api.assert_not_called()
        self.assertFalse((self.root / 'var').exists())
        self.assertFalse((self.root / 'home').exists())

    def test_pending_transaction_and_adoption_block_readonly_planning_and_stage(self):
        options = self.options()
        self.bound_payload(options)
        directory = self.root / 'var/lib/boiler/maintenance/transactions'
        directory.mkdir(parents=True)
        path = directory / ('a' * 32 + '.json')
        pending = json.dumps(dict(status='applying', root='mounted-target')).encode()
        path.write_bytes(pending)
        with self.assertRaisesRegex(RuntimeError, 'explicit recovery and fresh graphics planning'):
            self.plan()
        with patch('integration.maintenance_api') as engine_api:
            with self.assertRaisesRegex(RuntimeError, 'explicit recovery and fresh graphics planning'):
                stage(self.root, self.payload, options, 'A')
        engine_api.assert_not_called()
        self.assertEqual(pending, path.read_bytes())
        self.assertFalse((directory.parent / 'lock').exists())
        path.unlink()
        adoption = directory.parent / 'adoption-pending.json'; adoption.write_bytes(b'{}')
        with self.assertRaisesRegex(RuntimeError, 'explicit recovery and fresh graphics planning'):
            self.plan()
        self.assertEqual(b'{}', adoption.read_bytes())

    @unittest.skipIf(sys.platform == 'win32', 'Maintenance file transactions require POSIX fcntl')
    def test_partial_transaction_requires_explicit_recovery_then_a_fresh_plan(self):
        from maintenance import Engine
        options = self.options()
        self.bound_payload(options)
        installed = stage(self.root, self.payload, options, 'A')['components'][PROFILE]
        added = self.root / 'usr/lib/firmware/amdgpu/selected.bin'
        engine = Engine(self.root)
        journal_path = engine._journal(installed['operation']['transaction'])
        journal = json.loads(journal_path.read_text()); journal['status'] = 'applying'
        journal_path.write_text(json.dumps(journal))
        (engine.state_dir / 'state.json').write_text(json.dumps(journal['before_state']))
        self.initramfs.write_bytes(self.original_initramfs)
        before_journal = journal_path.read_bytes(); before_added = added.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'explicit recovery and fresh graphics planning'):
            self.plan()
        with self.assertRaisesRegex(RuntimeError, 'explicit recovery and fresh graphics planning'):
            stage(self.root, self.payload, options, 'A')
        self.assertEqual(before_journal, journal_path.read_bytes()); self.assertEqual(before_added, added.read_bytes())
        self.assertEqual([journal['id']], engine.recover())
        self.assertFalse(added.exists()); self.assertEqual(self.original_initramfs, self.initramfs.read_bytes())
        self.bound_payload(options)
        records, _ = self.plan()
        self.assertEqual({'usr/lib/firmware/amdgpu/selected.bin'}, set(records))
        recovered = stage(self.root, self.payload, options, 'A')['components'][PROFILE]
        self.assertTrue(Engine(self.root, read_only=True).verify_readonly(PROFILE)['ok'])
        engine.restore(recovered['operation']['transaction'])
        self.assertFalse(added.exists()); self.assertEqual(self.original_initramfs, self.initramfs.read_bytes())

    @unittest.skipIf(sys.platform == 'win32', 'Maintenance file transactions require POSIX fcntl')
    def test_candidate_transaction_restore_preserves_native_and_kernel(self):
        from maintenance import Engine
        self.candidate('amdgpu/native.bin')
        native = self.native('amdgpu/native.bin.zst')
        options = self.options()
        self.bound_payload(options)
        result = stage(self.root, self.payload, options, 'A')
        component = result['components'][PROFILE]
        self.assertEqual('files_installed', component['status'])
        self.assertTrue(graphics.verify_inventory(self.root, component))
        import graphics_boot
        self.assertTrue(graphics_boot.verify(self.root, component['early_boot_proof']))
        self.assertEqual('firmware-appended-original-preserved', component['early_boot'])
        engine = Engine(self.root, self.root / 'var/lib/boiler/maintenance')
        self.assertTrue(Engine(self.root, read_only=True).verify_readonly(PROFILE)['ok'])
        engine.restore(component['operation']['transaction'])
        self.assertFalse((self.root / 'usr/lib/firmware/amdgpu/selected.bin').exists())
        self.assertEqual(b'native', native.read_bytes())
        self.assertEqual(b'target-driver', self.module.read_bytes())
        self.assertEqual(self.original_initramfs, self.initramfs.read_bytes())


if __name__ == '__main__':
    unittest.main()
