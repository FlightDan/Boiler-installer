"""Offline Bluetooth candidates stay separate from Wi-Fi and unrelated firmware."""
import hashlib
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'components'))
from payload_lock import record
from wireless import resolve, selected, validate_bundle


class BluetoothFirmwareChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'root'; self.root.mkdir()
        self.payload = Path(self.tmp.name) / 'payload'; self.payload.mkdir()
        self.manifest = {}

    def tearDown(self): self.tmp.cleanup()

    def module(self, name, location='drivers/bluetooth'):
        path = self.root / 'usr/lib/modules/stock/kernel' / location / (name + '.ko.zst')
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'original-kernel')
        return path

    def bundle(self, vendor, names):
        records = []
        for name in names:
            path = self.payload / vendor / name; path.parent.mkdir(parents=True, exist_ok=True)
            data = name.encode(); path.write_bytes(data)
            records.append(record(vendor + '/' + name, data, target='usr/lib/firmware/' + name))
        bundle = dict(schema=1, vendor=vendor, transport='bluetooth', kind='firmware-only', version='pinned', files=records)
        self.manifest['bluetooth:' + vendor] = bundle
        return bundle

    def test_selection_retains_separate_wifi_and_bluetooth_components(self):
        self.assertEqual(['bluetooth:intel', 'intel', 'realtek'], selected(dict(
            wireless=['wifi:intel', 'bluetooth:intel'], repairs=['wifi:realtek', 'bluetooth:intel'])))
        with self.assertRaises(ValueError): selected({'wireless': ['bluetooth:unknown']})

    def test_matching_bluetooth_helper_allows_undeclared_runtime_candidates(self):
        for vendor, module, name in [('intel', 'btintel', 'intel/ibt-19-0-4.sfi'),
                                      ('realtek', 'btrtl', 'rtl_bt/rtl8852cu_fw_v2.bin'),
                                      ('mediatek', 'btmtk', 'mediatek/mt7925/BT_RAM_CODE_MT7925_1_1_hdr.bin')]:
            self.module(module); self.bundle(vendor, [name])
        before = {path: path.read_bytes() for path in self.root.rglob('*.ko.zst')}
        writes, plans = resolve(self.root, self.payload, self.manifest, list(self.manifest), lambda _: [])
        self.assertEqual(3, len(writes))
        for plan in plans.values():
            self.assertEqual('runtime_dependent', plan['coverage'])
            self.assertEqual('not_declared', plan['declared_coverage'])
            self.assertEqual('not_validated', plan['runtime'])
            self.assertEqual(1, len(plan['runtime_candidates']))
            self.assertIn('/drivers/bluetooth/', plan['modules'][0])
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob('*.ko.zst')})

    def test_wifi_or_generic_btusb_does_not_establish_vendor_support(self):
        self.bundle('intel', ['intel/ibt-19-0-4.sfi'])
        self.module('iwlwifi', 'drivers/net/wireless'); self.module('btusb')
        with self.assertRaisesRegex(ValueError, 'no existing'):
            resolve(self.root, self.payload, self.manifest, ['bluetooth:intel'], lambda _: [])
        self.module('btintel', 'drivers/net/wireless')
        with self.assertRaisesRegex(ValueError, 'no existing'):
            resolve(self.root, self.payload, self.manifest, ['bluetooth:intel'], lambda _: [])

    def test_intel_pcie_helper_is_supported(self):
        self.module('btintel_pcie'); self.bundle('intel', ['intel/ibt-0190-0291-pci.sfi'])
        writes, _ = resolve(self.root, self.payload, self.manifest, ['bluetooth:intel'], lambda _: [])
        self.assertIn('usr/lib/firmware/intel/ibt-0190-0291-pci.sfi', writes)

    def test_existing_compressed_candidates_and_configs_are_preserved(self):
        self.module('btrtl')
        self.bundle('realtek', ['rtl_bt/rtl8922au_fw.bin', 'rtl_bt/rtl8922au_config.bin'])
        existing = self.root / 'usr/lib/firmware/rtl_bt/rtl8922au_config.bin.zst'
        existing.parent.mkdir(parents=True); existing.write_bytes(b'board-specific-config')
        writes, plans = resolve(self.root, self.payload, self.manifest, ['bluetooth:realtek'], lambda _: [])
        self.assertEqual({'usr/lib/firmware/rtl_bt/rtl8922au_fw.bin'}, set(writes))
        self.assertEqual(b'board-specific-config', existing.read_bytes())
        self.assertEqual(['usr/lib/firmware/rtl_bt/rtl8922au_config.bin.zst'], plans['bluetooth:realtek']['preserved'])

    def test_missing_declared_names_remain_visible_with_runtime_candidates(self):
        self.module('btmtk'); self.bundle('mediatek', ['mediatek/BT_RAM_CODE_MT7961_1_2_hdr.bin'])
        requirements = ['mediatek/BT_RAM_CODE_MTFFFF_1_1_hdr.bin']
        writes, plans = resolve(self.root, self.payload, self.manifest, ['bluetooth:mediatek'], lambda _: requirements)
        plan = plans['bluetooth:mediatek']
        self.assertTrue(writes); self.assertEqual('partial', plan['declared_coverage'])
        self.assertEqual(requirements, plan['unavailable_requirements'])
        self.assertEqual('runtime_dependent', plan['coverage'])

    def test_bluetooth_bundle_requires_explicit_transport(self):
        bundle = self.bundle('intel', ['intel/ibt-19-0-4.sfi']); del bundle['transport']
        with self.assertRaisesRegex(ValueError, 'invalid pinned'):
            validate_bundle(bundle, self.payload, 'intel', 'bluetooth')

    def test_vendor_allowlist_rejects_gpu_sof_wifi_modules_and_foreign_firmware(self):
        for vendor, name in [('intel', 'i915/test.bin'), ('intel', 'intel/sof/test.ri'),
                             ('intel', 'intel/dsp_fw.bin'), ('intel', 'intel/iwlwifi/test.ucode'),
                             ('intel', 'intel/ibt-test.sfi/subfile'), ('realtek', 'rtw89/rtl8922au_fw.bin'),
                             ('mediatek', 'mediatek/mt7925/WIFI_RAM_CODE_MT7925_1_1.bin'),
                             ('mediatek', 'mediatek/mt7662u_rom_patch.bin'), ('intel', 'intel/driver.ko.gz'),
                             ('realtek', 'intel/ibt-19-0-4.sfi')]:
            with self.subTest(vendor=vendor, name=name):
                bundle = self.bundle(vendor, [name])
                with self.assertRaises(ValueError): validate_bundle(bundle, self.payload, vendor, 'bluetooth')

    def test_tampered_bluetooth_firmware_is_refused_before_any_plan(self):
        self.module('btintel'); self.bundle('intel', ['intel/ibt-19-0-4.sfi'])
        (self.payload / 'intel/intel/ibt-19-0-4.sfi').write_bytes(b'tampered')
        with self.assertRaises(ValueError): resolve(self.root, self.payload, self.manifest, ['bluetooth:intel'], lambda _: [])

    def test_locked_bluetooth_prepare_keeps_identity_and_scoped_notice(self):
        prepare = runpy.run_path(str(ROOT / 'scripts/prepare-wireless.py'))['prepare']
        bundle = self.bundle('intel', ['intel/ibt-19-0-4.sfi'])
        data = b'intel-bluetooth-redistribution'; (self.payload / 'notice').write_bytes(data)
        bundle.update(provenance=dict(url='https://example.invalid/immutable', revision='a' * 40, license='LICENCE.ibt_firmware'),
                      licenses=[record('notice', data, target='licenses/LICENCE.ibt_firmware')])
        lock = self.payload / 'lock.json'; lock.write_text(json.dumps(bundle))
        output = self.payload / 'prepared'
        prepared = prepare(lock, self.payload, output)
        self.assertEqual('bluetooth', prepared['transport'])
        self.assertEqual(data, (output / 'licenses/LICENCE.ibt_firmware').read_bytes())
        self.assertEqual({'usr/lib/firmware/intel/ibt-19-0-4.sfi'}, set(validate_bundle(prepared, output, 'intel', 'bluetooth')))

    def test_pinned_fetch_filters_unrelated_tree_files_and_requires_bt_license(self):
        namespace = runpy.run_path(str(ROOT / 'scripts/prepare-wireless.py'))
        blobs = {'intel/ibt-19-0-4.sfi': b'bluetooth', 'intel/dsp_fw.bin': b'not-bluetooth',
                 'intel/ibt-19-0-2.sfi': b'ibt-19-0-1.sfi',
                 'LICENSES/LICENCE.ibt_firmware': b'bt-license',
                 'WHENCE': b'Link: intel/ibt-19-0-0.sfi -> ibt-19-0-4.sfi\nLink: intel/ibt-19-0-1.sfi -> ibt-19-0-0.sfi\n'}
        def object_id(data): return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        def entry(name, path): return dict(name=name, mode=0o100644, id=object_id(blobs[path]))
        trees = {'intel': [entry('ibt-19-0-4.sfi', 'intel/ibt-19-0-4.sfi'), entry('dsp_fw.bin', 'intel/dsp_fw.bin')],
                 'LICENSES': [entry('LICENCE.ibt_firmware', 'LICENSES/LICENCE.ibt_firmware')], '': [entry('WHENCE', 'WHENCE')]}
        trees['intel'].append(dict(entry('ibt-19-0-2.sfi', 'intel/ibt-19-0-2.sfi'), mode=0o120000))
        def download(url):
            if '?format=JSON' in url:
                tree = url.split(namespace['BASE'], 1)[1].split('?')[0].rstrip('/')
                return b")]}'" + json.dumps(dict(entries=trees[tree])).encode()
            return blobs[url.split('/raw/' + namespace['REVISION'] + '/', 1)[1]]
        fetch = namespace['fetch_pinned']
        with patch.dict(fetch.__globals__, download=download):
            lock = json.loads(fetch('intel', self.payload / 'fetched', 'bluetooth').read_text())
            self.assertEqual({'usr/lib/firmware/intel/ibt-19-0-' + str(number) + '.sfi' for number in (0, 1, 2, 4)},
                             {item['target'] for item in lock['files']})
            for item in lock['files']:
                self.assertEqual('intel/ibt-19-0-4.sfi', item['source'])
                self.assertEqual(object_id(blobs[item['source']]), item['git_blob'])
                if 'alias_of' in item:
                    self.assertEqual(object_id(blobs['WHENCE']), item['whence']['git_blob'])
                    self.assertTrue(item['alias_evidence'])
            prepared_root = self.payload / 'prepared-aliases'
            prepared = namespace['prepare'](self.payload / 'fetched/lock.json', self.payload / 'fetched', prepared_root)
            for item in prepared['files']:
                self.assertEqual(b'bluetooth', (prepared_root / item['source']).read_bytes())
                self.assertFalse((prepared_root / item['source']).is_symlink())
                if item['target'].endswith('19-0-2.sfi'):
                    self.assertEqual('git-symlink', item['alias_evidence'][0]['kind'])
            self.assertEqual('LICENCE.ibt_firmware', lock['provenance']['license'])
            trees['LICENSES'] = []
            with self.assertRaisesRegex(ValueError, 'notice is missing'):
                fetch('intel', self.payload / 'missing-notice', 'bluetooth')

    def test_bluetooth_alias_resolution_rejects_cycles_escapes_and_conflicts(self):
        aliases = runpy.run_path(str(ROOT / 'scripts/prepare-wireless.py'))['bluetooth_aliases']
        regular = {'intel/ibt-19-0-4.sfi': 'a' * 40}
        bad = ['Link: intel/ibt-19-0-0.sfi -> ibt-19-0-0.sfi',
               'Link: intel/ibt-19-0-0.sfi -> ibt-19-0-1.sfi\nLink: intel/ibt-19-0-1.sfi -> ibt-19-0-0.sfi',
               'Link: intel/ibt-19-0-0.sfi -> ../../outside.sfi',
               'Link: intel/ibt-19-0-0.sfi -> /intel/ibt-19-0-4.sfi',
               'Link: intel/ibt-19-0-0.sfi -> ../i915/test.bin',
               'Link: intel/ibt-19-0-0.sfi -> ../intel/sof/test.sfi',
               'Link: intel/ibt-19-0-0.sfi -> ibt-missing.sfi',
               'Link: intel/ibt-19-0-0.sfi -> ibt-19-0-4.sfi\nLink: intel/ibt-19-0-0.sfi -> ibt-other.sfi']
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError): aliases('intel', text, regular)
        text = 'Link: intel/ibt-19-0-0.sfi -> ../intel/ibt-19-0-4.sfi'
        self.assertEqual('intel/ibt-19-0-4.sfi', aliases('intel', text, regular)['intel/ibt-19-0-0.sfi']['source'])
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            aliases('intel', text, regular, {'intel/ibt-19-0-0.sfi': ('ibt-other.sfi', 'b' * 40)})
        self.assertEqual({}, aliases('intel', 'Link: i915/irrelevant.bin -> ../../outside', regular))


if __name__ == '__main__': unittest.main()
