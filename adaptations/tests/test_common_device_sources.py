import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('common_device_sources', ROOT/'scripts/common-device-sources.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class CommonDeviceSourceTests(unittest.TestCase):
    def test_source_only_catalog_is_current(self):
        result = catalog.build()
        self.assertTrue(result['source_only'])
        self.assertEqual({row['id'] for row in result['resources']}, set(catalog.RESOURCES))
        self.assertEqual(result['boot_policy']['component_id'], 'common:ssd-gm7-boot-repair')
        self.assertTrue(all(row['boot_policy'] == 'common:ssd-gm7-boot-repair' for row in result['models']))

    def test_toon_codec_is_locked(self):
        catalog.validate_codec()

    def test_external_firmware_materialized_bytes_match_the_historical_lock(self):
        be201 = catalog.load(catalog.RESOURCES['resource:intel-be201-firmware'])
        self.assertTrue(all(row['availability'] == 'external-locked' for row in be201['files']))
        for row in be201['files']:
            raw=(ROOT/'common/network/intel/be201'/row['path']).read_bytes()
            self.assertEqual((len(raw),hashlib.sha256(raw).hexdigest()),(row['size'],row['sha256']))

    def test_bmi_source_closure_includes_the_header_and_service(self):
        bmi = catalog.load(catalog.RESOURCES['resource:bmi260-gyro'])
        self.assertIn('sources/bmi270.h', [row['path'] for row in bmi['files']])
        self.assertIn('sources/oxp3-gyro.service', [row['path'] for row in bmi['files']])

    def test_bmi_inputplumber_public_source_and_license(self):
        base = ROOT/'common/sensors/bosch/bmi260/gyro'
        patch = base/'sources/inputplumber-0.78.0-oxp3-gyro.patch'
        license = base/'licenses/InputPlumber-LICENSE'
        self.assertEqual((len(patch.read_bytes()), hashlib.sha256(patch.read_bytes()).hexdigest()),
                         (31057, 'e1fd31a17aaacb4bb35b1158dae4471f0ea74f21861ef581ac9985bba9d1bc2d'))
        self.assertEqual((len(license.read_bytes()), hashlib.sha256(license.read_bytes()).hexdigest()),
                         (35151, '7fe14716ed31cc71fc3bf9af293c368d464a853a70ea86e9995388f6b9e929e5'))
        self.assertIn('Version 3, 29 June 2007', license.read_text(encoding='utf-8'))
        bmi = catalog.load(catalog.RESOURCES['resource:bmi260-gyro'])
        self.assertIn('licenses/InputPlumber-LICENSE', bmi['licenses'])
        self.assertIn('sources/inputplumber-0.78.0-oxp3-gyro.patch', [r['path'] for r in bmi['files']])
        migration = json.loads((ROOT/'records/migrations/common-bmi260-inputplumber-20261103.json').read_text(encoding='utf-8'))
        self.assertEqual(migration['upstream']['sha256'], 'd8bee8bad14f4cd136c814ebcaa3299fa59202b6cdc6d26ec9c52af0e055f4d4')
        self.assertEqual(migration['file_maps'][0]['old_path'], 'cache/integration/sources/gyro/inputplumber-0.78.0-oxp3-gyro.patch')
        self.assertEqual(migration['license_source']['sha256'], hashlib.sha256(license.read_bytes()).hexdigest())
        self.assertFalse(migration['runtime_binaries_included'])

    def test_selected_advisory_transforms_reproduce_locked_final_bytes(self):
        cases = (
            ('common/gpu/intel/xe/xevmafix-724/transforms/xevma_model_advisory.py', 'xevma_model_advisory',
             'common/gpu/intel/xe/xevmafix-724/sources/load.py', 'common/gpu/intel/xe/xevmafix-724/final/load.py'),
            ('common/audio/realtek/alc245/speaker-route/transforms/speaker_model_advisory.py', 'speaker_model_advisory',
             'common/audio/realtek/alc245/speaker-route/sources/speaker_route.py', 'common/audio/realtek/alc245/speaker-route/final/speaker_route.py'),
        )
        for path, function, source, expected in cases:
            spec = importlib.util.spec_from_file_location(function, ROOT/path)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            actual = getattr(module, function)((ROOT/source).read_bytes())
            self.assertEqual(hashlib.sha256(actual).hexdigest(), hashlib.sha256((ROOT/expected).read_bytes()).hexdigest())

    def test_migration_maps_match_every_declared_source_file(self):
        catalog.validate_migration()

    def test_gm7_is_fileless_provider_only(self):
        policy = catalog.gm7()
        self.assertEqual(policy['id'], 'ssd:gm7-boot-repair')
        self.assertEqual(policy['version'], 1)
        definition = catalog.load(catalog.GM7)['definitions']['ssd-gm7-boot-repair']
        self.assertEqual(definition['version'], '0.1.0')
        self.assertEqual(definition['ssd_boot_policy']['version'], 1)


if __name__ == '__main__':
    unittest.main()
