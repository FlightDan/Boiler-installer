import struct
import gzip
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'components'), str(ROOT)]
import graphics_boot as gb
import microcode
import microcode_boot as mb


def amd(rev):
    patch = bytearray(64)
    struct.pack_into('<I', patch, 4, rev)
    struct.pack_into('<H', patch, 24, 0xa001) # family 0x19
    return struct.pack('<III', 0x00414d44, 0, 16) + bytes(16) + struct.pack('<II', 1, len(patch)) + patch


def archive(files):
    return gb._fragment(files, {})


class MicrocodeTests(unittest.TestCase):
    def test_catalog_and_selection(self):
        self.assertEqual(set(microcode.firmware_metadata(ROOT)), microcode.IDS)
        with self.assertRaises(ValueError): microcode.selected({'repairs':['microcode:intel','microcode:amd']})
        bundle = dict(microcode.firmware_metadata(ROOT)['microcode:amd'], kind='other')
        with self.assertRaises(ValueError): microcode.validate_metadata(bundle, 'microcode:amd')

    def test_changed_spec_invalidates_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'components').mkdir()
            for name in ('microcode-firmware.json', 'microcode-sources.json'):
                shutil.copyfile(ROOT/'components'/name, root/'components'/name)
            spec = root/'components/microcode-sources.json'
            spec.write_bytes(spec.read_bytes() + b' ')
            with self.assertRaises(ValueError): microcode.load_catalog(root)

    def test_amd_newest_order_and_invalid(self):
        merged = mb._amd_merge([amd(0x0a001101), amd(0x0a001105)])
        first = struct.unpack_from('<I', merged, 28 + 8 + 4)[0]
        second = struct.unpack_from('<I', merged, 28 + 72 + 8 + 4)[0]
        self.assertEqual((first, second), (0x0a001101, 0x0a001105))
        with self.assertRaises(ValueError): mb._amd_merge([amd(1)[:-1]])

    def test_intel_individual_extended_signature_checksum(self):
        header = [1, 0x128, 0, 0xb06d1, 0, 1, 0x80, 2000, 2080, 0, 0, 0]
        header[4] = (-sum(header)) & 0xffffffff
        main = struct.pack('<12I', *header) + b'\0' * 2000
        extended = [1, 0, 0, 0, 0, 0xb06d1, 0x80, 1]
        extended[1] = (-sum(extended)) & 0xffffffff
        with self.assertRaisesRegex(ValueError, 'individual extended signature'):
            mb._intel_valid(main + struct.pack('<8I', *extended))

    def test_standalone_is_included_in_boot_artifact_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'boot').mkdir()
            data = archive({'init': b'fixture'})
            for index in range(gb.MAX_IMAGES):
                (root / 'boot' / ('initramfs-' + str(index) + '.img')).write_bytes(data)
            (root / 'boot/amd-ucode.img').write_bytes(archive({'kernel/x86/microcode/AuthenticAMD.bin': amd(0x0a001101)}))
            with self.assertRaisesRegex(ValueError, 'combined CPU boot artifacts'):
                mb.prepare(root, 'microcode:amd', {'usr/lib/firmware/amd-ucode/test.bin': amd(0x0a001105)})

    def test_native_member_in_later_uncompressed_archive(self):
        name = 'kernel/x86/microcode/AuthenticAMD.bin'
        native = amd(0x0a001105)
        data = archive({'early_cpio': b'1'}) + b'\0' * 8 + archive({name: native})
        self.assertEqual(mb._native_member(data, name), native)

    def test_native_amd_standalone_accepts_zero_links_only_for_a_data_member(self):
        name = 'kernel/x86/microcode/AuthenticAMD.bin'
        native = amd(0x0a001105)
        data = gb._entry(name, native, 0o100644, 1)
        trailer = gb._entry('TRAILER!!!', b'', 0, 0)
        self.assertEqual(mb._native_member(data[:38] + b'00000000' + data[46:] + trailer, name), native)
        with self.assertRaisesRegex(ValueError, 'Unsafe native microcode member'):
            mb._native_member(data[:38] + b'00000002' + data[46:] + trailer, name)
        with self.assertRaisesRegex(ValueError, 'Unsafe native microcode member'):
            mb._native_member(archive({name: b''}), name)

    def test_native_member_after_compressed_archive_is_not_early(self):
        name = 'kernel/x86/microcode/AuthenticAMD.bin'
        data = archive({'early_cpio': b'1'}) + gzip.compress(archive({'init': b'init'}))
        data += b'\0' * (-len(data) % 4) + archive({name: amd(0x0a001105)})
        with self.assertRaisesRegex(ValueError, 'visible uncompressed early archive'):
            mb._native_member(data, name)

    def test_composition_preserves_native_and_graphics_and_detects_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); boot = root / 'boot'; boot.mkdir()
            name = 'kernel/x86/microcode/AuthenticAMD.bin'
            original = archive({name: amd(0x0a001101), 'init': b'old'})
            path = boot/'initramfs-test.img'; path.write_bytes(original)
            standalone = boot/'amd-ucode.img'; standalone.write_bytes(archive({name: amd(0x0a001103)}))
            graphic = original + archive({'usr/lib/firmware/amdgpu/test.bin':b'gpu'})
            writes, proof = mb.prepare(root, 'microcode:amd', {'usr/lib/firmware/amd-ucode/x.bin':amd(0x0a001105)},
                                       {'boot/initramfs-test.img':graphic}, {'schema':1})
            self.assertEqual(set(writes), {'boot/initramfs-test.img','boot/amd-ucode.img'})
            for target, data in writes.items(): (root/target).write_bytes(data)
            self.assertTrue(mb.verify(root, proof))
            data = path.read_bytes(); self.assertEqual(data[proof['artifacts']['boot/initramfs-test.img']['prefix_size']:], graphic)
            self.assertIn('usr/lib/firmware/amdgpu/test.bin', gb.inspect(data))
            path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            with self.assertRaises(ValueError): mb.verify(root, proof)

if __name__ == '__main__': unittest.main()
