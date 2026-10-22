"""Optional integration with the separately checked-out model adapter.

Set BOILER_MODEL_SOURCE to the model repository directory to run. The ordinary
generic-installer suite remains usable with no model checkout or network.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maintenance import Engine

MODEL = Path(os.environ['BOILER_MODEL_SOURCE']) if os.environ.get('BOILER_MODEL_SOURCE') else None


@unittest.skipUnless(MODEL and (MODEL/'adapter.py').is_file(), 'Set BOILER_MODEL_SOURCE to run the independent model adoption integration')
class AdoptionBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != 'nt' and os.geteuid() != 0:
            raise unittest.SkipTest('Actual adoption requires root on Linux; no ownership checks are bypassed')
        spec = importlib.util.spec_from_file_location('boiler_model_adoption_integration', MODEL/'adapter.py')
        cls.adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.adapter)

    def fixture(self, root, source_format):
        def write(name, data, mode=0o644):
            path = root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.chmod(path, mode)
        def snapshot(name, data):
            info = (root/name).stat()
            return {'exists': True, 'data': base64.b64encode(data).decode(),
                    'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid}
        key = (ROOT/'protocol/trusted-public-key.pem').read_bytes()
        key_name = 'usr/local/lib/oxp3-maintenance/releases/1.0.6-test/trusted-public-key.pem'
        runtime_name = 'usr/local/lib/oxp3-maintenance/releases/1.0.6-test/entry.py'
        write(key_name, key)
        write('usr/local/lib/oxp3-maintenance/current-release.json', json.dumps({'version': '1.0.6', 'maintenance_runtime': '1.0.6-test'}).encode())
        write('etc/passwd', b'root:x:0:0:root:/root:/bin/sh\ndeck:x:1000:1000:deck:/home/deck:/bin/sh\n')
        write('etc/model.conf', b'legacy installed configuration\n')
        write(runtime_name, b'print("legacy upgraded runtime")\n')
        targets = {'etc/model.conf': ('input', b'original configuration\n'), runtime_name: ('_maintenance', b'print("original runtime")\n')}
        originals, paths = {}, {}
        for name, (component, original_data) in targets.items():
            originals['A:'+name] = snapshot(name, original_data)
            paths[name] = {'component': component, 'snapshot': snapshot(name, (root/name).read_bytes())}
        if source_format == 'maintenance':
            value = {'format': 1, 'originals': originals, 'slots': {'A': {'current': {'id': 4, 'release': {'version': '1.0.6'}, 'paths': paths}}}}
            write('home/.oxp3-maintenance/index.json', json.dumps(value).encode(), 0o600)
        else:
            result = {'schema': 1, 'slot': 'A', 'release': {'version': '1.0.6'},
                      'components': {'input': {'status': 'files_installed'}, 'maintenance-tool': {'status': 'files_installed'}}}
            write('home/.oxp3-installer/integration/A/result.json', json.dumps(result).encode(), 0o600)
            for name, (component, _) in targets.items():
                label = 'maintenance-tool' if component == '_maintenance' else component
                journal = {'phase': 'installed', 'allowed': [name], 'original': {name: originals['A:'+name]},
                           'applied': {name: paths[name]['snapshot']['data']}, 'metadata': {name: {}}}
                write('home/.oxp3-installer/integration/A/'+label+'/state.json', json.dumps(journal).encode(), 0o600)
        def reader(name, **kwargs):
            # Fixture transport only: the real model CLI uses its no-follow,
            # root-owned descriptor reader. Here Engine still checks all live
            # snapshots and its real canonical public-key anchor.
            value = snapshot(name, (root/name).read_bytes())
            if name.endswith(('index.json', 'state.json', 'result.json')):
                value['mode'] = 0o600
            return value
        return key, key_name, runtime_name, targets, reader

    def test_translate_adopt_verify_and_restore_preserves_old_trust_and_runtime(self):
        for source_format in ('maintenance', 'installer'):
            with self.subTest(source_format=source_format), tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                root = work/'target'
                root.mkdir()
                key, key_name, runtime_name, targets, reader = self.fixture(root, source_format)
                original_files = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}
                receipt = self.adapter.collect_adoption(root, 'A', key, reader=reader)
                receipt_path = work/'adoption.private.json'
                receipt_path.write_text(json.dumps(receipt))
                os.chmod(receipt_path, 0o600)
                engine = Engine(root)
                result = engine.adopt(receipt_path)
                self.assertTrue(result['migration_complete'])
                self.assertTrue(result['preserve_legacy'])
                for name, data in original_files.items():
                    self.assertEqual((root/name).read_bytes(), data, 'Adoption must not modify old runtime, files, state or signing key')
                self.assertEqual((root/key_name).read_bytes(), key)
                self.assertEqual((root/runtime_name).read_bytes(), b'print("legacy upgraded runtime")\n')
                for component in receipt['components']:
                    self.assertTrue(engine.verify(component['id'])['ok'])
                repeated = engine.adopt(receipt_path)
                self.assertEqual(repeated['transactions'], result['transactions'], 'Exact-receipt retry must resume ownership without creating new generations')
                for transaction in reversed(result['transactions']):
                    engine.restore(transaction)
                for name, (_, original_data) in targets.items():
                    self.assertEqual((root/name).read_bytes(), original_data)
                for name, data in original_files.items():
                    if name not in targets:
                        self.assertEqual((root/name).read_bytes(), data, 'Explicit restore must preserve old journal history and signing trust')
                self.assertEqual(hashlib.sha256((root/key_name).read_bytes()).hexdigest(), hashlib.sha256(key).hexdigest())


if __name__ == '__main__':
    unittest.main()
