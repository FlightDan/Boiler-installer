import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import unittest
from pathlib import Path
from unittest.mock import patch
from maintenance import cpu_live_activation as cpu

ID='resource:intel-g3e-public-cpu-controls'
class System:
 def __init__(self,status='inactive'):self.status=status
 def service(self,unit):return self.status
 def boot_id(self):return 'a'*36
 def run(self,*args,**kwargs):raise AssertionError('Unstarted restoration must not run a hardware or service command')

class UnstartedRestoreTests(unittest.TestCase):
 def test_failed_before_sidecar_remains_removable_without_cpu_or_dependency_writes(self):
  calls=[]
  with patch.object(cpu.cpu_sidecar_store,'_snapshot',side_effect=FileNotFoundError), \
       patch.object(cpu.cpu_sidecar,'_public_state',return_value=None), \
       patch.object(cpu.cpu_sidecar,'_current_root',return_value=({'generation':'b'*32},'c'*32)) as bound, \
       patch.object(cpu.cpu_live_probe,'probe_observe',side_effect=AssertionError('No CPU writes/reads needed')), \
       patch.object(cpu.cpu_sidecar,'_dependencies',side_effect=AssertionError('Failed dependency is not required to remove files')):
   result=cpu.restore(ID,root=Path('/'),state=None,record_before_write=calls.append,system=System())
  self.assertEqual(result,calls[-1]);self.assertTrue(result['metadata_only']);self.assertEqual(result['phase'],'stopped')
  bound.assert_called_once_with(require_gate=False,verify_dependencies=False)
 def test_foreign_activation_generation_is_preserved(self):
  with patch.object(cpu.cpu_sidecar_store,'_snapshot',side_effect=FileNotFoundError), \
       patch.object(cpu.cpu_sidecar,'_public_state',return_value=None), \
       patch.object(cpu.cpu_sidecar,'_current_root',return_value=({'generation':'b'*32},'c'*32)):
   with self.assertRaisesRegex(cpu.CpuActivationError,'generation differs'):
    cpu.restore(ID,state={'kind':'BoilerCpuLiveRecovery','generation':'d'*32,'transaction':'c'*32},record_before_write=lambda x:self.fail('must not publish'),system=System())

if __name__=='__main__':unittest.main()
