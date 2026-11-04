import importlib.util
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('identity', ROOT/'scripts/model-identity.py')
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT/'brands/onexplayer/oxp3', self.root/'brands/onexplayer/oxp3')
        shutil.copytree(ROOT/'brands/onexplayer/x2-mini', self.root/'brands/onexplayer/x2-mini')
        (self.root/'devices').mkdir()
        for path in self.root.glob('brands/*/*/*.toon'):
            if path.name != path.parent.name + '.toon': continue
            model = identity.common.toon_format.loads(path.read_text(encoding='utf-8'), strict=True)
            for component in model['components']:
                for source in component['sources']:
                    dest = self.root/source
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(ROOT/source, dest)

    def save(self, path, model):
        path.write_text(identity.common.toon_format.dumps(model)+'\n', encoding='utf-8')

    def test_old_records_and_reproducible_views(self):
        identity.generate(self.root)
        # Canonical JSON digests from aea9ee329aee01896332f7156483436580acdb2a,
        # independently freeze every historical field, not the generated view.
        expected = {'oxp3': 'e5ff789b2fc54994d551ba403d12346010806a34402dbe3de75d1278d2240825',
                    'x2-mini': '348bd3fceef0a9ee26ad1f05c10b9c701dbe7010f3bc5801f4d7ad90a75fa9dd'}
        for slug, digest in expected.items():
            value = json.loads((self.root/'devices'/f'{slug}.json').read_bytes())
            raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        snapshot = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        identity.generate(self.root, check=True)
        identity.generate(self.root)
        self.assertEqual(snapshot, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        (self.root/'devices/oxp3.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Generated view differs'):
            identity.generate(self.root, check=True)

    def test_duplicate_alias_rejected(self):
        path = self.root/'brands/onexplayer/x2-mini/x2-mini.toon'
        model = identity.common.toon_format.loads(path.read_text(encoding='utf-8'), strict=True)
        model['legacy_aliases']['package'] = ['device:oxp3']
        model['runtime']['id'] = 'device:oxp3'
        self.save(path, model)
        with self.assertRaisesRegex(ValueError, 'Unrecognized historical aliases'):
            identity.generate(self.root)

    def test_rejects_forged_alias_for_new_brand_model(self):
        dest = self.root/'brands/example/oxp3'
        shutil.copytree(self.root/'brands/onexplayer/oxp3', dest)
        path = dest/'oxp3.toon'
        model = identity.common.toon_format.loads(path.read_text(encoding='utf-8'), strict=True)
        model['canonical_id'] = 'device:example-oxp3'
        for aliases in ({'guide': ['guide:made-up'], 'package': ['device:made-up']},
                        {'guide': ['guide:oxp3'], 'package': ['device:oxp3']},
                        {'guide': [], 'package': ['device:made-up']}):
            with self.subTest(aliases=aliases):
                model['legacy_aliases'] = aliases
                self.save(path, model)
                with self.assertRaisesRegex(ValueError, 'Unrecognized historical aliases'):
                    identity.generate(self.root)

    def test_rejects_unrecognized_model_toon_files(self):
        common = self.root/'brands/onexplayer/common/components.toon'
        common.parent.mkdir(parents=True, exist_ok=True)
        common.write_text('existing brand component definition', encoding='utf-8')
        self.assertEqual(identity.generate(self.root), 2)
        for name in ('brands/onexplayer/oxp3/typo.toon',
                     'brands/onexplayer/common/unknown.toon'):
            with self.subTest(name=name):
                extra = self.root/name
                extra.write_text('not a model', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'Unknown or misnamed model TOON'):
                    identity.generate(self.root)
                extra.unlink()

    def test_resource_projection_and_missing_source(self):
        path = self.root/'brands/onexplayer/oxp3/oxp3.toon'
        model = identity.common.toon_format.loads(path.read_text(encoding='utf-8'), strict=True)
        view = identity.resource_view(model)
        self.assertEqual(view['name'], model['runtime']['name']['en'])
        self.assertEqual(view['hardware_matching'], model['runtime']['hardware_matching'])
        model['resource_model']['hardware_matching'] = {'mode': 'wrong', 'pairs': []}
        self.save(path, model)
        with self.assertRaisesRegex(ValueError, 'duplicated resource identity'):
            identity.generate(self.root)
        del model['resource_model']['hardware_matching']
        model['components'][0]['sources'][0] = 'common/missing-file.py'
        self.save(path, model)
        with self.assertRaisesRegex(ValueError, 'Missing component source'):
            identity.generate(self.root)

    def test_same_slug_across_brands_without_legacy_alias(self):
        src = self.root/'brands/onexplayer/oxp3'
        dest = self.root/'brands/example/oxp3'
        shutil.copytree(src, dest)
        path = dest/'oxp3.toon'
        model = identity.common.toon_format.loads(path.read_text(encoding='utf-8'), strict=True)
        model['canonical_id'] = 'device:example-oxp3'
        model['legacy_aliases'] = {'guide': [], 'package': []}
        model['id'] = 'guide:example-oxp3'
        model['runtime']['id'] = 'device:example-oxp3'
        model['resource_model']['id'] = 'device:example-oxp3'
        model['device_record_path'] = 'devices/example-oxp3.json'
        model['guide'] = 'brands/example/oxp3/model.md'
        self.save(path, model)
        self.assertEqual(identity.generate(self.root), 3)
        index = identity.common.toon_format.loads((self.root/'models.toon').read_text(encoding='utf-8'), strict=True)
        self.assertEqual({r['id'] for r in index['models']}, {'device:example-oxp3', 'device:onexplayer-oxp3', 'device:onexplayer-x2-mini'})
        self.assertEqual(json.loads((self.root/'devices/example-oxp3.json').read_bytes())['id'], 'device:example-oxp3')

if __name__ == '__main__': unittest.main()
