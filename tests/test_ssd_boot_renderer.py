import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ssd_boot', Path(__file__).resolve().parents[1] / 'components' / 'ssd_boot.py')
ssd_boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ssd_boot)
render_grub_config = ssd_boot.render_grub_config
classify_reviewed_device = ssd_boot.classify_reviewed_device


class SsdBootRendererTests(unittest.TestCase):
    def test_exact_device_policy_and_missing_facts(self):
        self.assertEqual(classify_reviewed_device('NVMe', ' Predator  SSD GM7 1TB '), 'match')
        for bus, model in (('NVMe', 'Other SSD'), ('SATA', 'Predator SSD GM7 1TB'),
                           ('NVMe', 'Predator SSD GM7 2TB')):
            self.assertEqual(classify_reviewed_device(bus, model), 'not_applicable')
        for bus, model in ((None, 'Predator SSD GM7 1TB'), ('NVMe', ''), ('NVMe', None),
                           ('unknown', 'Predator SSD GM7 1TB')):
            with self.subTest(bus=bus, model=model), self.assertRaises(ValueError):
                classify_reviewed_device(bus, model)

    def test_entries_and_unrelated_bytes_preserved(self):
        original = (b'# keep\r\nmenuentry x {\r\n'
                    b'  steamenv_boot /boot/vmlinuz-main quiet split_lock_detect=off\r\n'
                    b'  linuxefi /boot/vmlinuz-fallback recovery\r\n}\r\n')
        expected = original.replace(b'split_lock_detect=off\r\n', b'split_lock_detect=off nvme.noacpi=1\r\n').replace(
            b'recovery\r\n', b'recovery nvme.noacpi=1\r\n')
        self.assertEqual(render_grub_config(original), expected)
        self.assertEqual(render_grub_config(expected), expected)

    def test_preexisting_parameter_is_preserved(self):
        original = b'linux /boot/vmlinuz-main nvme.noacpi=1 split_lock_detect=off\n'
        self.assertEqual(render_grub_config(original), original)

    def test_conflicts_and_ambiguous_entries_rejected(self):
        for extra in (b'nvme.noacpi=0', b'nvme.quirks=1', b'nvme.noacpi=1 nvme.noacpi=1', b'foo; echo x', b'foo # comment'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                render_grub_config(b'linux /boot/vmlinuz-main ' + extra + b'\n')
        with self.assertRaises(ValueError):
            render_grub_config(b'set nvme.noacpi=0\nlinux /boot/vmlinuz-main\n')

    def test_invalid_or_unsupported_configuration_rejected(self):
        for data in (b'', b'# only\n', b'linux /boot/vmlinuz-x\x00\n', b'linux /boot/vmlinuz-x\r', b'\xff'):
            with self.subTest(data=data), self.assertRaises((ValueError, UnicodeDecodeError)):
                render_grub_config(data)


if __name__ == '__main__':
    unittest.main()
