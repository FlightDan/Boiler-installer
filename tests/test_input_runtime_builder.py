"""Builder/authority fixtures. Synthetic ELF bytes are never runtime acceptance."""
import copy,hashlib,importlib.util,io,json,os,tarfile,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
PROJECT=Path(os.environ.get('BOILER_TEST_PROJECT',ROOT))
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
builder=module('input_native_bundle_builder',ROOT/'scripts/prepare-input-runtime.py')
importer=module('input_native_source_importer',ROOT/'scripts/import-input-runtime-source.py')
compiler=module('input_active_source_compiler',ROOT/'scripts/prepare-input-resource.py')
def sha(raw):return hashlib.sha256(raw).hexdigest()
class RuntimeBuilderTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.ip=self.base/'ip';self.ip.mkdir();self.native=self.base/'native.tar.gz'
  from protocol.resource_pool import decoder
  self.toon=decoder(PROJECT/'adaptations')
  sources={'LICENSE':b'synthetic license fixture\n','rootfs/usr/lib/systemd/system/inputplumber-suspend.service':b'[Service]\nExecStart=busctl fixture\nExecStop=busctl fixture\n[Install]\nWantedBy=sleep.target\n','rootfs/usr/share/dbus-1/system-services/org.shadowblip.InputPlumber.service':b'[D-BUS Service]\nName=com.github.guelfey.Demo\n'}
  for name,raw in sources.items():
   path=self.ip/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
  native={'bin/inputplumber':b'\x7fELF\x02'+b'fixture-not-executable'*3,'lib/ld-linux-x86-64.so.2':b'fixture-loader'}
  rows=[dict(path=name,size=len(raw),sha256=sha(raw)) for name,raw in native.items()]
  closure=dict(kind='InputPlumberNativeELFClosure',schema_version=1,version='inputplumber 0.78.0',source_build=True,files=rows)
  native['closure.json']=(json.dumps(closure)+'\n').encode()
  with tarfile.open(self.native,'w:gz') as stream:
   for name,raw in native.items():
    info=tarfile.TarInfo(name);info.size=len(raw);stream.addfile(info,io.BytesIO(raw))
  source_rows=[dict(path=name,size=len(raw),sha256=sha(raw)) for name,raw in sources.items()]
  value=dict(kind='BoilerInputNativeBuildLock',schema_version=1,source_build_verified=True,source_commit='082f67fba6aaff88441abdc482ae76b711ad2885',cargo_command=['cargo','build','--release','--offline','--locked','--bin','inputplumber'],native_runtime=dict(size=self.native.stat().st_size,sha256=sha(self.native.read_bytes())),source_files=source_rows,rootfs=[r for r in source_rows if r['path'].startswith('rootfs/')],test_fixture_only=True)
  self.proof=self.base/'proof.toon';self.proof.write_text(self.toon.dumps(value)+'\n');self.proof_sha=sha(self.proof.read_bytes())
 def tearDown(self):self.temp.cleanup()
 def build(self,output='bundle',**kwargs):return builder.build(PROJECT,self.ip,self.native,self.base/output,native_proof=self.proof,expected_native_proof_sha256=self.proof_sha,**kwargs)
 def test_active_definitions_cover_every_real_runtime_file_and_fixed_services(self):
  result=self.build();index=self.toon.loads((self.base/'bundle/input-runtime-index.toon').read_text(),strict=True)
  from protocol.resource_pool import decoder
  toon=decoder(PROJECT/'adaptations')
  resources={r['id']:toon.loads((self.base/'bundle'/r['path']).read_text()) for r in index['resources']}
  for identity,resource in resources.items():
   self.assertEqual(resource['install']['kind'],'boiler-input-runtime-v1');self.assertTrue(resource['activation']['autoload']);self.assertFalse(any('boiler-input-staging' in row['target'] for row in resource['install']['files']))
   self.assertEqual({r['path'] for r in resource['payload']['files']},{r['source'] for r in resource['install']['files']})
  runtime=self.toon.loads((self.base/'bundle'/builder.RUNTIME_MANIFEST).read_text(),strict=True)
  gyro=runtime['components'][builder.GYRO];self.assertEqual(gyro['requires'],['runtime:boiler-input'])
  source=next(r['source'] for r in runtime['components'][builder.INPUT]['files'] if r['target']=='usr/share/dbus-1/system-services/org.shadowblip.InputPlumber.service')
  self.assertIn(b'Name=org.shadowblip.InputPlumber',(self.base/'bundle'/source).read_bytes());self.assertNotIn(b'dbus-demo',(self.base/'bundle'/source).read_bytes())
 def test_self_asserted_archive_does_not_replace_external_proof_anchor(self):
  self.proof.write_text(self.proof.read_text()+' ')
  with self.assertRaisesRegex(ValueError,'proof changed'):self.build()
  self.assertFalse((self.base/'bundle').exists())
 def test_runtime_archive_hash_must_match_external_build_lock(self):
  self.native.write_bytes(self.native.read_bytes()+b'tampered')
  with self.assertRaisesRegex(ValueError,'external build lock'):self.build()
 def test_unlocked_rootfs_file_is_not_discovered_or_installed(self):
  rogue=self.ip/'rootfs/usr/bin/unlocked-hook';rogue.parent.mkdir(parents=True);rogue.write_bytes(b'not-locked')
  self.build();index=self.toon.loads((self.base/'bundle/input-runtime-index.toon').read_text(),strict=True);self.assertFalse(any('unlocked-hook' in row['path'] for row in index['files']))
 def test_authoritative_source_import_and_recompile_preserves_complete_active_payload(self):
  self.build();project=self.base/'source';project.mkdir()
  # Decoder is an explicit known input, never an adjacent-project fallback.
  import shutil
  for name in ('toon-parser-lock.json','vendor'):
   path=PROJECT/'adaptations'/name;target=project/'adaptations'/name
   if path.is_dir():shutil.copytree(path,target)
   else:target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
  importer.import_bundle(self.base/'bundle',project,PROJECT)
  result=compiler.build(project,self.base/'pool')
  self.assertTrue(result['active_lifecycle']);self.assertTrue(result['source_library_authoritative'])
  from protocol.resource_pool import validate_pool
  catalog=validate_pool(self.base/'pool');self.assertEqual({r['id'] for r in catalog['resources']},{builder.INPUT,builder.GYRO})
  # The fixed provider consumes precisely the authoritative resource's table.
  from maintenance.resource_runtime import ResourcePool
  from maintenance.input_activation import bundle
  pool=ResourcePool(self.base/'pool',catalog,local_test=True);bundle(pool,builder.INPUT);bundle(pool,builder.GYRO)
 def test_both_source_variants_compile_exact_r5_parameters_bytes_and_version(self):
  self.build();self.build(output='bundle-r5',multifunction=True);project=self.base/'source';project.mkdir()
  import shutil
  shutil.copytree(PROJECT/'adaptations/vendor',project/'adaptations/vendor');shutil.copyfile(PROJECT/'adaptations/toon-parser-lock.json',project/'adaptations/toon-parser-lock.json')
  importer.import_bundle(self.base/'bundle',project,PROJECT);importer.import_bundle(self.base/'bundle-r5',project,PROJECT,multifunction=True)
  compiler.build(project,self.base/'pool-r5',multifunction=True)
  from protocol.resource_pool import validate_pool
  from maintenance.resource_runtime import ResourcePool
  from maintenance.input_activation import bundle
  catalog=validate_pool(self.base/'pool-r5');pool=ResourcePool(self.base/'pool-r5',catalog,local_test=True)
  base,component,config=bundle(pool,builder.INPUT)
  self.assertTrue(pool.resource(builder.INPUT)['parameters']['multifunction']);self.assertTrue(component['version'].endswith('.r5'))
  self.assertEqual((base/config['source']).read_bytes(),(PROJECT/builder.SOURCE_PREFIX/'capmap-r5.yaml').read_bytes())
 def test_authoritative_version_is_compiled_into_actual_engine_manifest(self):
  self.build();project=self.base/'source';project.mkdir()
  import shutil
  shutil.copytree(PROJECT/'adaptations/vendor',project/'adaptations/vendor');shutil.copyfile(PROJECT/'adaptations/toon-parser-lock.json',project/'adaptations/toon-parser-lock.json')
  importer.import_bundle(self.base/'bundle',project,PROJECT)
  definition=project/'adaptations/devices/Controller/onexplayer-input/resource.toon';resource=self.toon.loads(definition.read_text(),strict=True);resource['version']='0.78.0-localtest.authority-edit1';definition.write_text(self.toon.dumps(resource)+'\n')
  compiler.build(project,self.base/'pool-edited')
  runtime=self.toon.loads((self.base/'pool-edited'/builder.RUNTIME_MANIFEST).read_text(),strict=True)
  self.assertEqual(runtime['components'][builder.INPUT]['version'],resource['version'])
if __name__=='__main__':unittest.main()
