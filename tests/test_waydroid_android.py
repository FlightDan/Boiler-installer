import importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from maintenance import waydroid_android_init as module
class AndroidInitTests(unittest.TestCase):
 def test_real_exit_after_initialization_writeahead(self):
  if not hasattr(os,'fork'):self.skipTest('requires Linux fork')
  from maintenance.engine import _json_write
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);marker=root/'action.json';state=module.prepare(root,'a'*32);pid=os.fork()
   if pid==0:
    def persist_and_exit(value):_json_write(marker,value);os._exit(79)
    with patch.object(module,'_inactive',return_value=True):module.activate(root,state,persist_and_exit)
    os._exit(80)
   _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),79)
   state=json.loads(marker.read_text())
   with patch.object(module,'_inactive',return_value=True),patch.object(module,'_execute',side_effect=RuntimeError('offline')):
    result=module.activate(root,state,lambda x:_json_write(marker,x))
   self.assertEqual(result['phase'],'failed');self.assertEqual(result['attempts'],2)

 def test_failed_network_retry_and_verify_image_mutation(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);generation='a'*32;state=module.prepare(root,generation);history=[]
   with patch.object(module,'_inactive',return_value=True),patch.object(module,'_execute',side_effect=RuntimeError('offline')):
    state=module.activate(root,state,lambda x:history.append(x))
   self.assertEqual(state['phase'],'failed')
   def initialized(images):
    images.joinpath('system.img').write_bytes(b'system');images.joinpath('vendor.img').write_bytes(b'vendor')
    work=root/'var/lib/waydroid';work.mkdir(parents=True,exist_ok=True);(work/'rootfs').mkdir()
    (work/'waydroid.cfg').write_text('[waydroid]\nimages_path = '+str(images)+'\nsystem_ota = '+module.SYSTEM+'/lineage/waydroid_x86_64/GAPPS.json\nvendor_ota = '+module.VENDOR+'/waydroid_x86_64/MAINLINE.json\nsystem_datetime = 1\nvendor_datetime = 1\n')
   with patch.object(module,'_inactive',return_value=True),patch.object(module,'_execute',side_effect=initialized):state=module.activate(root,state,lambda x:history.append(x))
   self.assertEqual(state['phase'],'ready');self.assertEqual(state['attempts'],2);module.verify(root,state)
   (root/'var/lib/boiler/waydroid-images'/generation/'system.img').write_bytes(b'changed')
   with self.assertRaisesRegex(RuntimeError,'changed'):module.verify(root,state)
 def test_foreign_data_preserved_before_write(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=root/'var/lib/waydroid/data';p.mkdir(parents=True);(p/'user').write_text('user')
   with self.assertRaisesRegex(RuntimeError,'user preservation'):module.prepare(root,'a'*32)
   self.assertEqual((p/'user').read_text(),'user')
 def test_retry_after_writeahead_before_stage_creation(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);state=module.prepare(root,'a'*32);saved=[]
   def kill(value):saved.append(value);raise RuntimeError('interruption')
   with patch.object(module,'_inactive',return_value=True):
    with self.assertRaisesRegex(RuntimeError,'interruption'):module.activate(root,state,kill)
   with patch.object(module,'_inactive',return_value=True),patch.object(module,'_execute',side_effect=RuntimeError('offline')):
    result=module.activate(root,saved[0],lambda _:None)
   self.assertEqual(result['phase'],'failed');self.assertEqual(result['attempts'],2)
if __name__=='__main__':unittest.main()
