import json, tempfile, unittest
from pathlib import Path
from maintenance.engine import Engine, MaintenanceError

class OptiRestoreEntryTests(unittest.TestCase):
 def test_direct_engine_restore_is_not_an_alternate_path_around_review(self):
  with tempfile.TemporaryDirectory() as directory:
   journal=Path(directory)/'journal.json'
   journal.write_text(json.dumps({'component':{'id':'resource:common-opti2xmx'}}))
   engine=object.__new__(Engine);engine.read_only=False;engine._journal=lambda _:journal
   with self.assertRaisesRegex(MaintenanceError,'reviewed resource lifecycle'):
    engine.restore('unreviewed')

if __name__=='__main__':unittest.main()
