import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import unittest
from unittest import mock
from maintenance import cpu_live_activation as provider,cpu_sidecar,cpu_sidecar_store,cpu_live_probe
from test_cpu_sidecar_ledger import values,GEN,BOOT,INV,TX

class System:
 def boot_id(self):return BOOT
 def invocation(self,unit):return INV
 def service(self,unit):return 'active'
 def run(self,*args,**kwargs):return (0,'Result=success\nExecMainStatus=0')
class UnloadTests(unittest.TestCase):
 def test_external_active_service_or_public_state_rejected_before_writes(self):
  class ExternalSystem(System):
   def __init__(self,status):self.status=status;self.calls=[]
   def service(self,unit):return self.status
   def run(self,*args,**kwargs):self.calls.append(args);return super().run(*args,**kwargs)
  for status,public in [('active',None),('inactive',{'external':True})]:
   system=ExternalSystem(status);events=[]
   with mock.patch.object(provider,'_validate'),mock.patch.object(cpu_sidecar,'_current_root',return_value=({'generation':GEN},TX)),mock.patch.object(cpu_sidecar,'_dependencies'),mock.patch.object(cpu_live_probe,'probe',return_value={}),mock.patch.object(cpu_sidecar,'_public_state',return_value=public),mock.patch.object(cpu_sidecar_store,'_snapshot',side_effect=cpu_sidecar_store.ledger.LedgerError('missing')):
    with self.assertRaises(provider.CpuActivationError):provider.activate(provider.IDENTITY,pool=object(),facts={},selection=[],record_before_write=events.append,approve=events.append,system=system)
   self.assertEqual(events,[]);self.assertEqual(system.calls,[])
 def test_disabled_after_public_restore_is_readonly_and_not_claimed_as_restart(self):
  before=values(4000000,'balance_power');after=values(3000000,'performance')
  entry={'boot':BOOT,'invocation':INV,'phase':'completed','before':before,'after':after,'expected_config_sha256':'c'*64}
  ledger={'generation':GEN,'transaction':TX,'boots':{BOOT:before},'attempts':[entry]}
  probe={'enabled':False,'config_sha256':'d'*64}
  with mock.patch.object(provider,'_validate'),mock.patch.object(cpu_sidecar,'_current_root',return_value=({'generation':GEN},TX)),mock.patch.object(cpu_sidecar,'_dependencies'),mock.patch.object(cpu_live_probe,'probe',return_value=probe),mock.patch.object(cpu_sidecar_store,'_snapshot',return_value=ledger),mock.patch.object(cpu_sidecar,'_observations',return_value=before),mock.patch.object(cpu_sidecar,'_public_state',return_value=None):
   result=provider.verify(provider.IDENTITY,pool=object(),facts={},selection=[],system=System())
  self.assertEqual(result['status'],'configured');self.assertEqual(result['policy'],'disabled');self.assertEqual(result['runtime'],'public-state-absent')

 def test_interrupted_stop_resumes_only_matching_durable_record(self):
  class StoppedSystem(System):
   def service(self,unit):return 'inactive'
  prior={'kind':'BoilerCpuLiveRestore','phase':'stopping','generation':GEN,'transaction':TX,'boot_id':BOOT,'invocation':INV,'external_before_stop':[]}
  state={'kind':'BoilerCpuLiveRecovery','generation':GEN,'transaction':TX}
  ledger={'generation':GEN,'transaction':TX,'attempts':[{'boot':BOOT,'invocation':INV,'phase':'stopped','external':['external']}]}
  writes=[]
  with mock.patch.object(cpu_sidecar,'_current_root',return_value=({'generation':GEN},TX)),mock.patch.object(cpu_sidecar_store,'_snapshot',return_value=ledger):
   result=provider.restore(provider.IDENTITY,state=state,record_before_write=writes.append,system=StoppedSystem(),prior_restore=prior)
   self.assertEqual(result['external_preserved'],['external']);self.assertEqual(writes,[result])
   ledger['attempts'][0]['invocation']='f'*32
   with self.assertRaises(provider.CpuActivationError):provider.restore(provider.IDENTITY,state=state,record_before_write=writes.append,system=StoppedSystem(),prior_restore=prior)

if __name__=='__main__':unittest.main()
