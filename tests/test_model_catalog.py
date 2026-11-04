import hashlib
import copy
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
from protocol.model_catalog import load_models,bundled_model_package,bundled_packages,REPOSITORY,catalog_records,guide_model
from protocol.manifest import ProtocolError

spec=importlib.util.spec_from_file_location('model_package_builder',ROOT/'scripts/package-installer.py')
packager=importlib.util.module_from_spec(spec);spec.loader.exec_module(packager)
spec=importlib.util.spec_from_file_location('model_catalog_preparer',ROOT/'scripts/prepare-model-catalog.py')
preparer=importlib.util.module_from_spec(spec);spec.loader.exec_module(preparer)


class ModelCatalogTests(unittest.TestCase):
    def test_branded_ids_distinguish_same_model_slug_and_legacy_is_path_bound(self):
        def row(brand, identity):
            return dict(id=identity, guide_id='guide:'+identity.split(':',1)[1],
                manifest=f'brands/{brand}/shared/manifest.json', name={'en':'Shared','zh':'Shared'},
                hardware_matching=dict(mode='advisory-exact-dmi-v1',pairs=[dict(manufacturer=brand,model='Shared')]))
        rows=[row('alpha','device:alpha-shared'),row('beta','device:beta-shared')]
        catalog=dict(kind='AdaptationSourceCatalog',schema_version=1,installable=False,
                     models=[r['manifest'] for r in rows],runtime_models=rows)
        self.assertEqual([r['id'] for r in catalog_records(catalog)],['device:alpha-shared','device:beta-shared'])
        rows[1]['id']='device:shared';rows[1]['guide_id']='guide:shared'
        with self.assertRaises(ProtocolError): catalog_records(catalog)
        rows[1]['id']='device:alpha-shared';rows[1]['guide_id']='guide:alpha-shared'
        with self.assertRaises(ProtocolError): catalog_records(catalog)

    def test_preparer_accepts_current_two_model_guides(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); checkout=root/'public'; checkout.mkdir()
            def git(*args):
                return subprocess.check_output(['git','-C',str(checkout),*args],stderr=subprocess.PIPE).decode().strip()
            git('init'); git('remote','add','origin','https://github.com/'+REPOSITORY+'.git')
            for member in ('adaptations.json','brands/onexplayer/oxp3/manifest.json','brands/onexplayer/x2-mini/manifest.json'):
                target=checkout/member; target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes((ROOT/'adaptations'/member).read_bytes())
            git('add','adaptations.json','brands'); git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','Current guide fixture')
            result=preparer.prepare(checkout,git('rev-parse','HEAD'),root/'prepared')
            self.assertEqual(len(result['files']),3)
            for identity in ('oxp3','x2-mini'):
                guide=json.loads((root/'prepared/snapshot/brands/onexplayer'/identity/'manifest.json').read_bytes())
                self.assertTrue(next(item for item in guide['components'] if item['id']=='fingerprint-unlock')['default'])

    def test_current_guides_allow_only_canonical_fingerprint_defaults(self):
        catalog=json.loads((ROOT/'adaptations/adaptations.json').read_bytes())
        rows={row['guide_id']:row for row in catalog_records(catalog)}
        source={'repository':REPOSITORY,'commit':'a'*40,'subtree':''}
        for identity in ('guide:oxp3','guide:x2-mini'):
            row=rows[identity]; path=row['manifest']
            guide=json.loads((ROOT/'adaptations'/path).read_bytes())
            rendered=guide_model(guide,path,source,row)
            self.assertFalse(rendered['installable'])
            self.assertEqual([item['id'] for item in rendered['components'] if item['default']],['fingerprint-unlock'])
            historical=copy.deepcopy(guide)
            next(item for item in historical['components'] if item['id']=='fingerprint-unlock')['default']=False
            self.assertFalse(guide_model(historical,path,source,row)['installable'])
            invalid=copy.deepcopy(guide)
            next(item for item in invalid['components'] if item['id']!='fingerprint-unlock')['default']=True
            with self.assertRaisesRegex(ProtocolError,'Invalid model option metadata'):
                guide_model(invalid,path,source,row)
            wrong_path=path.replace('brands/onexplayer/','brands/other/')
            wrong_guide=copy.deepcopy(guide); wrong_guide['guide']=wrong_path.removesuffix('manifest.json')+'model.md'
            wrong_row=copy.deepcopy(row); wrong_row['manifest']=wrong_path
            with self.assertRaises(ProtocolError):
                guide_model(wrong_guide,wrong_path,source,wrong_row)

    def fixture(self,root):
        shutil.copytree(ROOT/'components/model-catalog',root/'components/model-catalog')
        # The migration's final production lock is regenerated only after the
        # independent public source commit exists. Test the new lock shape here.
        path=root/'components/model-catalog/lock.json'
        lock=json.loads(path.read_bytes())
        lock['source']={'repository':REPOSITORY,'commit':'a'*40,'subtree':''}
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

    def test_historical_installer_catalog_lock_remains_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            shutil.copytree(ROOT/'components/model-catalog',root/'components/model-catalog')
            path=root/'components/model-catalog/lock.json';lock=json.loads(path.read_bytes())
            lock['source']=dict(repository='FlightDan/Boiler-installer',commit='a'*40,subtree='adaptations');path.write_text(json.dumps(lock))
            result=load_models(root=root,online=False)
            self.assertEqual(result['source']['repository'],'FlightDan/Boiler-installer')
            self.assertEqual(result['source']['subtree'],'adaptations')
            self.assertEqual(len(result['models']),2)

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
            self.assertTrue(all(item['default'] is False or item['id']=='fingerprint-unlock' for item in x2['components']))
            self.assertIn('/Boiler-adaptations/blob/'+'a'*40+'/brands/',x2['guide_url'])

    def test_known_fingerprint_defaults_and_false_values_reach_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root)
            lock_path=root/'components/model-catalog/lock.json';lock=json.loads(lock_path.read_bytes())
            for selected in (True,False):
                for entry in lock['files']:
                    if not entry['path'].endswith('/manifest.json'):continue
                    path=root/'components/model-catalog/snapshot'/entry['path'];guide=json.loads(path.read_bytes())
                    guide['components']=[item for item in guide['components'] if item['id']!='fingerprint-unlock']
                    guide['components'].append(dict(id='fingerprint-unlock',name='Fingerprint',recommendation='optional',default=selected,dependencies=[],sources=[],status='external_runtime_required',notes='Fixture'))
                    raw=json.dumps(guide).encode();path.write_bytes(raw);entry.update(size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
                lock_path.write_text(json.dumps(lock))
                result=load_models(root=root,online=False)
                for model in result['models']:
                    self.assertIs(next(item for item in model['components'] if item['id']=='fingerprint-unlock')['default'],selected)
            entry=next(x for x in lock['files'] if x['path'].endswith('/manifest.json'));path=root/'components/model-catalog/snapshot'/entry['path'];guide=json.loads(path.read_bytes());guide['components'][0]['default']=True
            raw=json.dumps(guide).encode();path.write_bytes(raw);entry.update(size=len(raw),sha256=hashlib.sha256(raw).hexdigest());lock_path.write_text(json.dumps(lock))
            with self.assertRaises(ProtocolError):load_models(root=root,online=False)

    def test_online_reads_exact_repository_commit_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);self.fixture(root)
            lock=json.loads((root/'components/model-catalog/lock.json').read_text())
            def reader(url,maximum):
                prefix='https://raw.githubusercontent.com/FlightDan/Boiler-adaptations/'+lock['source']['commit']+'/'
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
                destination=checkout/member;destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_bytes((snapshot/member).read_bytes())
            git('add','adaptations.json','brands');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','Public fixture')
            commit=git('rev-parse','HEAD')
            # A dirty working copy cannot replace the explicitly pinned commit.
            (checkout/'adaptations.json').write_text('{}')
            output=root/'prepared'
            lock=preparer.prepare(checkout,commit,output)
            self.assertEqual(lock['source'],dict(repository=REPOSITORY,commit=commit,subtree=''))
            for member in lock['files']:
                self.assertEqual((output/'snapshot'/member['path']).read_bytes(),(snapshot/member['path']).read_bytes())
            installed=root/'consumer/components/model-catalog';installed.parent.mkdir(parents=True)
            shutil.copytree(output,installed)
            result=load_models(root=root/'consumer',online=False)
            self.assertTrue(all('/'+commit+'/brands/' in model['guide_url'] for model in result['models']))
            git('remote','set-url','origin','https://github.com/FlightDan/Boiler-devices.git')
            with self.assertRaisesRegex(ValueError,'Boiler-adaptations'):
                preparer.prepare(checkout,commit,root/'rejected')

    def test_model_registry_asset_names_are_separate_from_maintenance_feed(self):
        source=(ROOT/'app/BoilerDevicePanel.cs').read_text(encoding='utf-8')
        base='https://github.com/FlightDan/Boiler-adaptations/releases/latest/download/'
        self.assertIn(base+'index.json',source)
        self.assertIn(base+'index.sig',source)
        self.assertNotIn('Boiler-devices/releases/latest',source)
        self.assertNotIn('Boiler-installer/releases/latest/download/boiler-device-index',source)
        maintenance=(ROOT/'scripts/maintenance_bootstrap.py').read_text(encoding='utf-8')
        self.assertIn('REGISTRY_ID = "registry:boiler-maintenance"',maintenance)
        self.assertIn('REPOSITORY = "FlightDan/Boiler-installer"',maintenance)

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
