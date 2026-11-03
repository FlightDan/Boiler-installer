import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('full_efi_sources',ROOT/'adaptations/brands/onexplayer/oxp3/bridge/full_efi_sources.py')
sources=importlib.util.module_from_spec(spec);spec.loader.exec_module(sources)
from components import oxp3_full_efi_provider,oxp3_efi_provider


class FullOxp3Sources(unittest.TestCase):
    def test_transform_is_exact_and_preserves_strict_oxp3_identity(self):
        bridge=ROOT/'adaptations/brands/onexplayer/oxp3/bridge'
        raw=(bridge/'adapter.py').read_bytes(); result=sources.transform('adapter.py',raw)
        self.assertIn(b"efi_context['source'].get('package_id') != 'device:oxp3'",result)
        self.assertIn(b'/mnt/payload/components/oxp3_full_efi_provider.lock.json',result)
        self.assertNotIn(b"efi_context['source'].get('package_id') != 'device:x2-mini'",result)
        with self.assertRaises(ValueError): sources.transform('adapter.py',raw+b'\n')
        maintenance=(bridge/'maintenance_adapter.py').read_bytes()
        self.assertEqual(sources.transform('maintenance_adapter.py',maintenance),maintenance)

    def test_full_provider_requires_its_contract_and_complete_maintenance_closure(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'package';(root/'payload/legacy/components/hdr').mkdir(parents=True)
            folder=root/'payload/legacy/maintenance/app/oxp3_maintenance';folder.mkdir(parents=True)
            file=folder/'adoption.py';file.write_bytes(b'pass\n')
            manifest=dict(id='device:oxp3',oxp3_full_efi_contract=dict(kind='oxp3-full-split-lock-efi-v1',component='oxp3:legacy-adaptation'))
            (root/'manifest.json').write_text(json.dumps(manifest))
            lock=Path(temp)/'lock.json';lock.write_text(json.dumps(dict(schema=1,kind='FixedFullOxp3EfiProviderSourceLock',
                public_commit='72f864e0498c10366493c74608c5ffed62cc99ca',
                files=[dict(path=file.relative_to(root).as_posix(),size=5,sha256=hashlib.sha256(b'pass\n').hexdigest())])))
            oxp3_full_efi_provider.verify(root,lock)
            code=("import importlib.util,sys; from pathlib import Path; "
                  "path=Path(sys.argv[1]); managed=path.resolve().parents[1]; "
                  "assert str(managed) not in sys.path; "
                  "spec=importlib.util.spec_from_file_location('_boiler_fixed_full_provider',path); "
                  "provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider); "
                  "provider.verify(Path(sys.argv[2]),Path(sys.argv[3]))")
            subprocess.run([sys.executable,'-I','-B','-c',code,str(ROOT/'components/oxp3_full_efi_provider.py'),
                            str(root),str(lock)],check=True)
            with self.assertRaises(ValueError): oxp3_efi_provider.verify(root,lock)
            manifest['id']='device:x2-mini';(root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'requires the OXP3 package'): oxp3_full_efi_provider.verify(root,lock)
            manifest['id']='device:oxp3';(root/'manifest.json').write_text(json.dumps(manifest))
            (folder/'injected.py').write_bytes(b'pass\n')
            with self.assertRaisesRegex(ValueError,'Unexpected OXP3 maintenance'): oxp3_full_efi_provider.verify(root,lock)


if __name__=='__main__': unittest.main()
