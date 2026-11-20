"""The shared PAM input is locked before either model overlay is built."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pam_overlay', ROOT/'scripts/build-fingerprint-overlay.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


class PamInputTests(unittest.TestCase):
    def test_missing_and_altered_bytes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'PAM module required'):
            overlay.read_pam_input(None)
        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory)/'pam_fprintd.so'
            module.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'differs from locked bytes'):
                overlay.read_pam_input(module)
            module.write_bytes(b'x'*overlay.PAM_SIZE)
            with self.assertRaisesRegex(ValueError, 'differs from locked bytes'):
                overlay.read_pam_input(module)
            with mock.patch.object(overlay, 'digest', return_value=overlay.PAM_SHA256):
                self.assertEqual(overlay.read_pam_input(module), module.read_bytes())

if __name__ == '__main__': unittest.main()
