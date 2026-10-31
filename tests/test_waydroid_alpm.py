import copy,importlib.util,json,os,tempfile,unittest
from pathlib import Path
from maintenance import waydroid_alpm_transaction as m
class Backend:
 def __init__(self):self.values={n:None for n in m.ORDER};self.interrupt=False;self.removals=[]
 def require_exact_target(self):pass
 def verify_inputs(self):pass
 def installed(self,name):return self.values[name]
 def install_fixed(self,names):
  for n in names:
   self.values[n]=m.VERSIONS[n]
   if self.interrupt:self.interrupt=False;raise RuntimeError('interrupted')
 def remove_fixed(self,names):
  self.removals.append(list(names))
  for n in names:self.values[n]=None
class AlpmTests(unittest.TestCase):
 def test_restore_before_activation_has_no_package_side_effects(self):
  backend=Backend();state=m.prepare(backend,'a'*32,'A');records=[]
  state=m.restore(backend,state,records.append)
  self.assertEqual(state['phase'],'restored');self.assertEqual(backend.removals,[])
  self.assertTrue(all(value is None for value in backend.values.values()))
 def test_process_exit_after_partial_package_install(self):
  if not hasattr(os,'fork'):self.skipTest('requires Linux fork')
  from maintenance.engine import _json_write
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);database=root/'db.json';marker=root/'marker.json';_json_write(database,{n:None for n in m.ORDER})
   class DiskBackend(Backend):
    def installed(self,name):return json.loads(database.read_text())[name]
    def install_fixed(self,names):
     for name in names:
      data=json.loads(database.read_text());data[name]=m.VERSIONS[name];_json_write(database,data)
      if os.getpid()!=parent:os._exit(79)
    def remove_fixed(self,names):
     data=json.loads(database.read_text())
     for name in names:data[name]=None
     _json_write(database,data)
   backend=DiskBackend();action=m.prepare(backend,'a'*32,'A');parent=os.getpid();pid=os.fork()
   if pid==0:
    m.activate(backend,action,lambda value:_json_write(marker,value));os._exit(80)
   _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),79)
   action=m.retry(backend,json.loads(marker.read_text()),lambda value:_json_write(marker,value));self.assertEqual(action['phase'],'installed')
   action=m.restore(backend,action,lambda value:_json_write(marker,value));self.assertEqual(action['phase'],'restored')

 def test_preexisting_exact_and_restart_after_partial_install(self):
  b=Backend();b.values['waydroid']=m.VERSIONS['waydroid'];a=m.prepare(b,'a'*32,'A');saved=[];b.interrupt=True
  with self.assertRaisesRegex(RuntimeError,'interrupted'):m.activate(b,a,lambda x:saved.append(copy.deepcopy(x)))
  a=m.retry(b,saved[-1],lambda x:saved.append(copy.deepcopy(x)));self.assertEqual(m.verify(b,a)['status'],'host-configured')
  result=m.restore(b,a,lambda x:saved.append(copy.deepcopy(x)));self.assertEqual(result['phase'],'restored');self.assertEqual(b.values['waydroid'],m.VERSIONS['waydroid']);self.assertEqual({n for n,v in b.values.items() if v is not None},{'waydroid'})
 def test_changed_package_blocks_all_removals(self):
  b=Backend();a=m.activate(b,m.prepare(b,'a'*32,'A'),lambda _:None);b.values['libgbinder']='9.0-1'
  with self.assertRaisesRegex(RuntimeError,'changed outside'):m.restore(b,a,lambda _:None)
  self.assertEqual(b.removals,[])
 def test_existing_version_conflict_blocks_preparation(self):
  b=Backend();b.values['waydroid']='9.0-1'
  with self.assertRaisesRegex(RuntimeError,'conflicts'):m.prepare(b,'a'*32,'A')
if __name__=='__main__':unittest.main()
