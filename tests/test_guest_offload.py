from pathlib import Path
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'guest'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'components'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adaptations/common/scripts'))
from offload_mounts import etc_overlay_options, mount_opt, mount_etc


class SourceOverlayChecks(unittest.TestCase):
    def test_actual_38_and_310_vendor_profiles_are_accepted(self):
        base = 'Where=/etc\nWhat=overlay\nType=overlay\nOptions=lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper,workdir=/var/lib/overlays/etc/work'
        self.assertTrue(etc_overlay_options(base))
        self.assertTrue(etc_overlay_options(base + ',index=off'))

    def test_unknown_mount_paths_and_flags_remain_rejected(self):
        base = 'Where=/etc\nWhat=overlay\nType=overlay\nOptions=lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper,workdir=/var/lib/overlays/etc/work'
        for text in (base.replace('upperdir=/var', 'upperdir=/unknown'), base + ',redirect_dir=on', base + ',index=on', base + '\nOptions=lowerdir=/tmp'):
            with self.assertRaises(RuntimeError): etc_overlay_options(text)


@unittest.skipUnless(os.name == 'posix', 'Guest overlay paths require Linux')
class EtcPreflightChecks(unittest.TestCase):
    def fixture(self, root):
        unit = root / 'usr/lib/systemd/system/etc.mount'
        unit.parent.mkdir(parents=True)
        unit.write_text('[Mount]\nWhere=/etc\nWhat=overlay\nType=overlay\n'
                        'Options=lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper,'
                        'workdir=/var/lib/overlays/etc/work,index=off\n')
        (root / 'etc').mkdir()

    @staticmethod
    def directory_fd(path):
        if not Path(path).is_dir():
            raise FileNotFoundError(str(path))
        return 999

    def test_new_image_only_readonly_preflight_accepts_missing_upper_and_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            fake = types.SimpleNamespace(directory_fd=self.directory_fd)
            with patch.dict(sys.modules, {'file_transaction': fake}), \
                    patch('offload_mounts.os.close'), patch('offload_mounts.subprocess.run') as run:
                mounts = []
                self.assertFalse(mount_etc(root, mounts, readonly=True, allow_uninitialized=True))
                self.assertEqual(mounts, [])
                self.assertFalse((root / 'var').exists())
                run.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, 'Persistent /etc overlay is missing'):
                    mount_etc(root, [], readonly=True)

    def test_partial_initialization_remains_an_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            (root / 'var/lib/overlays/etc/work').mkdir(parents=True)
            fake = types.SimpleNamespace(directory_fd=self.directory_fd)
            with patch.dict(sys.modules, {'file_transaction': fake}), \
                    patch('offload_mounts.os.close'), patch('offload_mounts.subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'Persistent /etc overlay is missing'):
                    mount_etc(root, [], readonly=True, allow_uninitialized=True)
                run.assert_not_called()

    def test_allow_uninitialized_does_not_bypass_normal_write_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            def ensure(path, mode):
                Path(path).mkdir(parents=True, exist_ok=True)
            fake = {'file_transaction': types.SimpleNamespace(directory_fd=self.directory_fd),
                    'safe_directories': types.SimpleNamespace(ensure_directory=ensure)}
            with patch.dict(sys.modules, fake), patch('offload_mounts.os.close'), \
                    patch('offload_mounts.subprocess.run') as run:
                mounts = []
                self.assertTrue(mount_etc(root, mounts, readonly=False, allow_uninitialized=True))
                self.assertEqual(mounts, [root / 'etc'])
                self.assertTrue((root / 'var/lib/overlays/etc/upper').is_dir())
                self.assertTrue((root / 'var/lib/overlays/etc/work').is_dir())
                self.assertEqual(run.call_count, 2)

    @unittest.skipUnless(os.name == 'posix', 'No-follow directory checks require Linux')
    def test_preflight_rejects_redirected_target_and_overlay_ancestors(self):
        from file_transaction import directory_fd
        for path in ('etc', 'var'):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary); root = base / 'root'; outside = base / 'outside'
                self.fixture(root)
                outside.mkdir()
                if path == 'etc':
                    (root / 'etc').rmdir()
                (root / path).symlink_to(outside, target_is_directory=True)
                with patch('offload_mounts.subprocess.run') as run:
                    with self.assertRaises(OSError):
                        mount_etc(root, [], readonly=True, allow_uninitialized=True)
                    run.assert_not_called()


class OffloadBoundaryChecks(unittest.TestCase):
    def fixture(self, root):
        unit = root / 'usr/lib/systemd/system/opt.mount'
        unit.parent.mkdir(parents=True)
        unit.write_text('[Mount]\nWhat=/home/.steamos/offload/opt\nWhere=/opt\nOptions=bind\n')
        (root / 'opt').mkdir()
        return unit

    def test_custom_effective_opt_mount_is_rejected_before_mount(self):
        for relative in ('usr/lib/systemd/system/opt.mount.d', 'etc/systemd/system/opt.mount.d'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.fixture(root)
                (root / 'home/.steamos/offload/opt').mkdir(parents=True)
                custom = root / relative / 'custom.conf'
                custom.parent.mkdir(parents=True)
                custom.write_text('[Mount]\nWhat=/other/path\n')
                with patch('offload_mounts.subprocess.run') as command:
                    with self.assertRaisesRegex(RuntimeError, 'Custom /opt'):
                        mount_opt(root, [], readonly=True)
                    command.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'No-follow mount directory checks require Linux')
    def test_readonly_opt_rejects_redirected_ancestor_before_mount(self):
        for location in ('home/.steamos', 'opt'):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary); root = base / 'root'; outside = base / 'outside'
                self.fixture(root)
                (outside / 'offload/opt').mkdir(parents=True)
                if location == 'home/.steamos':
                    (root / 'home').mkdir()
                    (root / location).symlink_to(outside, target_is_directory=True)
                else:
                    (root / 'home/.steamos/offload/opt').mkdir(parents=True)
                    (root / 'opt').rmdir()
                    (root / 'opt').symlink_to(outside, target_is_directory=True)
                mounts = []
                with patch('offload_mounts.subprocess.run') as command:
                    with self.assertRaises(RuntimeError): mount_opt(root, mounts, readonly=True)
                    command.assert_not_called()
                self.assertEqual(mounts, [])


if __name__ == '__main__': unittest.main()
