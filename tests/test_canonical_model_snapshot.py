"""Synthetic snapshot-to-resource regression; no physical installation involved."""
import hashlib,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from protocol.resource_pool import decoder
from resources import model_source

class CanonicalSnapshotTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name);self.project=self.root/'project';self.pool=self.root/'pool';(self.project/'scripts').mkdir(parents=True);(self.pool/'provenance').mkdir(parents=True)
  self.toon=decoder(ROOT/'adaptations');self.patch=patch.object(model_source,'decoder',return_value=self.toon);self.patch.start();self.names=[];self.rows=[]
  for identity in ('oxp3','x2-mini'):
   name='adaptations/brands/onexplayer/'+identity+'/'+identity+'.toon';self.names.append(name)
   spec=dict(schema_version=1,kind='BoilerModelResourceSource',id='device:'+identity,name=identity,hardware_matching=dict(mode='advisory-exact-dmi-v1',pairs=[]),parameters={},resources=[dict(id='resource:a'),dict(id='resource:b')])
   self.write_guide(name,dict(kind='ModelInstallGuide',id='guide:'+identity,resource_model=spec),add=True)
  (self.project/'scripts/source-files.json').write_text(json.dumps(dict(files=self.names+['adaptations/model-source-files.json'])))
  self.lock=dict(schema_version=1,kind='CanonicalModelSnapshot',repository='FlightDan/Boiler-adaptations',commit='0'*40,files=self.rows);self.save_lock()
  resources=[]
  for ident in ('a','b'):
   path='resources/'+ident+'.toon';payload='payload/'+ident;self.dump(self.pool/payload,dict(data=ident))
   license='licenses/shared';(self.pool/license).parent.mkdir(parents=True,exist_ok=True);(self.pool/license).write_text('fixture')
   self.dump(self.pool/path,dict(id='resource:'+ident,dependencies=[],payload=dict(files=[dict(path=payload)]),license_files=[license]));resources.append(dict(id='resource:'+ident,path=path))
  models=[]
  for ident in ('oxp3','x2-mini'):
   path='models/'+ident+'.toon';self.dump(self.pool/path,dict(schema_version=1,kind='BoilerResourceModel',id='device:'+ident,name=ident,hardware_matching=dict(mode='advisory-exact-dmi-v1',pairs=[]),resources=[],parameters={},pending=[],complete=False,official_release=False));models.append(dict(id='device:'+ident,path=path))
  self.catalog=dict(resources=resources,models=models)
 def tearDown(self):self.patch.stop();self.tmp.cleanup()
 def dump(self,path,obj):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(self.toon.dumps(obj)+'\n',encoding='utf-8')
 def write_guide(self,name,obj,add=False):
  path=self.project/name;self.dump(path,obj);raw=path.read_bytes();row=dict(path=name,size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
  if add:self.rows.append(row)
  else:self.rows[self.names.index(name)]=row
 def save_lock(self):
  index=dict(schema_version=1,kind='CanonicalAdaptationSourceFiles',files=[dict(path=row['path'].removeprefix('adaptations/'),size=row['size'],sha256=row['sha256']) for row in self.rows])
  raw=json.dumps(index).encode();(self.project/'adaptations/model-source-files.json').write_bytes(raw)
  self.lock['files']=self.rows+[dict(path='adaptations/model-source-files.json',size=len(raw),sha256=hashlib.sha256(raw).hexdigest())]
  (self.project/'adaptations/canonical-model-snapshot.json').write_text(json.dumps(self.lock))
 def model(self,name):return self.toon.loads((self.project/name).read_text(encoding='utf-8'),strict=True)
 def test_each_model_changes_compiled_resources_and_stale_snapshot_is_rejected(self):
  model_source.apply(self.pool,self.project,self.catalog)
  for name in self.names:
   guide=self.model(name);guide['resource_model']['resources'].pop();self.write_guide(name,guide)
   with self.assertRaisesRegex(ValueError,'snapshot differs'):model_source.sources(self.project)
   self.save_lock();model_source.apply(self.pool,self.project,self.catalog)
   ref=next(r for r in self.catalog['models'] if r['id']==guide['resource_model']['id']);result=self.toon.loads((self.pool/ref['path']).read_text(encoding='utf-8'),strict=True)
   self.assertEqual([r['resource'] for r in result['resources']],['resource:a'])
   if name==self.names[0]: self.assertTrue((self.pool/'payload/b').is_file())
   else:
    self.assertFalse((self.pool/'payload/b').exists());self.assertFalse((self.pool/'resources/b.toon').exists())
    self.assertTrue((self.pool/'licenses/shared').is_file())
 def test_pruned_pool_validates_and_each_model_installs_verifies_and_restores(self):
  from protocol.resource_pool import validate_pool
  from maintenance.resource_runtime import ResourcePool,ResourceSession
  for ref in self.catalog['resources']:
   ident=ref['id'].split(':')[1];payload='payload/'+ident;data=(self.pool/payload).read_bytes()
   entry=dict(path=payload,size=len(data),sha256=hashlib.sha256(data).hexdigest())
   resource=dict(schema_version=1,kind='BoilerResource',id=ref['id'],version='1.0.0',dependencies=[],hardware_requires={},compatibility={},payload=dict(files=[entry]),license_files=['licenses/shared'],install=dict(kind='files-v1',files=[dict(source=payload,target='etc/'+ident,mode=420,scope='root-slots')]),verify=dict(kind='sha256-v1'),restore=dict(kind='transaction-backup-v1'))
   self.dump(self.pool/ref['path'],resource);ref.update(version='1.0.0',sha256=hashlib.sha256((self.pool/ref['path']).read_bytes()).hexdigest())
  for name in self.names:
   guide=self.model(name);guide['resource_model']['resources'].pop();self.write_guide(name,guide)
  self.save_lock();model_source.apply(self.pool,self.project,self.catalog)
  self.assertFalse((self.pool/'payload/b').exists());self.assertEqual([r['id'] for r in self.catalog['resources']],['resource:a'])
  self.catalog.update(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False)
  self.catalog['files']=[dict(path=p.relative_to(self.pool).as_posix(),size=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(self.pool.rglob('*')) if p.is_file()]
  self.dump(self.pool/'catalog.toon',self.catalog);(self.pool/'catalog.json').write_text(json.dumps(self.catalog))
  validated=validate_pool(self.pool);pool=ResourcePool.for_local_test(self.pool,trust_local_test=True)
  for model in validated['models']:
   base=self.root/model['id'].split(':')[1];home=base/'home';home.mkdir(parents=True);slots={}
   for slot in ('A','B'):
    path=base/slot;(path/'etc').mkdir(parents=True);slots[slot]=dict(root=path,environment={},hardware={})
   session=ResourceSession(pool,slots,home);receipt=session.install(model['resources'],['resource:a'])
   self.assertEqual(receipt['status'],'committed');self.assertTrue(all(x['ok'] for x in session.verify(['resource:a'])))
   for slot in ('A','B','shared-home'):session.restore(receipt,slot)
   for slot in ('A','B'):self.assertFalse((slots[slot]['root']/'etc/a').exists())
 def test_duplicate_unknown_and_missing_dependency_rejected(self):
  name=self.names[0];original=self.model(name)
  for case in ('duplicate','unknown','dependency'):
   guide=json.loads(json.dumps(original));refs=guide['resource_model']['resources']
   if case=='duplicate':refs.append(refs[0])
   elif case=='unknown':refs.append(dict(id='resource:absent'))
   else:self.dump(self.pool/'resources/a.toon',dict(id='resource:a',dependencies=['resource:absent']))
   self.write_guide(name,guide);self.save_lock()
   with self.subTest(case=case),self.assertRaises(ValueError):model_source.apply(self.pool,self.project,self.catalog)
   self.dump(self.pool/'resources/a.toon',dict(id='resource:a',dependencies=[]))
if __name__=='__main__':unittest.main()