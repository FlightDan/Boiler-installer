import importlib.util
import tempfile
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('source_info',ROOT/'guest/source-info.py')
import sys
sys.path.insert(0,str(ROOT/'guest'))
source=importlib.util.module_from_spec(spec);spec.loader.exec_module(source)

class SourceSsdLedgerTests(unittest.TestCase):
    def test_absent_ledger_is_accepted_and_any_known_ledger_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory); source.reject_bound_ssd_home(home)
            (home/'.boiler').mkdir();source.reject_bound_ssd_home(home)
            (home/'.boiler/ssd-boot-v1').mkdir()
            with self.assertRaisesRegex(ValueError,'destination-bound SSD'): source.reject_bound_ssd_home(home)
    def test_nondirectory_owner_path_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory); (home/'.boiler').write_text('unexpected')
            with self.assertRaisesRegex(ValueError,'unsafe destination-bound'): source.reject_bound_ssd_home(home)

if __name__ == '__main__': unittest.main()
