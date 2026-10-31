import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import os,shutil,tempfile,unittest
from pathlib import Path
from maintenance.resource_runtime import ResourcePool,ResourceSession
from protocol.overlay_contract import IDENTITY,KIND,FILES
SOURCE=Path(os.environ.get('BOILER_ADAPTATIONS_SOURCE',str(Path(__file__).resolve().parents[2]/'Boiler-adaptations')))
if not (SOURCE/'common/telemetry/overlay/files/telemetry.py').is_file():
 raise unittest.SkipTest('Set BOILER_ADAPTATIONS_SOURCE to the matching locked adaptations checkout')
@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'requires Linux root')
class SlotsTest(unittest.TestCase):
 def test_two_root_installs_and_independent_restores(self):
  with tempfile.TemporaryDirectory() as temp:
   base=Path(temp);pooldir=base/'pool';home=base/'home';pooldir.mkdir();home.mkdir();source=SOURCE
   payload=[];files=[]
   for key,src,target,mode,size,digest in FILES:
    out=pooldir/src;out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/src,out)
    payload.append(dict(path=src,size=size,sha256=digest));files.append(dict(source=src,target=target,mode=mode,scope='root-slots'))
   resource=dict(id=IDENTITY,version='1.0.0',dependencies=[],external_component_dependencies=[],hardware_requires={},compatibility={},payload={'files':payload},install={'kind':KIND,'files':files},verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'})
   slots={}
   for slot in ('A','B'):
    root=base/slot;(root/'home').mkdir(parents=True);(root/'var').mkdir()
    slots[slot]=dict(root=root,environment={},hardware={},opt_offload=False)
   session=ResourceSession(ResourcePool(pooldir,{'resources':[resource]},local_test=True),slots,home,offline_image=True)
   receipt=session.install([{'resource':IDENTITY}],[IDENTITY]);self.assertEqual(len(receipt['transactions']),2)
   for target in slots.values():
    for _,src,path,mode,size,digest in FILES:self.assertEqual((target['root']/path).read_bytes(),(source/src).read_bytes())
   session.restore(receipt,'A')
   for _,src,path,mode,size,digest in FILES:self.assertFalse((slots['A']['root']/path).exists());self.assertTrue((slots['B']['root']/path).exists())
   session.restore(receipt,'B')
   for _,src,path,mode,size,digest in FILES:self.assertFalse((slots['B']['root']/path).exists())
if __name__=='__main__':unittest.main()
