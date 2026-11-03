"""Historical BMI lock relocation remains enforced by the public exporter."""
import hashlib,importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location('source_export_test',ROOT/'scripts/export-source.py');exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
class ExportRelocationTests(unittest.TestCase):
 def test_historical_lock_uses_checked_canonical_member(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);a=root/'adaptations';(a/'records/migrations').mkdir(parents=True)
   old='brands/onexplayer/common/scripts/gyro/gyro_binding.py';new='common/sensors/bosch/bmi260/gyro/sources/gyro_binding.py';raw=b'reviewed';row=dict(path=old,size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
   (a/'source-lock.json').write_text(json.dumps(dict(files=[row])));(a/'current-source-lock.json').write_text('{"files":[]}');(a/'upstream-source-lock.json').write_text('{"files":[]}')
   (a/'records/migrations/bmi260-source-relocation-20261103.json').write_text(json.dumps(dict(schema_version=1,kind='BoilerSourceRelocation',files=[dict(old_path=old,new_path=new,size=len(raw),sha256=row['sha256'])])))
   target=a/new;target.parent.mkdir(parents=True);target.write_bytes(raw)
   with patch.object(exporter,'verify_model_catalog'):
    exporter.verify_adaptation_snapshots(root,{'adaptations/'+new})
    target.write_bytes(b'changed')
    with self.assertRaisesRegex(ValueError,'differs from reviewed inventory'):exporter.verify_adaptation_snapshots(root,{'adaptations/'+new})
if __name__=='__main__':unittest.main()