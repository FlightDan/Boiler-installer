import contextlib, copy, unittest
from unittest.mock import patch
from types import SimpleNamespace
from maintenance import waydroid_activation as w

class WaydroidActivationTests(unittest.TestCase):
 def test_active_root_restore_opens_write_window_and_resumes_partial_removal(self):
  intent={'generation':'a'*32,'slot':'A','selection':{'trust':'local-test'}}
  state={'kind':'BoilerWaydroidRecovery','identity':w.IDENTITY,'generation':'a'*32,'slot':'A',
   'host':{'kind':'BoilerWaydroidPackageAction','generation':'a'*32,'slot':'A','phase':'installed','before':{n:None for n in w.alpm.ORDER},'added':list(w.alpm.ORDER)},'android':{'generation':'a'*32,'phase':'failed'}}
  writable=[];records=[]
  @contextlib.contextmanager
  def window(engine):
   writable.append(True)
   try:yield
   finally:writable.pop()
  class Backend:
   def __init__(self):self.values=dict(w.alpm.VERSIONS);self.interrupt=True
   def require_exact_target(self):pass
   def installed(self,name):return self.values[name]
   def remove_fixed(self,names):
    self_test.assertTrue(writable,'pacman removal escaped its writable root window')
    for name in names:
     self.values[name]=None
     if self.interrupt:self.interrupt=False;raise RuntimeError('partial package removal')
  self_test=self;backend=Backend()
  with patch.object(w,'LivePacman',return_value=backend),patch.object(w.android,'_inactive',return_value=True),patch('maintenance.resource_firstboot.checked_intent',return_value=intent),patch('maintenance.live_root.write_scope',side_effect=window),patch('maintenance.engine.Engine'):
   with self.assertRaisesRegex(RuntimeError,'partial package removal'):
    w.restore(w.IDENTITY,state=state,record_before_write=lambda value:records.append(copy.deepcopy(value)))
   self.assertEqual(records[-1]['host']['phase'],'removing');self.assertEqual(writable,[])
   result=w.restore(w.IDENTITY,state=records[-1],record_before_write=lambda value:records.append(copy.deepcopy(value)))
  self.assertEqual(result['status'],'restored');self.assertTrue(all(v is None for v in backend.values.values()));self.assertEqual(writable,[])
 def test_two_slot_preflight_rejects_foreign_package_before_restore(self):
  intent={'generation':'a'*32,'slot':'B','selection':{'trust':'local-test'}}
  state={'kind':'BoilerWaydroidRecovery','identity':w.IDENTITY,'generation':'a'*32,'slot':'B','host':{'kind':'BoilerWaydroidPackageAction','generation':'a'*32,'slot':'B','phase':'installed','before':{name:None for name in w.alpm.ORDER},'added':list(w.alpm.ORDER)},'android':{'generation':'a'*32,'phase':'ready'}}
  class Backend:
   def require_exact_target(self):pass
   def installed(self,name):return 'foreign-version' if name=='lxc' else w.alpm.VERSIONS[name]
  with patch.object(w,'LivePacman',return_value=Backend()):
   with self.assertRaisesRegex(RuntimeError,'outside this action'):w.preflight_slot('/fixture-B',intent,state)
   state['host']['generation']='c'*32
   with self.assertRaisesRegex(RuntimeError,'nested recovery generation'):w.preflight_slot('/fixture-B',intent,state)
 def test_network_failure_is_durable_and_retry_reuses_owned_host_state(self):
  intent={'generation':'a'*32,'slot':'A','selection':{'trust':'local-test'}}
  host={'generation':'a'*32,'slot':'A','phase':'prepared','before':{'lxc':None}};images={'generation':'a'*32,'phase':'prepared'};records=[]
  def host_retry(backend,state,record):
   value=dict(state,phase='installed');record(value);return value
  def image_fail(root,state,record):
   value=dict(state,phase='failed',error='offline');record(value);return value
  with patch('maintenance.live_root.write_scope',return_value=contextlib.nullcontext()),patch('maintenance.engine.Engine'),patch.object(w,'_binding',return_value=intent),patch.object(w,'_backend',return_value=object()),patch.object(w.android,'prepare',return_value=images),patch.object(w.alpm,'prepare',return_value=host),patch.object(w.alpm,'retry',side_effect=host_retry),patch.object(w.android,'activate',side_effect=image_fail):
   with self.assertRaisesRegex(RuntimeError,'offline'):w.activate(w.IDENTITY,pool=SimpleNamespace(local_test=True),facts={},selection=[w.IDENTITY],record_before_write=lambda x:records.append(copy.deepcopy(x)))
  failed=records[-1];self.assertEqual(failed['host']['phase'],'installed');self.assertEqual(failed['android']['phase'],'failed')
  def image_success(root,state,record):
   value=dict(state,phase='ready');record(value);return value
  with patch('maintenance.live_root.write_scope',return_value=contextlib.nullcontext()),patch('maintenance.engine.Engine'),patch.object(w,'_binding',return_value=intent),patch.object(w,'_backend',return_value=object()),patch.object(w.alpm,'retry',side_effect=host_retry),patch.object(w.android,'activate',side_effect=image_success):
   result=w.resume(w.IDENTITY,pool=SimpleNamespace(local_test=True),facts={},selection=[w.IDENTITY],state=failed,record_before_write=lambda x:records.append(copy.deepcopy(x)))
  self.assertEqual(result['status'],'configured');self.assertEqual(records[-1]['host']['before'],{'lxc':None});self.assertEqual(records[-1]['android']['phase'],'ready')

if __name__=='__main__':unittest.main()
