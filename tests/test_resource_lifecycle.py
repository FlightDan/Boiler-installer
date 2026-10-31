import contextlib,copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance import resource_lifecycle as lifecycle
from maintenance.resource_runtime import ResourceError
from maintenance.engine import _json_write

class RestoreReviewTests(unittest.TestCase):
 def test_pending_service_is_reconciled_only_from_explicit_review_and_keeps_original_before(self):
  state={'resources':{'resource:sample':{'activation_recovery':dict(boot_id='old',services=[dict(user=False,unit='owned.service',before='inactive',started=False,start_pending=True,invocation=None)])}}}
  original=copy.deepcopy(state)
  plan=dict(boot_id='new',services=[dict(user=False,unit='owned.service',active='active',invocation='c'*32)])
  lifecycle._reconcile_reviewed('resource:sample',state,plan)
  recovery=state['resources']['resource:sample']['activation_recovery'];row=recovery['services'][0]
  self.assertEqual(row['before'],'inactive');self.assertEqual(row['invocation'],'c'*32);self.assertFalse(row['start_pending'])
  self.assertEqual(state['resources']['resource:sample']['reviewed_restore_reconciliation']['before'],original['resources']['resource:sample']['activation_recovery'])
 def test_sameboot_external_invocation_is_never_adopted(self):
  state={'resources':{'resource:sample':{'activation_recovery':dict(boot_id='same',services=[dict(user=False,unit='owned.service',before='inactive',started=True,start_pending=False,invocation='a'*32)])}}}
  plan=dict(boot_id='same',services=[dict(user=False,unit='owned.service',active='active',invocation='b'*32)])
  lifecycle._reconcile_reviewed('resource:sample',state,plan)
  self.assertEqual(state['resources']['resource:sample']['activation_recovery']['services'][0]['invocation'],'a'*32)
 def test_exact_locked_recovery_does_not_recover_changed_pending(self):
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory);name='a'*32;path=base/(name+'.json')
   reviewed={'status':'restoring','component':{'id':'resource:sample'},'operations':[]}
   _json_write(path,dict(reviewed,unexpected='changed'))
   class Engine:
    journals=base
    recovered=False
    @contextlib.contextmanager
    def _lock(self,**kwargs):yield
    def _recover(self):self.recovered=True;return ['unsafe']
   engine=Engine()
   with self.assertRaisesRegex(ResourceError,'changed after restore review'):
    lifecycle._recover_reviewed(engine,'A','resource:sample',None,[dict(scope='A',transaction=name,journal_sha256=lifecycle.digest(reviewed))])
   self.assertFalse(engine.recovered)
 def test_foreign_second_slot_pending_refuses_before_any_mutation(self):
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory);engines={}
   for scope in ('A','B','shared-home'):
    folder=base/scope;folder.mkdir()
    class Engine:pass
    engine=Engine();engine.journals=folder;engines[scope]=engine
   _json_write(engines['A'].journals/('a'*32+'.json'),dict(status='restoring',component={'id':'resource:sample'},operations=[]))
   _json_write(engines['B'].journals/('b'*32+'.json'),dict(status='applying',component={'id':'resource:foreign'},operations=[]))
   receipt=dict(id='c'*32,transactions=[dict(scope='A',transaction='a'*32,component='resource:sample')])
   class Session:
    def __init__(self):
     self.engines={s:engines[s] for s in ('A','B')};self.shared=engines['shared-home']
     self.pool=type('Pool',(),{'resource':lambda self,i:{'install':{'kind':'files-v1'}}})()
    def _bound_receipt(self,identity):return receipt
   intent=dict(selection={'components':['resource:sample']},resource_receipt=receipt)
   before={str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()}
   with patch.object(lifecycle,'_session',return_value=(Session(),[])),patch.object(lifecycle,'_json_write') as write:
    with self.assertRaisesRegex(ResourceError,'Unrelated pending'):lifecycle._snapshot(intent,'resource:sample',None)
    write.assert_not_called()
   self.assertEqual(before,{str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()})

if __name__=='__main__':unittest.main()

