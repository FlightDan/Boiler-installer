"""Early firmware append preserves the exact image, and fails before writes."""
import gzip
import hashlib
import lzma
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'components'))
import graphics_boot as boot

PROFILE = 'graphics:amd-780m'
TARGET = 'usr/lib/firmware/amdgpu/selected.bin'


def archive(extra=(), *, legacy=False):
    items = [('lib', b'' if legacy else b'usr/lib', (stat.S_IFDIR | 0o755) if legacy else (stat.S_IFLNK | 0o777)),
             ('init', b'original-init', stat.S_IFREG | 0o755),
             ('usr/lib/modules/fixture/kernel/gpu.ko', b'physical-gpu-driver', stat.S_IFREG | 0o644),
             *extra, ('TRAILER!!!', b'', 0)]
    return b''.join(boot._entry(name, data, mode, i) for i, (name, data, mode) in enumerate(items, 1))


class GraphicsBootChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'boot').mkdir()
        self.path = self.root / 'boot/initramfs-linux.img'
        self.original = archive()
        self.path.write_bytes(self.original)

    def prepare(self, candidates=None):
        return boot.prepare(self.root, PROFILE, candidates or {TARGET: b'new-firmware'})

    def test_append_is_deterministic_readonly_and_preserves_original_bytes(self):
        writes, proof = self.prepare()
        self.assertEqual((writes, proof), self.prepare())
        self.assertEqual(self.original, self.path.read_bytes())
        result = writes['boot/initramfs-linux.img']
        self.assertTrue(result.startswith(self.original))
        inventory = boot.inspect(result)
        self.assertEqual(hashlib.sha256(b'physical-gpu-driver').hexdigest(), inventory['usr/lib/modules/fixture/kernel/gpu.ko'][0]['sha256'])
        self.assertEqual(hashlib.sha256(b'new-firmware').hexdigest(), inventory[TARGET][0]['sha256'])
        self.assertEqual('not_validated', proof['runtime'])
        self.path.write_bytes(result)
        self.assertTrue(boot.verify(self.root, proof))
        self.path.write_bytes(result.replace(b'original-init', b'tampered-init'))
        with self.assertRaisesRegex(ValueError, 'changed'):
            boot.verify(self.root, proof)

    def test_gzip_xz_mixed_archives_and_microcode_remain_exact_prefix(self):
        early = boot._entry('kernel/x86/microcode/GenuineIntel.bin', b'cpu-microcode', stat.S_IFREG | 0o644, 1) + boot._entry('TRAILER!!!', b'', 0, 2)
        for compressed in (gzip.compress(self.original), lzma.compress(self.original)):
            with self.subTest(magic=compressed[:4]):
                original = early + b'\0' * 512 + compressed
                self.path.write_bytes(original)
                writes, proof = self.prepare()
                result = writes['boot/initramfs-linux.img']
                self.assertTrue(result.startswith(original))
                self.assertIn('kernel/x86/microcode/GenuineIntel.bin', boot.inspect(result))
                self.assertEqual(len(original), proof['artifacts']['boot/initramfs-linux.img']['original_size'])

    def test_existing_compressed_firmware_preserved_in_each_image(self):
        stock = archive([(TARGET + '.zst', b'original-compressed', stat.S_IFREG | 0o600)])
        self.path.write_bytes(stock)
        fallback = self.root / 'boot/initramfs-linux-fallback.img'
        fallback.write_bytes(self.original)
        writes, proof = self.prepare()
        self.assertEqual({'boot/initramfs-linux-fallback.img'}, set(writes))
        self.assertEqual(stock, self.path.read_bytes())
        entry = proof['artifacts']['boot/initramfs-linux.img']
        self.assertEqual([TARGET + '.zst'], list(entry['preserved']))
        self.assertEqual(0, entry['fragment_size'])
        fallback.write_bytes(writes['boot/initramfs-linux-fallback.img'])
        self.assertTrue(boot.verify(self.root, proof))

    def test_native_compressed_candidate_can_be_added_without_reencoding(self):
        writes, _ = self.prepare({TARGET + '.xz': b'preserved-native-compressed'})
        entries = boot.inspect(writes['boot/initramfs-linux.img'])
        self.assertNotIn(TARGET, entries)
        self.assertEqual(hashlib.sha256(b'preserved-native-compressed').hexdigest(), entries[TARGET + '.xz'][0]['sha256'])

    def test_legacy_lib_firmware_directory_is_supported(self):
        self.path.write_bytes(archive(legacy=True))
        writes, _ = self.prepare()
        self.assertIn('lib/firmware/amdgpu/selected.bin', boot.inspect(writes['boot/initramfs-linux.img']))

    def test_stock_directory_trailing_slashes_are_normalized_only_for_directories(self):
        self.path.write_bytes(archive([('usr/', b'', stat.S_IFDIR | 0o755)]))
        writes, _ = self.prepare()
        self.assertIn(TARGET, boot.inspect(writes['boot/initramfs-linux.img']))
        self.path.write_bytes(archive([('unsafe/', b'file', stat.S_IFREG | 0o644)]))
        with self.assertRaisesRegex(ValueError, 'Unsafe payload path'):
            self.prepare()

    def test_stock_repeated_identical_lib_alias_and_zero_link_members_preserve(self):
        member = boot._entry(TARGET, b'stock-zero-link-count', stat.S_IFREG | 0o644, 5)
        member = member[:38] + b'00000000' + member[46:]
        original = archive() + member + boot._entry('TRAILER!!!', b'', 0, 6) + archive()
        self.path.write_bytes(original)
        writes, proof = self.prepare()
        self.assertEqual({}, writes)
        self.assertEqual(0, proof['artifacts']['boot/initramfs-linux.img']['preserved'][TARGET]['links'])
        self.assertTrue(boot.verify(self.root, proof))

    def test_identical_repeated_stock_firmware_is_preserved(self):
        original = archive([(TARGET, b'stock', stat.S_IFREG | 0o644)]) * 2
        self.path.write_bytes(original)
        writes, proof = self.prepare()
        self.assertEqual({}, writes)
        self.assertTrue(boot.verify(self.root, proof))

    def test_preexisting_raw_firmware_is_preserved_even_if_different(self):
        stock = archive([(TARGET, b'original-native', stat.S_IFREG | 0o644)])
        self.path.write_bytes(stock)
        writes, proof = self.prepare()
        self.assertEqual({}, writes)
        self.assertEqual('existing-initramfs-firmware-preserved', proof['status'])
        self.assertTrue(boot.verify(self.root, proof))

    def test_duplicates_links_and_parent_aliases_fail_before_mutation(self):
        cases = [
            [(TARGET, b'one', stat.S_IFREG | 0o644), (TARGET, b'two', stat.S_IFREG | 0o644)],
            [(TARGET, b'other', stat.S_IFLNK | 0o777)],
            [('usr/lib/firmware/amdgpu', b'../../elsewhere', stat.S_IFLNK | 0o777)],
            [(TARGET + '.zst', b'', stat.S_IFDIR | 0o755)],
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                original = archive(extra)
                self.path.write_bytes(original)
                with self.assertRaisesRegex(ValueError, 'Unsafe'):
                    self.prepare()
                self.assertEqual(original, self.path.read_bytes())

    def test_unsupported_or_corrupt_second_image_causes_no_partial_writes(self):
        bad = self.root / 'boot/initramfs-zbad.img'
        for data in (b'not-cpio', gzip.compress(self.original)[:-4], self.original[:-1], self.original + b'junk'):
            with self.subTest(data=data[:8]):
                bad.write_bytes(data)
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertEqual(self.original, self.path.read_bytes())
                self.assertEqual(data, bad.read_bytes())

    def test_decompression_output_and_entry_count_are_bounded(self):
        self.path.write_bytes(gzip.compress(self.original))
        with patch.object(boot, 'MAX_EXPANDED', 32):
            with self.assertRaisesRegex(ValueError, 'limit|oversized'):
                self.prepare()
        self.path.write_bytes(self.original)
        with patch.object(boot, 'MAX_ENTRIES', 1):
            with self.assertRaisesRegex(ValueError, 'count'):
                self.prepare()

    def test_wrong_profile_paths_and_missing_images_fail(self):
        with self.assertRaisesRegex(ValueError, 'candidate'):
            self.prepare({'etc/config': b'bad'})
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, 'initramfs'):
            self.prepare()

    def test_changed_compressed_member_and_empty_proof_are_rejected(self):
        self.path.write_bytes(gzip.compress(self.original))
        writes, proof = self.prepare()
        self.path.write_bytes(writes['boot/initramfs-linux.img'])
        self.assertTrue(boot.verify(self.root, proof))
        proof['artifacts'] = {}
        with self.assertRaisesRegex(ValueError, 'artifacts'):
            boot.verify(self.root, proof)


if __name__ == '__main__':
    unittest.main()
