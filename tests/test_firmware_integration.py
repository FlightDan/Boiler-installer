"""Firmware selection and maintenance boundaries; synthetic files, no hardware."""
import json
import hashlib
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'components'), str(ROOT / 'guest')]
from integration import stage, validate_options, verify_device_components
from wireless import plan_required
from maintenance import Engine
from payload_lock import record


class FirmwareIntegrationChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'root'
        (self.root / 'etc').mkdir(parents=True)
        (self.root / 'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.10\nBUILD_ID=fixture\n')
        (self.root / 'usr/lib/modules/fixture').mkdir(parents=True)

    def options(self, *ids, **extra):
        return dict(schema=1, repairs=list(ids), plugins=[], install_mode='custom', **extra)

    def prepared(self, vendor, filenames):
        folder = self.base / ('prepared-' + vendor)
        folder.mkdir()
        records = []
        for filename in filenames:
            data = ('locked-' + filename).encode()
            path = folder / 'firmware' / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            records.append(record('firmware/' + filename, data, target='usr/lib/firmware/' + filename))
        notice = b'Synthetic redistribution notice\n'
        (folder / 'LICENSE').write_bytes(notice)
        bundle = dict(schema=1, kind='firmware-only', transport='bluetooth', vendor=vendor,
                      version='test-pinned', provenance=dict(url='https://example.invalid/pinned', revision='test', license='test'),
                      files=records, licenses=[record('LICENSE', notice, target='licenses/LICENSE')])
        (folder / 'manifest.json').write_text(json.dumps(bundle))
        return folder

    def test_hidden_wireless_and_legacy_tokens_are_rejected_with_package_before_writes(self):
        for identity in ('wifi:intel', 'bluetooth:intel', 'bluetooth:realtek', 'bluetooth:mediatek'):
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, 'without a device package'):
                validate_options(self.options(identity, device_package='fixture', device_trust='developer-session', device_manifest_sha256='a'*64))
        for token in ('intel', 'wifi:realtek', 'bluetooth:mediatek'):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, 'without a device package'):
                validate_options(self.options(wireless=[token], device_package='fixture', device_trust='developer-session', device_manifest_sha256='a'*64))
        self.assertFalse((self.root / 'var').exists())

    def test_imported_wireless_cannot_bypass_hidden_selection_policy(self):
        options = self.options('bluetooth:intel')
        options['install_mode'] = 'imported'
        with self.assertRaisesRegex(ValueError, 'generic installation'):
            stage(self.root, '/unused', options, 'A')
        self.assertFalse((self.root / 'var').exists())

    def test_preparer_declares_six_components_and_rejects_duplicate_inputs(self):
        prepare = runpy.run_path(str(ROOT / 'scripts/prepare-integration.py'))['prepare']
        source = self.prepared('intel', ['intel/ibt-test.sfi'])
        prepared = prepare(self.base / 'payload', wireless=[source], development_firmware_cache=True)
        components = prepared['components']
        from graphics_catalog import firmware_metadata
        expected = {'core:decky', 'core:maintenance'} | {
            transport + ':' + vendor for transport in ('wifi', 'bluetooth')
            for vendor in ('intel', 'realtek', 'mediatek')} | set(firmware_metadata(ROOT))
        self.assertEqual(expected, set(components))
        self.assertTrue(components['bluetooth:intel']['available'])
        self.assertFalse(components['wifi:intel']['payload_cached'])
        self.assertFalse(components['bluetooth:realtek']['payload_cached'])
        self.assertEqual('bluetooth', components['bluetooth:intel']['transport'])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            prepare(self.base / 'duplicate', wireless=[source, source], development_firmware_cache=True)
        self.assertFalse((self.base / 'duplicate').exists())

    def test_on_demand_rejects_changed_target_state_before_any_write(self):
        prepare = runpy.run_path(str(ROOT / 'scripts/prepare-integration.py'))['prepare']
        source = self.prepared('intel', ['intel/ibt-test.sfi'])
        payload = self.base / 'payload'
        catalog = prepare(payload / 'integration', wireless=[source], development_firmware_cache=True)
        catalog['components']['bluetooth:intel']['delivery'] = 'on-demand'
        catalog_path = payload / 'integration/manifest.json'
        catalog_path.write_text(json.dumps(catalog))
        options = self.options('bluetooth:intel')
        with patch('wireless.module_requirements', return_value=([], ['kernel/drivers/bluetooth/btintel.ko'])):
            records, plans = plan_required(self.root, catalog['components'], ['bluetooth:intel'])
            expected = dict(schema=1, catalog_sha256=hashlib.sha256(catalog_path.read_bytes()).hexdigest(), selection=options,
                            slots={slot: dict(records=records, plans=plans) for slot in ('A', 'B')})
            (payload / 'firmware-plan.json').write_text(json.dumps(expected))
            native = self.root / 'usr/lib/firmware/intel/ibt-test.sfi'
            native.parent.mkdir(parents=True); native.write_bytes(b'new-native-file-after-planning')
            with self.assertRaisesRegex(ValueError, 'state changed'):
                stage(self.root, payload, options, 'A')
        self.assertEqual(b'new-native-file-after-planning', native.read_bytes())
        self.assertFalse((self.root / 'var').exists())
        self.assertFalse((self.root / 'home').exists())

    @unittest.skipIf(sys.platform == 'win32', 'Image integration uses POSIX fcntl; exercised separately in the Linux guest')
    def test_new_bluetooth_files_can_be_verified_and_restored_without_owning_native_firmware(self):
        prepare = runpy.run_path(str(ROOT / 'scripts/prepare-integration.py'))['prepare']
        source = self.prepared('intel', ['intel/ibt-test.sfi', 'intel/ibt-other.sfi'])
        payload = self.base / 'payload'
        prepare(payload / 'integration', wireless=[source], development_firmware_cache=True)
        native = self.root / 'usr/lib/firmware/intel/ibt-test.sfi.zst'
        native.parent.mkdir(parents=True)
        native.write_bytes(b'native-compressed-firmware')
        options = self.options('bluetooth:intel')
        with patch('wireless.module_requirements', return_value=([], ['usr/lib/modules/fixture/kernel/drivers/bluetooth/btintel.ko'])):
            result = stage(self.root, payload, options, 'A')
        installed = result['components']['bluetooth:intel']
        self.assertEqual('runtime_dependent', installed['coverage'])
        self.assertEqual('not_validated', installed['runtime'])
        self.assertEqual('not_declared', installed['declared_coverage'])
        self.assertEqual([], installed['unavailable_candidates'])
        component = result['install_plan']['components'][0]
        self.assertEqual(installed['runtime_reason'], component['runtime_reason'])
        self.assertEqual(set(), verify_device_components(self.root, payload, options, 'A', result['components']))
        engine = Engine(self.root, self.root / 'var/lib/boiler/maintenance', read_only=True)
        self.assertTrue(engine.verify_readonly('bluetooth:intel')['ok'])
        added = self.root / 'usr/lib/firmware/intel/ibt-other.sfi'
        self.assertTrue(added.is_file())
        Engine(self.root, self.root / 'var/lib/boiler/maintenance').restore(installed['operation']['transaction'])
        self.assertFalse(added.exists())
        self.assertEqual(b'native-compressed-firmware', native.read_bytes())


if __name__ == '__main__':
    unittest.main()
