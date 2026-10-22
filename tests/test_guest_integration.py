import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'components'))
sys.path.insert(0, str(ROOT / 'guest'))
from integration import stage, validate_options, check_conflicts
from source_profile import inspect_source


class GenericIntegrationChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name) / 'root'; self.root.mkdir()
        (self.root / 'etc').mkdir(); (self.root / 'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.8.14\nBUILD_ID=20260707.10\n')
        (self.root / 'usr/lib/modules/existing-kernel').mkdir(parents=True)

    def tearDown(self): self.tmp.cleanup()

    def inventory(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_no_upgrade_accepts_existing_actual_source(self):
        profile = inspect_source(self.root)
        self.assertEqual('3.8.14', profile['version']); self.assertEqual(['existing-kernel'], profile['kernels'])

    def test_source_metadata_has_bounded_unique_fields(self):
        metadata = self.root / 'etc/os-release'
        metadata.write_text('X=' + 'x'*65536)
        with self.assertRaisesRegex(ValueError, 'limit'): inspect_source(self.root)
        metadata.write_text('ID=steamos\nID=holo\nVERSION_ID=3.8.14\nBUILD_ID=test\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate'): inspect_source(self.root)

    def test_absolute_source_metadata_link_stays_inside_image(self):
        metadata = self.root / 'etc/os-release'
        target = self.root / 'usr/lib/os-release'; target.write_bytes(metadata.read_bytes()); metadata.unlink()
        try: metadata.symlink_to('/usr/lib/os-release')
        except OSError: self.skipTest('Native symlink creation is unavailable')
        self.assertEqual('3.8.14', inspect_source(self.root)['version'])
        result = stage(self.root, '/missing', dict(schema=1, repairs=[], plugins=[], install_mode='vanilla'), 'A')
        self.assertEqual('3.8.14', result['source']['VERSION_ID'])
        metadata.unlink(); metadata.symlink_to('../../outside')
        with self.assertRaisesRegex(ValueError, 'escapes'): inspect_source(self.root)

    def test_vanilla_empty_performs_no_device_or_updater_writes(self):
        before = self.inventory()
        result = stage(self.root, Path(self.tmp.name) / 'missing-payload', dict(schema=1, repairs=[], plugins=[], install_mode='vanilla'), 'A')
        self.assertEqual('no-components-selected', result['status']); self.assertEqual(before, self.inventory())
        self.assertFalse((self.root / 'var').exists())
        sys.path.insert(0, str(ROOT))
        from protocol import validate_manifest
        self.assertIs(result['install_plan'], validate_manifest(result['install_plan'], 'InstallPlan'))
        self.assertEqual('3.8.14', result['install_plan']['source']['version'])

    def test_imported_does_not_reinject_even_if_stale_choices_exist(self):
        before = self.inventory()
        result = stage(self.root, '/missing', dict(schema=1, repairs=['core:decky'], plugins=[], install_mode='imported'), 'B')
        self.assertEqual('preserved-imported-image', result['status']); self.assertEqual(before, self.inventory())

    def test_vanilla_explicit_selection_is_not_ignored(self):
        with self.assertRaises(FileNotFoundError):
            stage(self.root, '/missing', dict(schema=1, repairs=['core:maintenance'], plugins=[], install_mode='vanilla'), 'A')

    def test_untrusted_device_package_fails_before_any_write(self):
        before = self.inventory()
        with self.assertRaisesRegex(ValueError, 'trust'):
            stage(self.root, '/missing', dict(schema=1, repairs=[], plugins=[], device_package='oxp3'), 'A')
        self.assertEqual(before, self.inventory())

    def test_no_generic_legacy_device_policy(self):
        for component in ('system-update-hold', 'input', 'fan-driver'):
            with self.assertRaisesRegex(ValueError, 'explicitly trusted'):
                stage(self.root, '/missing', dict(schema=1, repairs=[component], plugins=[]), 'A')

    def test_options_fail_closed(self):
        validate_options(dict(schema=1, repairs=[], plugins=[], wireless=None, device_components=None, device_package=None))
        with self.assertRaises(ValueError): validate_options(dict(schema=1, repairs=['core:decky'] * 2))
        with self.assertRaises(ValueError): validate_options(dict(schema=1, plugins=['unknown']))
        with self.assertRaises(ValueError): validate_options(dict(schema=1, repairs=[], install_mode='unknown'))

    def test_cross_model_generic_conflicts_are_rejected_before_apply(self):
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            check_conflicts([dict(id='oxp3:decky', conflicts=['core:decky'])], ['oxp3:decky','core:decky'])
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            check_conflicts([dict(id='core:decky', conflicts=['oxp3:decky'])], ['oxp3:decky','core:decky'])
        check_conflicts([dict(id='oxp3:decky', conflicts=['core:decky'])], ['oxp3:decky'])

    def test_preparation_reports_missing_inputs_unavailable(self):
        prepare = runpy.run_path(str(ROOT / 'scripts/prepare-integration.py'))['prepare']
        output = Path(self.tmp.name) / 'prepared'
        manifest = prepare(output)
        self.assertTrue(manifest['components']['core:maintenance']['available'])
        self.assertFalse(manifest['components']['core:decky']['available'])
        self.assertFalse(manifest['components']['wifi:intel']['available'])
        with self.assertRaises(FileExistsError): prepare(output)
        self.assertFalse(any('oxp3' in p.as_posix() for p in output.rglob('*')))


if __name__ == '__main__': unittest.main()
