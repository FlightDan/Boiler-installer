"""Synthetic authority overlay checks. BOILER_INSTALLER_ROOT is explicit test input."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT=Path(__file__).resolve().parents[1]
INSTALLER=os.environ.get('BOILER_INSTALLER_ROOT')
if not INSTALLER: raise RuntimeError('BOILER_INSTALLER_ROOT is required')
INSTALLER=Path(INSTALLER).resolve(strict=True)
sys.path.insert(0,str(INSTALLER))
spec=importlib.util.spec_from_file_location('authority_fingerprint_overlay',ROOT/'scripts/build-fingerprint-overlay.py')
overlay=importlib.util.module_from_spec(spec);spec.loader.exec_module(overlay)


def encoded(v): return (json.dumps(v,ensure_ascii=False,indent=2)+'\n').encode()
def sha(v): return hashlib.sha256(v).hexdigest()

class OverlayTests(unittest.TestCase):
    def test_fixed_inputs_reject_other_model_native_and_version(self):
        arguments=['device:oxp3',overlay.RELEASE_BASES['device:oxp3'],overlay.RELEASE_NATIVE,
                   '0.2.0-preview.1',ROOT,'a'*40,INSTALLER,'b'*40]
        for pos,value in ((0,'device:x2-mini'),(1,'0'*64),(2,'0'*64),(3,'0.1.0-localtest.1'),(3,None)):
            args=arguments.copy();args[pos]=value
            with self.subTest(pos=pos,value=value),self.assertRaises(ValueError): overlay.release_inputs(*args)

    def test_installer_engine_must_support_release_minimum(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'protocol').mkdir();manifest=root/'protocol/manifest.py'
            for version in ('0.1.4','0.0.9'):
                manifest.write_text('ENGINE_VERSION = "'+version+'"\n')
                with self.assertRaisesRegex(ValueError,'0.1.5'): overlay.require_installer_engine(root)
            manifest.write_text('ENGINE_VERSION = "0.1.5"\n')
            overlay.require_installer_engine(root)
            manifest.write_text('ENGINE_VERSION = "0.1.5"\nENGINE_VERSION = "0.1.4"\n')
            with self.assertRaises(ValueError): overlay.require_installer_engine(root)

    def test_explicit_installer_protocol_cannot_be_reused_from_another_root(self):
        overlay.load_protocol(INSTALLER)
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaisesRegex(ValueError,'another installer root'): overlay.load_protocol(name)

    def test_foreign_cached_protocol_submodule_is_rejected(self):
        overlay.load_protocol(INSTALLER)
        with tempfile.TemporaryDirectory() as name:
            foreign=Path(name)/'cpu_contract.py'
            foreign.write_text('def mutable_target(*args):\n    raise AssertionError("foreign code executed")\n')
            spec=importlib.util.spec_from_file_location('protocol.cpu_contract',foreign)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            with mock.patch.dict(sys.modules,{'protocol.cpu_contract':module}):
                with self.assertRaisesRegex(ValueError,'protocol.cpu_contract'):
                    overlay.build(Path(name)/'base','0'*64,Path(name)/'native','0'*64,'0'*64,
                                  Path(name)/'rejected',installer_root=INSTALLER)
                self.assertFalse((Path(name)/'rejected').exists())
        same=importlib.import_module('protocol.cpu_contract')
        self.assertTrue(Path(same.__file__).resolve().is_relative_to(INSTALLER/'protocol'))
        overlay.load_protocol(INSTALLER)

    def test_installer_snapshot_execution_uses_its_own_authority_sources(self):
        with tempfile.TemporaryDirectory() as name:
            snapshot=Path(name)/'installer/adaptations'
            inventory=json.loads((ROOT/'model-source-files.json').read_bytes())
            for row in inventory['files']:
                source=ROOT/row['path']; target=snapshot/row['path']
                target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,target)
            spec=importlib.util.spec_from_file_location('snapshot_overlay',snapshot/'scripts/build-fingerprint-overlay.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            self.assertEqual(module.SOURCE_ROOT,snapshot)
            self.assertEqual(module.prep.SOURCE_ROOT,snapshot)
            self.assertEqual(Path(module.prep.fingerprint_provenance.__file__).resolve(), snapshot/'scripts/fingerprint_provenance.py')
            native=Path(name)/'native';native.mkdir();(native/'runtime-manifest.json').write_bytes(b'{}');(native/'fixture').write_bytes(b'fixture')
            with mock.patch.object(module.prep.native,'validate',return_value={'files':[{'path':'fixture'}]}):
                result=module.prep.prepare(native,'a'*64,sha((snapshot/'fingerprint-source-lock.json').read_bytes()),Path(name)/'prepared')
            self.assertFalse(result['installable'])
            self.assertTrue((Path(name)/'prepared/sources/common/fingerprint/bridge/main.py').is_file())

    def test_checkout_byte_and_commit_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'member').write_bytes(b'locked\n')
            def git(*args): return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE).decode().strip()
            git('init');git('remote','add','origin','https://github.com/FlightDan/Boiler-adaptations.git')
            git('add','member');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture')
            commit=git('rev-parse','HEAD')
            overlay.verify_checkout(root,overlay.REPOSITORY,commit,['member'])
            (root/'member').write_bytes(b'tampered\n')
            with self.assertRaisesRegex(ValueError,'Source bytes differ'):
                overlay.verify_checkout(root,overlay.REPOSITORY,commit,['member'])
            git('add','member');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','changed')
            with self.assertRaisesRegex(ValueError,'equal HEAD'):
                overlay.verify_checkout(root,overlay.REPOSITORY,commit,['member'])

    def test_public_installer_commit_and_whitelist_are_verified(self):
        manifest=json.loads((INSTALLER/'scripts/source-files.json').read_bytes())
        overlay.verify_checkout(INSTALLER,overlay.INSTALLER_REPOSITORY,
            '04dce422d0c4831ced6d2b9efdbd852e7b02a724',manifest['files'])
        with self.assertRaises(ValueError):
            overlay.verify_checkout(INSTALLER,'Other/repository','04dce422d0c4831ced6d2b9efdbd852e7b02a724',manifest['files'])
        with self.assertRaises(ValueError):
            overlay.verify_checkout(INSTALLER,overlay.INSTALLER_REPOSITORY,'0'*40,manifest['files'])

    def test_release_rebuild_preserves_base_evidence_and_contract(self):
        overlay.load_protocol(INSTALLER)
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);base=root/'base';base.mkdir()
            def package(wifi=False,decky=True):
                return {'schema_version':1,'kind':'DevicePackage','id':'device:oxp3','version':'0.1.0-test.1',
                    'source':{'kind':'local','path':'fixture'},'compatibility':{},'files':[],
                    'components':[{'schema_version':1,'kind':'ComponentManifest','id':'oxp3:legacy-adaptation',
                        'version':'0.1.0-test.1','files':[]}],
                    'legacy_capabilities':[{'id':'oxp3:wifi','available':wifi,'default':False},
                                           {'id':'oxp3:decky','available':decky,'default':False}],
                    'selection_contract':{'kind':'oxp3-legacy-options-v1','component':'oxp3:legacy-adaptation'},
                    'shared_adaptations':{'kind':'boiler-shared-static-v1','components':[],'warnings':[]}}
            def prepare(runtime,native,source,out):
                out=Path(out)
                for path in [*overlay.MODEL_RULES['device:oxp3'][:1],*overlay.TARGETS]:
                    dest=out/'sources'/path;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/path,dest)
                dest=out/'sources'/overlay.SHARED_PROFILE/'COPYING';dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/overlay.SHARED_PROFILE/'COPYING',dest)
                native_dir=out/'native';native_dir.mkdir(parents=True);(native_dir/'runtime-manifest.json').write_bytes(encoded({'files':[]}));(native_dir/'closure-proof.json').write_bytes(b'{}')
            source_hash=sha((ROOT/'fingerprint-source-lock.json').read_bytes())
            good=encoded(package());(base/'manifest.json').write_bytes(good)
            def run(output):
                return overlay.build(base,sha(good),root/'native','a'*64,source_hash,output,model_id='device:oxp3',
                    installer_root=INSTALLER,release=True,version='0.2.0-preview.1',
                    source_repository=overlay.REPOSITORY,source_commit='c'*40,authority_root=ROOT,installer_commit='d'*40)
            with mock.patch.object(overlay,'release_inputs'),mock.patch.object(overlay.prep,'prepare',side_effect=prepare):
                report=run(root/'first');second=run(root/'second')
                self.assertEqual(report['sha256'],second['sha256'])
                result=json.loads((root/'first/package/manifest.json').read_bytes())
                self.assertEqual(result['source'],{'kind':'github','repository':overlay.REPOSITORY,'commit':'c'*40})
                self.assertEqual(result['minimum_engine_version'],'0.1.5')
                self.assertEqual(result['components'][-1]['minimum_engine_version'],'0.1.5')
                self.assertEqual((root/'first/package/fingerprint/evidence/base-manifest.json').read_bytes(),good)
                provenance=json.loads((root/'first/package/fingerprint/evidence/build-provenance.json').read_bytes())
                self.assertEqual(provenance['base_source']['commit'],overlay.BASE_AUTHORITY_COMMIT)
                self.assertEqual(provenance['builder_source']['commit'],'c'*40)
                self.assertEqual(provenance['installer_source']['commit'],'d'*40)
                self.assertNotIn(str(root),json.dumps(provenance))
                self.assertEqual(report['base_source']['commit'],overlay.BASE_AUTHORITY_COMMIT)
                self.assertEqual(report['installer_source']['commit'],'d'*40)
                self.assertNotIn(str(root),json.dumps(report))
                for bad in (package(wifi=True),package(decky=False)):
                    raw=encoded(bad);(base/'manifest.json').write_bytes(raw)
                    with self.assertRaisesRegex(ValueError,'Decky or BE201'):
                        overlay.build(base,sha(raw),root/'native','a'*64,source_hash,root/'rejected',model_id='device:oxp3',
                            installer_root=INSTALLER,release=True,version='0.2.0-preview.1',source_repository=overlay.REPOSITORY,
                            source_commit='c'*40,authority_root=ROOT,installer_commit='d'*40)
                    self.assertFalse((root/'rejected').exists())

if __name__=='__main__': unittest.main()
