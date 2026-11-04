import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol.manifest import ProtocolError, validate_manifest, validate_package
from protocol.firmware_contract import HARDWARE, TARGETS, VERIFY, RESTORE


class BE201PackageTests(unittest.TestCase):
    def make_package(self, root):
        resource_path = 'common/network/intel/be201/resource.toon'
        manifest = {'schema_version': 1, 'kind': 'DevicePackage', 'id': 'device:test',
                    'version': '1.0.0', 'source': {'kind': 'local', 'path': 'fixture'},
                    'components': [], 'files': [], 'be201_firmware':
                    {'kind': 'intel-be201-firmware-v1', 'resource': resource_path}}
        def add(name, data):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            row = {'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            manifest['files'].append(row)
            return row
        add(resource_path, b'fixture resource')
        sources = [add('common/network/intel/be201/files/' + str(n), bytes([n])) for n in range(9)]
        licenses = ['common/network/intel/be201/sources/license-' + str(n) for n in range(2)]
        for name in licenses: add(name, b'license')
        resource = {'schema_version': 1, 'kind': 'BoilerResource', 'id': 'resource:intel-be201-firmware',
                    'hardware_requires': HARDWARE, 'compatibility': {'architectures':['x86_64'],
                    'os_ids':['steamos'], 'os_versions':['3.10'], 'kernels':['fixture']},
                    'payload': {'files': sources}, 'license_files': licenses, 'verify': VERIFY, 'restore': RESTORE,
                    'install': {'kind': 'intel-be201-firmware-v1', 'files': [
                        {'source': row['path'], 'target': target, 'mode': 420, 'scope': 'root-slots', 'missing_only': missing}
                        for row, (target, missing) in zip(sources, TARGETS.items())]}}
        (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        return manifest, resource

    def test_package_contract_and_inventory_are_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, resource = self.make_package(root)
            with patch('protocol.resource_pool.decoder') as decoder:
                decoder.return_value.loads.return_value = resource
                validate_package(root, allow_unsigned=True)
                resource['install']['files'][0]['missing_only'] = False
                with self.assertRaises(ProtocolError): validate_package(root, allow_unsigned=True)
                resource['install']['files'][0]['missing_only'] = True
                resource['payload']['files'][0]['sha256'] = '0' * 64
                with self.assertRaises(ProtocolError): validate_package(root, allow_unsigned=True)

    def test_descriptor_rejects_legacy_wifi_and_extra_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest, _ = self.make_package(Path(tmp))
            manifest['legacy_capabilities'] = [{'id':'oxp3:wifi', 'available':True}]
            with self.assertRaises(ProtocolError): validate_manifest(manifest)
            manifest['legacy_capabilities'] = []
            manifest['be201_firmware']['script'] = 'script.sh'
            with self.assertRaises(ProtocolError): validate_manifest(manifest)


if __name__ == '__main__': unittest.main()