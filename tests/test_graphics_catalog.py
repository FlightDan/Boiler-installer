"""Editable experimental graphics metadata retains explicit runtime limits."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'components')]
from graphics_catalog import load_catalog, metadata, validate_catalog, firmware_metadata


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class GraphicsCatalogueChecks(unittest.TestCase):
    def setUp(self): self.catalogue = load_catalog(ROOT)

    def test_distinct_hardware_families_and_confirmed_gpu_names_are_retained(self):
        profiles = {item['id']: item for item in self.catalogue['profiles']}
        self.assertEqual(11, len(profiles))
        self.assertEqual(['AMD Ryzen 7 7840U', 'AMD Ryzen 7 8840U'], profiles['graphics:amd-780m']['processors'])
        self.assertEqual('RDNA3', profiles['graphics:amd-780m']['architecture'])
        self.assertEqual('RDNA3.5', profiles['graphics:amd-880m']['architecture'])
        self.assertEqual('RDNA2', profiles['graphics:amd-z2-go']['architecture'])
        self.assertEqual(['8086:b080', '8086:b082'], profiles['graphics:intel-g3-extreme']['pci_ids'])
        self.assertEqual('Intel Arc B390', profiles['graphics:intel-g3-extreme']['graphics_name'])
        self.assertFalse({'7800', '8800'} & {item['token'] for item in self.catalogue['requests']})
        for profile in profiles.values():
            self.assertTrue(profile['install_available'])
            self.assertEqual('experimental-baseline', profile['status'])
            self.assertEqual('not_validated', profile['gates']['runtime'])
            self.assertNotIn('profile_file_lock', profile['gates'])

    def test_hardware_evidence_cannot_claim_runtime_coverage_or_inject_records(self):
        mutations = [
            lambda v: v['profiles'][0]['gates'].update(runtime='validated'),
            lambda v: v['profiles'][0].update(files=['amdgpu/*']),
            lambda v: v['profiles'][0].update(download_plan=['i915/*']),
            lambda v: v['profiles'][0]['hardware_evidence'][0].update(scope='firmware-coverage'),
            lambda v: v['profiles'][0]['hardware_evidence'][0].update(url='https://www.amd.com@evil.invalid/spec'),
            lambda v: v['requests'][0].update(token='7800'),
            lambda v: v['firmware_origin'].update(revision='main'),
            lambda v: v['delivery'].update(installer_bundled_binaries=True),
        ]
        for mutate in mutations:
            value = copy.deepcopy(self.catalogue)
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError): validate_catalog(value)

    def test_metadata_preparation_never_fetches_or_reads_cached_firmware(self):
        preparer = module('prepare-graphics-catalog')
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'catalogue'
            with patch('urllib.request.urlopen', side_effect=AssertionError('No network in metadata preparation')):
                result = preparer.prepare(output)
            self.assertEqual(['manifest.json'], [p.name for p in output.rglob('*') if p.is_file()])
            self.assertEqual(0, result['firmware_binary_count'])
            self.assertTrue(result['install_available'])
            self.assertEqual('not_validated', result['runtime'])
            self.assertEqual(metadata(ROOT), json.loads((output / 'manifest.json').read_text(encoding='utf-8')))
            with self.assertRaises(FileExistsError): preparer.prepare(output)

    def test_generated_metadata_has_scoped_real_byte_records(self):
        bundles = firmware_metadata(ROOT)
        self.assertEqual({p['id'] for p in self.catalogue['profiles']}, set(bundles))
        for identity, bundle in bundles.items():
            with self.subTest(identity=identity):
                self.assertEqual('not_validated', bundle['runtime'])
                self.assertTrue(bundle['files'])
                self.assertTrue(bundle['licenses'])
                self.assertTrue(all(len(f['sha256']) == 64 and f['size'] > 0 for f in bundle['files']))
        intel_paths = {f['target'] for f in bundles['graphics:intel-g3']['files']}
        self.assertIn('usr/lib/firmware/xe/ptl_guc_70.bin', intel_paths)
        self.assertIn('usr/lib/firmware/i915/xe3lpd_3002_dmc.bin', intel_paths)
        self.assertNotIn('usr/lib/firmware/i915/xe3p_lpd_dmc.bin', intel_paths)

    def test_changed_spec_rejects_stale_snapshot_and_cross_family_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'components').mkdir()
            for name in ('graphics-baseline-spec.json', 'graphics-support.json', 'graphics-firmware.json'):
                shutil.copyfile(ROOT / 'components' / name, root / 'components' / name)
            spec_path = root / 'components/graphics-baseline-spec.json'
            spec = json.loads(spec_path.read_text(encoding='utf-8'))
            spec['revision'] = '1' * 40
            spec['upstream_url'] = spec['upstream_url'].replace(self.catalogue['firmware_origin']['revision'], spec['revision'])
            spec_path.write_text(json.dumps(spec), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'stale'): firmware_metadata(root)
            shutil.copyfile(ROOT / 'components/graphics-baseline-spec.json', spec_path)
            snapshot_path = root / 'components/graphics-firmware.json'
            snapshot = json.loads(snapshot_path.read_text(encoding='utf-8'))
            record = snapshot['components']['graphics:amd-780m']['files'][0]
            record['target'] = 'usr/lib/firmware/amdgpu/aldebaran_mec.bin'
            record['source'] = 'graphics/amd-780m/firmware/amdgpu/aldebaran_mec.bin'
            record['url'] = self.catalogue['firmware_origin']['url'] + 'amdgpu/aldebaran_mec.bin'
            snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'selected candidate family'): firmware_metadata(root)


if __name__ == '__main__': unittest.main()
