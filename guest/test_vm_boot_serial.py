import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import tempfile
import json

spec = importlib.util.spec_from_file_location('vm_boot_serial', Path(__file__).with_name('vm-boot-serial.py'))
serial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serial)

class SerialPolicyTests(unittest.TestCase):
    def test_filesystem_and_partition_uuid_select_the_mounted_slot(self):
        partition = b'11111111-2222-3333-4444-555555555555'
        filesystem = b'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        kernel = '/boot/vmlinuz-linux-neptune-616'
        # The supplied 3.8 recovery uses --fs-uuid; 3.10 uses --part-uuid.
        for selector, identity in ((b'fs-uuid', filesystem), (b'part-uuid', partition)):
            before = b'search --no-floppy --' + selector + b' --set=root ' + identity + b'\n'
            before += (b'\tsteamenv_boot\tlinux ' + kernel.encode() + b' console=tty1 quiet\n') * 3
            after, count = serial.transform(before, {kernel}, partition, filesystem)
            self.assertEqual(count, 3)
            self.assertEqual(after.replace(serial.SUFFIX, b''), before)
            self.assertEqual(serial.transform(after, {kernel}, partition, filesystem), (after, 3))
            with self.assertRaisesRegex(ValueError, 'slot root'):
                serial.transform(before, {kernel}, filesystem, partition)
        with self.assertRaisesRegex(ValueError, 'slot root'):
            serial.transform(b'search --no-floppy --fs-uuid --set=root ' + filesystem + b'\nsteamenv_boot linux ' + kernel.encode() + b'\n', {kernel}, partition)

    def test_varying_kernels_and_entries_preserve_other_bytes(self):
        for kernel, count in [('linux-neptune', 1), ('linux-neptune-72', 3)]:
            line = b'\tsteamenv_boot\tlinux /boot/vmlinuz-' + kernel.encode() + b' console=tty1 quiet\r\n'
            before = b'# GRUB\r\nmenuentry Windows {\r\n chainloader /EFI/Microsoft/bootmgfw.efi\r\n}\r\n' + line * count
            after, actual = serial.transform(before, {'/boot/vmlinuz-' + kernel})
            self.assertEqual(actual, count)
            self.assertEqual(after, before.replace(b' quiet\r\n', b' quiet' + serial.SUFFIX + b'\r\n'))
            self.assertEqual(serial.transform(after, {'/boot/vmlinuz-' + kernel}), (after, count))

    def test_conflicts_and_unsupported_forms_are_rejected(self):
        kernel = {'/boot/vmlinuz-linux-neptune'}
        prefix = b'steamenv_boot linux /boot/vmlinuz-linux-neptune'
        for data in (prefix + b' console=ttyS0,9600\n', prefix + b' oxp3.serial-console=0\n', prefix + b'\nlinux /boot/vmlinuz-other\n', b'steamenv_boot linux ${kernel}\n', prefix, b'# empty\n'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                serial.transform(data, kernel)
        with self.assertRaises(ValueError):
            serial.transform(prefix + b'\n', set())

    def test_both_preimages_checked_and_unmount_failure_rejects(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            def local(path):
                return base / str(path).lstrip('/')
            for path, value in [('/sys/class/dmi/id/sys_vendor', 'QEMU'), ('/sys/block/vdc/serial', 'boiler-vm-serial-overlay')]:
                target = local(path); target.parent.mkdir(parents=True, exist_ok=True); target.write_text(value)
            parts = []
            for index, name in enumerate(('esp', 'efi-A', 'efi-B', 'rootfs-A', 'rootfs-B', 'var-A', 'var-B', 'home'), 1):
                parts.append({'name': name, 'node': '/dev/vdc' + str(index), 'uuid': 'uuid-' + name})
            configs = []
            for slot in ('A', 'B'):
                root = local('/mnt/vm-serial-root-' + slot); (root / 'boot').mkdir(parents=True)
                (root / 'boot/vmlinuz-test').write_bytes(b'kernel')
                cfg = local('/mnt/vm-serial-efi-' + slot) / 'EFI/steamos/grub.cfg'; cfg.parent.mkdir(parents=True)
                cfg.write_bytes(('search --no-floppy --part-uuid --set=root uuid-rootfs-' + slot.lower() + '\n').encode() + b'steamenv_boot linux /boot/vmlinuz-test console=tty1\n')
                configs.append(cfg)
            def command(*args):
                if args[0] == 'sfdisk': return json.dumps({'partitiontable': {'label': 'gpt', 'partitions': parts}})
                if args[0] == 'blkid': return 'btrfs'
                if args[-1] == '/dev/vdc' and '--getro' in args: return '0'
                return str(80 * 1024**3)
            before = configs[0].read_bytes()
            configs[1].write_bytes(b'unsupported\n')
            with mock.patch.object(serial, 'Path', side_effect=local), mock.patch.object(serial, 'command', side_effect=command), mock.patch.object(serial.subprocess, 'run'):
                with self.assertRaises(ValueError): serial.run()
            self.assertEqual(configs[0].read_bytes(), before)
            configs[1].write_bytes(b'search --no-floppy --part-uuid --set=root uuid-rootfs-b\nsteamenv_boot linux /boot/vmlinuz-test console=tty1\n')
            def fail_unmount(args, **kwargs):
                if args[0] == 'umount': raise RuntimeError('unmount failed')
            with mock.patch.object(serial, 'Path', side_effect=local), mock.patch.object(serial, 'command', side_effect=command), mock.patch.object(serial.subprocess, 'run', side_effect=fail_unmount):
                with self.assertRaisesRegex(RuntimeError, 'Unmount failed'): serial.run()

if __name__ == '__main__':
    unittest.main()
