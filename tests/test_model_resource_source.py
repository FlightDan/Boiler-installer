"""Model source declarations drive built model resource references."""
import copy,hashlib,json,shutil,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from protocol.resource_pool import decoder, json_document
from resources import model_source

class ModelSourceTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.work=tempfile.TemporaryDirectory(); cls.base=Path(cls.work.name)/'base'
  import importlib.util
  spec=importlib.util.spec_from_file_location('builder',ROOT/'scripts/prepare-resource-pool.py'); mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
  mod.build(ROOT,cls.base,local_candidate=True); cls.toon=decoder(ROOT/'adaptations')
 @classmethod
 def tearDownClass(cls): cls.work.cleanup()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.dir=Path(self.tmp.name);self.pool=self.dir/'pool';shutil.copytree(self.base,self.pool);self.project=self.dir/'project'
  for name in model_source.sources(ROOT):
   target=self.project/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,target)
  (self.project/'scripts').mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/'scripts/source-files.json',self.project/'scripts/source-files.json')
  self.catalog=json_document((self.pool/'catalog.json').read_bytes());self.patch=patch.object(model_source,'decoder',return_value=self.toon);self.patch.start()
 def tearDown(self): self.patch.stop();self.tmp.cleanup()
 def load(self,identity):return self.toon.loads((self.project/({'device:oxp3':'adaptations/brands/onexplayer/models/onexplayer-3.toon','device:x2-mini':'adaptations/brands/onexplayer/models/x2-mini.toon'}[identity])).read_text(encoding='utf-8'),strict=True)
 def save(self,identity,value):(self.project/({'device:oxp3':'adaptations/brands/onexplayer/models/onexplayer-3.toon','device:x2-mini':'adaptations/brands/onexplayer/models/x2-mini.toon'}[identity])).write_text(self.toon.dumps(value)+'\n',encoding='utf-8')
 def test_reference_edits_drive_only_selected_model_and_are_recorded(self):
  identity='device:oxp3';spec=self.load(identity);removed='resource:intel-be201-firmware';spec['resources']=[r for r in spec['resources'] if r['id']!=removed];self.save(identity,spec)
  model_source.apply(self.pool,self.project,self.catalog)
  rows={r['id']:r for r in self.catalog['models']};get=lambda i:self.toon.loads((self.pool/rows[i]['path']).read_text(encoding='utf-8'),strict=True)
  self.assertNotIn(removed,[r['resource'] for r in get(identity)['resources']]);self.assertIn(removed,[r['resource'] for r in get('device:x2-mini')['resources']])
  proof=json.loads((self.pool/'provenance/model-resource-sources.json').read_text(encoding='utf-8'));entry=next(x for x in proof['files'] if x['model']==identity);self.assertEqual(entry['sha256'],hashlib.sha256((self.project/entry['path']).read_bytes()).hexdigest())
 def test_source_can_reference_catalog_resource_without_generated_model_entry(self):
  identity='device:oxp3';ref=next(r for r in self.catalog['models'] if r['id']==identity);path=self.pool/ref['path'];model=self.toon.loads(path.read_text(encoding='utf-8'),strict=True)
  wanted=model['resources'].pop()['resource'];path.write_text(self.toon.dumps(model)+'\n',encoding='utf-8')
  model_source.apply(self.pool,self.project,self.catalog)
  result=self.toon.loads(path.read_text(encoding='utf-8'),strict=True);self.assertIn(wanted,[r['resource'] for r in result['resources']])
 def test_legacy_catalog_accepts_source_models_and_rejects_duplicate_identity(self):
  import importlib.util
  spec=importlib.util.spec_from_file_location('catalog_for_source_test',ROOT/'adaptations/scripts/toon_catalog.py');catalog=importlib.util.module_from_spec(spec);spec.loader.exec_module(catalog)
  cloned=self.dir/'adaptations';shutil.copytree(ROOT/'adaptations',cloned)
  self.assertEqual(len(catalog.models(cloned)),2)
  duplicate=cloned/'brands/onexplayer/models/extra.toon';shutil.copy2(cloned/'brands/onexplayer/models/onexplayer-3.toon',duplicate)
  with self.assertRaises(catalog.CatalogError):catalog.models(cloned)
 def test_new_whitelisted_model_builds_without_code_changes_or_hdr(self):
  project=self.dir/'full-project';shutil.copytree(ROOT,project)
  source='adaptations/brands/example/models/handheld.toon';target=project/source;target.parent.mkdir(parents=True)
  spec=dict(schema_version=1,kind='BoilerModelResourceSource',id='device:handheld',name='Example Handheld',hardware_matching=dict(mode='advisory-exact-dmi-v1',pairs=[]),parameters={},resources=[dict(id='resource:common-slot-switch')])
  target.write_text(self.toon.dumps(spec)+'\n',encoding='utf-8');whitelist=project/'scripts/source-files.json';data=json.loads(whitelist.read_text(encoding='utf-8'));data['files'].append(source);whitelist.write_text(json.dumps(data)+'\n',encoding='utf-8')
  import importlib.util
  module_spec=importlib.util.spec_from_file_location('builder_extra_model',project/'scripts/prepare-resource-pool.py');builder=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(builder)
  output=self.dir/'three-model-pool';result=builder.build(project,output,local_candidate=True);self.assertEqual(result['models'],3)
  catalog=builder.validate_pool(output);created=next(m for m in catalog['models'] if m['id']=='device:handheld')
  self.assertEqual(created['hardware_matching']['pairs'],[]);self.assertEqual([r['resource'] for r in created['resources']],['resource:common-slot-switch']);self.assertFalse(created['complete'])
  spec['id']='device:1handheld';target.write_text(self.toon.dumps(spec)+'\n',encoding='utf-8')
  with self.assertRaises(ValueError):builder.build(project,self.dir/'invalid-model-pool',local_candidate=True)
 def test_unsupported_whitelisted_model_filename_cannot_disappear(self):
  whitelist=self.project/'scripts/source-files.json';data=json.loads(whitelist.read_text(encoding='utf-8'));data['files'].append('adaptations/brands/example/models/Upper.toon');whitelist.write_text(json.dumps(data),encoding='utf-8')
  with self.assertRaisesRegex(ValueError,'Unsupported model source path'):model_source.sources(self.project)
 def test_nested_or_unlisted_model_cannot_disappear(self):
  whitelist=self.project/'scripts/source-files.json';before=whitelist.read_bytes();data=json.loads(before)
  for candidate in ('adaptations/brands/example/models/sub/extra.toon',r'adaptations\brands\example\models\extra.toon'):
   changed=copy.deepcopy(data);changed['files'].append(candidate);whitelist.write_text(json.dumps(changed),encoding='utf-8')
   with self.subTest(candidate=candidate),self.assertRaisesRegex(ValueError,'Unsupported model source path'):model_source.sources(self.project)
  whitelist.write_bytes(before);unlisted=self.project/'adaptations/brands/example/models/extra.toon';unlisted.parent.mkdir(parents=True);unlisted.write_text('unused',encoding='utf-8')
  with self.assertRaisesRegex(ValueError,'explicit whitelist'):model_source.sources(self.project)
 def test_missing_dependency_is_rejected_at_build(self):
  identity='device:oxp3';spec=self.load(identity);spec['resources']=[r for r in spec['resources'] if r['id']!='resource:onexplayer-battery-common'];self.save(identity,spec)
  with self.assertRaisesRegex(ValueError,'missing dependencies'): model_source.apply(self.pool,self.project,self.catalog)
 def test_unknown_duplicate_and_invalid_hdr_are_rejected(self):
  identity='device:oxp3';original=self.load(identity)
  for case in ('unknown','duplicate','hdr'):
   spec=json.loads(json.dumps(original))
   if case=='unknown':spec['resources'].append(dict(id='resource:not-available'))
   elif case=='duplicate':spec['resources'].append(spec['resources'][0])
   else:spec['hdr']['peak']=700
   self.save(identity,spec)
   with self.subTest(case=case),self.assertRaises(ValueError):model_source.apply(self.pool,self.project,self.catalog)
if __name__=='__main__':unittest.main()
