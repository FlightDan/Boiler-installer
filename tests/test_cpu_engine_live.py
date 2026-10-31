import copy,os,tempfile,unittest
from pathlib import Path
from unittest import mock
from maintenance.engine import Engine,MaintenanceError
from maintenance import cpu_live_resources as live
from protocol.cpu_contract import TARGETS, CONFIG, IDENTITY, POLICY

PAYLOAD=Path(os.environ['BOILER_CPU_TEST_POOL']) if os.environ.get('BOILER_CPU_TEST_POOL') else None
NAMES=('000-cpu_control.py','001-power_control.py','002-main.py','003-index.js',
       '004-oxp3-cpu.service','005-plugin.json','006-package.json','007-config.json')

def component():
 return {'schema_version':1,'kind':'ComponentManifest','id':IDENTITY,'version':'0.2.0',
   'cpu_live_contract':copy.deepcopy(live.MARKER),'cpu_mutable_config':copy.deepcopy(POLICY),
   'compatibility':{},'requires':[],'conflicts':[],
   'files':[{'source':name,'target':target,'size':(PAYLOAD/name).stat().st_size,'sha256':sha,
             'mode':0o600 if target==CONFIG else 0o644}
            for name,(target,sha) in zip(NAMES,TARGETS.items())]}

def permit(engine,pool,slots,slot,*,operation,binding):
 return live._CpuLiveFilePermit(live._KEY,engine,pool,slots,slot,operation,binding)

@unittest.skipIf(PAYLOAD is None, "An explicit public CPU pool fixture is required")
class EngineLifecycleTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.engine=Engine(self.root);self.slots={'A':{'root':self.root},'B':{'root':self.root/'other'}}
 def tearDown(self):self.temp.cleanup()
 def test_actual_files_verify_and_preserve_user_edit_on_restore(self):
  manifest=component()
  with self.assertRaises(MaintenanceError):self.engine.apply(manifest,PAYLOAD)
  with mock.patch.object(live,'authorize_files',side_effect=permit):
   result=self.engine.apply_cpu_live(manifest,PAYLOAD,pool=object(),slots=self.slots,slot='A',operation='offline-install',binding=object())
   self.assertTrue(Engine(self.root,read_only=True).verify_readonly(IDENTITY)['ok'])
   config=self.root/CONFIG;config.write_text('{"enabled": false, "note": "user"}');config.chmod(0o600)
   self.assertTrue(Engine(self.root,read_only=True).verify_readonly(IDENTITY)['ok'])
   self.engine.restore_cpu_live(result['transaction'],pool=object(),slots=self.slots,slot='A',binding=object())
   self.assertIn('user',config.read_text())
   self.assertFalse((self.root/'var/lib/oxp3-cpu/cpu_control.py').exists())
 def test_created_default_removed_on_restore(self):
  with mock.patch.object(live,'authorize_files',side_effect=permit):
   result=self.engine.apply_cpu_live(component(),PAYLOAD,pool=object(),slots=self.slots,slot='A',operation='offline-install',binding=object())
   self.engine.restore_cpu_live(result['transaction'],pool=object(),slots=self.slots,slot='A',binding=object())
   self.assertFalse((self.root/CONFIG).exists())

@unittest.skipIf(PAYLOAD is None, "An explicit public CPU pool fixture is required")
class HistoricalRetryTests(unittest.TestCase):
 def test_old_journals_allow_new_A_then_B_and_reject_foreign_replacement(self):
  from maintenance import resource_access, resource_firstboot, resource_lifecycle, resource_reinstall, retry_documents, resource_activation, cpu_sidecar_store
  from maintenance.engine import _json_write
  from test_cpu_live_contract import resource
  import contextlib
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp);roots={s:base/s for s in ('A','B')}
   for root in roots.values():root.mkdir()
   engines={s:Engine(root) for s,root in roots.items()};slots={s:{'root':root} for s,root in roots.items()}
   oldgroup='a'*32;manifest=component();manifest['resource_group']=oldgroup
   with mock.patch.object(live,'authorize_files',side_effect=permit):
    old={s:e.apply_cpu_live(manifest,PAYLOAD,pool=None,slots=slots,slot=s,operation='offline-install',binding=None)['transaction'] for s,e in engines.items()}
    for s,e in engines.items():e.restore_cpu_live(old[s],pool=None,slots=slots,slot=s,binding=None)
   engine=engines['A'];engine.journals.mkdir(parents=True,exist_ok=True);state_engine=engine._state()
   for n,cid in enumerate(('core:maintenance','core:resource-firstboot')):
    tx=str(n+1)*32;_json_write(engine._journal(tx),{'id':tx,'root':engine.identity,'status':'committed','component':{'id':cid},'operations':[]})
    state_engine['components'][cid]={'transaction':tx}
   _json_write(engine.state_dir/'state.json',state_engine)
   gen='c'*32;boot='12345678-1234-1234-1234-123456789abc';now='22345678-1234-1234-1234-123456789abc'
   receipt={'id':oldgroup,'transactions':[{'scope':s,'component':IDENTITY,'transaction':old[s]} for s in roots]}
   intent={'generation':gen,'slot':'A','root_partuuid':'fixture','target_roles':{},'selection':{'components':[IDENTITY]},'resource_receipt':receipt}
   action={'kind':'BoilerResourceRetryAction','schema_version':1,'token':'b'*32,'status':'reinstalling','phase':'installing','generation':gen,'target_roles':{},'original_receipts':{IDENTITY:receipt},'closure':[IDENTITY],'group':'d'*32}
   state={'generation':gen,'slot':'A','root_partuuid':'fixture','resources':{IDENTITY:{'status':'restored','restore_result':{'kind':'BoilerCpuLiveRestore','phase':'stopped','generation':gen,'transaction':old['A'],'boot_id':boot,'invocation':None,'metadata_only':True}}},'maintenance_restore':{'identity':IDENTITY,'reviewed_sha256':'e'*64},'maintenance_retry':action}
   class Pool:
    def resource(self,identity):return resource()
   session=SimpleNamespace(engines=engines,_bound_receipt=lambda group:receipt)
   def inspect(*args):
    journals={}
    for slot,owner in engines.items():
     item=owner._state()['components'].get(IDENTITY)
     if item:journals[(slot,IDENTITY)]=resource_firstboot.read_private(owner._journal(item['transaction']))
    return {'id':action['group']},{},journals,{}
   class System:
    status='inactive'
    def boot_id(self):return now
    def service(self,unit):return self.status
   realpath=Path
   def mapped(value):
    p=realpath(value)
    return roots['A']/p.relative_to('/') if p.is_absolute() else p
   def factory(root,*args,**kwargs):
    return engine if str(root)==str(roots['A']) else Engine(root,*args,**kwargs)
   @contextlib.contextmanager
   def pool(*args):yield Pool(),[]
   def authorize(owner,pool,slots,slot,*,operation,binding):
    live.completed_stop(intent,state,binding=binding,operation=operation)
    return permit(owner,pool,slots,slot,operation=operation,binding=binding)
   token=resource_access.operation.set('/')
   try:
    with mock.patch.object(live,'Path',side_effect=mapped),mock.patch('maintenance.engine.Engine',side_effect=factory), \
         mock.patch.object(resource_firstboot,'open_pool',pool),mock.patch.object(resource_lifecycle,'_session',return_value=(session,[])), \
         mock.patch.object(resource_lifecycle,'_retry_preflight',return_value=(session,[])), \
         mock.patch.object(resource_reinstall,'inspect',side_effect=inspect),mock.patch.object(retry_documents,'hydrate',side_effect=lambda x,*a:x), \
         mock.patch.object(resource_activation,'LiveSystem',System),mock.patch.object(cpu_sidecar_store,'_snapshot_with_history',side_effect=FileNotFoundError), \
         mock.patch.object(live,'authorize_files',side_effect=authorize):
     fresh=component();fresh['resource_group']=action['group']
     for slot in ('A','B'):
      result=engines[slot].apply_cpu_live(fresh,PAYLOAD,pool=Pool(),slots=slots,slot=slot,operation='reviewed-reinstall',binding=object())
      self.assertNotEqual(result['transaction'],old[slot])
      if slot=='A':System.status='unknown'
     self.assertTrue(all(e._state()['components'][IDENTITY]['transaction']!=old[s] for s,e in engines.items()))
     with mock.patch.object(System,'boot_id',return_value=boot):
      self.assertFalse(live.completed_stop(intent,state,binding=object(),operation='reviewed-reinstall')['historical'])
     foreign=engines['A']._state();foreign['components'][IDENTITY]['transaction']='f'*32;_json_write(engines['A'].state_dir/'state.json',foreign)
     with self.assertRaises(Exception):live.completed_stop(intent,state,binding=object(),operation='reviewed-reinstall')
   finally:resource_access.operation.reset(token)

if __name__=='__main__':unittest.main()

