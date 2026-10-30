import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import contextlib
import io

spec = importlib.util.spec_from_file_location('xevma_loader', Path(__file__).resolve().parents[1] / 'adaptations/brands/onexplayer/oxp3/current/xevma-20261029/load.py')
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)

class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        values = {
            'proc/sys/kernel/osrelease': loader.KERNEL,
            'etc/os-release': 'ID=steamos\nVERSION_ID=3.10\nBUILD_ID=20260924.1000\n',
            'sys/class/dmi/id/sys_vendor': 'ONE-NETBOOK',
            'sys/class/dmi/id/product_name': 'ONEXPLAYER 3',
            str(loader.MODULE).lstrip('/'): 'fixture-module',
        }
        for name, value in values.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(value)
        old = loader.MODULE_SHA256
        self.addCleanup(setattr, loader, 'MODULE_SHA256', old)
        loader.MODULE_SHA256 = hashlib.sha256(b'fixture-module').hexdigest()

    def test_exact_profile(self):
        self.assertEqual(loader.validate(self.root), self.root / str(loader.MODULE).lstrip('/'))

    def test_wrong_kernel_os_build_and_device_rejected(self):
        for name in ('proc/sys/kernel/osrelease', 'etc/os-release', 'sys/class/dmi/id/sys_vendor', 'sys/class/dmi/id/product_name'):
            with self.subTest(name=name):
                p = self.root / name
                previous = p.read_text()
                p.write_text('unaccepted')
                with self.assertRaises(RuntimeError):
                    loader.validate(self.root)
                p.write_text(previous)

    def test_modified_module_rejected(self):
        (self.root / str(loader.MODULE).lstrip('/')).write_text('modified')
        with self.assertRaises(RuntimeError):
            loader.validate(self.root)

    def test_ambiguous_os_release_rejected(self):
        p = self.root / 'etc/os-release'
        p.write_text(p.read_text() + 'BUILD_ID=20260924.1000\n')
        with self.assertRaises(RuntimeError):
            loader.validate(self.root)

    def test_loading_requires_validated_path_and_stock_xe(self):
        with mock.patch.object(loader.os, 'geteuid', return_value=0, create=True), mock.patch.object(loader, 'validate', return_value=Path(loader.MODULE)), mock.patch.object(Path, 'is_dir', return_value=False), mock.patch.object(loader.subprocess, 'run') as run:
            loader.main()
        self.assertEqual(run.call_args_list, [
            mock.call(['/usr/bin/kmod', 'modprobe', 'xe'], check=True),
            mock.call(['/usr/bin/kmod', 'insmod', str(Path(loader.MODULE))], check=True),
        ])

    def test_existing_module_reports_unverified_without_reload(self):
        output = io.StringIO()
        with mock.patch.object(loader.os, 'geteuid', return_value=0, create=True), mock.patch.object(loader, 'validate', return_value=Path(loader.MODULE)), mock.patch.object(Path, 'is_dir', return_value=True), mock.patch.object(loader.subprocess, 'run') as run, contextlib.redirect_stdout(output):
            loader.main()
        run.assert_not_called()
        self.assertIn('active build is not verified', output.getvalue())
        self.assertIn('reboot required', output.getvalue())

if __name__ == '__main__':
    unittest.main()
