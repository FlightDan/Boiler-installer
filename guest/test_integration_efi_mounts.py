"""Exercise the guest scripts' EFI mount branches against separate slot files."""
import ast
from pathlib import Path
import shutil
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
GRUB = 'efi/EFI/steamos/grub.cfg'


def efi_branch(filename):
    tree = ast.parse((HERE / filename).read_text(encoding='utf-8'))
    return next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                        and child.func.id == 'mount' and any(isinstance(value, ast.Constant)
                        and value.value == 'vfat' for value in child.args)
                        for statement in node.body for child in ast.walk(statement)))


def execute(node, scope):
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 '<guest-efi-branch>', 'exec'), scope)


class EfiMountChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'root'
        self.root.mkdir()
        self.parts = {}
        self.calls = []
        for slot in ('A', 'B'):
            esp = Path(self.temp.name) / ('efi-' + slot)
            file = esp / 'EFI/steamos/grub.cfg'
            file.parent.mkdir(parents=True)
            file.write_text('slot=' + slot)
            self.parts['efi-' + slot] = esp

    def mount(self, node, path, *flags):
        self.calls.append((node, path, flags))
        shutil.copytree(node, path)

    def scope(self, slot, **extra):
        return dict(root=self.root, parts=self.parts, slot=slot, mount=self.mount,
                    ssd_plans=extra.pop('ssd_plans', {}), ssd_selected=extra.pop('ssd_selected', False),
                    model_needed=extra.pop('model_needed', False), review_kwargs=extra.pop('review_kwargs', {}), **extra)

    def clear_mount(self):
        if (self.root / 'efi').exists():
            shutil.rmtree(self.root / 'efi')

    def test_writer_mounts_each_selected_device_package_slot(self):
        branch = efi_branch('integration-image.py')
        for slot in ('A', 'B'):
            for package in ('oxp3', 'local-device'):
                with self.subTest(slot=slot, package=package):
                    execute(branch, self.scope(slot, options={'device_package': package}))
                    self.assertEqual((self.root / GRUB).read_text(), 'slot=' + slot)
                    self.assertEqual(self.calls[-1][2], ('-t', 'vfat'))
                    self.clear_mount()
        count = len(self.calls)
        execute(branch, self.scope('A', options={'repairs': ['wifi:intel']}))
        self.assertEqual(len(self.calls), count)

    def test_verifier_mounts_readonly_efi_before_authenticated_package_verification(self):
        branch = efi_branch('integration-verify.py')
        tree = ast.parse((HERE / 'integration-verify.py').read_text(encoding='utf-8'))
        package_check = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                             and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                             and node.value.func.id == 'verify_device_components')
        self.assertLess(branch.lineno, package_check.lineno)
        for slot in ('A', 'B'):
            with self.subTest(slot=slot):
                markers = []
                def verify(root, payload, options, actual_slot, components):
                    self.assertEqual(actual_slot, slot)
                    self.assertEqual((root / GRUB).read_text(), 'slot=' + slot)
                    return {'device:test'}
                scope = self.scope(slot, options={'device_package': 'oxp3'}, result={'components': {}},
                                   verify_device_components=verify,
                                   guest_marker=lambda *args: markers.append(args))
                # Reproduce the reported failure with an unmounted ESP.
                with self.assertRaises(FileNotFoundError): execute(package_check, scope)
                execute(branch, scope)
                execute(package_check, scope)
                self.assertEqual((self.root / GRUB).read_text(), 'slot=' + slot)
                self.assertEqual(self.calls[-1][2], ('-t', 'vfat', '-o', 'ro'))
                self.assertEqual(scope['device_ids'], {'device:test'})
                self.assertIn(('boiler-install-slot', slot + '\n'), markers)
                # A genuinely missing file still fails after the mount.
                (self.root / GRUB).unlink()
                with self.assertRaises(FileNotFoundError): execute(package_check, scope)
                self.clear_mount()

    def test_no_efi_mount_for_generic_components(self):
        for filename in ('integration-image.py', 'integration-verify.py'):
            branch = efi_branch(filename)
            for options in ({}, {'plugins': ['core:decky']}, {'repairs': ['wifi:intel']}):
                execute(branch, self.scope('A', options=options))
        self.assertEqual(self.calls, [])

    def test_selected_ssd_mounts_each_slot_without_a_model_package(self):
        for filename in ('integration-image.py', 'integration-verify.py'):
            branch = efi_branch(filename)
            for slot in ('A', 'B'):
                markers = []
                scope = self.scope(slot, options={}, ssd_plans={'A': {}, 'B': {}}, ssd_selected=True,
                                   guest_marker=lambda *args: markers.append(args))
                execute(branch, scope)
                self.assertEqual((self.root / GRUB).read_text(), 'slot=' + slot)
                self.clear_mount()


if __name__ == '__main__':
    unittest.main()
