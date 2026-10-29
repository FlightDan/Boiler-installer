from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'guest'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'components'))
from offload_mounts import etc_overlay_options, mount_opt


class SourceOverlayChecks(unittest.TestCase):
    def test_actual_38_and_310_vendor_profiles_are_accepted(self):
        base = 'Where=/etc\nWhat=overlay\nType=overlay\nOptions=lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper,workdir=/var/lib/overlays/etc/work'
        self.assertTrue(etc_overlay_options(base))
        self.assertTrue(etc_overlay_options(base + ',index=off'))

    def test_unknown_mount_paths_and_flags_remain_rejected(self):
        base = 'Where=/etc\nWhat=overlay\nType=overlay\nOptions=lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper,workdir=/var/lib/overlays/etc/work'
        for text in (base.replace('upperdir=/var', 'upperdir=/unknown'), base + ',redirect_dir=on', base + ',index=on', base + '\nOptions=lowerdir=/tmp'):
            with self.assertRaises(RuntimeError): etc_overlay_options(text)


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
