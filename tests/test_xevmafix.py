import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import contextlib
import io

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('xevma_loader', ROOT / 'adaptations/brands/onexplayer/oxp3/current/xevma-20261029/load.py')
transform_spec = importlib.util.spec_from_file_location('xevma_kmod_transform', ROOT/'adaptations/common/xevma/kmod_transform.py')
transformer = importlib.util.module_from_spec(transform_spec); transform_spec.loader.exec_module(transformer)
raw = Path(spec.origin).read_bytes()
loader = importlib.util.module_from_spec(spec)
exec(compile(transformer.transform(raw), spec.origin, 'exec'), loader.__dict__)

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
            mock.call(['modprobe', 'xe'], executable='/usr/bin/kmod', check=True),
            mock.call(['insmod', str(Path(loader.MODULE))], executable='/usr/bin/kmod', check=True),
        ])

    def test_checked_failures_propagate_and_stop_next_operation(self):
        for failed in (0, 1):
            with self.subTest(failed=failed), mock.patch.object(loader.os, 'geteuid', return_value=0, create=True), \
                    mock.patch.object(loader, 'validate', return_value=Path(loader.MODULE)), \
                    mock.patch.object(Path, 'is_dir', return_value=False), \
                    mock.patch.object(loader.subprocess, 'run', side_effect=[None]*failed+[loader.subprocess.CalledProcessError(1,'fixture')]) as run:
                with self.assertRaises(loader.subprocess.CalledProcessError): loader.main()
                self.assertEqual(run.call_count, failed+1)

    def test_raw_and_patch_anchors_remain_exact(self):
        self.assertEqual(hashlib.sha256(raw).hexdigest(), 'bf49070cb295c872c3059480cc0a393c77bc1b129ffdaba05e12e90ec78dc646')
        with self.assertRaises(ValueError): transformer.transform(raw+b'\n')
        with self.assertRaises(ValueError): transformer.transform(transformer.transform(raw))
        with self.assertRaises(ValueError): transformer.transform(raw+transformer.REPLACEMENTS[0][0])

    def test_x2_and_resource_advisories_survive_kmod_transform(self):
        spec=importlib.util.spec_from_file_location('x2_bridge_fixed',ROOT/'adaptations/scripts/build-complete-bridge.py')
        x2=importlib.util.module_from_spec(spec); spec.loader.exec_module(x2)
        old=x2.transform('upstream-repairs/xevmafix/load.py',raw)
        updated=transformer.transform(old)
        for before,after in transformer.REPLACEMENTS: updated=updated.replace(after,before)
        self.assertEqual(updated,old)
        import sys
        sys.path.insert(0,str(ROOT/'scripts/resources'))
        import hardware_name_patches
        resource=hardware_name_patches.xevma_model_advisory(raw)
        self.assertIn(b"fields.get('DRIVER') == 'xe'",resource)
        self.assertIn(b"executable='/usr/bin/kmod'",resource)
        for before,after in transformer.REPLACEMENTS: resource=resource.replace(after,before)
        self.assertEqual(hashlib.sha256(resource).hexdigest(),
            '9c4202e2d868daa124c1d2857d8be1dd41ecf513d306dc6ddbb7529e23484504')

    def test_existing_module_reports_unverified_without_reload(self):
        output = io.StringIO()
        with mock.patch.object(loader.os, 'geteuid', return_value=0, create=True), mock.patch.object(loader, 'validate', return_value=Path(loader.MODULE)), mock.patch.object(Path, 'is_dir', return_value=True), mock.patch.object(loader.subprocess, 'run') as run, contextlib.redirect_stdout(output):
            loader.main()
        run.assert_not_called()
        self.assertIn('active build is not verified', output.getvalue())
        self.assertIn('reboot required', output.getvalue())

if __name__ == '__main__':
    unittest.main()
