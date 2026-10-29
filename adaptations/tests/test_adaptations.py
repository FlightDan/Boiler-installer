import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('adaptation_collection', ROOT/'scripts/adaptations.py')
collection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collection)


class AdaptationCollectionTests(unittest.TestCase):
    def copy_collection(self, destination):
        for name in ('adaptations.json', 'source-plan.json', 'source-lock.json'):
            shutil.copyfile(ROOT/name, destination/name)
        for name in ('common', 'brands', 'licenses'):
            shutil.copytree(ROOT/name, destination/name)

    def update_model(self, root, transform):
        path = root/'brands/onexplayer/x2-mini/manifest.json'
        model = json.loads(path.read_text(encoding='utf-8'))
        transform(model)
        path.write_text(json.dumps(model, ensure_ascii=False), encoding='utf-8')

    def test_collection_and_source_only_choices(self):
        result = collection.check()
        self.assertEqual(result['models'], 2)
        self.assertFalse(result['installable'])
        models = {model['id']: model for _, model in collection.records(ROOT)}
        self.assertEqual(len(models['guide:oxp3']['components']), 28)
        x2 = {item['id']: item for item in models['guide:x2-mini']['components']}
        self.assertEqual(x2['pl4-175w']['status'], 'implementation_missing')
        self.assertEqual(x2['pl4-175w']['sources'], [])
        self.assertEqual(x2['desktop-hdr']['status'], 'external_runtime_required')
        self.assertEqual(len(x2['desktop-hdr']['sources']), 4)
        self.assertEqual(x2['hdr']['sources'], [])
        self.assertTrue(all(x2[name]['recommendation'] == 'excluded'
                            for name in ('xe-dsb', 'fan-driver', 'charging', 'oxp3-light', 'legacy-runtime')))

    def test_tampered_source_fails_inventory_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_collection(root)
            source = root/'common/scripts/split_lock.py'
            source.write_bytes(source.read_bytes()+b'\n# changed\n')
            with self.assertRaisesRegex(ValueError, 'source changed'):
                collection.check(root)

    def test_extra_script_needs_explicit_source_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_collection(root)
            (root/'brands/onexplayer/x2-mini/scripts/unreviewed.py').write_text('pass\n')
            with self.assertRaisesRegex(ValueError, 'Uninventoried'):
                collection.check(root)

    def test_guide_table_cannot_drift_from_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_collection(root)
            self.update_model(root, lambda model: model['components'][0].update(notes='Changed recommendation'))
            with self.assertRaisesRegex(ValueError, 'Guide differs'):
                collection.check(root)

    def test_missing_dependencies_and_runtime_defaults_are_rejected(self):
        for change, message in (
            (lambda model: model['components'][0].update(dependencies=['missing-component']), 'dependency'),
            (lambda model: model['components'][0].update(default=True), 'installation defaults'),
            (lambda model: model.update(installable=True), 'not a DevicePackage'),
        ):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.copy_collection(root)
                self.update_model(root, change)
                with self.assertRaisesRegex(ValueError, message):
                    collection.check(root)

    def test_new_candidate_cannot_claim_uninventoried_script(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_collection(root)
            self.update_model(root, lambda model: model['components'][0].update(sources=['private-or-missing.py']))
            with self.assertRaisesRegex(ValueError, 'Uninventoried component source'):
                collection.check(root)

    def test_stale_model_provenance_is_rejected(self):
        for change in (lambda model: model['source_revision'].update(commit='a'*40),
                       lambda model: model.update(source_inventory='other-inventory.json')):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.copy_collection(root)
                self.update_model(root, change)
                with self.assertRaisesRegex(ValueError, 'Model source revision'):
                    collection.check(root)

    def test_local_guide_links_resolve_inside_collection(self):
        guides = [ROOT/'ADAPTATIONS.md', ROOT/'common/components.md',
                  ROOT/'brands/onexplayer/common/components.md']
        guides += [ROOT/model['guide'] for _, model in collection.records(ROOT)]
        for guide in guides:
            for target in re.findall(r'\]\(([^)]+)\)', guide.read_text(encoding='utf-8')):
                if '://' in target or target.startswith('#'):
                    continue
                path = (guide.parent/target.split('#')[0]).resolve()
                self.assertTrue(path.is_relative_to(ROOT), (guide, target))
                self.assertTrue(path.exists(), (guide, target))

    def test_supplementary_license_cannot_be_silently_changed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_collection(root)
            (root/'licenses/GPL-2.0.txt').write_text('license omitted\n')
            with self.assertRaisesRegex(ValueError, 'license inventory mismatch'):
                collection.check(root)


if __name__ == '__main__':
    unittest.main()
