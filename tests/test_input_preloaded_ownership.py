import copy,hashlib,os,sys,tempfile,types,unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from maintenance import input_activation as activation,input_ownership as ownership
import maintenance
from maintenance.engine import Engine


class OwnershipTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'root';self.root.mkdir()
  self.expected=dict(kind='BoilerHidBinding',schema_version=1,boot='11111111-1111-1111-1111-111111111111',kernel=ownership.KERNEL,device='0003:1A86:FE00.0001',root_partuuid='ab',root_uuid='cd',generation='a'*32,module_sha256='b'*64)
  self.record=dict(self.expected,previous='hid-generic',loaded_before=False,phase='active')
 def tearDown(self):self.tmp.cleanup()
 def test_foreign_boot_root_device_kernel_generation_hash_and_phase_rejected(self):
  for field in ('boot','root_partuuid','root_uuid','device','kernel','generation','module_sha256','kind','schema_version','loaded_before','phase'):
   row=copy.deepcopy(self.record);row[field]=True if field in ('schema_version','loaded_before') else 'foreign'
   with self.subTest(field=field),self.assertRaises(ValueError):ownership.validate(row,self.expected)
 def test_phase_table_rejects_unreachable_module_and_binding_combinations(self):
  for phase,loaded,driver in [('active',False,'hid-oxp'),('active',True,'hid-generic'),('prepared',False,None),('restoring',False,'hid-oxp')]:
   row=dict(self.record,phase=phase)
   with self.subTest(phase=phase,loaded=loaded,driver=driver),self.assertRaises(ValueError):
    ownership.classify(row,self.expected,loaded=loaded,driver=driver,stock='hid-generic',custom='hid-oxp')
 def test_context_binds_committed_journal_and_installed_module(self):
  payload=self.root/'payload';payload.mkdir();data=b'ko';(payload/'module').write_bytes(data);digest=hashlib.sha256(data).hexdigest()
  component=dict(schema_version=1,kind='ComponentManifest',id='runtime:boiler-input',version='1.0.0',requires=[],input_live_generation='a'*32,files=[dict(source='module',target='usr/lib/boiler-input/hid-oxp.ko',size=len(data),sha256=digest,mode=420)])
  Engine(self.root).apply(component,payload)
  from maintenance.live_root import RootCommands
  original_read=Path.read_text
  def read(path,*args,**kwargs):
   return self.expected['boot'] if str(path).replace('\\','/')=='/proc/sys/kernel/random/boot_id' else original_read(path,*args,**kwargs)
  with patch.object(RootCommands,'identity',return_value=dict(partuuid='ab',uuid='cd')),patch.object(ownership.os,'uname',return_value=types.SimpleNamespace(release=ownership.KERNEL),create=True),patch.object(Path,'read_text',read):
   value=ownership.context(activation.INPUT,self.expected['device'],root=self.root,expected_sha256=digest)
   self.assertEqual(value['generation'],'a'*32);self.assertEqual(value['module_sha256'],digest)
   with self.assertRaisesRegex(ValueError,'Selected input module'):ownership.context(activation.INPUT,self.expected['device'],root=self.root,expected_sha256='0'*64)
   (self.root/'usr/lib/boiler-input/hid-oxp.ko').write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'Installed input module'):ownership.context(activation.INPUT,self.expected['device'],root=self.root,expected_sha256=digest)
 def test_unknown_hid_preload_refuses_before_callback_or_service_write(self):
  fake=types.ModuleType('maintenance.input_hid_binding');fake.STATE=self.root/'run';fake.target=lambda:self.expected['device'];fake.driver=lambda device:'hid-oxp'
  calls=[];component=dict(files=[dict(target=ownership.SPECS[activation.INPUT][1],sha256='b'*64)])
  original=Path.exists
  def exists(path):return True if str(path).replace('\\','/')=='/sys/module/hid_oxp' else original(path)
  with patch.dict(sys.modules,{'maintenance.input_hid_binding':fake}),patch.object(maintenance,'input_hid_binding',fake,create=True),patch.object(Path,'exists',exists),patch.object(activation,'live'),patch.object(activation,'_hardware',return_value={}),patch.object(activation,'bundle',return_value=(self.root,component,{})),patch.object(ownership,'context',return_value=self.expected),patch.object(ownership,'protected_record',side_effect=FileNotFoundError),patch.object(activation,'command',side_effect=lambda *a,**k:calls.append(a)):
   before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
   with self.assertRaisesRegex(activation.InputActivationError,'Unknown preloaded'):activation.activate(activation.INPUT,root=self.root,pool=object(),facts={},selection=[activation.INPUT],record_before_write=lambda x:calls.append(x),approve=lambda x:calls.append(x))
   self.assertEqual(calls,[]);self.assertEqual(before,{str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
 def test_unknown_gyro_preload_refuses_before_callback_or_service_write(self):
  class Backend:
   def driver(self):return 'oxp3_bmi260'
   def loaded(self):return True
  fake=types.ModuleType('maintenance.input_gyro_binding');fake.SystemBackend=Backend;fake.DEVICE='i2c-10EC5280:00';fake.STOCK='bmi160_i2c';fake.CUSTOM='oxp3_bmi260';fake.STATE=self.root/'run'
  calls=[];component=dict(files=[dict(target=ownership.SPECS[activation.GYRO][1],sha256='b'*64)])
  with patch.dict(sys.modules,{'maintenance.input_gyro_binding':fake}),patch.object(maintenance,'input_gyro_binding',fake,create=True),patch.object(activation,'live'),patch.object(activation,'_hardware',return_value={}),patch.object(activation,'bundle',return_value=(self.root,component,{})),patch.object(ownership,'context',return_value=self.expected),patch.object(ownership,'protected_record',side_effect=FileNotFoundError),patch.object(activation,'command',side_effect=lambda *a,**k:calls.append(a)):
   with self.assertRaisesRegex(activation.InputActivationError,'Unknown preloaded'):activation.activate(activation.GYRO,root=self.root,pool=object(),facts={},selection=[activation.GYRO],record_before_write=lambda x:calls.append(x),approve=lambda x:calls.append(x))
   self.assertEqual(calls,[]);self.assertEqual(list(self.root.iterdir()),[])
 @unittest.skipUnless(os.name=='posix','HID helper uses Linux locking')
 def test_hid_fast_path_requires_current_owned_record(self):
  from maintenance import input_hid_binding as hid
  original=Path.exists
  def exists(path):return True if str(path)=='/sys/module/hid_oxp' else original(path)
  with patch.object(hid,'target',return_value=self.expected['device']),patch.object(hid,'driver',return_value='hid-oxp'),patch.object(Path,'exists',exists),patch.object(hid,'state',return_value=None),patch.object(hid,'locked',side_effect=AssertionError('must not lock or write')):
   with self.assertRaisesRegex(RuntimeError,'Unknown preloaded'):hid.start()
  with patch.object(hid,'target',return_value=self.expected['device']),patch.object(hid,'driver',return_value='hid-oxp'),patch.object(Path,'exists',exists),patch.object(hid,'state',return_value=self.record),patch.object(hid,'locked',return_value=nullcontext()),patch.object(ownership,'context',return_value=self.expected),patch.object(hid,'save',side_effect=AssertionError('must not write')):
   self.assertIsNone(hid.start())
 @unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'Requires controlled Linux root filesystem')
 def test_private_state_rejects_links_and_modes(self):
  directory=self.root/'private';directory.mkdir(mode=0o700);state=directory/'state.json';state.write_text('{}');state.chmod(0o600)
  self.assertEqual(ownership.protected_record(state),{})
  state.chmod(0o644)
  with self.assertRaises(Exception):ownership.protected_record(state)
  state.chmod(0o600);link=directory/'hardlink';os.link(state,link)
  with self.assertRaises(Exception):ownership.protected_record(state)
  link.unlink();os.chown(state,12345,12345)
  with self.assertRaises(Exception):ownership.protected_record(state)
  os.chown(state,0,0);directory.chmod(0o755)
  with self.assertRaises(Exception):ownership.protected_record(state)
  directory.chmod(0o700);state.write_text('{"phase":"active","phase":"prepared"}')
  with self.assertRaises(ValueError):ownership.protected_record(state)
  state.unlink();state.symlink_to(directory/'missing')
  with self.assertRaises(Exception):ownership.protected_record(state)
 @unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'Requires controlled Linux root filesystem')
 def test_foreign_real_inode_refuses_without_byte_or_metadata_change(self):
  directory=self.root/'run';directory.mkdir(mode=0o700);path=directory/'state.json'
  record=copy.deepcopy(self.record);record['generation']='f'*32
  import json
  path.write_text(json.dumps(record));path.chmod(0o600)
  fake=types.ModuleType('maintenance.input_hid_binding');fake.STATE=directory;fake.target=lambda:self.expected['device'];fake.driver=lambda device:'hid-oxp'
  original=Path.exists
  def exists(p):return True if str(p)=='/sys/module/hid_oxp' else original(p)
  def snapshot():
   st=path.lstat();parent=directory.lstat()
   return (path.read_bytes(),st.st_ino,st.st_mode,st.st_uid,st.st_gid,st.st_nlink,st.st_mtime_ns,st.st_ctime_ns,parent.st_ino,parent.st_mode,parent.st_mtime_ns,parent.st_ctime_ns)
  before=snapshot()
  with patch.dict(sys.modules,{'maintenance.input_hid_binding':fake}),patch.object(maintenance,'input_hid_binding',fake,create=True),patch.object(Path,'exists',exists),patch.object(ownership,'context',return_value=self.expected):
   with self.assertRaises(activation.InputActivationError):activation._preflight_preloaded_custom_ownership(activation.INPUT,root=self.root,generation=self.expected['generation'])
  self.assertEqual(snapshot(),before)


if __name__=='__main__':unittest.main()
