"""Production firmware stays pinned metadata until an installation needs bytes."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'components')]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


preparer = module('prepare-integration')
packager = module('package-installer')


class FirmwareDeliveryMetadataChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def test_default_never_reads_cached_bytes_and_preserves_full_lock_evidence(self):
        original = Path.read_bytes
        def no_cached_bytes(path):
            self.assertNotIn('cache', path.parts)
            self.assertNotIn('wireless', path.parts)
            return original(path)
        with patch.object(Path, 'read_bytes', no_cached_bytes):
            output = self.base / 'integration'
            manifest = preparer.prepare(output)
        self.assertFalse((output / 'wireless').exists())
        for transport in ('wifi', 'bluetooth'):
            for vendor in ('intel', 'realtek', 'mediatek'):
                item = manifest['components'][transport + ':' + vendor]
                self.assertTrue(item['available'])
                self.assertTrue(item['metadata_available'])
                self.assertFalse(item['payload_cached'])
                self.assertEqual('on-demand', item['delivery'])
                prefix = 'bluetooth/' if transport == 'bluetooth' else ''
                lock = json.loads((ROOT / ('components/locks/' + prefix + vendor + '.json')).read_text())
                base = 'wireless/' + prefix + vendor + '/'
                self.assertEqual([dict(e, source=base + 'firmware/' + e['target'].removeprefix('usr/lib/firmware/'))
                                  for e in lock['files']], item['files'])
                self.assertEqual([dict(e, source=base + e['target']) for e in lock['licenses']], item['licenses'])
        packager.validate_integration(ROOT, manifest)

    def test_explicit_full_input_requires_development_opt_in_before_reading(self):
        with self.assertRaisesRegex(ValueError, 'development-firmware-cache'):
            preparer.prepare(self.base / 'output', wireless=[self.base / 'missing-input'])
        self.assertFalse((self.base / 'output').exists())

    def test_development_bundle_is_marked_and_production_rejects_it(self):
        from payload_lock import record
        source = self.base / 'prepared'
        source.mkdir()
        data = b'fixture firmware'
        (source / 'firmware').mkdir()
        (source / 'firmware/test.ucode').write_bytes(data)
        notice = b'fixture notice'
        (source / 'LICENSE').write_bytes(notice)
        bundle = dict(schema=1, kind='firmware-only', vendor='intel', version='fixture',
                      files=[record('firmware/test.ucode', data, target='usr/lib/firmware/test.ucode')],
                      licenses=[record('LICENSE', notice, target='licenses/LICENSE')])
        (source / 'manifest.json').write_text(json.dumps(bundle))
        manifest = preparer.prepare(self.base / 'development', wireless=[source], development_firmware_cache=True)
        self.assertTrue(manifest['components']['wifi:intel']['payload_cached'])
        with self.assertRaisesRegex(ValueError, 'metadata-only on-demand'):
            packager.validate_integration(ROOT, manifest)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            preparer.prepare(self.base / 'duplicate', wireless=[source, source], development_firmware_cache=True)

    def test_production_rejects_each_changed_lock_record_and_delivery_mode(self):
        manifest = preparer.prepare(self.base / 'integration')
        for field, value in (('sha256', '0' * 64), ('size', 0), ('source', 'other'),
                             ('url', 'https://example.invalid/other'), ('git_blob', '0' * 40),
                             ('target', 'usr/lib/firmware/unreviewed')):
            with self.subTest(field=field):
                changed = copy.deepcopy(manifest)
                changed['components']['wifi:intel']['files'][0][field] = value
                with self.assertRaisesRegex(ValueError, 'differs from reviewed lock'):
                    packager.validate_integration(ROOT, changed)
        for field, value in (('payload_cached', True), ('delivery', 'offline'), ('metadata_available', False)):
            changed = copy.deepcopy(manifest)
            changed['components']['bluetooth:realtek'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'metadata-only on-demand'):
                packager.validate_integration(ROOT, changed)
        changed = copy.deepcopy(manifest)
        changed['components']['bluetooth:intel']['licenses'][0]['url'] = 'https://example.invalid/license'
        with self.assertRaisesRegex(ValueError, 'differs from reviewed lock'):
            packager.validate_integration(ROOT, changed)

    def test_package_inventory_rejects_any_extra_firmware_even_with_metadata_only_manifest(self):
        manifest = preparer.prepare(self.base / 'cache/integration')
        # Validate the full catalogue against the actual reviewed project. The
        # isolated package inventory then exercises the staging directory only.
        packager.validate_integration(ROOT, manifest)
        with patch.object(packager, 'validate_integration'):
            inventory = packager.integration_package_files(self.base, manifest)
            self.assertTrue(inventory)
            self.assertFalse(any(name.startswith('wireless/') for name in inventory))
            extra = self.base / 'cache/integration/wireless/intel/firmware/extra.ucode'
            extra.parent.mkdir(parents=True)
            extra.write_bytes(b'cached data must never become package input')
            with self.assertRaisesRegex(ValueError, 'unlisted files'):
                packager.integration_package_files(self.base, manifest)


if __name__ == '__main__':
    unittest.main()
