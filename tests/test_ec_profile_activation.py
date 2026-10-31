import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import unittest
from types import SimpleNamespace
from unittest import mock
from maintenance import ec_profile,ec_profile_activation as provider

class System:
 def __init__(self):self.active=False;self.calls=[]
 def boot_id(self):return '12345678-1234-1234-1234-123456789abc'
 def service(self,unit,user=False):return 'active' if self.active else 'inactive'
 def invocation(self,unit,user=False):return 'b'*32 if self.active else None
 def run(self,*args,**kwargs):
  self.calls.append(args)
  if args[:2]==('systemctl','start'):self.active=True
  if args[:2]==('systemctl','stop'):self.active=False
  return (0,'')
class Pool:
 def resource(self,identity):return {'ec_profile_contract':{'profile':'oxp3-g1i-v1'}}
class ECProviderTests(unittest.TestCase):
 def test_missing_initramfs_proof_rejected_before_module_or_gate_access(self):
  intent={'selection':{'components':[ec_profile.IDENTITY]},'ec_board_profile':ec_profile.PROFILE}
  class Root:
   def identity(self):return {'partuuid':'fixture'}
  with mock.patch.object(ec_profile.os,'uname',return_value=SimpleNamespace(release=ec_profile.KERNEL)),mock.patch.object(ec_profile,'RootCommands',Root),mock.patch.object(ec_profile,'checked_intent',return_value=intent),mock.patch.object(ec_profile,'Engine') as engine,mock.patch.object(ec_profile,'_fixed_module') as module:
   with self.assertRaises(ec_profile.ECProfileError):ec_profile._context(gate=True)
   engine.assert_not_called();module.assert_not_called()
 def test_load_recorded_before_service_and_conflict_never_unloads(self):
  sys=System();events=[];intent={'generation':'a'*32}
  with mock.patch.object(ec_profile,'_context',return_value=(intent,'c'*32)),mock.patch.object(ec_profile,'_loaded',side_effect=[False,True]):
   result=provider.activate(ec_profile.IDENTITY,pool=Pool(),facts={},selection=[ec_profile.IDENTITY],
      record_before_write=lambda state:events.append(('record',state)),approve=lambda identity:events.append(('approve',identity)),system=sys)
  self.assertEqual(events[0][0],'record');self.assertTrue(events[0][1]['load_pending'])
  self.assertEqual(events[1][0],'approve');self.assertEqual(result['ownership'],'loaded-by-this-generation')
  self.assertFalse(any('unload' in str(c) or 'remove' in str(c) for c in sys.calls))
  sys=System()
  with mock.patch.object(ec_profile,'_context',return_value=(intent,'c'*32)),mock.patch.object(ec_profile,'_loaded',side_effect=ec_profile.ECProfileError('foreign')):
   with self.assertRaises(ec_profile.ECProfileError):provider.activate(ec_profile.IDENTITY,pool=Pool(),facts={},selection=[ec_profile.IDENTITY],record_before_write=lambda x:None,approve=lambda x:None,system=sys)
  self.assertEqual(sys.calls,[])
 def test_loader_requires_wal_and_uses_only_fixed_module_parameter(self):
  intent={'generation':'a'*32};transaction='c'*32
  state={'generation':'a'*32,'resources':{ec_profile.IDENTITY:{'status':'activating','activation_recovery':
   {'kind':'BoilerEcProfileRecovery','generation':'a'*32,'transaction':transaction,'load_pending':True}}}}
  with mock.patch.object(ec_profile,'_context',return_value=(intent,transaction)),mock.patch.object(ec_profile,'read_private',return_value=state),mock.patch.object(ec_profile,'_loaded',side_effect=[False,True]),mock.patch.object(ec_profile.subprocess,'run') as command:
   self.assertEqual(ec_profile.load()['status'],'loaded')
   self.assertEqual(command.call_args.args[0],['/usr/bin/insmod',str(ec_profile.MODULE),'board_profile=oxp3-g1i-v1'])
  state['resources'][ec_profile.IDENTITY]['activation_recovery']['load_pending']=False
  with mock.patch.object(ec_profile,'_context',return_value=(intent,transaction)),mock.patch.object(ec_profile,'read_private',return_value=state),mock.patch.object(ec_profile,'_loaded',return_value=False),mock.patch.object(ec_profile.subprocess,'run') as command:
   with self.assertRaises(ec_profile.ECProfileError):ec_profile.load()
   command.assert_not_called()

if __name__=='__main__':unittest.main()
