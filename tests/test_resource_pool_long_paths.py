import os,sys,tempfile,unittest,shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from protocol.manifest import _filesystem_path
from protocol.resource_pool import local

class PoolLongPathTests(unittest.TestCase):
 def test_actual_long_descendant_has_the_same_containment_authority_as_short_root(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)/'pool';root.mkdir()
   name='/'.join(['long-public-runtime-'+str(i)+'x'*35 for i in range(7)])+'/payload.bin'
   target=_filesystem_path(root/name);target.parent.mkdir(parents=True);target.write_bytes(b'pinned native library')
   self.assertGreater(len(str(target)),260)
   result=local(root,name)
   self.assertEqual(_filesystem_path(result).read_bytes(),b'pinned native library')
   with self.assertRaisesRegex(ValueError,'Unsafe path'):local(root,'../outside')
   self.assertEqual(root.parent.resolve(),Path(directory).resolve())
   shutil.rmtree(_filesystem_path(root))
 def test_short_linked_member_is_still_rejected(self):
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory);root=base/'pool';root.mkdir();outside=base/'outside';outside.mkdir();(outside/'payload').write_bytes(b'outside')
   try:os.symlink(outside,root/'linked',target_is_directory=True)
   except OSError as error:self.skipTest('Platform cannot create the temporary symlink: '+str(error))
   with self.assertRaisesRegex(ValueError,'Linked pool member'):local(root,'linked/payload')

if __name__=='__main__':unittest.main()

