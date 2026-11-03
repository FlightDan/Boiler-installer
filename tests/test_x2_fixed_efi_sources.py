import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'x2_fixed_efi_sources', ROOT/'adaptations/brands/onexplayer/x2-mini/bridge/fixed_efi_sources.py')
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)
BRIDGE = ROOT/'adaptations/brands/onexplayer/oxp3/bridge'
sys.path.insert(0, str(ROOT))
from components import x2_efi_provider, oxp3_efi_provider
OUTPUT_SHA256 = {
    'adapter.py': '0dbf0d91901e9268c900061b41915e911f799c47ec7ea6fab9451e6d2d8fcd4e',
    'maintenance_adapter.py': '49a9392f40e254f3eef9c2fd804161eb740db4610e1317b1f9c46a07b84a2ff3',
}


class X2FixedEfiSources(unittest.TestCase):
    def test_provider_loads_by_filename_without_components_on_search_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'package'
            (root/'payload/legacy/components/hdr').mkdir(parents=True)
            directory=root/'payload/legacy/maintenance/app/oxp3_maintenance'
            directory.mkdir(parents=True)
            (root/'manifest.json').write_text(json.dumps({'id':'device:x2-mini',
                'x2_efi_contract':{'kind':'x2-split-lock-efi-v1','component':'oxp3:legacy-adaptation'}}))
            module=directory/'adoption.py'; module.write_bytes(b'pass\n')
            lock=Path(temp)/'lock.json'
            lock.write_text(json.dumps(dict(schema=1,kind='FixedX2EfiProviderSourceLock',
                public_commit='72f864e0498c10366493c74608c5ffed62cc99ca',
                files=[dict(path=module.relative_to(root).as_posix(),size=5,
                            sha256=hashlib.sha256(b'pass\n').hexdigest())])))
            code = '''
import importlib.util, pathlib, sys
source=pathlib.Path(sys.argv[1]); project=source.parent.parent
assert all(pathlib.Path(p or '.').resolve() != source.parent for p in sys.path)
assert all(pathlib.Path(p or '.').resolve() != project for p in sys.path)
spec=importlib.util.spec_from_file_location('adapter_loaded_provider', source)
provider=importlib.util.module_from_spec(spec); spec.loader.exec_module(provider)
assert len(provider.verify(sys.argv[2],sys.argv[3])) == 64
assert all(pathlib.Path(p or '.').resolve() != source.parent for p in sys.path)
assert pathlib.Path(sys.path[0]).resolve() == project
'''
            result=subprocess.run([sys.executable,'-I','-B','-c',code,
                                   str(ROOT/'components/x2_efi_provider.py'),str(root),str(lock)],
                                  cwd=temp,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_exact_reviewed_inputs_generate_x2_only_bridge(self):
        for name in sources.INPUT_SHA256:
            raw = (BRIDGE/name).read_bytes()
            generated = sources.transform(name, raw)
            self.assertEqual(generated, sources.transform(name, raw))
            self.assertEqual(hashlib.sha256(generated).hexdigest(), OUTPUT_SHA256[name])
            self.assertIn(b"manifest.get('id') != 'device:x2-mini'", generated)
            self.assertNotIn(b"manifest.get('id') != 'device:oxp3'", generated)
            if name == 'adapter.py':
                self.assertIn(b"efi_context['source'].get('package_id') != 'device:x2-mini'", generated)
                self.assertIn(b'/mnt/payload/components/x2_efi_provider.lock.json', generated)
                self.assertIn(b'model_efi_interception', generated)
            with self.assertRaises(ValueError):
                sources.transform(name, raw + b'\n')

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(ValueError):
            sources.transform('unreviewed.py', b'')

    def test_transform_source_is_explicitly_pinned(self):
        self.assertEqual(hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest(),
                         '44b04774e27d5bded09b6803d4fd124980e739518e41c8cf59c44c606e26f856')

    def test_x2_provider_rejects_unlisted_maintenance_import_and_other_lock_kind(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'package'
            (root/'payload/legacy/components/hdr').mkdir(parents=True)
            directory=root/'payload/legacy/maintenance/app/oxp3_maintenance'
            directory.mkdir(parents=True)
            manifest={'id':'device:x2-mini','x2_efi_contract':{'kind':'x2-split-lock-efi-v1','component':'oxp3:legacy-adaptation'}}
            (root/'manifest.json').write_text(json.dumps(manifest))
            module=directory/'adoption.py'; module.write_bytes(b'pass\n')
            name=module.relative_to(root).as_posix()
            lock=Path(temp)/'lock.json'
            value=dict(schema=1,kind='FixedX2EfiProviderSourceLock',
                       public_commit='72f864e0498c10366493c74608c5ffed62cc99ca',
                       files=[dict(path=name,size=5,sha256=hashlib.sha256(b'pass\n').hexdigest())])
            lock.write_text(json.dumps(value))
            x2_efi_provider.verify(root,lock)
            module.write_bytes(b'fail\n')
            with self.assertRaisesRegex(ValueError, 'Fixed provider source differs'):
                x2_efi_provider.verify(root,lock)
            module.write_bytes(b'pass\n')
            manifest['id']='device:oxp3'; (root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'requires the X2 package'):
                x2_efi_provider.verify(root,lock)
            manifest['id']='device:x2-mini'; (root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError): oxp3_efi_provider.verify(root,lock)
            (directory/'unreviewed.py').write_bytes(b'pass\n')
            with self.assertRaisesRegex(ValueError,'Unexpected X2 maintenance'):
                x2_efi_provider.verify(root,lock)


if __name__ == '__main__':
    unittest.main()
