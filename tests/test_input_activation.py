import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import os
import unittest
from unittest.mock import patch
from maintenance import Engine
import maintenance.input_activation as activation

class ActivationTests(unittest.TestCase):
 def setUp(self):
  self.temporary=tempfile.TemporaryDirectory();self.base=Path(self.temporary.name);self.root=self.base/'root';self.root.mkdir()
  self.payload=self.base/'payload';self.payload.mkdir();(self.payload/'binary').write_bytes(b'fresh-source-runtime')
  (self.payload/'default').write_bytes(b'version: 1\nid: oxp3-installer\n')
  raw=(self.payload/'binary').read_bytes();default=(self.payload/'default').read_bytes()
  self.component=dict(schema_version=1,kind='ComponentManifest',id=activation.COMPONENTS[activation.INPUT],version='0.78.0-test',requires=[],files=[dict(source='binary',target='usr/bin/inputplumber',size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),mode=0o644)])
  self.config=dict(source='default',target=activation.CONFIGS[activation.INPUT],size=len(default),sha256=hashlib.sha256(default).hexdigest(),mode=0o600)
  self.units={'inputplumber.service':dict(enabled='enabled',active='active'),'boiler-input-hid.service':dict(enabled='not-found',active='unknown'),'inputplumber-suspend.service':dict(enabled='not-found',active='unknown')}
  self.events=[];self.saved=[];self.fail=None
  self.patches=[patch.object(activation,'live'),patch.object(activation,'_hardware',return_value={'actual':True}),patch.object(activation,'bundle',return_value=(self.payload,self.component,self.config)),patch.object(activation,'command',side_effect=self.command),patch.object(activation,'_preflight_preloaded_custom_ownership',return_value=False),patch.object(activation,'verify',return_value={'real-verification-replaced-in-fixture':True})]
  for current in self.patches:current.start()
 def tearDown(self):
  for current in reversed(self.patches):current.stop()
  self.temporary.cleanup()
 def command(self,*args,**kwargs):
  self.events.append(args)
  if args[0].endswith('systemctl'):
   operation=args[1]
   if operation in ('is-enabled','is-active'):return self.units[args[2]]['enabled' if operation=='is-enabled' else 'active']
   if self.fail==(operation,args[2] if len(args)>2 else None):self.fail=None;raise RuntimeError('injected service failure')
   if operation in ('start','stop','enable','disable'):
    self.units[args[2]]['active' if operation in ('start','stop') else 'enabled']={'start':'active','stop':'inactive','enable':'enabled','disable':'disabled'}[operation]
  return ''
 def callback(self,state):
  if not self.saved:
   self.assertFalse((self.root/'usr/bin/inputplumber').exists())
   self.assertFalse((self.root/'var/lib/boiler/maintenance').exists())
   self.assertFalse(any(len(e)>1 and e[1] in ('stop','start','enable','disable') for e in self.events))
  self.saved.append(copy.deepcopy(state))
 def install(self):
  return activation.activate(activation.INPUT,root=self.root,pool=None,facts={},selection=[activation.INPUT],record_before_write=self.callback,approve=lambda _:None)
 def restore(self,result):
  return activation.restore(activation.INPUT,root=self.root,state=result['recovery'],record_before_write=lambda s:self.saved.append(copy.deepcopy(s)))
 def test_write_ahead_then_real_engine_install_and_restore(self):
  result=self.install();self.assertEqual(result['status'],'active')
  self.assertTrue((self.root/'usr/bin/inputplumber').is_file());self.assertTrue(self.saved[1]['transaction'])
  self.restore(result);self.assertFalse((self.root/'usr/bin/inputplumber').exists())
  self.assertEqual(self.units['inputplumber.service'],dict(enabled='enabled',active='active'))
 def test_callback_failure_has_no_engine_or_service_mutation(self):
  def denied(_):raise RuntimeError('durable owner refused')
  with self.assertRaises(RuntimeError):activation.activate(activation.INPUT,root=self.root,pool=None,facts={},selection=[activation.INPUT],record_before_write=denied,approve=lambda _:None)
  self.assertFalse((self.root/'var').exists());self.assertEqual(self.units['inputplumber.service']['active'],'active')
 def test_missing_callback_refuses_before_hardware_or_writes(self):
  with self.assertRaises(activation.InputActivationError):activation.activate(activation.INPUT,root=self.root,pool=None,facts={},selection=[],record_before_write=None)
  self.assertFalse((self.root/'var').exists())
 def test_existing_user_capmap_preserved_through_install_restore(self):
  path=self.root/self.config['target'];path.parent.mkdir(parents=True);path.write_bytes(b'user-map-and-mode');path.chmod(0o600)
  before=path.read_bytes();mode=path.stat().st_mode;result=self.install();self.assertTrue(result['config_preserved']);self.restore(result)
  self.assertEqual(path.read_bytes(),before);self.assertEqual(path.stat().st_mode,mode)
 def test_post_install_user_edit_preserved_on_restore(self):
  result=self.install();path=self.root/self.config['target'];path.write_bytes(b'user-custom-map');self.restore(result)
  self.assertEqual(path.read_bytes(),b'user-custom-map')
 def test_post_install_user_removal_not_recreated(self):
  result=self.install();path=self.root/self.config['target'];path.unlink();self.restore(result);self.assertFalse(path.exists())
 def test_service_start_failure_restores_files_and_original_service(self):
  self.fail=('start','boiler-input-hid.service')
  with self.assertRaisesRegex(activation.InputActivationError,'prior state restored'):self.install()
  self.assertFalse((self.root/'usr/bin/inputplumber').exists());self.assertEqual(self.units['inputplumber.service']['active'],'active')
 def test_changed_immutable_file_blocks_before_service_stop(self):
  result=self.install();(self.root/'usr/bin/inputplumber').write_bytes(b'external');self.events.clear()
  with self.assertRaisesRegex(activation.InputActivationError,'immutable'):self.restore(result)
  self.assertFalse(any(len(e)>1 and e[1]=='stop' for e in self.events));self.assertEqual((self.root/'usr/bin/inputplumber').read_bytes(),b'external')
 def test_restore_ownership_refusal_before_callback_and_services(self):
  result=self.install();self.events.clear();saved=len(self.saved)
  before={str(p.relative_to(self.root)):(p.read_bytes(),p.stat().st_mode,p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
  with patch.object(activation,'_preflight_preloaded_custom_ownership',side_effect=activation.InputActivationError('foreign')):
   with self.assertRaisesRegex(activation.InputActivationError,'foreign'):self.restore(result)
  after={str(p.relative_to(self.root)):(p.read_bytes(),p.stat().st_mode,p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
  self.assertEqual(self.events,[]);self.assertEqual(len(self.saved),saved);self.assertEqual(before,after)
 def test_changed_generation_refuses_before_service_stop(self):
  result=self.install();state=copy.deepcopy(result['recovery']);state['transaction']='f'*32;self.events.clear()
  with self.assertRaisesRegex(activation.InputActivationError,'superseded'):activation.restore(activation.INPUT,root=self.root,state=state,record_before_write=lambda s:None)
  self.assertFalse(self.events)
 def test_verify_rejects_superseded_same_bytes_generation(self):
  result=self.install();new=copy.deepcopy(self.component);new.update(input_live_generation='f'*32,version='0.78.0-new-generation')
  Engine(self.root).apply(new,self.payload);self.patches[-1].stop();self.events.clear()
  before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
  with self.assertRaisesRegex(activation.InputActivationError,'superseded'):
   activation.verify(activation.INPUT,root=self.root,state=result['recovery'])
  self.assertFalse(self.events)
  self.assertEqual(before,{str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
 def test_verify_rejects_restored_generation(self):
  result=self.install();self.restore(result);self.patches[-1].stop();self.events.clear()
  with self.assertRaisesRegex(activation.InputActivationError,'not currently installed'):
   activation.verify(activation.INPUT,root=self.root,state=result['recovery'])
  self.assertFalse(self.events)
 def test_gyro_dependency_prevents_input_only_restore_before_live_changes(self):
  result=self.install();raw=b'gyro';(self.payload/'gyro').write_bytes(raw)
  gyro=dict(schema_version=1,kind='ComponentManifest',id=activation.COMPONENTS[activation.GYRO],version='1.0.0',requires=[activation.COMPONENTS[activation.INPUT]],files=[dict(source='gyro',target='usr/lib/boiler-input/gyro-test',size=4,sha256=hashlib.sha256(raw).hexdigest(),mode=0o644)])
  Engine(self.root).apply(gyro,self.payload);self.events.clear()
  with self.assertRaisesRegex(activation.InputActivationError,'dependent gyro'):self.restore(result)
  self.assertFalse(self.events)
 def exited_after_commit(self):
  import maintenance
  recovery=self.base/'recovery.json'
  script='''import sys,json,os
from pathlib import Path
sys.path.insert(0,BASELINE)
import maintenance
maintenance.__path__.insert(0,CANDIDATE)
import maintenance.input_activation as activation
from maintenance.engine import Engine
activation.live=lambda root:None
activation._hardware=lambda identity:{}
activation._preflight_preloaded_custom_ownership=lambda *args,**kwargs:False
activation.bundle=lambda pool,identity:(Path(PAYLOAD),COMPONENT,CONFIG)
def command(*args,**kwargs):
 if args[1]=='is-enabled':return 'enabled' if args[2]=='inputplumber.service' else 'not-found'
 if args[1]=='is-active':return 'active' if args[2]=='inputplumber.service' else 'unknown'
 return ''
activation.command=command
def record(value):Path(RECOVERY).write_text(json.dumps(value))
original=Engine.apply
def exit_after_commit(self,*args,**kwargs):
 original(self,*args,**kwargs)
 os._exit(73)
Engine.apply=exit_after_commit
activation.activate(activation.INPUT,root=Path(ROOT),pool=None,facts={},selection=[activation.INPUT],record_before_write=record,approve=lambda _:None)
'''
  namespace=dict(BASELINE=str(Path(maintenance.__file__).parents[1]),CANDIDATE=str(Path(activation.__file__).parent),PAYLOAD=str(self.payload),COMPONENT=self.component,CONFIG=self.config,RECOVERY=str(recovery),ROOT=str(self.root))
  prefix='\n'.join(name+'='+repr(value) for name,value in namespace.items())+'\n'
  result=subprocess.run([sys.executable,'-B','-c',prefix+script],capture_output=True,text=True)
  self.assertEqual(result.returncode,73,result.stdout+result.stderr)
  state=json.loads(recovery.read_text());self.assertIsNone(state['transaction'])
  return state
 def test_real_exit_after_commit_missing_callback_recovers_own_generation(self):
  state=self.exited_after_commit()
  activation.restore(activation.INPUT,root=self.root,state=state,record_before_write=lambda _:None)
  self.assertFalse((self.root/'usr/bin/inputplumber').exists())
 def test_real_exit_after_commit_then_supersession_does_not_restore_new_generation(self):
  state=self.exited_after_commit();new=copy.deepcopy(self.component);new.update(input_live_generation='f'*32,version='0.79.0-test')
  newer=Engine(self.root).apply(new,self.payload);self.events.clear()
  with self.assertRaisesRegex(activation.InputActivationError,'superseded'):activation.restore(activation.INPUT,root=self.root,state=state,record_before_write=lambda _:None)
  self.assertFalse(self.events)
  self.assertEqual(Engine(self.root,read_only=True)._state()['components'][self.component['id']]['transaction'],newer['transaction'])
 def test_new_boot_resume_retains_first_install_before_state_and_transaction(self):
  result=self.install();original=copy.deepcopy(result['recovery']);records=[]
  before=list(Engine(self.root,read_only=True).journals.glob('*.json'))
  with patch.object(activation,'_boot_id',return_value='22222222-2222-2222-2222-222222222222'),patch.object(activation,'_preflight_preloaded_custom_ownership',return_value=True):
   resumed=activation.resume_boot(activation.INPUT,root=self.root,pool=None,facts={},selection=[activation.INPUT],state=original,record_before_write=lambda s:records.append(copy.deepcopy(s)),approve=lambda _:None)
  after=list(Engine(self.root,read_only=True).journals.glob('*.json'))
  self.assertEqual(before,after);self.assertEqual(resumed['recovery']['transaction'],original['transaction'])
  self.assertEqual(resumed['recovery']['generation'],original['generation']);self.assertEqual(resumed['recovery']['services'],original['services']);self.assertEqual(resumed['recovery']['config'],original['config'])
  self.assertTrue(records[0]['boot_resuming']);self.assertFalse(records[-1]['boot_resuming'])
 @unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,'Root-owned inactive Linux namespace required')
 def test_offline_restore_runs_only_root_scoped_enablement_no_live_commands(self):
  result=self.install();path=self.root/self.config['target'];path.write_bytes(b'preserved-user-map');self.events.clear()
  restored=activation.restore_offline(activation.INPUT,root=self.root,state=result['recovery'],record_before_write=lambda _:None)
  self.assertFalse((self.root/'usr/bin/inputplumber').exists());self.assertEqual(path.read_bytes(),b'preserved-user-map')
  self.assertFalse(restored['drivers_touched']);self.assertFalse(restored['services_executed'])
  self.assertTrue(all(row[:3]==('/usr/bin/systemctl','--root',str(self.root)) for row in self.events))

if __name__=='__main__':unittest.main()
