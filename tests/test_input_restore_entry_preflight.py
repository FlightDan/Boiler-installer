import copy,sys,types,unittest
from unittest.mock import patch
from maintenance import input_activation
fake_firstboot=types.ModuleType('maintenance.resource_firstboot')
fake_firstboot.status=lambda:None
fake_firstboot.validate_restore_binding=lambda *a:None
fake_firstboot.operation_lock=lambda *a:None
with patch.dict(sys.modules,{'maintenance.resource_firstboot':fake_firstboot}):
 from maintenance import resource_lifecycle

class RestoreEntryPreflightTests(unittest.TestCase):
 def test_foreign_ownership_refuses_before_firstboot_write(self):
  identity=input_activation.INPUT;recovery={'generation':'a'*32};state={'resources':{identity:{'activation_recovery':recovery}}}
  before=copy.deepcopy(state);calls=[]
  with patch.object(resource_lifecycle.firstboot,'status',return_value=state),patch.object(resource_lifecycle.firstboot,'validate_restore_binding'),patch.object(input_activation,'preflight_restore',return_value={}),patch.object(input_activation,'_preflight_preloaded_custom_ownership',side_effect=input_activation.InputActivationError('foreign')),patch.object(resource_lifecycle,'_json_write',side_effect=lambda *a:calls.append(a)):
   with self.assertRaisesRegex(input_activation.InputActivationError,'foreign'):
    resource_lifecycle._restore_input(identity,{}, {}, 'digest')
  self.assertEqual(calls,[]);self.assertEqual(state,before)
 def test_outer_entry_rejects_before_lock_mount_service_or_writes(self):
  identity=input_activation.INPUT;recovery={'generation':'a'*32};state={'resources':{identity:{'activation_recovery':recovery}}}
  before=copy.deepcopy(state);calls=[]
  def forbidden(*a,**kw):calls.append((a,kw));raise AssertionError('Unexpected mutation or mount')
  with patch.object(resource_lifecycle,'_intent',return_value={}),patch.object(resource_lifecycle.firstboot,'status',return_value=state),patch.object(resource_lifecycle.firstboot,'validate_restore_binding'),patch.object(input_activation,'preflight_restore',return_value={}),patch.object(input_activation,'_preflight_preloaded_custom_ownership',side_effect=input_activation.InputActivationError('foreign')),patch.object(resource_lifecycle.firstboot,'operation_lock',side_effect=forbidden),patch.object(resource_lifecycle,'TargetMounts',side_effect=forbidden),patch.object(resource_lifecycle,'Engine',side_effect=forbidden),patch.object(resource_lifecycle,'_json_write',side_effect=forbidden),patch.object(input_activation,'command',side_effect=forbidden):
   with self.assertRaisesRegex(input_activation.InputActivationError,'foreign'):resource_lifecycle.restore(identity,'digest')
  self.assertEqual(calls,[]);self.assertEqual(state,before)

if __name__=='__main__':unittest.main()
