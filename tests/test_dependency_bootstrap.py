import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('boiler_dependency_bootstrap_tests', ROOT/'scripts/bootstrap-dependencies.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class DependencyBootstrapTests(unittest.TestCase):
    def fixture(self, work, *, unsafe=False):
        archive = work/'public.zip'
        member = '../outside.exe' if unsafe else 'runtime/tool.exe'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr(member, b'pinned public tool')
            output.writestr('research/not-exported.py', b'not selected')
        entry = {'member': member, 'target': 'runtime/tool.exe', 'group': 'qemu', 'size': 18,
                 'sha256': hashlib.sha256(b'pinned public tool').hexdigest()}
        lock = {'schema_version': 1, 'kind': 'BoilerDependencyLock', 'versions': {}, 'profiles': {'runtime': ['qemu']},
                'archives': [{'id': 'audited-public-runtime', 'name': archive.name, 'url': 'https://example.invalid/public.zip',
                              'size': archive.stat().st_size, 'sha256': bootstrap.hash_file(archive), 'members': [entry]}]}
        lock_path = work/'lock.json'
        lock_path.write_text(json.dumps(lock))
        return archive, lock_path

    def test_clean_prepare_repeat_and_verify_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            archive, lock = self.fixture(work)
            root = work/'prepared'
            first = bootstrap.prepare(root, 'runtime', public_installer=archive, lock_path=lock, offline=True)
            self.assertEqual(first['created'], 1)
            self.assertEqual((root/'runtime/tool.exe').read_bytes(), b'pinned public tool')
            self.assertFalse((root/'research').exists())
            second = bootstrap.prepare(root, 'runtime', public_installer=archive, lock_path=lock, offline=True)
            self.assertEqual(second['created'], 0)
            verified = bootstrap.prepare(root, 'runtime', public_installer=archive, lock_path=lock, check_only=True)
            self.assertEqual(verified['mode'], 'verified-only')
            self.assertEqual(first['files'], verified['files'])

    def test_tampered_cache_and_existing_target_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            archive, lock = self.fixture(work)
            root = work/'prepared'
            bootstrap.prepare(root, 'runtime', public_installer=archive, lock_path=lock, offline=True)
            target = root/'runtime/tool.exe'
            target.write_bytes(b'admin change')
            with self.assertRaises(ValueError):
                bootstrap.prepare(root, 'runtime', public_installer=archive, lock_path=lock, offline=True)
            self.assertEqual(target.read_bytes(), b'admin change')
            archive.write_bytes(b'corrupt archive')
            with self.assertRaises(ValueError):
                bootstrap.prepare(work/'new-target', 'runtime', public_installer=archive, lock_path=lock, offline=True)
            self.assertFalse((work/'new-target/runtime/tool.exe').exists())

    def test_archive_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            archive, lock = self.fixture(work, unsafe=True)
            with self.assertRaises(ValueError):
                bootstrap.prepare(work/'prepared', 'runtime', public_installer=archive, lock_path=lock, offline=True)
            self.assertFalse((work/'outside.exe').exists())

    def test_offline_missing_input_does_not_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            _, lock = self.fixture(work)
            with self.assertRaises(ValueError):
                bootstrap.prepare(work/'prepared', 'runtime', cache=work/'empty-cache', lock_path=lock, offline=True)

    def test_real_lock_covers_all_required_runtime_and_no_model_payload(self):
        lock = json.loads((ROOT/'scripts/runtime-dependencies.lock.json').read_text())
        public = next(item for item in lock['archives'] if item['id'] == 'audited-public-runtime')
        members = {item['target'] for item in public['members']}
        for required in ('runtime/qemu-min/qemu-system-x86_64.exe', 'runtime/archive/7z.exe', 'runtime/alpine/boot/vmlinuz-virt',
                         'runtime/desync/desync.exe', 'runtime/aria2/aria2c.exe', 'components/refind/manage.py', 'components/refind/refind_x64.efi'):
            self.assertIn(required, members)
        self.assertTrue(all(not name.startswith(('research/', 'cache/integration/', 'components/controls/')) for name in members))
        self.assertEqual(len(lock['offline_packages']), 43)
        inventory = {item['target']: item for item in public['members']}
        for package in lock['offline_packages']:
            name = 'runtime/payload/apks/'+package['name']+'-'+package['version']+'.apk'
            self.assertIn(name, inventory)
            self.assertEqual(inventory[name]['size'], package['bytes'])
        self.assertEqual(lock['versions']['dotnet_sdk'], json.loads((ROOT/'global.json').read_text())['sdk']['version'])


if __name__ == '__main__':
    unittest.main()
