import copy,tempfile,unittest
from pathlib import Path
from maintenance import resource_postrestore as post
from maintenance.resource_runtime import ResourceError
from maintenance.resource_firstboot import STATE,read_private
from maintenance.engine import _json_write

ID='resource:common-telemetry-overlay'
class PostRestoreTests(unittest.TestCase):
 def fixture(self):
  intent={'generation':'a'*32};receipt={'id':'b'*32,'transactions':[{'scope':'A'},{'scope':'B'}]}
  state={'generation':intent['generation'],'resources':{ID:{'status':'restored','activation_recovery':{'generation':intent['generation'],'holders':['A','B']}}},
   'maintenance_restore':{'identity':ID,'status':'restored','reviewed_sha256':'c'*64}}
  return intent,receipt,state
 def test_interrupted_post_action_resumes_original_and_progress_separately(self):
  intent,receipt,state=self.fixture();seen=[]
  class Provider:
   def resume_original(self,identity,*,root,state,prior_resume,record_before_write):
    seen.append((copy.deepcopy(state),copy.deepcopy(prior_resume)))
    record_before_write({'kind':'FixtureProgress','generation':intent['generation'],'done':['A']})
    if len(seen)==1:raise RuntimeError('simulated process boundary')
    self.assertion=state['holders']==['A','B'] and prior_resume['done']==['A']
    return {'status':'restored'}
  provider=Provider()
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   with self.assertRaisesRegex(RuntimeError,'process boundary'):post.run(ID,provider,intent,receipt,state,root=root)
   retained=read_private(root/STATE)
   self.assertTrue(retained['resources'][ID]['resume_original_pending'])
   post.run(ID,provider,intent,receipt,retained,root=root)
   self.assertTrue(provider.assertion);self.assertFalse(retained['resources'][ID]['resume_original_pending'])
 def test_foreign_receipt_activation_and_action_refuse_before_callback(self):
  for change in ('receipt','activation','action'):
   intent,receipt,state=self.fixture()
   class Provider:
    calls=0
    def resume_original(self,identity,*,record_before_write,**kw):self.calls+=1;record_before_write({'kind':'FixtureProgress'});return {}
   provider=Provider()
   with tempfile.TemporaryDirectory() as d:
    root=Path(d);post.run(ID,provider,intent,receipt,state,root=root)
    if change=='receipt':receipt['id']='d'*32
    elif change=='activation':state['resources'][ID]['activation_recovery']['holders']=['B']
    else:state['maintenance_restore']['reviewed_sha256']='e'*64
    with self.assertRaisesRegex(ResourceError,'different action'):post.run(ID,provider,intent,receipt,state,root=root)
    self.assertEqual(provider.calls,1)
 def test_files_must_already_be_restored(self):
  intent,receipt,state=self.fixture();state['resources'][ID]['status']='active'
  with self.assertRaisesRegex(ResourceError,'completed file'):post.run(ID,object(),intent,receipt,state)
 def test_another_pending_shared_action_is_refused_before_restore(self):
  from maintenance.resource_lifecycle import _restore_marker_preflight
  intent,receipt,state=self.fixture()
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);marker=root/'home/.boiler/resource-maintenance/lifecycle-action.json'
   _json_write(marker,dict(kind='BoilerResourceMaintenanceAction',generation=intent['generation'],identity='resource:other',status='restoring',reviewed_sha256='c'*64))
   with self.assertRaisesRegex(ResourceError,'Another interrupted'):_restore_marker_preflight(ID,intent,root=root)
   _json_write(marker,dict(kind='BoilerResourceMaintenanceAction',generation=intent['generation'],identity=ID,status='restoring',reviewed_sha256='c'*64))
   _json_write(root/STATE,state);_restore_marker_preflight(ID,intent,root=root)
   state['maintenance_restore']['reviewed_sha256']='d'*64;_json_write(root/STATE,state)
   with self.assertRaisesRegex(ResourceError,'binding changed'):_restore_marker_preflight(ID,intent,root=root)

if __name__=='__main__':unittest.main()
