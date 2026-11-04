"""Synthetic model-package to fixed-resource lifecycle checks."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from test_firmware_resources import ENV, FACTS, synthetic_pool
from maintenance.be201_model_resource import DESCRIPTOR, RESOURCE, model_resource_pool
from maintenance.resource_runtime import ResourceError, ResourceSession
from protocol.firmware_contract import IDENTITY, TARGETS
from protocol.resource_pool import decoder


class ModelFirmwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = synthetic_pool(self.base / 'model')
        for name in ('catalog.json', 'catalog.toon', 'model.toon'):
            (self.root / name).unlink()
        toon = decoder()
        record = toon.loads((self.root / 'resource.toon').read_text(encoding='utf-8'), strict=True)
        for row in record['payload']['files']:
            old = row['path']
            new = 'common/network/intel/be201/files/' + old.replace('/', '-')
            destination = self.root / new
            destination.parent.mkdir(parents=True, exist_ok=True)
            (self.root / old).replace(destination)
            row['path'] = new
            for target in record['install']['files']:
                if target['source'] == old:
                    target['source'] = new
        (self.root / 'firmware').rmdir()
        (self.root / 'license').unlink()
        record['license_files'] = ['common/network/intel/be201/sources/one',
                                   'common/network/intel/be201/sources/two']
        for name in record['license_files']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture license')
        resource = self.root / RESOURCE
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_text(toon.dumps(record) + '\n', encoding='utf-8')
        (self.root / 'resource.toon').unlink()
        files = []
        for path in self.root.rglob('*'):
            if path.is_file():
                data = path.read_bytes()
                files.append(dict(path=path.relative_to(self.root).as_posix(),
                                  size=len(data), sha256=hashlib.sha256(data).hexdigest()))
        self.manifest = dict(schema_version=1, kind='DevicePackage', id='device:fixture',
                             version='0.1.0', source={'kind': 'local', 'path': 'fixture'},
                             compatibility={}, trust={'status': 'unsigned-development'},
                             components=[], files=files, be201_firmware=DESCRIPTOR)
        self.write_manifest()
        self.slots = {}
        for name in ('A', 'B'):
            root = self.base / name
            root.mkdir()
            self.slots[name] = dict(root=root, environment=dict(ENV), hardware=dict(FACTS))
        self.home = self.base / 'home'
        self.home.mkdir()

    def write_manifest(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def test_explicit_trust_and_inventory_are_required(self):
        with self.assertRaises(ResourceError):
            model_resource_pool(self.root)
        self.assertTrue(model_resource_pool(self.root, trust_local_test=True).local_test)
        (self.root / 'common/network/intel/be201/files/firmware-0').write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            model_resource_pool(self.root, trust_local_test=True)

    def test_unknown_facts_stop_before_slot_writes(self):
        pool = model_resource_pool(self.root, trust_local_test=True)
        self.slots['B']['hardware'] = {}
        session = ResourceSession(pool, self.slots, self.home)
        with self.assertRaises(ResourceError):
            session.install([{'resource': IDENTITY}], [IDENTITY])
        self.assertFalse(any((self.slots['A']['root'] / p).exists() for p in TARGETS))

    def test_two_slot_install_verify_and_restore(self):
        pool = model_resource_pool(self.root, trust_local_test=True)
        session = ResourceSession(pool, self.slots, self.home)
        target = next(path for path, missing in TARGETS.items() if not missing)
        old = self.slots['A']['root'] / target
        old.parent.mkdir(parents=True)
        old.write_bytes(b'prior firmware')
        refs = [{'resource': IDENTITY}]
        receipt = session.install(refs, [IDENTITY])
        self.assertEqual(len(receipt['transactions']), 2)
        self.assertTrue(all(row['ok'] for row in session.verify([IDENTITY])))
        for slot in ('A', 'B'):
            session.restore(receipt, slot)
        self.assertEqual(old.read_bytes(), b'prior firmware')
        self.assertFalse((self.slots['B']['root'] / target).exists())
