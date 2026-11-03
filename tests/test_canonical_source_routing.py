"""Canonical common source bytes generate the unchanged reviewed runtime."""
import sys,unittest,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'scripts/resources')]
from split_components import Sources,speaker_model_advisory
from hardware_name_patches import xevma_model_advisory

class CanonicalSourceRoutingTests(unittest.TestCase):
 def test_speaker_and_xe_use_common_sources_and_preserve_runtime_bytes(self):
  root=ROOT/'adaptations';source=Sources(root)
  for folder,name,common,transform in (
   ('upstream-20261029','speaker_route.py','common/audio/realtek/alc245/speaker-route/final/speaker_route.py',speaker_model_advisory),
   ('xevma-20261029','load.py','common/gpu/intel/xe/xevmafix-724/final/load.py',xevma_model_advisory)):
   original='brands/onexplayer/oxp3/current/'+folder+'/'+name
   raw=source.read(original)
   self.assertTrue(source.common_used[original].startswith('common/'))
   self.assertEqual(raw,(root/original).read_bytes())
   built=transform(raw);self.assertEqual(built,(root/common).read_bytes())
   if name=='load.py':
    self.assertIn(b"executable='/usr/bin/kmod'",built)
    self.assertNotIn(b"['/usr/bin/kmod', 'insmod'",built)
 def test_be201_common_builder_retains_fixed_firmware_policy(self):
  from resources import build_wifi_pool
  from protocol.resource_pool import validate_pool
  with tempfile.TemporaryDirectory() as temp:
   output=Path(temp)/'pool';build_wifi_pool.build(ROOT,output);resource=validate_pool(output)['resources'][0]
   self.assertEqual(resource['id'],'resource:intel-be201-firmware')
   self.assertEqual(len(resource['install']['files']),9)
   self.assertTrue(all(row['path'].startswith('common/network/intel/be201/') for row in resource['payload']['files']))
   c106=next(row for row in resource['install']['files'] if row['target'].endswith('c106.ucode'))
   self.assertFalse(c106['missing_only'])
   self.assertTrue(all(row['missing_only'] for row in resource['install']['files'] if row is not c106))
if __name__=='__main__':unittest.main()