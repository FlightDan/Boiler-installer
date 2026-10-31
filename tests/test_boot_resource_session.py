import hashlib,sys,tempfile,unittest,subprocess,os,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance.resource_runtime import ResourcePool,ResourceSession
from maintenance.boot_resources import GRUB,SPLIT,GM7

class SlotBootTransactions(unittest.TestCase):
 def test_distinct_slot_grub_inputs_produce_bound_files_and_restore_originals(self):
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory);pooldir=base/'pool';pooldir.mkdir();home=base/'home';home.mkdir();slots={};original={}
   resource=dict(id='resource:common-boot-fixes',version='1.0.0',dependencies=[],hardware_requires={},compatibility={},
    parameters=dict(split_lock=True,gm7_resume=True),fixed_targets=[GRUB,SPLIT,GM7],payload={'files':[]},install={'kind':'boiler-boot-settings-v1','files':[]},verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'})
   for slot in ('A','B'):
    root=base/slot;(root/GRUB).parent.mkdir(parents=True);(root/'home').mkdir();(root/'var').mkdir()
    data=('steamenv_boot /boot/vmlinuz-test root=PARTUUID='+slot+' quiet\n').encode();(root/GRUB).write_bytes(data);original[slot]=data
    slots[slot]=dict(root=root,environment={},hardware={'target_disk':{'bus':'NVMe','model':'YMTC GM7'}},opt_offload=False)
   pool=ResourcePool(pooldir,{'resources':[resource]},local_test=True);refs=[{'resource':resource['id']}]
   session=ResourceSession(pool,slots,home,offline_image=True);receipt=session.install(refs,[resource['id']])
   self.assertEqual(receipt['status'],'committed');self.assertEqual(len(receipt['transactions']),2)
   for slot,target in slots.items():
    text=(target['root']/GRUB).read_text();self.assertIn('split_lock_detect=off',text);self.assertIn('nvme.noacpi=1',text)
    self.assertIn('PARTUUID='+slot,text)
   proof=session.verify([resource['id']]);self.assertTrue(all(row['ok'] for row in proof),proof)
   for slot in ('A','B'):
    session.restore(receipt,slot)
    self.assertEqual((slots[slot]['root']/GRUB).read_bytes(),original[slot])
    self.assertFalse((slots[slot]['root']/SPLIT).exists());self.assertFalse((slots[slot]['root']/GM7).exists())

def group_fixture(base, create=False):
 pooldir=base/'pool';home=base/'home';slots={}
 if create:pooldir.mkdir();home.mkdir()
 resource=dict(id='resource:common-boot-fixes',version='1.0.0',dependencies=[],hardware_requires={},compatibility={},
  parameters=dict(split_lock=True,gm7_resume=True),fixed_targets=[GRUB,SPLIT,GM7],payload={'files':[]},install={'kind':'boiler-boot-settings-v1','files':[]},verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'})
 for slot in ('A','B'):
  root=base/slot
  if create:
   (root/GRUB).parent.mkdir(parents=True);(root/'home').mkdir();(root/'var').mkdir()
   (root/GRUB).write_text('steamenv_boot /boot/vmlinuz-test root=PARTUUID='+slot+' quiet\n')
  slots[slot]=dict(root=root,environment={},hardware={'target_disk':{'bus':'NVMe','model':'YMTC GM7'}},opt_offload=False)
 return ResourceSession(ResourcePool(pooldir,{'resources':[resource]},local_test=True),slots,home,offline_image=True),[{'resource':resource['id']}]

class SlotBootGroupRetry(unittest.TestCase):
 def test_missing_actual_disk_facts_refuses_retry(self):
  with tempfile.TemporaryDirectory() as directory:
   session,refs=group_fixture(Path(directory),True)
   for slot in session.slots.values():slot['hardware']={}
   with self.assertRaisesRegex(Exception,'Actual target disk facts'):session.install(refs,['resource:common-boot-fixes'],group='d'*32)
 def test_kill_after_first_derived_boot_commit_reuses_slot_payload(self):
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory);session,refs=group_fixture(base,True)
   result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'boot-child',str(base)],capture_output=True,text=True)
   self.assertEqual(result.returncode,93,result.stderr)
   receipt=session.install(refs,['resource:common-boot-fixes'],group='d'*32)
   self.assertEqual(len(receipt['transactions']),2)
   for slot in ('A','B'):
    text=(base/slot/GRUB).read_text();self.assertIn('PARTUUID='+slot,text);self.assertIn('nvme.noacpi=1',text)
    self.assertTrue((base/slot/GM7).is_file())
   self.assertTrue(all(r['ok'] for r in session.verify(['resource:common-boot-fixes'])))

def boot_child(base):
 import maintenance.engine as engine
 session,refs=group_fixture(base);original=engine._json_write
 def kill(path,value):
  original(path,value)
  if value.get('status')=='committed' and value.get('component',{}).get('resource_group')=='d'*32:os._exit(93)
 engine._json_write=kill
 session.install(refs,['resource:common-boot-fixes'],group='d'*32)

if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='boot-child':boot_child(Path(sys.argv[2]))
 else:unittest.main()
