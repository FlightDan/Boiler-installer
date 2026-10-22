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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maintenance import Engine, MaintenanceError
from maintenance import engine as implementation

MODEL = Path(os.environ['BOILER_MODEL_SOURCE']) if os.environ.get('BOILER_MODEL_SOURCE') else None


class AdoptionReceiptIntakeTests(unittest.TestCase):
    """The privileged intake boundary needs no external model checkout."""
    def setUp(self):
        if os.name != 'nt' and os.geteuid() != 0:
            self.skipTest('Adoption requires Linux root; ownership checks remain enabled')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.root = self.work/'target'
        (self.root/'etc').mkdir(parents=True)
        target = self.root/'etc/model.conf'
        target.write_bytes(b'applied configuration\n')
        target.chmod(0o644)
        info = target.stat()
        def snapshot(data):
            return dict(exists=True, data=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest(),
                        mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
        key = (ROOT/'protocol/trusted-public-key.pem').read_bytes()
        name = 'usr/local/lib/legacy/trusted-public-key.pem'
        key_path = self.root/name
        key_path.parent.mkdir(parents=True)
        key_path.write_bytes(key)
        key_path.chmod(0o644)
        anchor = dict(path=name, size=len(key), sha256=hashlib.sha256(key).hexdigest(), role='trusted-public-key')
        receipt = dict(schema_version=1, kind='LegacyAdoptionReceipt', device_id='oxp3', slot='B',
                       preserve_legacy=True, translation_complete=True, trusted_public_key=anchor, evidence=[anchor],
                       components=[dict(id='oxp3:input', version='1.0.6', operations=[dict(target='etc/model.conf',
                                        before=snapshot(b'original configuration\n'), after=snapshot(target.read_bytes()))])])
        self.raw = json.dumps(receipt).encode()
        self.receipt = self.work/'adoption.private.json'
        self.receipt.write_bytes(self.raw)
        self.receipt.chmod(0o600)
        self.engine = Engine(self.root)

    def test_private_receipt_adopts_original_bytes_and_uses_the_same_digest(self):
        result = self.engine.adopt(self.receipt)
        self.assertTrue(result['migration_complete'])
        journal = json.loads(self.engine._journal(result['transactions'][0]).read_text())
        self.assertEqual(journal['adoption_receipt'], hashlib.sha256(self.raw).hexdigest())
        self.assertTrue(self.engine.verify('oxp3:input')['ok'])
        self.engine.restore(result['transactions'][0])
        self.assertEqual((self.root/'etc/model.conf').read_bytes(), b'original configuration\n')

    @unittest.skipIf(os.name == 'nt', 'Protected ancestor traversal requires Linux')
    def test_root_owned_private_leaf_under_a_writable_ancestor_is_refused(self):
        parent = self.work/'writable'
        parent.mkdir()
        parent.chmod(0o777)
        leaf = parent/'root-private'
        leaf.mkdir(mode=0o700)
        receipt = leaf/'receipt.json'
        receipt.write_bytes(self.raw)
        receipt.chmod(0o600)
        with self.assertRaisesRegex(MaintenanceError, 'writable ancestor'):
            self.engine.adopt(receipt)
        self.assertEqual(self.engine._state()['components'], {})
        self.assertEqual(list(self.engine.journals.glob('*.json')), [])

    @unittest.skipIf(os.name == 'nt', 'Descriptor no-follow and FIFO checks require Linux')
    def test_receipt_intake_refuses_ancestor_links_leaf_links_and_fifos(self):
        protected = self.work/'protected'
        protected.mkdir(mode=0o700)
        receipt = protected/'receipt.json'
        receipt.write_bytes(self.raw)
        receipt.chmod(0o600)
        alias = self.work/'alias'
        alias.symlink_to(protected, target_is_directory=True)
        linked = self.work/'linked.json'
        linked.symlink_to(receipt)
        fifo = self.work/'fifo'
        os.mkfifo(fifo, 0o600)
        for candidate in (alias/'receipt.json', linked, fifo):
            with self.subTest(candidate=candidate), self.assertRaises(MaintenanceError):
                self.engine.adopt(candidate)
        self.assertEqual(self.engine._state()['components'], {})

    @unittest.skipIf(os.name == 'nt', 'Receipt pathname rebinding check requires Linux file descriptors')
    def test_receipt_rebinding_after_open_cannot_change_parsed_bytes_or_digest(self):
        original_open = os.open
        rebound = []
        changed = json.loads(self.raw)
        replacement = b'untrusted replacement original\n'
        changed['components'][0]['operations'][0]['before'].update(
            data=base64.b64encode(replacement).decode(), sha256=hashlib.sha256(replacement).hexdigest())
        def opening(path, flags, *args, **kwargs):
            descriptor = original_open(path, flags, *args, **kwargs)
            if path == self.receipt.name and kwargs.get('dir_fd') is not None and not rebound:
                rebound.append(True)
                self.receipt.rename(self.work/'original-held-inode.json')
                self.receipt.write_text(json.dumps(changed))
                self.receipt.chmod(0o600)
            return descriptor
        with mock.patch.object(implementation.os, 'open', side_effect=opening):
            result = self.engine.adopt(self.receipt)
        self.assertTrue(rebound)
        self.assertNotEqual(self.receipt.read_bytes(), self.raw)
        journal = json.loads(self.engine._journal(result['transactions'][0]).read_text())
        self.assertEqual(journal['adoption_receipt'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(base64.b64decode(journal['operations'][0]['before']['data']), b'original configuration\n')


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
