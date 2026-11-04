import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT/'records/migrations/x2-installer-catalog-entrypoints-20261104.json'

class X2CatalogEntryTests(unittest.TestCase):
    def test_new_entrypoints_differ_only_in_catalog_location(self):
        record = json.loads(RECORD.read_bytes())
        imported = json.loads((ROOT/record['historical_import_record']).read_bytes())
        historical = {row['path']: row for row in imported['files']}
        self.assertEqual(record['source_baseline'], 'aea9ee329aee01896332f7156483436580acdb2a')
        self.assertEqual(len(record['files']), 2)
        expected = {
            'brands/onexplayer/x2-mini/bridge/scripts/build.py': ('brands/onexplayer/x2-mini/bridge/scripts/build-installer-catalog.py', "confined(adaptations, 'scripts/toon_catalog.py')", "confined(project_root, 'scripts/toon_catalog.py')"),
            'scripts/build-x2-complete.py': ('scripts/build-x2-complete-installer-catalog.py', "regular(root, 'adaptations/scripts/toon_catalog.py')", "regular(root, 'scripts/toon_catalog.py')"),
        }
        self.assertEqual({row['old_path'] for row in record['files']}, set(expected))
        for row in record['files']:
            self.assertEqual((row['new_path'],row['old_expression'],row['new_expression']), expected[row['old_path']])
            old = (ROOT/row['old_path']).read_bytes()
            new = (ROOT/row['new_path']).read_bytes()
            self.assertEqual((len(old), hashlib.sha256(old).hexdigest()),
                             (row['old_size'], row['old_sha256']))
            self.assertEqual((len(new), hashlib.sha256(new).hexdigest()),
                             (row['new_size'], row['new_sha256']))
            self.assertEqual((row['old_size'], row['old_sha256']),
                             (historical[row['old_path']]['size'], historical[row['old_path']]['sha256']))
            before, after = row['old_expression'].encode(), row['new_expression'].encode()
            self.assertEqual(old.count(before), 1)
            self.assertEqual(new, old.replace(before, after))

    def test_complete_entrypoint_uses_only_explicit_installer_root(self):
        path = ROOT/'scripts/build-x2-complete-installer-catalog.py'
        spec = importlib.util.spec_from_file_location('x2_current_test', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            old = project/'adaptations/scripts/toon_catalog.py'
            old.parent.mkdir(parents=True)
            old.write_text("raise AssertionError('old path must not be loaded')\n")
            with self.assertRaisesRegex(ValueError, 'Missing or escaping input'):
                module.load_recipe(project, project/'adaptations')
            new = project/'scripts/toon_catalog.py'; new.parent.mkdir()
            new.write_text('''\ndef models(root):\n    return [("source", {"id":"guide:x2-mini", "build":{"adapter":"x2-complete-v1", "complete":True, "missing":[], "inputs":[{"name":"base", "sha256":"cb39c94bbe7eb351221c380ebdb7a875a097b52c17e90bfb0f1edc22b12ab684"}, {"name":"bridge_overlay"}, {"name":"hdr_overlay"}]}, "package":{"kind":"BoilerInheritedPackageDefinition", "base_input":"base"}})]\n''')
            model, inputs = module.load_recipe(project, project/'adaptations')
            self.assertEqual(model['id'], 'guide:x2-mini')
            self.assertEqual(set(inputs), {'base', 'bridge_overlay', 'hdr_overlay'})

if __name__ == '__main__': unittest.main()
