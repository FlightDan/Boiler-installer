"""Selection and trust boundary for the explicit canonical build entry."""
import importlib.util,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
spec=importlib.util.spec_from_file_location('canonical_catalog_test',ROOT/'scripts/toon_catalog.py');catalog=importlib.util.module_from_spec(spec);spec.loader.exec_module(catalog)
from resources import model_source
import protocol.manifest

class CanonicalBuildEntryTests(unittest.TestCase):
 def test_x2_uses_one_builder_per_adapter_for_both_catalog_layouts(self):
  for name,expected in (('x2-current-v1','adaptations/brands/onexplayer/x2-mini/bridge/scripts/build.py'),
                        ('x2-complete-v1','adaptations/scripts/build-x2-complete.py')):
   model=dict(canonical_id='device:onexplayer-x2-mini',build=dict(adapter=name))
   self.assertEqual(catalog.adapter(model)[0],expected)
   old=dict(build=dict(adapter=name));self.assertEqual(catalog.adapter(old),catalog.ADAPTERS[name])
   with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);entry=root/expected;entry.parent.mkdir(parents=True);entry.write_text('fixture')
    self.assertEqual(catalog.local(root,catalog.adapter(model)[0]),entry)
    with self.assertRaises(catalog.CatalogError):catalog.local(root,'../'+expected)

 def test_selected_model_drives_base_and_common_stage(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);refs={'kind':'fixture'};model={'resource_model':{'id':'device:oxp3','common_references':refs}};inputs={k:root/k for k in ('base','stage','public-installer')};calls=[]
   commit=json.loads((ROOT/model_source.SNAPSHOT).read_bytes())['commit']
   impl=types.SimpleNamespace(stage_snapshot=lambda stage:({'stage':'fixture'},{'source_commit':commit}),stage_reference=lambda content,identity:(refs,{}),build=lambda *args,**kwargs:(calls.append((args,kwargs)) or {'id':'device:oxp3','official_release':False}))
   fake_spec=types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda value:None))
   with patch.object(catalog,'check_sources'),patch.object(model_source,'sources'),patch.object(protocol.manifest,'validate_package',return_value={'id':'device:oxp3'}),patch.object(catalog.importlib.util,'spec_from_file_location',return_value=fake_spec),patch.object(catalog.importlib.util,'module_from_spec',return_value=impl):
    result=catalog.canonical_build(ROOT,model,root/'output',inputs,'a'*64,version='fixture')
    self.assertEqual(result['id'],'device:oxp3');self.assertEqual(calls[0][1]['version'],'fixture')
    impl.stage_snapshot=lambda stage:({'stage':'fixture'}, {})
    with self.assertRaisesRegex(ValueError,'requires the current canonical snapshot stage'):
     catalog.canonical_build(ROOT,model,root/'legacy',inputs,'a'*64)
   with patch.object(catalog,'check_sources'),patch.object(model_source,'sources'),patch.object(protocol.manifest,'validate_package',return_value={'id':'device:x2-mini'}):
    with self.assertRaisesRegex(ValueError,'identity differ'):catalog.canonical_build(ROOT,model,root/'other',inputs,'a'*64)
if __name__=='__main__':unittest.main()