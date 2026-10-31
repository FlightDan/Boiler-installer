import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from maintenance import cpu_live_resources as live,resource_access,resource_targets,resource_firstboot,resource_activation,live_root
from test_cpu_live_contract import resource

GEN='a'*32;TX='b'*32;BOOT='12345678-1234-1234-1234-123456789abc'
class Target:
 def __init__(self,roots,intent):self.roots=roots;self.intent=intent;self.readonly=False;self.mounts=['held'];self.calls=0
 def revalidate(self):self.calls+=1
class Commands:
 def identity(self):return {'partuuid':'fixture'}
class System:
 def boot_id(self):return BOOT
 def service(self,unit):return 'inactive'
class Pool:
 def resource(self,identity):return resource()
class LiveBindingTests(unittest.TestCase):
 def test_requires_lock_scope_lease_and_stopped_receipt(self):
  with tempfile.TemporaryDirectory() as tmp:
   roots={s:Path(tmp)/s for s in ('A','B')}
   for p in roots.values():p.mkdir()
   slots={s:{'root':p} for s,p in roots.items()};engine=SimpleNamespace(root=roots['A'],target_domain='root')
   intent={'generation':GEN,'slot':'A','root_partuuid':'fixture','target_roles':{},
           'resource_receipt':{'transactions':[{'component':live.IDENTITY,'scope':'A','transaction':TX}]}}
   state={'generation':GEN,'resources':{live.IDENTITY:{'status':'activation_restored','restore_result':
     {'kind':'BoilerCpuLiveRestore','phase':'stopped','generation':GEN,'boot_id':BOOT,'transaction':TX}}},
     'maintenance_restore':{'identity':live.IDENTITY,'reviewed_sha256':'c'*64}}
   binding=Target(roots,intent)
   with mock.patch.object(resource_targets,'TargetMounts',Target),mock.patch.object(resource_firstboot,'checked_intent',return_value=intent),mock.patch.object(resource_firstboot,'read_private',return_value=state),mock.patch.object(live_root,'RootCommands',Commands),mock.patch.object(resource_activation,'LiveSystem',System),mock.patch.object(live,'completed_stop',side_effect=lambda i,s,**kw: None if s['resources'][live.IDENTITY]['restore_result']['transaction']==TX else (_ for _ in ()).throw(ValueError('wrong transaction'))):
    with self.assertRaises(ValueError):live.authorize_files(engine,Pool(),slots,'A',operation='restore/recover',binding=binding)
    a=resource_access.operation.set('/');b=resource_access.write_scope.set('/')
    try:
     permit=live.authorize_files(engine,Pool(),slots,'A',operation='restore/recover',binding=binding)
     self.assertIsInstance(permit,live._CpuLiveFilePermit);self.assertGreater(binding.calls,0)
     state['resources'][live.IDENTITY]['restore_result']['transaction']='d'*32
     with self.assertRaises(ValueError):live.authorize_files(engine,Pool(),slots,'A',operation='restore/recover',binding=binding)
    finally:resource_access.write_scope.reset(b);resource_access.operation.reset(a)

class CompletedStopTests(unittest.TestCase):
 def test_readonly_stop_proof_and_foreign_attempt_rejected(self):
  import contextlib
  from maintenance import engine, cpu_resources, cpu_sidecar_store
  intent={'generation':GEN,'slot':'A','selection':{'components':[live.IDENTITY]},
          'resource_receipt':{'transactions':[{'component':live.IDENTITY,'scope':'A','transaction':TX}]}}
  proof={'kind':'BoilerCpuLiveRestore','phase':'stopped','generation':GEN,'transaction':TX,'boot_id':BOOT,'invocation':'e'*32}
  state={'generation':GEN,'slot':'A','resources':{live.IDENTITY:{'status':'activation_restored','restore_result':proof}},
         'maintenance_restore':{'identity':live.IDENTITY,'reviewed_sha256':'c'*64}}
  class Engine:
   identity='root-id'
   def __init__(self,*a,**kw):pass
   def _journal(self,tx):return tx
   def _state(self):return {'components':{'core:maintenance':{'transaction':'1'},'core:resource-firstboot':{'transaction':'2'}}}
  journals={TX:{'id':TX,'root':'root-id','status':'restored','component':{'id':live.IDENTITY}},
            '1':{'id':'1','root':'root-id','status':'committed','component':{'id':'core:maintenance'},'operations':[]},
            '2':{'id':'2','root':'root-id','status':'committed','component':{'id':'core:resource-firstboot'},'operations':[]}}
  ledger={'generation':GEN,'transaction':TX,'attempts':[{'boot':BOOT,'invocation':'e'*32,'phase':'stopped'}]}
  @contextlib.contextmanager
  def pool(*a):yield Pool(),[]
  with mock.patch.object(engine,'Engine',Engine),mock.patch.object(live,'fixed_component'), \
       mock.patch.object(resource_firstboot,'read_private',side_effect=lambda path:journals[path]), \
       mock.patch.object(resource_firstboot,'open_pool',pool),mock.patch.object(cpu_sidecar_store,'_snapshot_with_history',return_value=ledger), \
       mock.patch.object(resource_activation,'LiveSystem',System):
   token=resource_access.operation.set('/')
   try:
    self.assertFalse(live.completed_stop(intent,state)['historical'])
    ledger['attempts'][-1]['invocation']='f'*32
    with self.assertRaises(ValueError):live.completed_stop(intent,state)
    ledger['attempts'][-1]['invocation']='e'*32
    proof['transaction']='f'*32
    with self.assertRaises(ValueError):live.completed_stop(intent,state)
    proof['transaction']=TX
    with mock.patch.object(System,'boot_id',return_value='22345678-1234-1234-1234-123456789abc'):
     self.assertTrue(live.completed_stop(intent,state)['historical'])
     ledger['attempts'].insert(0,{'boot':'22345678-1234-1234-1234-123456789abc','invocation':'f'*32,'phase':'prepared'})
     with self.assertRaises(ValueError):live.completed_stop(intent,state)
     ledger['attempts'].pop(0)
     with mock.patch.object(System,'service',return_value='unknown'):
      self.assertTrue(live.completed_stop(intent,state)['historical'])
    with mock.patch.object(System,'service',return_value='unknown'):
     self.assertFalse(live.completed_stop(intent,state)['historical'])
     journals[TX]['status']='committed'
     with self.assertRaisesRegex(ValueError,'not quiescent'):live.completed_stop(intent,state)
     journals[TX]['status']='restored'
     original_exists=Path.exists
     with mock.patch.object(Path,'exists',lambda path: True if str(path)=='/etc/systemd/system/oxp3-cpu.service' else original_exists(path)):
      with self.assertRaisesRegex(ValueError,'not quiescent'):live.completed_stop(intent,state)
    for status in ('active','activating','reloading'):
     with mock.patch.object(System,'service',return_value=status):
      with self.assertRaises(ValueError):live.completed_stop(intent,state)
   finally:resource_access.operation.reset(token)

if __name__=='__main__':unittest.main()

