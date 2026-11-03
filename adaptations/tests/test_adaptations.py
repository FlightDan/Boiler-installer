import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('adaptation_collection', ROOT/'scripts/adaptations.py')
collection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collection)


class AdaptationCollectionTests(unittest.TestCase):
    def copy_collection(self, destination):
        inventory=json.loads((ROOT/'model-source-files.json').read_bytes())
        for name in ['model-source-files.json','records/installer-adaptation-source-import-20261104.json']+[row['path'] for row in inventory['files']]:
            target=destination/name; target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)

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
        self.assertEqual(len(models['guide:oxp3']['components']), 29)
        x2 = {item['id']: item for item in models['guide:x2-mini']['components']}
        self.assertEqual(x2['pl4-175w']['status'], 'implementation_missing')
        self.assertEqual(x2['pl4-175w']['sources'], [])
        self.assertEqual(x2['desktop-hdr']['status'], 'external_runtime_required')
        self.assertEqual(len(x2['desktop-hdr']['sources']), 4)
        self.assertEqual(x2['hdr']['sources'], [])
        self.assertTrue(x2['fingerprint-unlock']['default'])
        self.assertEqual(x2['gyro']['sources'], ['brands/onexplayer/x2-mini/common-device-sources.toon'])

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
            self.update_model(root, lambda model: model['components'][1].update(notes='Changed recommendation'))
            with self.assertRaisesRegex(ValueError, 'Guide differs'):
                collection.check(root)

    def test_missing_dependencies_and_runtime_defaults_are_rejected(self):
        for change, message in (
            (lambda model: model['components'][1].update(dependencies=['missing-component']), 'dependency'),
            (lambda model: model['components'][1].update(default=True), 'installation defaults'),
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
            self.update_model(root, lambda model: model['components'][1].update(sources=['private-or-missing.py']))
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
        guides = [ROOT/'docs/ADAPTATIONS.md', ROOT/'common/components.md',
                  ROOT/'brands/onexplayer/common/components.md']
        guides += [ROOT/model['guide'] for _, model in collection.records(ROOT)]
        for guide in guides:
            for target in re.findall(r'\]\(([^)]+)\)', guide.read_text(encoding='utf-8')):
                if '://' in target or target.startswith('#'):
                    continue
                path = (guide.parent/target.split('#')[0]).resolve()
                self.assertTrue(path.is_relative_to(ROOT), (guide, target))
                self.assertTrue(path.exists(), (guide, target))

    def test_gyro_model_reference_must_be_unique_and_local(self):
        for sources in ([], ['brands/onexplayer/oxp3/common-device-sources.toon'],
                        ['brands/onexplayer/x2-mini/common-device-sources.toon'] * 2,
                        ['common/sensors/bosch/bmi260/gyro/source.toon']):
            with self.subTest(sources=sources), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.copy_collection(root)
                self.update_model(root, lambda m: next(c for c in m['components'] if c['id'] == 'gyro').update(sources=sources))
                with self.assertRaisesRegex(ValueError, 'own model TOON'):
                    collection.check(root)

    def test_missing_gyro_component_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_collection(root)
            self.update_model(root, lambda m: m.update(components=[c for c in m['components'] if c['id'] != 'gyro']))
            with self.assertRaisesRegex(ValueError, 'Missing gyro component'):
                collection.check(root)

    def test_common_gyro_reference_missing_duplicate_or_wrong_fails(self):
        for mutation in ('missing', 'duplicate', 'wrong'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.copy_collection(root)
                path = root/'brands/onexplayer/x2-mini/common-device-sources.toon'
                data = path.read_text(encoding='utf-8')
                line = '  "resource:bmi260-gyro",common/sensors/bosch/bmi260/gyro/source.toon\n'
                if mutation == 'missing':
                    data = data.replace(line, '')
                elif mutation == 'duplicate':
                    data = data.replace('resources[4]', 'resources[5]').replace(line, line + line)
                else:
                    data = data.replace('common/sensors/bosch/bmi260/gyro/source.toon', 'wrong/source.toon')
                if mutation == 'missing':
                    data = data.replace('resources[4]', 'resources[3]')
                path.write_text(data, encoding='utf-8')
                with self.assertRaises(ValueError):
                    collection.check(root)

    def test_third_model_gyro_toon_is_validated(self):
        for case in ('valid', 'no-gyro', 'other-gyro', 'other-gyro-uninventoried', 'missing', 'wrong', 'duplicate', 'identity', 'source-only'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.copy_collection(root)
                directory = root/'brands/onexplayer/test-model'
                directory.mkdir()
                guide = 'brands/onexplayer/test-model/model.md'
                manifest = 'brands/onexplayer/test-model/manifest.json'
                toon = 'brands/onexplayer/test-model/common-device-sources.toon'
                shutil.copyfile(root/'brands/onexplayer/x2-mini/model.md', root/guide)
                model = json.loads((root/'brands/onexplayer/x2-mini/manifest.json').read_text(encoding='utf-8'))
                model['id'] = 'guide:test-model'
                model['guide'] = guide
                next(item for item in model['components'] if item['id']=='fingerprint-unlock')['default'] = False
                if case == 'no-gyro':
                    model['components'] = [item for item in model['components'] if item['id'] != 'gyro']
                else:
                    sources = ([('brands/onexplayer/common/scripts/input_profile.py' if case == 'other-gyro' else 'unreviewed-gyro.py')]
                               if case in ('other-gyro', 'other-gyro-uninventoried') else [toon])
                    next(item for item in model['components'] if item['id'] == 'gyro')['sources'] = sources
                (root/manifest).write_text(json.dumps(model, ensure_ascii=False), encoding='utf-8')
                collection.guide(root, model, write=True)
                catalog = json.loads((root/'adaptations.json').read_text(encoding='utf-8'))
                catalog['models'].append(manifest)
                (root/'adaptations.json').write_text(json.dumps(catalog), encoding='utf-8')
                reference = ('schema_version: 1\nkind: BoilerModelCommonReferences\n'
                             'model_id: "device:test-model"\nsource_only: true\n'
                             'resources[1]{id,source}:\n'
                             '  "resource:bmi260-gyro",common/sensors/bosch/bmi260/gyro/source.toon\n')
                if case == 'wrong':
                    reference = reference.replace('common/sensors/bosch/bmi260/gyro/source.toon', 'wrong/source.toon')
                elif case == 'duplicate':
                    reference = reference.replace('resources[1]', 'resources[2]') + '  "resource:bmi260-gyro",common/sensors/bosch/bmi260/gyro/source.toon\n'
                elif case == 'identity':
                    reference = reference.replace('device:test-model', 'device:x2-mini')
                elif case == 'source-only':
                    reference = reference.replace('source_only: true', 'source_only: false')
                if case not in ('missing', 'no-gyro', 'other-gyro', 'other-gyro-uninventoried'):
                    (root/toon).write_text(reference, encoding='utf-8')
                if case in ('valid', 'no-gyro', 'other-gyro'):
                    self.assertEqual(collection.check(root)['models'], 3)
                else:
                    with self.assertRaises(ValueError):
                        collection.check(root)

    def test_relocated_source_tamper_missing_or_legacy_copy_fails(self):
        for mutation in ('tamper', 'missing', 'legacy'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.copy_collection(root)
                new = root/(collection.NEW_GYRO + 'launch.sh')
                if mutation == 'tamper':
                    new.write_bytes(new.read_bytes() + b'changed')
                elif mutation == 'missing':
                    new.unlink()
                else:
                    old = root/(collection.OLD_GYRO + 'launch.sh')
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(new, old)
                with self.assertRaises((ValueError, FileNotFoundError)):
                    collection.check(root)

    def test_relocation_mapping_rejects_changed_path_or_hash(self):
        for key, value in (('new_path', 'wrong/file'), ('sha256', '0'*64)):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.copy_collection(root)
                path = root/collection.RELOCATION
                record = json.loads(path.read_text(encoding='utf-8'))
                record['files'][0][key] = value
                path.write_text(json.dumps(record), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'relocation metadata'):
                    collection.check(root)

    def test_historical_source_hashes_match_migration_record(self):
        for name in ('source-plan.json', 'source-lock.json'):
            for mutation in ('record-hash', 'file-bytes'):
                with self.subTest(name=name, mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.copy_collection(root)
                    if mutation == 'record-hash':
                        path = root/collection.RELOCATION
                        record = json.loads(path.read_text(encoding='utf-8'))
                        record['historical_source_sha256'][name] = '0'*64
                        path.write_text(json.dumps(record), encoding='utf-8')
                    else:
                        path = root/name
                        path.write_bytes(path.read_bytes() + b'\n')
                    with self.assertRaisesRegex(ValueError, 'Historical source bytes differ'):
                        collection.check(root)

    def test_prepare_preserves_lock_and_never_recreates_old_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_collection(root)
            old_dir = root/collection.OLD_GYRO
            if old_dir.exists():
                old_dir.rmdir()
            plan = collection.read(root/'source-plan.json')
            lock_bytes = (root/'source-lock.json').read_bytes()
            mapping = collection.relocations(root, collection.read(root/'source-lock.json'))
            sources = {e['upstream_path']: (root/mapping.get(e['path'], e['path'])).read_bytes() for e in plan['files']}
            def fake_git(args, stderr=None):
                command = args[3:]
                if command[:2] == ['remote', 'get-url']:
                    return ('https://github.com/' + plan['source']['repository']).encode()
                if command[0] == 'rev-parse':
                    return plan['source']['commit'].encode()
                if command[0] == 'ls-tree':
                    return b'100644 blob 1234 file'
                if command[0] == 'show':
                    return sources[command[1].split(':', 1)[1]]
                raise AssertionError(command)
            with patch.object(collection.subprocess, 'check_output', side_effect=fake_git):
                canonical = root/(collection.NEW_GYRO + 'launch.sh')
                expected = canonical.read_bytes()
                for damage in ('missing', 'tampered'):
                    if damage == 'missing':
                        canonical.unlink()
                    else:
                        canonical.write_bytes(b'tampered')
                    collection.prepare(root/'unused', root)
                    self.assertEqual(canonical.read_bytes(), expected)
                    self.assertEqual((root/'source-lock.json').read_bytes(), lock_bytes)
                    self.assertFalse((root/collection.OLD_GYRO).exists())
                source = next(iter(sources))
                sources[source] += b'changed'
                before = {e['path']: (root/mapping.get(e['path'], e['path'])).read_bytes() for e in plan['files']}
                with self.assertRaisesRegex(ValueError, 'immutable historical lock'):
                    collection.prepare(root/'unused', root)
                self.assertEqual((root/'source-lock.json').read_bytes(), lock_bytes)
                self.assertEqual(before, {e['path']: (root/mapping.get(e['path'], e['path'])).read_bytes() for e in plan['files']})
                self.assertFalse((root/collection.OLD_GYRO).exists())

    def test_supplementary_license_cannot_be_silently_changed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_collection(root)
            (root/'licenses/GPL-2.0.txt').write_text('license omitted\n')
            with self.assertRaisesRegex(ValueError, 'license inventory mismatch'):
                collection.check(root)


if __name__ == '__main__':
    unittest.main()
