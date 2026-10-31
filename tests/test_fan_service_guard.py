import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import os,tempfile,unittest
from pathlib import Path
from unittest import mock
from maintenance import fan_service_guard as guard
from maintenance import fan_live_activation as provider

GEN='a'*32;BOOT='12345678-1234-1234-1234-123456789abc';INV='b'*32
class GuardTests(unittest.TestCase):
 def test_guarded_start_and_stop(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);ctx=({'generation':GEN},BOOT,INV,{'configuration':{'mode':'auto'}})
   with mock.patch.object(guard,'ROOT',root),mock.patch.object(guard,'_context',return_value=ctx) as context,mock.patch.object(guard.ec_readonly_probe,'probe',return_value={'values':{'pwm':100}}):
    self.assertEqual(guard.prestart()['phase'],'prepared')
    with self.assertRaises(guard.FanGuardError):guard.prestart()
    with mock.patch.object(guard.subprocess,'run') as command:
     with self.assertRaises(guard.FanGuardError):guard.stop_auto()
     command.assert_not_called()
    self.assertEqual(guard.poststart()['phase'],'started')
    self.assertEqual(guard.owned(GEN,BOOT,INV)['phase'],'started')
    self.assertEqual(guard.prestop()['phase'],'stopping')
    with mock.patch.object(guard.subprocess,'run') as command:
     guard.stop_auto();command.assert_called_once();context.assert_called_with(require_config=False)
    self.assertEqual(guard.poststop()['phase'],'stopped')
    with self.assertRaises(guard.FanGuardError):guard.owned(GEN,BOOT,INV)
 def test_config_change_during_start_is_not_claimed(self):
  with tempfile.TemporaryDirectory() as tmp:
   first=({'generation':GEN},BOOT,INV,{'configuration':{'mode':'auto'}})
   after=({'generation':GEN},BOOT,INV,{'configuration':{'mode':'balanced'}})
   with mock.patch.object(guard,'ROOT',Path(tmp)),mock.patch.object(guard,'_context',side_effect=[first,after]),mock.patch.object(guard.ec_readonly_probe,'probe',return_value={'values':{}}):
    guard.prestart()
    with self.assertRaises(guard.FanGuardError):guard.poststart()
 def test_interrupted_stop_requires_matching_stopped_invocation(self):
  class System:
   def service(self,unit):return 'inactive'
   def boot_id(self):return BOOT
  state={'kind':'BoilerFanRecovery','generation':GEN,'config':{'phase':'created'},'services':[{'user':False,'unit':'oxp3-fan.service','before':'inactive'}]}
  prior={'kind':'BoilerFanRestore','phase':'stopping','generation':GEN,'boot_id':BOOT,'invocation':INV}
  events=[]
  class RootCommands:
   def identity(self):return {'partuuid':'fixture'}
  with mock.patch.object(provider,'RootCommands',RootCommands),mock.patch.object(provider,'checked_intent',return_value={'generation':GEN}),mock.patch.object(provider.ec_profile,'_context'),mock.patch.object(provider.ec_profile,'_loaded',return_value=True),mock.patch.object(guard,'owned') as owned,mock.patch.object(provider.fan_live_config,'restore',return_value={'phase':'restored'}),mock.patch.object(provider.resource_activation,'restore') as service:
   result=provider.restore(provider.IDENTITY,state=state,record_before_write=events.append,system=System(),prior_restore=prior)
   owned.assert_called_once_with(GEN,BOOT,INV,phase='stopped');service.assert_not_called();self.assertEqual(result['phase'],'stopped')
   prior['boot_id']='00000000-0000-0000-0000-000000000000'
   with self.assertRaises(provider.FanActivationError):provider.restore(provider.IDENTITY,state=state,record_before_write=events.append,system=System(),prior_restore=prior)

if __name__=='__main__':unittest.main()
