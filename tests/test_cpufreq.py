import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'components')]
import cpufreq
import cpufreq_runtime as runtime
from payload_lock import checked
from protocol import validate_manifest


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class RuntimeChecks(unittest.TestCase):
    def run_case(self, vendor, initial, *, identity=None, fail=False, readback=None, missing=False):
        expected, path, _ = runtime.VENDORS[vendor]
        values = {'/proc/cpuinfo': 'vendor_id : ' + (identity or expected), path: initial}
        writes = []
        def read(name):
            if name == path and missing: raise FileNotFoundError(name)
            return values[name]
        def write(name, value):
            writes.append((name, value))
            if readback is not None: values[name] = readback
            elif not fail: values[name] = value
            if fail: raise OSError('fixture failure')
        result = runtime.activate(vendor, read, write)
        return result, writes, path

    def test_supported_modes_and_fixed_writes(self):
        for vendor, modes in [('intel', ('off', 'passive')), ('amd', ('disable', 'passive', 'guided'))]:
            for mode in modes:
                with self.subTest(vendor=vendor, mode=mode):
                    result, writes, path = self.run_case(vendor, mode)
                    self.assertEqual({'status': 'active', 'actual': 'active'}, result)
                    self.assertEqual([(path, 'active')], writes)
            result, writes, _ = self.run_case(vendor, 'active')
            self.assertEqual('active', result['status']); self.assertEqual([], writes)

    def test_unavailable_and_mismatched_do_not_write(self):
        for kw, status, reason in [({'identity':'Other'}, 'skipped', 'vendor_mismatch'),
                                   ({'missing':True}, 'unavailable', 'interface_unavailable'),
                                   ({}, 'unavailable', 'unknown_status')]:
            result, writes, _ = self.run_case('intel', 'unknown', **kw)
            self.assertEqual((status, reason), (result['status'], result['reason']))
            self.assertEqual([], writes)

    def test_failures_report_actual_readback(self):
        for kw, actual in [({'fail': True}, 'off'), ({'readback': 'passive'}, 'passive'),
                           ({'fail': True, 'readback': 'active'}, 'active')]:
            result, writes, path = self.run_case('intel', 'off', **kw)
            self.assertEqual({'status': 'activation_failed', 'actual': actual}, result)
            self.assertEqual([(path, 'active')], writes)

    def test_cpuinfo_unavailable(self):
        def read(_): raise OSError('missing')
        self.assertEqual('cpuinfo_unavailable', runtime.activate('amd', read, lambda *_: self.fail())['reason'])


class MetadataChecks(unittest.TestCase):
    def test_selection_and_static_assets(self):
        self.assertEqual('cpufreq:intel', cpufreq.selected({'repairs':['microcode:amd','cpufreq:intel']}))
        for repairs in [['cpufreq:intel','cpufreq:amd'], ['cpufreq:other'], 'invalid']:
            with self.assertRaises(ValueError): cpufreq.selected({'repairs': repairs})
        for identity, bundle in cpufreq.metadata(ROOT).items():
            component, data = cpufreq.prepare(ROOT, identity)
            self.assertEqual(bundle['component'], component)
            validate_manifest(component, 'ComponentManifest')
            self.assertEqual(3, len(data))
            self.assertNotIn('hooks', component)
            with tempfile.TemporaryDirectory() as temp:
                for name, content in data.items():
                    path = Path(temp) / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
                for entry in bundle['files']:
                    self.assertEqual(data[entry['source']], checked(temp, entry))
                entry = bundle['files'][0]; (Path(temp) / entry['source']).write_bytes(b'tampered')
                with self.assertRaises(ValueError): checked(temp, entry)

    def test_package_inventory_and_changed_metadata(self):
        preparer = module('prepare-integration'); packager = module('package-installer')
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); manifest = preparer.prepare(base / 'cache/integration')
            changed = copy.deepcopy(manifest)
            changed['components']['cpufreq:intel']['files'][0]['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'CPU frequency inventory differs'):
                packager.validate_integration(ROOT, changed)
            from unittest.mock import patch
            with patch.object(packager, 'validate_integration'):
                inventory = packager.integration_package_files(base, manifest)
            self.assertEqual(6, len([name for name in inventory if name.startswith('cpufreq/')]))


if __name__ == '__main__': unittest.main()
