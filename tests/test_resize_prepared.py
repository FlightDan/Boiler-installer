"""Home-growth gate in guest/resize_prepared.py: a repaired home is never grown or written."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'guest'))
from resize_prepared import grow_home, require_clean_home

NODE = '/tmp/oxp-capacity-home'


def fake_check(code):
    calls = []

    def check(args, check=False):
        calls.append(list(args))
        return SimpleNamespace(returncode=code)

    return check, calls


class HomeRepairGate(unittest.TestCase):
    def test_clean_home_is_grown_once(self):
        check, calls = fake_check(0)
        with mock.patch('resize_prepared.run') as resize:
            grow_home(NODE, check)
        self.assertEqual([['e2fsck', '-pf', NODE]], calls)
        resize.assert_called_once_with('resize2fs', NODE)

    def test_repaired_or_failed_home_is_never_grown(self):
        for code in (1, 2, 4, 8):
            with self.subTest(e2fsck_exit=code):
                check, _ = fake_check(code)
                with mock.patch('resize_prepared.run') as resize:
                    with self.assertRaisesRegex(RuntimeError, r'needs repair \(e2fsck exit %d\)' % code):
                        grow_home(NODE, check)
                resize.assert_not_called()

    def test_require_clean_home_accepts_only_exit_zero(self):
        check, _ = fake_check(0)
        require_clean_home(NODE, check)
        check, _ = fake_check(1)
        with self.assertRaisesRegex(RuntimeError, 'exit 1'):
            require_clean_home(NODE, check)


@unittest.skipUnless(all(shutil.which(tool) for tool in ('e2fsck', 'debugfs', 'mkfs.ext4', 'truncate')),
                     'needs e2fsprogs on Linux; Windows cannot run e2fsck')
class RealE2fsckGate(unittest.TestCase):
    def make_image(self, directory):
        image = Path(directory) / 'home.img'
        subprocess.run(['truncate', '-s', '64M', str(image)], check=True)
        subprocess.run(['mkfs.ext4', '-q', '-F', '-O', 'casefold', '-T', 'huge', '-m', '0', str(image)], check=True)
        return image

    def test_clean_image_is_grown(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch('resize_prepared.run') as resize:
            grow_home(str(self.make_image(directory)))
        resize.assert_called_once()

    def test_repaired_image_is_refused_before_resize(self):
        with tempfile.TemporaryDirectory() as directory:
            image = self.make_image(directory)
            # A wrong free-block count is repaired by e2fsck -p with the same log text as a clean run;
            # only the exit code (1) reveals the repair.
            subprocess.run(['debugfs', '-w', '-R', 'set_super_value free_blocks_count 1', str(image)],
                           check=True, capture_output=True)
            with mock.patch('resize_prepared.run') as resize:
                with self.assertRaisesRegex(RuntimeError, r'e2fsck exit 1'):
                    grow_home(str(image))
            resize.assert_not_called()


if __name__ == '__main__':
    unittest.main()
