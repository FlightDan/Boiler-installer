import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT/'records/migrations/x2-installer-catalog-entrypoints-20261104.json'

class X2CatalogEntryTests(unittest.TestCase):
    def test_complete_entrypoint_uses_only_explicit_installer_root(self):
        path = ROOT/'scripts/build-x2-complete.py'
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
