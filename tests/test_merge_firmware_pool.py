import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('merge_firmware', ROOT/'scripts/resources/merge_firmware_resource_pool.py')
merge = importlib.util.module_from_spec(spec); spec.loader.exec_module(merge)
from protocol.resource_pool import decoder, validate_pool
from protocol.firmware_contract import IDENTITY, KIND, VERIFY, RESTORE, TARGETS, HARDWARE, validate_contract


def pool(root, wifi=False, *, license_path=None, wrong_model=False, bad_policy=False):
    root.mkdir(); toon = decoder(); files = {}
    def write(name, raw): files[name] = raw
    def document(name, value): write(name, (toon.dumps(value)+'\n').encode())
    payload, targets = [], []
    rows = list(TARGETS.items()) if wifi else [('etc/base.conf', False)]
    for index, (target, missing) in enumerate(rows):
        name = ('firmware/' if wifi else 'base/')+str(index)
        data = b'shared-ddc' if wifi and target.endswith('.ddc') else (b'wifi-' if wifi else b'base-')+str(index).encode()
        write(name, data); payload.append(dict(path=name, size=len(data), sha256=merge.sha(data)))
        row = dict(source=name, target=target, mode=420, scope='root-slots')
        if wifi: row['missing_only'] = missing
        targets.append(row)
    license_path = license_path or ('licenses/wifi' if wifi else 'licenses/shared')
    write(license_path, b'wifi-license' if wifi else b'base-license')
    provenance = 'provenance/wifi.json' if wifi else 'provenance/base.json'; write(provenance, b'{"source":"public-fixture"}\n')
    resource = dict(schema_version=1, kind='BoilerResource', id=IDENTITY if wifi else 'resource:base', version='1.0.0', dependencies=[],
        hardware_requires=copy.deepcopy(HARDWARE) if wifi else {},
        compatibility=dict(architectures=['x86_64'], os_ids=['steamos'], os_versions=['3.10'], kernels=['fixture']) if wifi else {},
        payload=dict(files=payload), install=dict(kind=KIND if wifi else 'files-v1', files=targets),
        verify=VERIFY if wifi else dict(kind='sha256-v1'), restore=RESTORE, license_files=[license_path],
        provenance=dict(source_lock=provenance, files=[dict(payload=r['path'], member='origin/'+r['path'], sha256=r['sha256']) for r in payload]))
    if bad_policy: resource['install']['files'][0]['missing_only'] = False
    resource_path = 'wifi/resource.toon' if wifi else 'base/resource.toon'
    document(resource_path, resource); write(resource_path.replace('.toon', '.json'), merge.json_bytes(resource))
    models = []
    for name in ('oxp3', 'x2-mini'):
        identity = 'device:'+name
        if wrong_model and name == 'x2-mini': identity = 'device:other'
        model = dict(schema_version=1, kind='BoilerResourceModel', id=identity, name=('Wifi must not replace base' if wifi else 'Original '+name),
            complete=False, hardware_matching={'advisory_only': True} if wifi else {'mode': 'advisory-exact-dmi-v1', 'pairs': [{'manufacturer': 'Fixture', 'model': name}]},
            parameters={} if wifi else {'hdr': {'peak_nits': 900}},
            resources=[dict(resource=resource['id'], enabled=False, parameters={})],
            pending=[dict(component='physical-wifi-acceptance' if wifi else 'existing-work', reason='preserve this exact record', status='pending')])
        path = 'brands/onexplayer/models/'+name+'.toon'; document(path, model)
        models.append(dict(id=identity, path=path, sha256=merge.sha(files[path])))
    catalog = dict(schema_version=1, kind='BoilerResourceCatalog', trust='unsigned-development', official_release=False,
        resources=[dict(id=resource['id'], version=resource['version'], path=resource_path, sha256=merge.sha(files[resource_path]))], models=models,
        files=[dict(path=n, size=len(d), sha256=merge.sha(d)) for n, d in sorted(files.items())])
    document('catalog.toon', catalog); write('catalog.json', merge.json_bytes(catalog))
    for name, raw in files.items():
        path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    return merge.sha(files['catalog.toon'])


class MergeFirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)
        self.base = self.root/'base'; self.wifi = self.root/'wifi'; self.base_sha = pool(self.base); self.wifi_sha = pool(self.wifi, True)
    def build(self, name='output', **kwargs):
        return merge.build(self.base, self.wifi, self.root/name, base_sha256=self.base_sha, wifi_sha256=self.wifi_sha, **kwargs)
    def snapshot(self, root): return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def test_deterministic_shared_resource_and_identical_ddc_sources_have_one_copy(self):
        before = [self.snapshot(p) for p in (self.base, self.wifi)]
        result = self.build(); self.build('second')
        self.assertEqual(self.snapshot(self.root/'output'), self.snapshot(self.root/'second'))
        self.assertEqual(result['resources'], 2); self.assertEqual(result['deduplicated_members'], 1)
        catalog = validate_pool(self.root/'output'); resource = next(r for r in catalog['resources'] if r['id'] == IDENTITY)
        validate_contract(resource); self.assertEqual(len(resource['payload']['files']), 8); self.assertEqual(len(resource['install']['files']), 9)
        ddc = [r for r in resource['install']['files'] if r['target'].endswith('.ddc')]
        self.assertEqual(len({r['source'] for r in ddc}), 1)
        for model in catalog['models']: self.assertEqual(sum(r['resource'] == IDENTITY for r in model['resources']), 1)
        self.assertEqual(before, [self.snapshot(p) for p in (self.base, self.wifi)])

    def test_base_identity_parameters_existing_todos_and_special_contract_preserved(self):
        self.build(); before = validate_pool(self.base); after = validate_pool(self.root/'output')
        self.assertEqual(before['resources'][0], after['resources'][0])
        for original in before['models']:
            changed = next(m for m in after['models'] if m['id'] == original['id'])
            for key in ('id', 'name', 'hardware_matching', 'parameters', 'complete'): self.assertEqual(original[key], changed[key])
            self.assertEqual(changed['resources'][:-1], original['resources']); self.assertEqual(changed['pending'][:-1], original['pending'])
            self.assertEqual(changed['pending'][-1]['component'], 'physical-wifi-acceptance')
            self.assertFalse(changed['resources'][-1]['enabled']); self.assertFalse(changed['resources'][-1]['default'])
        fixed = next(r for r in after['resources'] if r['id'] == IDENTITY)
        self.assertEqual(fixed['install']['kind'], KIND); self.assertEqual(fixed['verify'], VERIFY); self.assertEqual(fixed['restore'], RESTORE)

    def test_licenses_origin_locks_and_deduplication_evidence_retained(self):
        self.build(); output = self.root/'output'
        for source in (self.base, self.wifi):
            for p in source.rglob('*'):
                if p.is_file() and p.relative_to(source).as_posix().startswith(('licenses/', 'provenance/')):
                    self.assertEqual(p.read_bytes(), (output/p.relative_to(source)).read_bytes())
        proof = json.loads((output/'provenance/merge-fixed-wifi-v1.json').read_bytes())
        self.assertEqual([r['catalog_sha256'] for r in proof['inputs']], [self.base_sha, self.wifi_sha])
        self.assertEqual(len(proof['payload_deduplication']), 1)
        entry = proof['payload_deduplication'][0]; self.assertEqual(merge.sha((output/entry['canonical']).read_bytes()), entry['sha256'])
        fixed = next(r for r in validate_pool(output)['resources'] if r['id'] == IDENTITY)
        self.assertTrue(all((output/r['payload']).is_file() for r in fixed['provenance']['files']))

    def test_digest_or_corrupted_source_refused_before_creating_output(self):
        with self.assertRaisesRegex(ValueError, 'Catalog hash mismatch'):
            merge.build(self.base, self.wifi, self.root/'wrong', base_sha256='0'*64, wifi_sha256=self.wifi_sha)
        self.assertFalse((self.root/'wrong').exists())
        (self.wifi/'firmware/0').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'inventory mismatch'): self.build()
        self.assertFalse((self.root/'output').exists())

    def test_existing_output_and_output_inside_input_preserved(self):
        output = self.root/'output'; output.mkdir(); (output/'sentinel').write_bytes(b'preserve')
        with self.assertRaises(FileExistsError): self.build()
        self.assertEqual((output/'sentinel').read_bytes(), b'preserve')
        with self.assertRaisesRegex(ValueError, 'inside an input'):
            merge.build(self.base, self.wifi, self.base/'nested', base_sha256=self.base_sha, wifi_sha256=self.wifi_sha)
        self.assertFalse((self.base/'nested').exists())

    def test_different_source_bytes_at_same_license_path_rejected_without_partial_output(self):
        wifi = self.root/'conflicting-wifi'; digest = pool(wifi, True, license_path='licenses/shared')
        with self.assertRaisesRegex(ValueError, 'Conflicting source bytes'):
            merge.build(self.base, wifi, self.root/'conflict-output', base_sha256=self.base_sha, wifi_sha256=digest)
        self.assertFalse((self.root/'conflict-output').exists()); self.assertFalse(list(self.root.glob('.merge-fixed-wifi-*')))

    def test_wrong_model_set_and_changed_firmware_policy_fail_closed(self):
        wifi = self.root/'wrong-model'; digest = pool(wifi, True, wrong_model=True)
        with self.assertRaisesRegex(ValueError, 'model sets must match'):
            merge.build(self.base, wifi, self.root/'bad-model', base_sha256=self.base_sha, wifi_sha256=digest)
        bad = self.root/'bad-policy'; digest = pool(bad, True, bad_policy=True)
        with self.assertRaisesRegex(ValueError, 'missing-only policy'):
            merge.build(self.base, bad, self.root/'bad-policy-output', base_sha256=self.base_sha, wifi_sha256=digest)
        self.assertFalse((self.root/'bad-model').exists()); self.assertFalse((self.root/'bad-policy-output').exists())


if __name__ == '__main__': unittest.main()
