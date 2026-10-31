"""Public builder rejection checks and optional real-asset artifact verification."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--project-root', type=Path, default=HERE.parent)
parser.add_argument('--plugin-zip', type=Path)
parser.add_argument('--built', type=Path)
args, remaining = parser.parse_known_args()
spec = importlib.util.spec_from_file_location('shared_base_builder', HERE/'scripts/build-shared-base.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class SharedBaseBuilderChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='oxp3-shared-base-test-')
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.output = self.work/'output'

    def build(self, **changes):
        values = dict(project_root=args.project_root, base=self.work/'base',
            plugin_zip=self.work/'plugin.zip', archives=[], output=self.output)
        values.update(changes)
        return builder.build(**values)

    def test_changed_lock_is_rejected_before_output(self):
        path = self.work/'lock.json'
        path.write_bytes(builder.INPUT_LOCK.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError, 'input lock differs'):
            self.build(input_lock=path)
        self.assertFalse(self.output.exists())

    def test_incomplete_unanchored_package_is_rejected_before_output(self):
        base = self.work/'base'; base.mkdir()
        (base/'manifest.json').write_text('{"schema_version":1,"kind":"DevicePackage","components":[]}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'manifest digest changed'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_existing_output_is_preserved_before_input_reads(self):
        self.output.mkdir(); marker = self.output/'user-file'; marker.write_bytes(b'preserved')
        with self.assertRaisesRegex(ValueError, 'new output'):
            self.build(input_lock=self.work/'does-not-exist')
        self.assertEqual(marker.read_bytes(), b'preserved')

    def test_output_inside_input_package_is_rejected_before_input_reads(self):
        base = self.work/'base'; base.mkdir()
        with self.assertRaisesRegex(ValueError, 'inside the input package'):
            self.build(base=base, output=base/'new-build', input_lock=self.work/'does-not-exist')
        self.assertEqual(list(base.iterdir()), [])

    def test_output_with_parent_alias_inside_input_is_rejected_before_reads(self):
        base = self.work/'base'; base.mkdir()
        other = self.work/'other'; other.mkdir()
        output = other/'..'/'base'/'new-build'
        with self.assertRaisesRegex(ValueError, 'inside the input package'):
            self.build(base=base, output=output, input_lock=self.work/'does-not-exist')
        self.assertFalse(output.exists())

    def test_changed_bridge_source_is_rejected_without_execution(self):
        root = self.work/'source'
        path = root/'adaptations/brands/onexplayer/oxp3/bridge/scripts/build.py'
        path.parent.mkdir(parents=True); path.write_text('raise AssertionError("must not run")', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'reviewed SHA-256'):
            builder.load_bridge(root)

    def test_public_lock_has_exact_plugin_members_and_preserved_licenses(self):
        self.assertEqual(builder.digest(builder.INPUT_LOCK), builder.INPUT_LOCK_SHA256)
        pins = json.loads(builder.INPUT_LOCK.read_bytes())
        self.assertEqual(len(pins['plugin_release']['files']), 36)
        self.assertEqual(pins['plugin_archive']['sha256'], 'd2d6b3ccdfd73c8676450dcce5312330db861276e1ab2062e56efb0ac5043fea')
        self.assertEqual(pins['plugin_archive']['size_bytes'], 1483205)
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'fsr_compat/licenses/wimlib/COPYING.GPLv3.txt',
                     'fsr_compat/licenses/wimlib/COPYING.LGPL.txt'):
            self.assertIn(name, pins['plugin_release']['files'])
        self.assertEqual(len(pins['optiscaler_release']['archives']), 3)

    def test_missing_archive_set_is_rejected_by_authentic_public_reader(self):
        if args.plugin_zip is None:
            self.skipTest('Supply explicit --plugin-zip to test real public input authentication')
        bridge = builder.load_bridge(args.project_root)
        pins = json.loads(builder.INPUT_LOCK.read_bytes())
        with self.assertRaisesRegex(ValueError, 'exactly all three pinned'):
            bridge.read_opti_inputs(args.plugin_zip, [], pins)

    def test_changed_plugin_is_rejected_by_authentic_public_reader(self):
        bridge = builder.load_bridge(args.project_root)
        plugin = self.work/'plugin.zip'; plugin.write_bytes(b'not the public plugin')
        pins = json.loads(builder.INPUT_LOCK.read_bytes())
        with self.assertRaisesRegex(ValueError, 'locked size and SHA-256'):
            bridge.read_opti_inputs(plugin, [], pins)

    def test_actual_built_artifact_matches_historical_manifest_zip_and_license_inventory(self):
        if args.built is None:
            self.skipTest('Supply explicit --built after a real locked-byte rebuild')
        package = args.built/'package'
        self.assertEqual(builder.digest(package/'manifest.json'), builder.FINAL_SHA256)
        manifest = json.loads((package/'manifest.json').read_bytes())
        self.assertEqual(manifest['version'], builder.FINAL_VERSION)
        self.assertEqual(len(manifest['files']), 505)
        self.assertEqual(manifest['source']['path'], builder.HISTORICAL_SOURCE_LABEL)
        self.assertEqual(builder.digest(args.built/('boiler-device-oxp3-'+builder.FINAL_VERSION+'.zip')), builder.ARCHIVE_SHA256)
        pins = json.loads(builder.INPUT_LOCK.read_bytes())
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'fsr_compat/licenses/wimlib/COPYING.GPLv3.txt'):
            self.assertEqual(builder.digest(package/'payload/legacy/components/opti2xmx'/name), pins['plugin_release']['files'][name])
        for name, expected in builder.CPU_FILES.items():
            self.assertEqual(builder.digest(package/'payload/legacy/components/controls/cpu'/name), expected)
        self.assertEqual(builder.digest(package/'payload/legacy/integration/opti2xmx/runtime.tar.gz'),
            pins['plugin_release']['runtime']['sha256'])


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
