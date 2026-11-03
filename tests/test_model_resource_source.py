"""Canonical model-source and snapshot regressions."""
import json,shutil,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from protocol.resource_pool import decoder
from resources import model_source

class ModelSourceTests(unittest.TestCase):
 def test_snapshot_contains_two_complete_authoritative_model_guides(self):
  sources=model_source.sources(ROOT);self.assertEqual(len(sources),2)
  toon=decoder(ROOT/'adaptations');identities=set()
  for name in sources:
   guide=toon.loads((ROOT/name).read_text(encoding='utf-8'),strict=True);spec=guide['resource_model']
   self.assertEqual(guide['id'].replace('guide:','device:',1),spec['id']);identities.add(spec['id'])
   common=spec['common_references'];self.assertEqual(common['model_id'],spec['id'])
   self.assertEqual({r['id'] for r in common['resources']},{'resource:intel-xevmafix-724','resource:intel-be201-firmware','resource:oxp3-speaker-route','resource:bmi260-gyro'})
   self.assertEqual([r['id'] for r in common['boot_policies']],['ssd:gm7-boot-repair'])
  self.assertEqual(identities,{'device:oxp3','device:x2-mini'})
 def test_stale_or_unlisted_model_snapshot_is_rejected(self):
  lock=json.loads((ROOT/model_source.SNAPSHOT).read_bytes())
  with tempfile.TemporaryDirectory() as temp:
   project=Path(temp)
   for name in [model_source.SNAPSHOT,'scripts/source-files.json']+[r['path'] for r in lock['files']]:
    target=project/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
   sources=model_source.sources(project);path=project/sources[0];raw=path.read_bytes();path.write_bytes(raw+b'\n')
   with self.assertRaisesRegex(ValueError,'snapshot differs'):model_source.sources(project)
   path.write_bytes(raw)
   extra=project/'adaptations/brands/example/extra/extra.toon';extra.parent.mkdir(parents=True);extra.write_text('unused')
   with self.assertRaisesRegex(ValueError,'whitelist'):model_source.sources(project)
if __name__=='__main__':unittest.main()