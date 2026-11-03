import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('canonical_common', ROOT/'scripts/prepare-canonical-common.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CanonicalPreparationTests(unittest.TestCase):
    def test_archive_hash_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'source.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('common/test', b'content')
            with self.assertRaisesRegex(ValueError, 'archive SHA-256'):
                module.source_archive(path)
            with mock.patch.object(module, 'ARCHIVE_SHA', hashlib.sha256(path.read_bytes()).hexdigest()):
                archive, names = module.source_archive(path)
                self.assertEqual(names, {'common/test'})
                archive.close()

    def test_unsafe_archive_member_rejected_even_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'source.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('../escape', b'content')
            with mock.patch.object(module, 'ARCHIVE_SHA', hashlib.sha256(path.read_bytes()).hexdigest()):
                with self.assertRaisesRegex(ValueError, 'Unsafe archive member'):
                    module.source_archive(path)

    def test_gyro_archive_and_members_are_independently_pinned(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'public.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('cache/integration/gyro/001.bin', b'public')
            asset = {'size': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            members = {'cache/integration/gyro/001.bin': (6, hashlib.sha256(b'public').hexdigest())}
            with mock.patch.object(module, 'GYRO_ASSET', asset), mock.patch.object(module, 'GYRO_MEMBERS', members):
                self.assertEqual(module.gyro_public_members(ROOT, path), {'cache/integration/gyro/001.bin': b'public'})
                bad = {'cache/integration/gyro/001.bin': (6, hashlib.sha256(b'alter!').hexdigest())}
                with mock.patch.object(module, 'GYRO_MEMBERS', bad):
                    with self.assertRaisesRegex(ValueError, 'member hash differs'):
                        module.gyro_public_members(ROOT, path)
                path.write_bytes(path.read_bytes()+b'x')
                with self.assertRaisesRegex(ValueError, 'locked size and SHA-256'):
                    module.gyro_public_members(ROOT, path)

    def test_locked_input_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'input'; path.write_bytes(b'good')
            rows = {'input': {'size': 4, 'sha256': hashlib.sha256(b'good').hexdigest()}}
            self.assertEqual(module.locked_input(temp, rows, 'input'), b'good')
            path.write_bytes(b'evil')
            with self.assertRaisesRegex(ValueError, 'Locked input differs'):
                module.locked_input(temp, rows, 'input')
            with self.assertRaisesRegex(ValueError, 'not locked'):
                module.locked_input(temp, rows, 'other')

    def test_bmi_source_and_license_match_authenticated_public_archive(self):
        prefix = module.PREFIXES[3]
        patch = 'cache/integration/sources/gyro/inputplumber-0.78.0-oxp3-gyro.patch'
        license_name = 'cache/integration/sources/gyro/InputPlumber-LICENSE'
        source = {prefix+'sources/inputplumber-0.78.0-oxp3-gyro.patch': b'patch',
                  prefix+'licenses/InputPlumber-LICENSE': b'license'}
        original = {patch: b'patch', license_name: b'license'}
        module.validate_gyro_sources(source, original)
        original[patch] = b'changed'
        with self.assertRaisesRegex(ValueError, 'Canonical BMI source differs'):
            module.validate_gyro_sources(source, original)

if __name__ == '__main__': unittest.main()
