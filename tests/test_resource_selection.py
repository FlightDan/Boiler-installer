import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol.resource_selection import ROOT, resource_catalog, select_resources
from protocol.__main__ import main
from protocol.resource_pool import decoder, validate_pool


class ResourceSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # No previously built cache is needed for source tests. The fixture is
        # independently inventoried and goes through the real TOON validator.
        cls.temp = tempfile.TemporaryDirectory(prefix='boiler-resource-selection-')
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)/'pool'; root.mkdir()
        toon = decoder(); files = {}
        def write(name, data):
            path = root/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); files[name] = data
        def document(name, value): write(name, (toon.dumps(value)+'\n').encode())
        write('licenses/example', b'fixture license')
        resources = []
        for name, dependencies in (('base', []), ('dependent', ['resource:base'])):
            identity = 'resource:'+name; payload = 'devices/fixture/'+name+'/payload'
            data = ('fixture '+name).encode(); write(payload, data)
            item = dict(schema_version=1, kind='BoilerResource', id=identity, version='1.0.0',
                dependencies=dependencies, payload=dict(files=[dict(path=payload,size=len(data),sha256=hashlib.sha256(data).hexdigest())]),
                install=dict(kind='files-v1',files=[dict(source=payload,target='etc/'+name,mode=420,scope='root-slots')]),
                verify=dict(kind='sha256-v1'),restore=dict(kind='transaction-backup-v1'),license_files=['licenses/example'])
            path = 'devices/fixture/'+name+'/resource.toon'; document(path,item)
            resources.append(dict(id=identity,version='1.0.0',path=path,sha256=hashlib.sha256(files[path]).hexdigest()))
        models=[]
        for identity in ('device:oxp3','device:x2-mini'):
            name=identity.split(':')[1]; path='models/'+name+'.toon'
            model=dict(schema_version=1,kind='BoilerResourceModel',id=identity,name=name,
                hardware_matching=dict(mode='advisory-only'),resources=[dict(resource=r['id'],parameters={}) for r in resources],
                pending=[dict(component='unimplemented')])
            document(path,model);models.append(dict(id=identity,path=path,sha256=hashlib.sha256(files[path]).hexdigest()))
        catalog=dict(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False,
            resources=resources,models=models,files=[dict(path=n,size=len(d),sha256=hashlib.sha256(d).hexdigest()) for n,d in sorted(files.items())])
        document('catalog.toon',catalog);write('catalog.json',(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n').encode())
        digest=hashlib.sha256((root/'catalog.toon').read_bytes()).hexdigest()
        cls.pool_patch=patch('protocol.resource_selection._pool',side_effect=lambda _: (root,validate_pool(root),digest))
        cls.pool_patch.start();cls.addClassCleanup(cls.pool_patch.stop)
        cls.catalog = resource_catalog()
        cls.model = cls.catalog['models'][0]
        cls.choices = [row['id'] for row in cls.model['components'] if row['selectable']]

    def choose(self, **kwargs):
        return select_resources(self.model['id'], self.choices[:1], self.catalog['catalog_sha256'], **kwargs)

    def test_no_self_declared_trust(self):
        self.assertFalse(self.catalog['deployment_available'])
        self.assertEqual(self.catalog['trust'], 'untrusted-local-candidate')
        with self.assertRaisesRegex(ValueError, 'consent'):
            self.choose()
        with self.assertRaisesRegex(ValueError, 'consent'):
            self.choose(trust_local_test='local-test')

    def test_real_selection_and_cli(self):
        result = self.choose(trust_local_test=True)
        self.assertEqual(result['trust'], 'local-test')
        self.assertEqual(result['components'], self.choices[:1])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(['resource-selection', self.model['id'], self.catalog['catalog_sha256'],
                           *self.choices[:1], '--trust-local-test'])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())['result'], result)

    def test_changed_catalog_unknown_and_duplicate_choices(self):
        with self.assertRaisesRegex(ValueError, 'changed'):
            select_resources(self.model['id'], self.choices[:1], '0' * 64, trust_local_test=True)
        for choices in ([self.choices[0]] * 2, [], ['resource:unimplemented']):
            with self.assertRaises(ValueError):
                select_resources(self.model['id'], choices, self.catalog['catalog_sha256'], trust_local_test=True)

    def test_real_dependency_closure(self):
        dependent = next(row for row in self.model['components'] if row['selectable'] and row['dependencies'])
        result = select_resources(self.model['id'], [dependent['id']], self.catalog['catalog_sha256'], trust_local_test=True)
        self.assertEqual(result['components'][-1], dependent['id'])
        self.assertTrue(set(dependent['dependencies']) < set(result['components']))

    def test_model_cannot_authorize_other_models_resources(self):
        fake = copy.deepcopy(self.catalog)
        fake['models'][0]['components'] = []
        with patch('protocol.resource_selection.resource_catalog', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'not selectable'):
                self.choose(trust_local_test=True)

    def test_unavailable_and_dependency_cycle_rejected(self):
        fake = copy.deepcopy(self.catalog)
        fake['models'][0]['components'][0]['selectable'] = False
        with patch('protocol.resource_selection.resource_catalog', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'not selectable'):
                self.choose(trust_local_test=True)
        fake['models'][0]['components'][0]['selectable'] = True
        fake['models'][0]['components'][0]['dependencies'] = self.choices[:1]
        with patch('protocol.resource_selection.resource_catalog', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'Cyclic'):
                self.choose(trust_local_test=True)

    def test_no_default_installability_or_legacy_bundle(self):
        self.assertEqual(len(self.catalog['models']), 2)
        for model in self.catalog['models']:
            self.assertTrue(model['pending'])
            self.assertTrue(all(row['default'] is False for row in model['components']))
            self.assertTrue(all('Local-test' in row['reason'] for row in model['components'] if row['selectable']))
        self.assertNotIn('directory', self.catalog)


if __name__ == '__main__':
    unittest.main()
