"""X2 external module preflight does not admit altered or misplaced inputs."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fte_overlay', ROOT/'scripts/build-fingerprint-overlay.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


class FteModulePreflight(unittest.TestCase):
    def test_missing_and_altered_module_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root/'bad.ko'
            bad.write_bytes(b'not a module')
            package = {'id':'device:x2-mini', 'components':[], 'shared_adaptations':{'components':[]}}
            with mock.patch.object(overlay,'load_protocol'), mock.patch.object(overlay,'read_pam_input',return_value=b'pam'), mock.patch.object(overlay,'validate_package',return_value=package,create=True):
                for module in (None,bad):
                    with self.subTest(module=module), self.assertRaisesRegex(ValueError,'FTE3600 module'):
                        overlay.build(root/'base','0'*64,root/'native','0'*64,'0'*64,root/'out',model_id='device:x2-mini',installer_root=root,fte_module=module)
                    self.assertFalse((root/'out').exists())

    def test_module_is_rejected_for_oxp3(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = {'id':'device:oxp3','components':[],'shared_adaptations':{'components':[]}}
            with mock.patch.object(overlay,'load_protocol'), mock.patch.object(overlay,'read_pam_input',return_value=b'pam'), mock.patch.object(overlay,'validate_package',return_value=package,create=True):
                with self.assertRaisesRegex(ValueError,'X2-only'):
                    overlay.build(root/'base','0'*64,root/'native','0'*64,'0'*64,root/'out',model_id='device:oxp3',installer_root=root,fte_module=root/'module')
                self.assertFalse((root/'out').exists())

if __name__ == '__main__': unittest.main()
