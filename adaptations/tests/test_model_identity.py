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
        shutil.copytree(ROOT/'devices', self.root/'devices')
        shutil.copytree(ROOT/'vendor', self.root/'vendor')
        shutil.copy2(ROOT/'toon-parser-lock.json', self.root/'toon-parser-lock.json')
        shutil.copy2(ROOT/'common-device-source-index.json', self.root/'common-device-source-index.json')
        def copy(name):
            dest = self.root/name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT/name, dest)
        for migration in identity.common.MIGRATIONS: copy(migration)
        copy(identity.common.GM7)
        for source in identity.common.RESOURCES.values():
            copy(source)
            value = identity.common.toon_format.loads((ROOT/source).read_text(encoding='utf-8'), strict=True)
            base = Path(source).parent
            for row in value['files']:
                if row['availability'] == 'checked-in': copy(base/row['path'])
            for license in value['licenses']: copy(base/license)
        for path in self.root.glob('devices/*/*.toon'):
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
            value = json.loads((ROOT/'tests/fixtures/model-identity-aea9ee'/f'{slug}.json').read_bytes())
            raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        current = json.loads(identity.protocol_views(self.root)['devices/x2-mini.json'])
        self.assertEqual(current['name']['en'], 'ONEXPLAYER X2 Mini')
        self.assertEqual(current['hardware_matching']['pairs'], [{'manufacturer': 'ONE-NETBOOK', 'model': 'ONEXPLAYER X2 Mini'}, {'manufacturer': 'ONE-NETBOOK', 'model': 'ONEXPLAYER X2Mini'}])
        model = identity.common.toon_format.loads((self.root/'devices/onexplayer/x2-mini.toon').read_text(encoding='utf-8'), strict=True)
        self.assertEqual(model['resource_model']['hdr'], {'profile': 'x2-mini', 'full_frame': 472, 'peak': 1100})
        snapshot = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        identity.generate(self.root, check=True)
        identity.generate(self.root)
        self.assertEqual(snapshot, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        (self.root/'models.toon').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Generated view differs'):
            identity.generate(self.root, check=True)

    def test_duplicate_alias_rejected(self):
        path = self.root/'devices/onexplayer/x2-mini.toon'
        model = identity.common.toon_format.loads(path.read_text(encoding='utf-8'), strict=True)
        model['legacy_aliases']['package'] = ['device:oxp3']
        model['runtime']['id'] = 'device:oxp3'
        self.save(path, model)
        with self.assertRaisesRegex(ValueError, 'Unrecognized historical aliases'):
            identity.generate(self.root)

    def test_rejects_forged_alias_for_new_brand_model(self):
        dest = self.root/'brands/example/oxp3'
        shutil.copytree(self.root/'brands/onexplayer/oxp3', dest)
        path = self.root/'devices/example/oxp3.toon'
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.root/'devices/onexplayer/oxp3.toon', path)
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
        for name in ('devices/onexplayer/invalid_name.toon', 'devices/onexplayer/unknown.toon'):
            with self.subTest(name=name):
                extra = self.root/name
                extra.write_text('not a model', encoding='utf-8')
                with self.assertRaises(ValueError):
                    identity.generate(self.root)
                extra.unlink()
        duplicate = self.root/'brands/onexplayer/oxp3/oxp3.toon'
        duplicate.write_bytes((self.root/'devices/onexplayer/oxp3.toon').read_bytes())
        with self.assertRaises(ValueError):
            identity.generate(self.root)

    def test_resource_projection_and_missing_source(self):
        path = self.root/'devices/onexplayer/oxp3.toon'
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

    def test_published_model_requires_all_resources_and_gm7(self):
        path = self.root/'devices/onexplayer/oxp3.toon'
        model = identity.common.toon_format.loads(path.read_text(encoding='utf-8'), strict=True)
        original = json.loads(json.dumps(model['resource_model']['common_references']))
        for mutation in ('missing-resource', 'missing-gm7'):
            with self.subTest(mutation=mutation):
                refs = json.loads(json.dumps(original))
                if mutation == 'missing-resource': refs['resources'].pop()
                else: refs['boot_policies'] = []
                model['resource_model']['common_references'] = refs
                self.save(path, model)
                with self.assertRaisesRegex(ValueError, 'Published model common resources or GM7 differ'):
                    identity.generate(self.root)

    def test_referenced_source_bytes_must_match_lock(self):
        path = self.root/'common/sensors/bosch/bmi260/gyro/sources/launch.sh'
        path.write_bytes(path.read_bytes()+b'tampered')
        with self.assertRaisesRegex(ValueError, 'source bytes differ'):
            identity.generate(self.root)

    def test_unrecognized_reference_and_absent_reference(self):
        src = self.root/'brands/onexplayer/oxp3'
        dest = self.root/'brands/example/oxp3'
        shutil.copytree(src, dest)
        path = self.root/'devices/example/oxp3.toon'
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.root/'devices/onexplayer/oxp3.toon', path)
        model = identity.common.toon_format.loads(path.read_text(encoding='utf-8'), strict=True)
        model['canonical_id'] = 'device:example-oxp3'
        model['legacy_aliases'] = {'guide': [], 'package': []}
        model['id'] = 'guide:example-oxp3'
        model['runtime']['id'] = model['canonical_id']
        model['resource_model']['id'] = model['canonical_id']
        model['device_record_path'] = 'devices/example-oxp3.json'
        model['guide'] = 'brands/example/oxp3/model.md'
        refs = model['resource_model']['common_references']
        refs['model_id'] = model['canonical_id']
        refs['resources'] = refs['resources'][:1]
        refs['boot_policies'] = []
        baseline = json.loads(json.dumps(refs))
        for mutation in ('unknown', 'wrong-path', 'duplicate', 'wrong-id', 'unsupported-policy'):
            with self.subTest(mutation=mutation):
                refs = json.loads(json.dumps(baseline))
                if mutation == 'unknown': refs['resources'][0]['id'] = 'resource:unknown'
                elif mutation == 'wrong-path': refs['resources'][0]['source'] = 'common/wrong.toon'
                elif mutation == 'duplicate': refs['resources'].append(dict(refs['resources'][0]))
                elif mutation == 'wrong-id': refs['model_id'] = 'device:oxp3'
                else: refs['boot_policies'] = [{'id': 'ssd:unknown'}]
                model['resource_model']['common_references'] = refs
                self.save(path, model)
                with self.assertRaises(ValueError): identity.generate(self.root)
        del model['resource_model']['common_references']
        self.save(path, model)
        (dest/'common-device-sources.toon').unlink()
        identity.generate(self.root)
        self.assertFalse((dest/'common-device-sources.toon').exists())
        (dest/'common-device-sources.toon').write_text('stale')
        with self.assertRaisesRegex(ValueError, 'Undeclared common reference view'):
            identity.generate(self.root, check=True)

    def test_same_slug_across_brands_without_legacy_alias(self):
        src = self.root/'brands/onexplayer/oxp3'
        dest = self.root/'brands/example/oxp3'
        shutil.copytree(src, dest)
        path = self.root/'devices/example/oxp3.toon'
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.root/'devices/onexplayer/oxp3.toon', path)
        model = identity.common.toon_format.loads(path.read_text(encoding='utf-8'), strict=True)
        model['canonical_id'] = 'device:example-oxp3'
        model['legacy_aliases'] = {'guide': [], 'package': []}
        model['id'] = 'guide:example-oxp3'
        model['runtime']['id'] = 'device:example-oxp3'
        model['resource_model']['id'] = 'device:example-oxp3'
        refs = model['resource_model']['common_references']
        refs['model_id'] = model['canonical_id']
        refs['resources'] = [next(row for row in refs['resources'] if row['id'] == 'resource:bmi260-gyro')]
        refs.pop('boot_policies')
        next(item for item in model['components'] if item['id']=='gyro')['sources'] = ['brands/example/oxp3/common-device-sources.toon']
        (dest/'common-device-sources.toon').unlink()
        model['device_record_path'] = 'devices/example-oxp3.json'
        model['guide'] = 'brands/example/oxp3/model.md'
        self.save(path, model)
        with self.assertRaisesRegex(ValueError, 'only for published models'):
            identity.generate(self.root)
        return
        index = identity.common.toon_format.loads((self.root/'models.toon').read_text(encoding='utf-8'), strict=True)
        self.assertEqual({r['id'] for r in index['models']}, {'device:example-oxp3', 'device:onexplayer-oxp3', 'device:onexplayer-x2-mini'})
        self.assertEqual(json.loads((self.root/'devices/example-oxp3.json').read_bytes())['id'], 'device:example-oxp3')
        ref = identity.common.toon_format.loads((dest/'common-device-sources.toon').read_text(encoding='utf-8'), strict=True)
        self.assertEqual(ref, refs)
        self.assertNotIn('boot_policies', ref)
        self.assertEqual((self.root/'common-device-source-index.json').read_bytes(), (ROOT/'common-device-source-index.json').read_bytes())
        for slug in ('oxp3', 'x2-mini'):
            name = f'brands/onexplayer/{slug}/common-device-sources.toon'
            self.assertEqual((self.root/name).read_bytes(), (ROOT/name).read_bytes())
        identity.generate(self.root, check=True)
        (dest/'common-device-sources.toon').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'Generated view differs'):
            identity.generate(self.root, check=True)

if __name__ == '__main__': unittest.main()
