import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'components'))
from payload_lock import record
from wireless import resolve, selected, existing_firmware


class WirelessChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'root'; self.root.mkdir()
        self.payload = Path(self.tmp.name) / 'payload'; self.payload.mkdir()
        for name in ('iwlwifi', 'rtw89_8852a', 'mt7921e'):
            path = self.root / 'usr/lib/modules/stock/kernel/drivers/net/wireless' / (name + '.ko.zst')
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'target-module')
        self.manifest = {}
        self.requirements = {'iwlwifi': ['intel.bin'], 'rtw89_8852a': ['rtw89/realtek.bin'], 'mt7921e': ['mediatek/mediatek.bin']}

    def tearDown(self): self.tmp.cleanup()

    def probe(self, path): return self.requirements[path.name.split('.ko')[0]]

    def bundle(self, vendor, filename, data=b'firmware'):
        source = vendor + '/' + filename
        path = self.payload / source; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        item = record(source, data, target='usr/lib/firmware/' + filename)
        self.manifest['wifi:' + vendor] = dict(schema=1, kind='firmware-only', vendor=vendor, version='pinned', files=[item])

    def test_multi_vendor_preserves_kernel_and_resolves_actual_modules(self):
        for vendor, filename in [('intel', 'intel.bin'), ('realtek', 'rtw89/realtek.bin'), ('mediatek', 'mediatek/mediatek.bin')]:
            self.bundle(vendor, filename)
        before = {p.as_posix(): p.read_bytes() for p in self.root.rglob('*.zst')}
        writes, plans = resolve(self.root, self.payload, self.manifest, ['intel', 'realtek', 'mediatek'], self.probe)
        self.assertEqual(3, len(writes)); self.assertEqual(3, len(plans))
        self.assertTrue(all(path.startswith('usr/lib/firmware/') for path in writes))
        self.assertEqual(before, {p.as_posix(): p.read_bytes() for p in self.root.rglob('*.zst')})

    def test_missing_target_module_is_refused(self):
        self.bundle('intel', 'intel.bin')
        next(self.root.rglob('iwlwifi.ko.zst')).unlink()
        with self.assertRaisesRegex(ValueError, 'no existing'): resolve(self.root, self.payload, self.manifest, ['intel'], self.probe)

    def test_missing_requirement_fails_closed(self):
        self.bundle('intel', 'other.bin')
        with self.assertRaisesRegex(ValueError, 'cannot satisfy'): resolve(self.root, self.payload, self.manifest, ['intel'], self.probe)

    def test_partial_declared_coverage_is_explicit_and_never_fabricates_blob(self):
        self.bundle('intel', 'intel.bin')
        self.requirements['iwlwifi'] = ['intel.bin', 'unreleased-chip.ucode']
        writes, plans = resolve(self.root, self.payload, self.manifest, ['intel'], self.probe)
        self.assertEqual({'usr/lib/firmware/intel.bin'}, set(writes))
        self.assertEqual('partial', plans['wifi:intel']['coverage'])
        self.assertEqual(['unreleased-chip.ucode'], plans['wifi:intel']['unavailable_requirements'])
        self.assertEqual('not_validated', plans['wifi:intel']['runtime'])

    def test_only_documented_iwl6000_api_fallback_is_accepted(self):
        self.bundle('intel', 'iwlwifi-6000-4.ucode')
        self.requirements['iwlwifi'] = ['iwlwifi-6000-6.ucode']
        writes, plans = resolve(self.root, self.payload, self.manifest, ['intel'], self.probe)
        self.assertIn('usr/lib/firmware/iwlwifi-6000-4.ucode', writes)
        self.assertEqual({'iwlwifi-6000-6.ucode':'iwlwifi-6000-4.ucode'}, plans['wifi:intel']['alternatives'])
        self.requirements['iwlwifi'] = ['iwlwifi-unknown-6.ucode']
        with self.assertRaisesRegex(ValueError, 'cannot satisfy'): resolve(self.root, self.payload, self.manifest, ['intel'], self.probe)

    def test_missing_payload_never_succeeds(self):
        with self.assertRaises(FileNotFoundError): resolve(self.root, self.payload, {}, ['intel'], self.probe)

    def test_tampered_payload_is_refused(self):
        self.bundle('intel', 'intel.bin')
        (self.payload / 'intel/intel.bin').write_bytes(b'modified')
        with self.assertRaises(ValueError): resolve(self.root, self.payload, self.manifest, ['intel'], self.probe)

    def test_actual_content_collision_is_refused(self):
        self.bundle('intel', 'intel.bin')
        self.bundle('realtek', 'intel.bin', b'different')
        self.requirements['rtw89_8852a'] = ['intel.bin']
        with self.assertRaisesRegex(ValueError, 'Conflicting'): resolve(self.root, self.payload, self.manifest, ['intel', 'realtek'], self.probe)

    def test_identical_overlap_has_one_owner_and_explicit_dependency(self):
        self.bundle('intel', 'intel.bin')
        self.bundle('realtek', 'intel.bin')
        self.requirements['rtw89_8852a'] = ['intel.bin']
        writes, plans = resolve(self.root, self.payload, self.manifest, ['intel', 'realtek'], self.probe)
        self.assertEqual(1, len(writes)); self.assertEqual([], plans['wifi:realtek']['files'])
        self.assertEqual(['wifi:intel'], plans['wifi:realtek']['requires'])

    def test_existing_compressed_firmware_is_preserved(self):
        self.bundle('intel', 'intel.bin', b'new')
        old = self.root / 'usr/lib/firmware/intel.bin.zst'; old.parent.mkdir(parents=True); old.write_bytes(b'existing')
        writes, plans = resolve(self.root, self.payload, self.manifest, ['intel'], self.probe)
        self.assertEqual({}, writes); self.assertEqual(b'existing', old.read_bytes())
        self.assertEqual(['usr/lib/firmware/intel.bin.zst'], plans['wifi:intel']['preserved'])

    def test_native_absolute_firmware_link_resolves_inside_image_only(self):
        real = self.root / 'usr/lib/firmware/vendor/real.bin'; real.parent.mkdir(parents=True); real.write_bytes(b'native')
        alias = self.root / 'usr/lib/firmware/intel.bin'
        try: alias.symlink_to('/usr/lib/firmware/vendor/real.bin')
        except OSError: self.skipTest('Native symlink creation is unavailable')
        self.assertTrue(existing_firmware(self.root, 'usr/lib/firmware/intel.bin'))
        self.bundle('intel', 'intel.bin', b'new')
        writes, plans = resolve(self.root, self.payload, self.manifest, ['intel'], self.probe)
        self.assertEqual({}, writes); self.assertEqual(b'native', real.read_bytes()); self.assertTrue(alias.is_symlink())

    def test_native_escaping_or_cyclic_link_fails_closed(self):
        directory = self.root / 'usr/lib/firmware'; directory.mkdir(parents=True)
        alias = directory / 'intel.bin'
        try: alias.symlink_to('../../../../outside')
        except OSError: self.skipTest('Native symlink creation is unavailable')
        with self.assertRaisesRegex(ValueError, 'escapes'): existing_firmware(self.root, 'usr/lib/firmware/intel.bin')
        alias.unlink(); alias.symlink_to('intel.bin')
        with self.assertRaisesRegex(ValueError, 'cycle'): existing_firmware(self.root, 'usr/lib/firmware/intel.bin')

    def test_native_directory_alias_preserves_exact_and_wildcard_firmware(self):
        real = self.root / 'usr/lib/firmware/real/native.bin'; real.parent.mkdir(parents=True); real.write_bytes(b'native')
        alias = self.root / 'usr/lib/firmware/vendor'
        try: alias.symlink_to('/usr/lib/firmware/real', target_is_directory=True)
        except OSError: self.skipTest('Native symlink creation is unavailable')
        self.bundle('intel', 'vendor/native.bin', b'new')
        for requirement in ('vendor/native.bin', 'vendor/nat*.bin'):
            self.requirements['iwlwifi'] = [requirement]
            writes, plans = resolve(self.root, self.payload, self.manifest, ['intel'], self.probe)
            self.assertEqual({}, writes)
            self.assertEqual(['usr/lib/firmware/vendor/native.bin'], plans['wifi:intel']['preserved'])
            self.assertEqual(b'native', real.read_bytes())

    def test_kernel_transplant_cannot_be_packaged_as_firmware(self):
        self.bundle('intel', 'driver.ko')
        with self.assertRaisesRegex(ValueError, 'firmware only'): resolve(self.root, self.payload, self.manifest, ['intel'], self.probe)

    def test_selection_is_multi_vendor_and_normalizes_component_ids(self):
        self.assertEqual(['intel', 'mediatek', 'realtek'], selected({'wireless': ['intel', 'wifi:realtek'], 'repairs': ['wifi:mediatek']}))
        with self.assertRaises(ValueError): selected({'wireless': ['unknown']})

    def test_intel_pnvm_companion_is_resolved_without_kernel_changes(self):
        self.bundle('intel', 'iwlwifi-test-c106.ucode')
        data = b'pnvm-calibration'
        path = self.payload / 'intel/iwlwifi-test.pnvm'; path.write_bytes(data)
        self.manifest['wifi:intel']['files'].append(record('intel/iwlwifi-test.pnvm', data, target='usr/lib/firmware/iwlwifi-test.pnvm'))
        self.requirements['iwlwifi'] = ['iwlwifi-test-c106.ucode']
        writes, _ = resolve(self.root, self.payload, self.manifest, ['intel'], self.probe)
        self.assertEqual(data, writes['usr/lib/firmware/iwlwifi-test.pnvm'])

    def test_locked_prepare_is_reproducible_and_keeps_license_files(self):
        import runpy
        prepare = runpy.run_path(str(ROOT / 'scripts/prepare-wireless.py'))['prepare']
        self.bundle('intel', 'intel.bin')
        license_data = b'redistribution-notice'
        (self.payload / 'license.txt').write_bytes(license_data)
        bundle = self.manifest['wifi:intel']
        bundle.update(provenance=dict(url='https://example.invalid/immutable', revision='a'*40, license='test-license'),
                      licenses=[record('license.txt', license_data, target='licenses/NOTICE')])
        lock = self.payload / 'lock.json'; lock.write_text(json.dumps(bundle))
        first, second = self.payload / 'prepared-a', self.payload / 'prepared-b'
        self.assertEqual(prepare(lock, self.payload, first), prepare(lock, self.payload, second))
        self.assertEqual(license_data, (first / 'licenses/NOTICE').read_bytes())
        with self.assertRaises(FileExistsError): prepare(lock, self.payload, first)


if __name__ == '__main__': unittest.main()
