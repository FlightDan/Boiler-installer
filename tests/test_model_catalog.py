import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.error import URLError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.model_catalog import load_models,bundled_model_package,bundled_packages,REPOSITORY
from protocol.manifest import ProtocolError

spec=importlib.util.spec_from_file_location('model_package_builder',ROOT/'scripts/package-installer.py')
packager=importlib.util.module_from_spec(spec);spec.loader.exec_module(packager)
spec=importlib.util.spec_from_file_location('model_catalog_preparer',ROOT/'scripts/prepare-model-catalog.py')
preparer=importlib.util.module_from_spec(spec);spec.loader.exec_module(preparer)


class ModelCatalogTests(unittest.TestCase):
    def fixture(self,root):
        shutil.copytree(ROOT/'components/model-catalog',root/'components/model-catalog')
        # The migration's final production lock is regenerated only after the
        # independent public source commit exists. Test the new lock shape here.
        path=root/'components/model-catalog/lock.json'
        lock=json.loads(path.read_bytes())
        lock['source']={'repository':REPOSITORY,'commit':'a'*40,'subtree':'adaptations'}
        path.write_text(json.dumps(lock))

    def bundle(self,root):
        package=root/'cache/model-bundles/packages/oxp3'
        (package/'payload').mkdir(parents=True)
        (package/'payload/data').write_bytes(b'fixture\n')
        manifest={'schema_version':1,'kind':'DevicePackage','id':'device:oxp3','version':'0.1.0-test.1',
                  'source':{'kind':'local','path':'fixture'},'compatibility':{},'components':[],
                  'files':[{'path':'payload/data','size':8,'sha256':hashlib.sha256(b'fixture\n').hexdigest()}]}
        data=(json.dumps(manifest)+'\n').encode()
        (package/'manifest.json').write_bytes(data)
        descriptor={'schema_version':1,'kind':'LocalTestModelBundles','packages':[{
            'id':manifest['id'],'version':manifest['version'],'directory':'cache/model-bundles/packages/oxp3',
            'manifest_sha256':hashlib.sha256(data).hexdigest()}]}
        (root/'cache/model-bundles/manifest.json').write_text(json.dumps(descriptor))
        return package

    def test_offline_guides_are_display_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root)
            result=load_models(root=root,online=False)
            self.assertTrue(result['offline'])
            self.assertEqual({model['id'] for model in result['models']},{'device:oxp3','device:x2-mini'})
            self.assertEqual(result['bundled_packages'],[])
            x2=next(model for model in result['models'] if model['id']=='device:x2-mini')
            pl4=next(item for item in x2['components'] if item['id']=='pl4-175w')
            self.assertEqual(pl4['recommendation'],'pending')
            self.assertFalse(x2['installable'])
            self.assertTrue(all(item['default'] is False for item in x2['components']))
            self.assertIn('/Boiler-installer/blob/'+'a'*40+'/adaptations/brands/',x2['guide_url'])

    def test_online_reads_exact_repository_commit_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root)
            lock=json.loads((root/'components/model-catalog/lock.json').read_text())
            def reader(url,maximum):
                prefix='https://raw.githubusercontent.com/FlightDan/Boiler-installer/'+lock['source']['commit']+'/adaptations/'
                self.assertTrue(url.startswith(prefix))
                return (root/'components/model-catalog/snapshot'/url.removeprefix(prefix)).read_bytes()
            self.assertFalse(load_models(root=root,reader=reader)['offline'])

    def test_missing_or_unsafe_subtree_is_rejected(self):
        for subtree in (None,'../adaptations','/adaptations','adaptations/../private','adaptations\\private'):
            with self.subTest(subtree=subtree),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);self.fixture(root)
                path=root/'components/model-catalog/lock.json';lock=json.loads(path.read_bytes())
                lock['source']['subtree']=subtree;path.write_text(json.dumps(lock))
                with self.assertRaises(ProtocolError):load_models(root=root,online=False)

    @unittest.skipUnless(shutil.which('git'),'Git is required for the source-commit fixture')
    def test_preparer_reads_exact_public_commit_subtree_and_retains_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);checkout=root/'public';checkout.mkdir()
            def git(*args):
                return subprocess.check_output(['git','-C',str(checkout),*args],stderr=subprocess.PIPE).decode().strip()
            git('init');git('remote','add','origin','https://github.com/'+REPOSITORY+'.git')
            snapshot=ROOT/'components/model-catalog/snapshot'
            for member in ('adaptations.json','brands/onexplayer/oxp3/manifest.json','brands/onexplayer/x2-mini/manifest.json'):
                destination=checkout/'adaptations'/member;destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_bytes((snapshot/member).read_bytes())
            git('add','adaptations');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','Public fixture')
            commit=git('rev-parse','HEAD')
            # A dirty working copy cannot replace the explicitly pinned commit.
            (checkout/'adaptations/adaptations.json').write_text('{}')
            output=root/'prepared'
            lock=preparer.prepare(checkout,commit,output)
            self.assertEqual(lock['source'],dict(repository=REPOSITORY,commit=commit,subtree='adaptations'))
            for member in lock['files']:
                self.assertEqual((output/'snapshot'/member['path']).read_bytes(),(snapshot/member['path']).read_bytes())
            installed=root/'consumer/components/model-catalog';installed.parent.mkdir(parents=True)
            shutil.copytree(output,installed)
            result=load_models(root=root/'consumer',online=False)
            self.assertTrue(all('/'+commit+'/adaptations/' in model['guide_url'] for model in result['models']))
            git('remote','set-url','origin','https://github.com/FlightDan/Boiler-devices.git')
            with self.assertRaisesRegex(ValueError,'Boiler-installer'):
                preparer.prepare(checkout,commit,root/'rejected')

    def test_model_registry_asset_names_are_separate_from_maintenance_feed(self):
        source=(ROOT/'app/BoilerDevicePanel.cs').read_text(encoding='utf-8')
        base='https://github.com/FlightDan/Boiler-installer/releases/latest/download/'
        self.assertIn(base+'boiler-device-index.json',source)
        self.assertIn(base+'boiler-device-index.sig',source)
        self.assertNotIn('Boiler-devices/releases/latest',source)

    def test_network_failure_uses_bundled_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root)
            def unavailable(*args):raise URLError('offline')
            self.assertTrue(load_models(root=root,reader=unavailable)['offline'])

    def test_corrupt_remote_metadata_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root)
            with self.assertRaisesRegex(ProtocolError,'pinned source'):
                load_models(root=root,reader=lambda *args:b'{}')

    def test_corrupt_offline_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root)
            (root/'components/model-catalog/snapshot/adaptations.json').write_text('{}')
            with self.assertRaisesRegex(ProtocolError,'pinned source'):
                load_models(root=root,online=False)

    def test_local_bundle_requires_explicit_session_trust(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root);self.bundle(root)
            with self.assertRaisesRegex(ProtocolError,'Explicit session trust'):
                bundled_model_package('device:oxp3',root=root)
            result=bundled_model_package('device:oxp3',root=root,trust_local_test=True)
            self.assertEqual(result['package']['id'],'device:oxp3')
            self.assertEqual(len(load_models(root=root,online=False)['bundled_packages']),1)
            with self.assertRaisesRegex(ProtocolError,'No bundled executable'):
                bundled_model_package('device:x2-mini',root=root,trust_local_test=True)

    def test_bundle_payload_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root);package=self.bundle(root)
            (package/'payload/data').write_bytes(b'changed\n')
            with self.assertRaises(ProtocolError):
                bundled_model_package('device:oxp3',root=root,trust_local_test=True)

    def test_public_packaging_rejects_development_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root);self.bundle(root)
            with self.assertRaisesRegex(ValueError,'--local-test'):
                packager.local_model_package_files(root)
            files=packager.local_model_package_files(root,local_test=True)
            self.assertIn('cache/model-bundles/packages/oxp3/payload/data',files)
            (root/'cache/model-bundles/unlisted').write_text('not approved')
            with self.assertRaisesRegex(ValueError,'unlisted'):
                packager.local_model_package_files(root,local_test=True)

    def test_bundle_path_cannot_select_an_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root);self.bundle(root)
            path=root/'cache/model-bundles/manifest.json';value=json.loads(path.read_text())
            value['packages'][0]['directory']='components'
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ProtocolError,'fixed identity'):
                bundled_packages(root)

    def test_unreviewed_default_expansion_cannot_enter_test_installer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root);package=self.bundle(root)
            selection=json.dumps({'repairs':['oxp3:input'],'plugins':[]}).encode()
            (package/'selection.json').write_bytes(selection)
            manifest=json.loads((package/'manifest.json').read_text())
            manifest['files'].append({'path':'selection.json','size':len(selection),'sha256':hashlib.sha256(selection).hexdigest()})
            manifest['selection_contract']={'kind':'oxp3-legacy-options-v1','component':'oxp3:legacy-adaptation'}
            manifest['legacy_capabilities']=[{'id':'oxp3:input','default':True,'available':True},
                                             {'id':'oxp3:hdr','default':True,'available':True}]
            data=json.dumps(manifest).encode();(package/'manifest.json').write_bytes(data)
            descriptor=root/'cache/model-bundles/manifest.json'
            value=json.loads(descriptor.read_text());value['packages'][0]['manifest_sha256']=hashlib.sha256(data).hexdigest()
            descriptor.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'UI defaults differ'):
                packager.local_model_package_files(root,local_test=True)


if __name__=='__main__':unittest.main()
