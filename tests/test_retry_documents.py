import copy,importlib,sys,tempfile,unittest,json
from pathlib import Path
SDK=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SDK))
import maintenance
docs=importlib.import_module('maintenance.retry_documents')
from maintenance.engine import _json_write
from maintenance.resource_firstboot import read_private

class DocumentsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.owner=Path(self.tmp.name)/'state.json'
  self.value=dict(kind='BoilerResourceRetryAction',schema_version=1,token='a'*32,group='b'*32,
   generation='c'*32,phase='prepared',status='reinstalling',original_intents={'A':{'large':'x'*700000},'B':{'large':'y'*700000}})
 def tearDown(self):self.tmp.cleanup()
 def test_large_body_small_runtime_state(self):
  ref=docs.compact(self.value,self.owner);_json_write(self.owner,{'maintenance_retry':ref})
  self.assertLess(self.owner.stat().st_size,1024)
  self.assertEqual(docs.hydrate(read_private(self.owner)['maintenance_retry'],self.owner),self.value)
 def test_phase_and_document_mismatch_refuse(self):
  ref=docs.compact(self.value,self.owner);wrong=copy.deepcopy(ref);wrong['phase']='installing'
  with self.assertRaisesRegex(ValueError,'ownership differs'):docs.hydrate(wrong,self.owner)
  path=docs._path(self.owner,ref);path.write_bytes(path.read_bytes()+b' ')
  with self.assertRaisesRegex(ValueError,'differs from'):docs.hydrate(ref,self.owner)
 def test_crash_before_reference_can_reuse_same_document(self):
  ref=docs.compact(self.value,self.owner);self.assertFalse(self.owner.exists())
  self.assertEqual(docs.compact(self.value,self.owner),ref)
 def test_digest_cannot_escape(self):
  ref=docs.compact(self.value,self.owner);ref['document_sha256']='../escape'
  with self.assertRaisesRegex(ValueError,'digest'):docs.hydrate(ref,self.owner)

if __name__=='__main__':unittest.main()
