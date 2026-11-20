import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from protocol.component_versions import component_releases, validate_model_requests
from protocol.local import import_local
from protocol.manifest import ProtocolError, validate_manifest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('resolve_model_components', ROOT/'scripts/resolve-model-components.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
SOURCE = dict(kind='github', repository='FlightDan/Boiler-adaptations', commit='b'*40)
RESOURCE = 'resource:onexplayer-microphone'
COMPONENT = 'oxp3:microphone'
URL = 'https://github.com/FlightDan/Boiler-adaptations/releases/download/test-fixture/output.zip'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class ComponentVersionsTests(unittest.TestCase):
    def archive_fixture(self, path, model, devices, version, *, channel='stable', requires=(), digest_named=False,
                        component_id=COMPONENT, resource_id=RESOURCE):
        data = (version + '\n').encode()
        file = dict(size=len(data), sha256=sha(data))
        component = dict(schema_version=1, kind='ComponentManifest', id=component_id,
                         version=version, requires=list(requires), files=[dict(
                             source='data', target='etc/boiler-component-version-test', **file)])
        manifest = dict(schema_version=1, kind='DevicePackage', id=model or component_id, version=version,
                        compatibility=dict(device_ids=devices), source=SOURCE,
                        files=[dict(path='data', **file)], components=[component])
        if not model:
            manifest['component_release'] = dict(channel=channel, resource_id=resource_id)
            manifest['minimum_engine_version'] = '0.1.6'
        raw = json.dumps(manifest).encode()
        digest = sha(raw)
        path = path/digest if digest_named else path
        path.mkdir()
        (path/'data').write_bytes(data)
        (path/'manifest.json').write_bytes(raw)
        entry = dict(id=manifest['id'], version=version, compatibility=manifest['compatibility'],
                     source=SOURCE, size=len(raw), sha256=digest, declared_source=SOURCE)
        if not model:
            entry.update(component_release=manifest['component_release'], requires=list(requires),
                         minimum_engine_version=manifest['minimum_engine_version'])
        return entry

    def test_resolves_one_shared_release_with_per_model_compatibility_and_exact_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            model_ids = ('oxp3', 'x2-mini')
            bases = {model: self.archive_fixture(work/model, 'device:' + model, [model], '1.0.0') for model in model_ids}
            inputs = work/'components'
            inputs.mkdir()
            entries = []
            for version, devices, channel, requires in [
                ('2.0.0', list(model_ids), 'stable', ()),
                ('3.0.0', ['oxp3'], 'stable', ()),
                ('4.0.0-beta.1', list(model_ids), 'preview', ()),
                ('5.0.0', list(model_ids), 'stable', ()),
                ('6.0.0', list(model_ids), 'stable', ('fixture:missing',)),
            ]:
                entry = self.archive_fixture(inputs, None, devices, version, channel=channel, requires=requires,
                                             digest_named=True)
                entries.append(entry)
            index = dict(kind='DeviceIndex', schema_version=1, id=builder.DEVICE_REGISTRY, version='1.0.0',
                         generated_at='test', sequence=1, packages=list(bases.values()) + entries,
                         revocations=[entries[3]['sha256']])
            validate_manifest(index, 'DeviceIndex')
            cache = work/'cache'
            cache.mkdir()
            anchor = sha(json.dumps(index).encode())
            (cache/'current.json').write_text(json.dumps({'sha256': anchor}), encoding='utf-8')
            commit = json.loads((ROOT/'adaptations/canonical-model-snapshot.json').read_bytes())['commit']
            self.assertEqual({row['model'] for row in builder.affected_models(ROOT, commit, RESOURCE, '2.0.0')},
                             {'device:oxp3', 'device:x2-mini'})
            with patch.object(builder, 'cached_index', return_value=index):
                for model, expected in [('oxp3', entries[1]), ('x2-mini', entries[0])]:
                    environment = dict(architecture='x86_64', os_id='steamos', os_version='3.10',
                                       device_id=model, kernels=['test-kernel'])
                    target = work/(model + '.zip')
                    result = builder.build(ROOT, work/model, bases[model]['sha256'], cache, environment, target,
                                           version='2.0.0', commit=commit, release_url=URL, local_payloads=inputs)
                    with zipfile.ZipFile(target) as bundle:
                        raw = bundle.read('manifest.json')
                        package = json.loads(raw)
                        self.assertEqual(sha(raw), result['sha256'])
                        self.assertEqual(result['source']['sha256'], sha(target.read_bytes()))
                        self.assertEqual(package['component_resolution'][COMPONENT]['manifest_sha256'], expected['sha256'])
                        self.assertEqual(package['component_resolution'][COMPONENT]['index_sha256'], anchor)
                        component = package['components'][0]
                        self.assertEqual(component['version'], expected['version'])
                        self.assertEqual(bundle.read(component['files'][0]['source']), (expected['version'] + '\n').encode())
                        self.assertEqual(bundle.namelist().count(component['files'][0]['source']), 1)
                        self.assertEqual(package['compatibility']['kernels'], ['test-kernel'])
                    payload = work/('guest-' + model)
                    options = dict(device_package='reviewed', device_manifest_sha256=result['sha256'],
                                   device_trust='signed-index', device_components=[COMPONENT])
                    (payload/'device-packages').mkdir(parents=True)
                    import_local(target, payload/'device-packages/reviewed', developer=True)
                    with patch.object(sys, 'path', [str(ROOT/'components'), *sys.path]):
                        from components.integration import device_plan
                        _, components, _, descriptor = device_plan(payload, options,
                            dict(ID='steamos', VERSION_ID='3.10', KERNELS=['test-kernel']))
                        self.assertEqual(components[0]['version'], expected['version'])
                        self.assertEqual(descriptor['manifest_sha256'], result['sha256'])
                        with self.assertRaisesRegex(ValueError, 'actual image'):
                            device_plan(payload, options, dict(ID='steamos', VERSION_ID='3.10', KERNELS=['wrong-kernel']))

    def test_component_archive_records_the_exact_static_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            entry = self.archive_fixture(work/'release', None, ['oxp3'], '2.0.0')
            result = builder.build_component(work/'release', entry['sha256'], work/'release.zip', release_url=URL)
            self.assertEqual(result['component_release'], entry['component_release'])
            self.assertEqual(result['declared_source'], SOURCE)
            self.assertEqual(result['compatibility'], entry['compatibility'])

    def test_request_pin_and_ambiguous_release_order(self):
        request = dict(id=RESOURCE, version='2.0.0', default=False)
        self.assertEqual(validate_model_requests([request]), {RESOURCE: request})
        with self.assertRaises(ProtocolError):
            validate_model_requests([dict(request, version='latest')])
        base = dict(id=COMPONENT, component_release=dict(resource_id=RESOURCE, channel='stable'),
                    compatibility=dict(device_ids=['oxp3']))
        index = {'packages': [dict(base, version='2.0', sha256='c'*64),
                              dict(base, version='2.0.0', sha256='d'*64)]}
        with self.assertRaisesRegex(ProtocolError, 'Ambiguous'):
            component_releases(index, COMPONENT, {'device_id': 'oxp3'})

    def test_default_selection_is_valid_as_a_combined_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            identities = ('resource:one', 'resource:two')
            entries = [self.archive_fixture(work/f'release-{i}', None, ['oxp3'], '2.0.0',
                                           component_id=identity, resource_id=identity)
                       for i, identity in enumerate(identities)]
            index = {'packages': entries, 'revocations': []}
            requested = validate_model_requests([dict(id=identity) for identity in identities])
            base = work/'base'
            base.mkdir()
            model = dict(id='device:oxp3', components=[], files=[])
            stages = [work/'both', work/'one']
            for stage in stages:
                stage.mkdir()

            def acquire(entry):
                return {'directory': work/f'release-{identities.index(entry["id"])}'}

            with self.assertRaisesRegex(ProtocolError, 'target collision'):
                builder._compose(index, 'a'*64, model, requested, base, stages[0], {'device_id': 'oxp3'}, acquire)
            requested['resource:two'].update(default=False, version='2.0.0')
            manifest = builder._compose(index, 'a'*64, model, requested, base, stages[1], {'device_id': 'oxp3'}, acquire)
            self.assertIs(manifest['components'][0]['default'], True)
            self.assertNotIn('selection_policy', manifest['components'][0])
            self.assertEqual(manifest['components'][1]['selection_policy'], 'explicit-opt-in')
