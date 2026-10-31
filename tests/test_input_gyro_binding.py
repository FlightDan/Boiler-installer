import copy
import json, os, subprocess, sys, tempfile
from pathlib import Path
import unittest
from contextlib import nullcontext
from maintenance.input_gyro_binding import BindingManager,BindingError,DEVICE,KERNEL,STOCK,CUSTOM

class Backend:
 def __init__(self,current=STOCK,loaded=False):self.current=current;self.module=loaded;self.record=None;self.events=[];self.fault=None
 def locked(self):return nullcontext()
 def guard(self,**kwargs):pass
 def boot(self):return '11111111-1111-1111-1111-111111111111'
 def ownership(self):return dict(kind='BoilerGyroBinding',schema_version=1,boot=self.boot(),kernel=KERNEL,device=DEVICE,root_partuuid='a',root_uuid='b',generation='1'*32,module_sha256='2'*64)
 def read_protected(self):return copy.deepcopy(self.record)
 def driver(self):return self.current
 def read(self):return copy.deepcopy(self.record)
 def save(self,row):self.events.append(('save',row['phase']));self.record=copy.deepcopy(row)
 def clear(self):self.record=None;self.events.append(('clear',))
 def loaded(self):return self.module
 def ensure_custom(self):
  self.events.append(('insmod',));self.module=True
  if self.fault=='insmod':raise RuntimeError('injected insmod error')
 def unload_owned(self):self.events.append(('rmmod',));self.module=False
 def unbind(self,name):
  if name!=self.current:raise BindingError('owner mismatch')
  self.events.append(('unbind',name));self.current=None
 def bind(self,name):
  self.events.append(('bind',name))
  if self.fault=='bind' and name==CUSTOM:raise RuntimeError('injected bind error')
  self.current=name

class GyroBindingTests(unittest.TestCase):
 def test_stock_binding_restored_and_owned_module_unloaded(self):
  backend=Backend();manager=BindingManager(backend);manager.start();self.assertEqual(backend.current,CUSTOM)
  self.assertEqual(backend.events[0],('save','prepared'));manager.stop();self.assertEqual(backend.current,STOCK);self.assertFalse(backend.module);self.assertIsNone(backend.record)
 def test_preexisting_module_on_stock_binding_is_rejected_without_write(self):
  backend=Backend(loaded=True);manager=BindingManager(backend)
  with self.assertRaises(BindingError):manager.start()
  self.assertTrue(backend.module);self.assertEqual(backend.current,STOCK);self.assertEqual(backend.events,[])
 def test_preexisting_custom_binding_is_not_taken_over(self):
  backend=Backend(current=CUSTOM,loaded=True);manager=BindingManager(backend)
  with self.assertRaises(BindingError):manager.start()
  self.assertEqual(backend.current,CUSTOM);self.assertTrue(backend.module);self.assertEqual(backend.events,[])
 def test_current_owned_custom_binding_resumes_without_mutation(self):
  backend=Backend(current=CUSTOM,loaded=True);backend.record=dict(backend.ownership(),previous=STOCK,phase='active',loaded_before=False)
  before=copy.deepcopy(backend.record);result=BindingManager(backend).start()
  self.assertEqual(result['status'],'active');self.assertEqual(backend.record,before);self.assertEqual(backend.events,[])
 def test_reachable_interrupted_start_resumes_same_generation_and_prior(self):
  for loaded,current in ((False,STOCK),(True,STOCK),(True,None),(True,CUSTOM)):
   backend=Backend(current=current,loaded=loaded);backend.record=dict(backend.ownership(),previous=STOCK,phase='prepared',loaded_before=False)
   with self.subTest(loaded=loaded,current=current):
    BindingManager(backend).start()
    self.assertEqual(backend.current,CUSTOM);self.assertEqual(backend.record['phase'],'active')
    self.assertEqual(backend.record['previous'],STOCK);self.assertEqual(backend.record['generation'],'1'*32)
 def test_reachable_interrupted_restore_completes(self):
  for loaded,current in ((True,CUSTOM),(True,None),(True,STOCK),(False,STOCK)):
   backend=Backend(current=current,loaded=loaded);backend.record=dict(backend.ownership(),previous=STOCK,phase='restoring',loaded_before=False)
   with self.subTest(loaded=loaded,current=current):
    BindingManager(backend).stop()
    self.assertEqual(backend.current,STOCK);self.assertFalse(backend.module);self.assertIsNone(backend.record)
 def test_process_exit_at_reachable_binding_boundaries(self):
  # Mock hardware in a persistent fixture, but terminate the real helper process
  # at each operation boundary so no exception cleanup can run.
  script='''
import json,os,sys
from pathlib import Path
from contextlib import nullcontext
sys.path.insert(0,sys.argv[1])
from maintenance.input_gyro_binding import BindingManager,STOCK,CUSTOM,DEVICE,KERNEL
p=Path(sys.argv[2]);operation=sys.argv[3];stop_at=sys.argv[4]
class B:
 def __init__(self): self.v=json.loads(p.read_text())
 def persist(self,event):
  p.write_text(json.dumps(self.v))
  if event==stop_at: os._exit(42)
 def locked(self):return nullcontext()
 def guard(self,**kw):pass
 def boot(self):return '11111111-1111-1111-1111-111111111111'
 def ownership(self):return dict(kind='BoilerGyroBinding',schema_version=1,boot=self.boot(),kernel=KERNEL,device=DEVICE,root_partuuid='a',root_uuid='b',generation='1'*32,module_sha256='2'*64)
 def read_protected(self):return self.v['record']
 def read(self):return self.v['record']
 def driver(self):return self.v['driver']
 def loaded(self):return self.v['loaded']
 def save(self,row):self.v['record']=row;self.persist('save_'+row['phase'])
 def clear(self):self.v['record']=None;self.persist('clear')
 def ensure_custom(self):self.v['loaded']=True;self.persist('insmod')
 def unload_owned(self):self.v['loaded']=False;self.persist('unload')
 def unbind(self,name):self.v['driver']=None;self.persist('unbind_'+name)
 def bind(self,name):self.v['driver']=name;self.persist('bind_'+name)
b=B();getattr(BindingManager(b),operation)()
'''
  sdk=str(Path(__file__).resolve().parents[1])
  for operation,event in [('start','save_prepared'),('start','insmod'),('start','unbind_'+STOCK),('start','bind_'+CUSTOM),('start','save_active'),('stop','save_restoring'),('stop','unbind_'+CUSTOM),('stop','bind_'+STOCK),('stop','unload'),('stop','clear')]:
   with self.subTest(operation=operation,event=event),tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'state.json';helper=Path(tmp)/'helper.py';helper.write_text(script)
    b=Backend();record=None if operation=='start' else dict(b.ownership(),previous=STOCK,phase='active',loaded_before=False)
    path.write_text(json.dumps(dict(driver=STOCK if operation=='start' else CUSTOM,loaded=operation=='stop',record=record)))
    args=[sys.executable,'-B',str(helper),sdk,str(path),operation]
    self.assertEqual(subprocess.run(args+[event]).returncode,42)
    self.assertEqual(subprocess.run(args+['no-exit']).returncode,0)
    final=json.loads(path.read_text())
    if operation=='start':self.assertEqual((final['driver'],final['record']['phase'],final['record']['previous'],final['record']['generation']),(CUSTOM,'active',STOCK,'1'*32))
    else:self.assertEqual((final['driver'],final['loaded'],final['record']),(STOCK,False,None))
 def test_foreign_owned_custom_binding_rejects_without_mutation(self):
  for field in ('boot','root_partuuid','root_uuid','generation','module_sha256','device','kernel'):
   backend=Backend(current=CUSTOM,loaded=True);backend.record=dict(backend.ownership(),previous=STOCK,phase='active',loaded_before=False);backend.record[field]='foreign';before=copy.deepcopy(backend.record)
   with self.subTest(field=field),self.assertRaises(BindingError):BindingManager(backend).start()
   self.assertEqual(backend.record,before);self.assertEqual(backend.events,[])
 def test_module_failure_restores_stock_and_unloads_owned_module(self):
  backend=Backend();backend.fault='insmod'
  with self.assertRaisesRegex(BindingError,'original binding restored'):BindingManager(backend).start()
  self.assertEqual(backend.current,STOCK);self.assertFalse(backend.module);self.assertIsNone(backend.record)
 def test_bind_failure_restores_stock_and_unloads_owned_module(self):
  backend=Backend();backend.fault='bind'
  with self.assertRaisesRegex(BindingError,'original binding restored'):BindingManager(backend).start()
  self.assertEqual(backend.current,STOCK);self.assertFalse(backend.module)
 def test_changed_external_driver_refuses_preserving_record(self):
  backend=Backend();manager=BindingManager(backend);manager.start();backend.current='other-driver';before=copy.deepcopy(backend.record)
  with self.assertRaises(BindingError):manager.stop()
  self.assertEqual(backend.current,'other-driver');self.assertEqual(backend.record,before)
 def test_stale_boot_record_cannot_detach_device(self):
  backend=Backend();manager=BindingManager(backend);manager.start();backend.record['boot']='22222222-2222-2222-2222-222222222222';events=len(backend.events)
  with self.assertRaises(BindingError):manager.stop()
  self.assertEqual(len(backend.events),events);self.assertEqual(backend.current,CUSTOM)
if __name__=='__main__':unittest.main()
