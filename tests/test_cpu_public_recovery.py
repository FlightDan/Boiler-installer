import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest import mock
from maintenance import cpu_sidecar_recovery as recovery,cpu_sidecar,cpu_sidecar_store,cpu_live_probe,cpu_sidecar_ledger as ledger
from test_cpu_sidecar_ledger import values,GEN,BOOT,INV,TX

class RecoveryTests(unittest.TestCase):
 def attempt(self):
  before=values(4000000,'balance_power');desired=values(3000000,'performance')
  state=ledger.prepare(None,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,config_sha256='c'*64,expected_config_sha256='d'*64,before=before,desired=desired,expected_after=desired,public_state=None)
  return state,before,desired
 def test_only_exact_public_transaction_is_accepted(self):
  state,before,desired=self.attempt();attempt=state['attempts'][-1]
  public={'before':before,'owned':{p:list({before[p],desired[p]}) for p in before}}
  recovery.validate_public(attempt,public)
  public['owned'][next(iter(before))].append('3500000')
  with self.assertRaises(recovery.RecoveryError):recovery.validate_public(attempt,public)
 def test_public_restore_under_lock_after_durable_gate_close(self):
  state,before,desired=self.attempt();public={'before':before,'owned':{p:list({before[p],desired[p]}) for p in before}}
  class System:
   def service(self,unit):return 'failed'
   def boot_id(self):return BOOT
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);gate=root/'gate';gate.write_text('gate');lock=root/'lock';events=[];writes=[]
   def public_restore():
    self.assertFalse(gate.exists());self.assertEqual(events[0]['phase'],'reconciling');writes.append(True)
    return {'external_changes':[]}
   with mock.patch.object(cpu_sidecar,'GATE',gate),mock.patch.object(recovery,'LOCK',lock),mock.patch.object(cpu_sidecar,'_current_root',return_value=({'generation':GEN},TX)),mock.patch.object(cpu_sidecar,'read_private',return_value={'generation':GEN,'identity':recovery.IDENTITY}),mock.patch.object(cpu_sidecar_store,'_snapshot_with_history',return_value=state),mock.patch.object(cpu_sidecar_store,'_transition') as transition,mock.patch.object(cpu_live_probe,'probe_observe'),mock.patch.object(cpu_sidecar,'_observations',side_effect=[desired,before]),mock.patch.object(cpu_sidecar,'_public_state',return_value=public),mock.patch.object(recovery,'_module',return_value={'restore_locked':public_restore}):
    result=recovery.recover(state={'kind':'BoilerCpuLiveRecovery','generation':GEN,'transaction':TX},record_before_write=events.append,system=System())
   self.assertEqual(result['phase'],'stopped');self.assertEqual(len(writes),1);self.assertEqual(transition.call_args.args[1],'reconciled')
 def test_actual_pinned_public_restore_preserves_third_value(self):
  state,before,desired=self.attempt();paths=list(before);current=copy.deepcopy(before)
  current[paths[0]]=desired[paths[0]];current[paths[2]]='3500000'
  public={'before':before,'owned':{p:list({before[p],desired[p]}) for p in before}}
  class System:
   def service(self,unit):return 'failed'
   def boot_id(self):return BOOT
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);gate=root/'gate';gate.write_text('gate');public_file=root/'state.json';public_file.write_text(json.dumps(public));public_file.chmod(0o600)
   files={p:root/('policy'+str(i)) for i,p in enumerate(paths)}
   for p in paths:files[p].write_text(current[p]+'\n')
   def mapped(path):return files.get(str(path),Path(path))
   def observations(_):return {p:files[p].read_text().strip() for p in paths}
   source=Path(os.environ['BOILER_CPU_TEST_POOL'])/'000-cpu_control.py'
   with mock.patch.object(recovery,'MODULE',source):namespace=recovery._module()
   namespace['STATE']=public_file;namespace['Path']=mapped
   with mock.patch.object(cpu_sidecar,'GATE',gate),mock.patch.object(recovery,'LOCK',root/'lock'),mock.patch.object(cpu_sidecar,'_current_root',return_value=({'generation':GEN},TX)),mock.patch.object(cpu_sidecar,'read_private',return_value={'generation':GEN,'identity':recovery.IDENTITY}),mock.patch.object(cpu_sidecar_store,'_snapshot_with_history',return_value=state),mock.patch.object(cpu_sidecar_store,'_transition'),mock.patch.object(cpu_live_probe,'probe_observe'),mock.patch.object(cpu_sidecar,'_observations',side_effect=observations),mock.patch.object(cpu_sidecar,'_public_state',return_value=public),mock.patch.object(recovery,'_module',return_value=namespace):
    result=recovery.recover(state={'kind':'BoilerCpuLiveRecovery','generation':GEN,'transaction':TX},record_before_write=lambda value:None,system=System())
   self.assertEqual(files[paths[0]].read_text().strip(),before[paths[0]])
   self.assertEqual(files[paths[2]].read_text().strip(),'3500000');self.assertIn(paths[2],result['external_preserved']);self.assertFalse(public_file.exists())
 def test_missing_public_state_recovers_only_owned_value_after_checkpoint(self):
  state,before,desired=self.attempt();current=copy.deepcopy(before);paths=list(before)
  current[paths[0]]=desired[paths[0]];current[paths[2]]='3500000';after=copy.deepcopy(before);after[paths[2]]='3500000'
  class System:
   def service(self,unit):return 'failed'
   def boot_id(self):return BOOT
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);gate=root/'gate';gate.write_text('gate');target=root/'policy';target.write_text(current[paths[0]]);events=[]
   def mapped(path):return target if str(path)==paths[0] else Path(path)
   def writer(path,value):
    self.assertFalse(gate.exists());self.assertEqual(events[-1]['target'],path);target.write_text(value+'\n')
   with mock.patch.object(cpu_sidecar,'GATE',gate),mock.patch.object(recovery,'LOCK',root/'lock'),mock.patch.object(recovery,'Path',side_effect=mapped),mock.patch.object(cpu_sidecar,'_current_root',return_value=({'generation':GEN},TX)),mock.patch.object(cpu_sidecar,'read_private',return_value={'generation':GEN,'identity':recovery.IDENTITY}),mock.patch.object(cpu_sidecar_store,'_snapshot_with_history',return_value=state),mock.patch.object(cpu_sidecar_store,'_transition'),mock.patch.object(cpu_live_probe,'probe_observe'),mock.patch.object(cpu_sidecar,'_observations',side_effect=[current,after]),mock.patch.object(cpu_sidecar,'_public_state',return_value=None),mock.patch.object(recovery,'_module',return_value={'write_verified':writer}):
    result=recovery.recover(state={'kind':'BoilerCpuLiveRecovery','generation':GEN,'transaction':TX},record_before_write=events.append,system=System())
   self.assertEqual(target.read_text().strip(),before[paths[0]]);self.assertEqual(result['external_preserved'],[paths[2]])
 def test_history_restores_first_not_previous_invocation_before(self):
  state,first,desired=self.attempt()
  state=ledger.complete(state,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=desired,config_sha256='d'*64)
  state=ledger.stopped(state,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=desired)
  newer=values(2000000,'power')
  state=ledger.prepare(state,generation=GEN,boot=BOOT,invocation='f'*32,transaction=TX,config_sha256='c'*64,expected_config_sha256='d'*64,before=desired,desired=newer,expected_after=newer)
  baseline,owned=recovery._owned_history(state,BOOT,first)
  self.assertEqual(baseline,first)
  for path in first:self.assertEqual(owned[path],{desired[path],newer[path]})
 def test_partial_kill_then_full_first_before_and_external_preserved(self):
  state,first,old=self.attempt();state=ledger.complete(state,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=old,config_sha256='d'*64)
  state=ledger.stopped(state,generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=old)
  middle=values(2500000,'balance_performance')
  state=ledger.prepare(state,generation=GEN,boot=BOOT,invocation='a'*32,transaction=TX,config_sha256='c'*64,expected_config_sha256='d'*64,before=old,desired=middle,expected_after=middle)
  state=ledger.complete(state,generation=GEN,boot=BOOT,invocation='a'*32,transaction=TX,after=middle,config_sha256='d'*64)
  state=ledger.stopped(state,generation=GEN,boot=BOOT,invocation='a'*32,transaction=TX,after=middle);new=values(2000000,'power')
  state=ledger.prepare(state,generation=GEN,boot=BOOT,invocation='f'*32,transaction=TX,config_sha256='c'*64,expected_config_sha256='d'*64,before=middle,desired=new,expected_after=new)
  paths=list(first);current=copy.deepcopy(first);current[paths[0]]=new[paths[0]];current[paths[2]]='3500000';current[paths[4]]=old[paths[4]]
  class System:
   def service(self,unit):return 'failed'
   def boot_id(self):return BOOT
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);gate=root/'gate';gate.write_text('gate');target=root/'policy';target.write_text(current[paths[0]]);target2=root/'policy2';target2.write_text(current[paths[4]]);receipt=root/'receipt'
   def mapped(path):return {paths[0]:target,paths[4]:target2}.get(str(path),Path(path))
   def record(value):receipt.write_text(json.dumps(value))
   def writer_kill(path,value):target.write_text(value+'\n');os._exit(73)
   def invoke(observations,writer,prior=None):
    with mock.patch.object(cpu_sidecar,'GATE',gate),mock.patch.object(recovery,'LOCK',root/'lock'),mock.patch.object(recovery,'Path',side_effect=mapped),mock.patch.object(cpu_sidecar,'_current_root',return_value=({'generation':GEN},TX)),mock.patch.object(cpu_sidecar,'read_private',return_value={'generation':GEN,'identity':recovery.IDENTITY}),mock.patch.object(cpu_sidecar_store,'_snapshot_with_history',return_value=state),mock.patch.object(cpu_sidecar_store,'_transition'),mock.patch.object(cpu_live_probe,'probe_observe'),mock.patch.object(cpu_sidecar,'_observations',side_effect=observations),mock.patch.object(cpu_sidecar,'_public_state',return_value=None),mock.patch.object(recovery,'_module',return_value={'write_verified':writer}):
     return recovery.recover(state={'kind':'BoilerCpuLiveRecovery','generation':GEN,'transaction':TX},record_before_write=record,system=System(),prior_restore=prior)
   child=os.fork()
   if child==0:invoke([current],writer_kill);os._exit(1)
   _,status=os.waitpid(child,0);self.assertEqual(os.waitstatus_to_exitcode(status),73)
   self.assertFalse(gate.exists());self.assertEqual(target.read_text().strip(),first[paths[0]])
   partial=copy.deepcopy(first);partial[paths[2]]='3500000';partial[paths[4]]=old[paths[4]]
   after=copy.deepcopy(first);after[paths[2]]='3500000'
   def finish(path,value):self.assertEqual(path,paths[4]);target2.write_text(value+'\n')
   result=invoke([partial,after],finish,json.loads(receipt.read_text()))
   self.assertEqual(target2.read_text().strip(),first[paths[4]])
   self.assertEqual(result['phase'],'stopped');self.assertEqual(result['external_preserved'],[paths[2]])

if __name__=='__main__':unittest.main()
