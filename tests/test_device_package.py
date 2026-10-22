"""Generic preparation tests require no model-repository checkout."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.manifest import ProtocolError
spec = importlib.util.spec_from_file_location('device_prepare', ROOT/'scripts/prepare-device-package.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)

class DevicePackageTests(unittest.TestCase):
    def fixture(self, base):
            base.mkdir()
            (base/'payload').mkdir()
            (base/'payload/data').write_bytes(b'configuration\n')
            entry = {'path': 'payload/data', 'sha256': hashlib.sha256(b'configuration\n').hexdigest(), 'size': 14}
            manifest = {'schema_version': 1, 'kind': 'DevicePackage', 'id': 'device:fixture', 'version': '0.1.0',
                        'source': {'kind': 'local', 'path': 'fixture'}, 'compatibility': {},
                        'trust': {'status': 'unsigned-development'}, 'components': [], 'files': [entry]}
            (base/'manifest.json').write_text(json.dumps(manifest))
            return manifest

    def test_unsigned_requires_explicit_trust(self):
            with tempfile.TemporaryDirectory() as work:
                source = Path(work)/'source'
                self.fixture(source)
                with self.assertRaises(ProtocolError):
                    prepare.prepare(source, Path(work)/'rejected')
                result = prepare.prepare(source, Path(work)/'accepted', allow_unsigned=True)
                self.assertEqual(result['trust'], 'unsigned-development')
                self.assertFalse(result['official_release'])

    def test_untrusted_digest_cannot_promote_dev_package(self):
            with tempfile.TemporaryDirectory() as work:
                source = Path(work)/'source'
                self.fixture(source)
                digest = hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest()
                with self.assertRaises(ValueError):
                    prepare.prepare(source, Path(work)/'target', manifest_sha256=digest)

    def test_tampered_package_is_not_prepared(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work)/'source'
            self.fixture(source)
            (source/'payload/data').write_bytes(b'tampered')
            with self.assertRaises(ProtocolError):
                prepare.prepare(source, Path(work)/'target', allow_unsigned=True)
            self.assertFalse((Path(work)/'target').exists())

if __name__ == '__main__':
    unittest.main()
