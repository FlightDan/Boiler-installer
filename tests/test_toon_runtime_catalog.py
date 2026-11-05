"""New TOON models remain display-only through pinned online/offline snapshots."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol import model_catalog as runtime
from protocol.manifest import ProtocolError


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec); spec.loader.exec_module(result); return result


catalog=module('runtime_toon_catalog',ROOT/'scripts/toon_catalog.py')
preparer=module('runtime_snapshot_preparer',ROOT/'scripts/prepare-model-catalog.py')
bundles=module('runtime_bundle_preparer',ROOT/'scripts/prepare-model-bundles.py')


class ToonRuntimeCatalogTests(unittest.TestCase):
    def source_fixture(self,root):
        adaptations=root/'source/adaptations'
        shutil.copytree(ROOT/'adaptations', adaptations)
        parser=catalog.decoder(adaptations)
        original=adaptations/'devices/onexplayer/oxp3.toon'
        model=parser.loads(original.read_bytes())
        model.update(id='guide:example-future',canonical_id='device:example-future',legacy_aliases=dict(guide=[],package=[]),device_record_path='devices/example-future.json',name='未来机型',guide='brands/example/future/model.md')
        model['resource_model']['id']='device:example-future'
        model['resource_model'].pop('common_references', None)
        model['runtime']=dict(id='device:example-future',name={'en':'Future Demo','zh':'未来机型'},
            hardware_matching=dict(mode='advisory-exact-dmi-v1',pairs=[dict(manufacturer='EXAMPLE INC',model='FUTURE 1')]))
        model['components']=[dict(id='base-system',name='未来机型说明',recommendation='pending',default=False,
            dependencies=[],sources=[],status='implementation_missing',notes='Display-only synthetic model; no install implementation.')]
        model['build'].update(complete=False,missing=['Synthetic future implementation is not supplied.'])
        model['package']=dict(schema_version=1,kind='DevicePackage',id='device:example-future',version='0.1.0',
            source=dict(kind='local',path='synthetic-test-only'),components=[dict(schema_version=1,kind='ComponentManifest',
                id='future:fixture',version='0.1.0',default=False,files=[dict(source='payload/data',target='etc/future-test.conf',
                    size=8,sha256=hashlib.sha256(b'fixture\n').hexdigest(),mode=420)])])
        guide=adaptations/model['guide']; guide.parent.mkdir(parents=True)
        shutil.copyfile(adaptations/'brands/onexplayer/oxp3/model.md',guide)
        path=adaptations/'devices/example/future.toon'; path.parent.mkdir(parents=True)
        path.write_text(parser.dumps(model),encoding='utf-8')
        catalog.generate(adaptations)
        index=parser.loads((adaptations/'models.toon').read_bytes())
        self.assertEqual(len(index['models']),3)
        generated=json.loads((adaptations/'adaptations.json').read_bytes())
        self.assertEqual(len(generated['runtime_models']),3)
        return adaptations

    def snapshot_fixture(self,root):
        adaptations=self.source_fixture(root)
        checkout=root/'public'; checkout.mkdir()
        def git(*args): return subprocess.check_output(['git','-C',str(checkout),*args],stderr=subprocess.PIPE)
        subprocess.check_output(['git','init','-q',str(checkout)],stderr=subprocess.PIPE)
        git('config','user.name','Model snapshot fixture'); git('config','user.email','fixture@example.invalid')
        git('config','core.autocrlf','false'); git('remote','add','origin','https://github.com/'+runtime.REPOSITORY+'.git')
        for name,raw in catalog.views(adaptations).items():
            destination=checkout/name; destination.parent.mkdir(parents=True,exist_ok=True); destination.write_bytes(raw)
        git('add','.'); git('commit','-qm','Synthetic three-model display metadata')
        commit=git('rev-parse','HEAD').decode().strip()
        # The preparer must read exact committed Git objects, not mutable files.
        (checkout/'adaptations.json').write_text('{}')
        consumer=root/'consumer'
        lock=preparer.prepare(checkout,commit,consumer/'components/model-catalog')
        self.assertEqual(len(lock['files']),4)
        return consumer,lock

    def test_third_toon_reaches_real_git_snapshot_and_offline_online_views(self):
        with tempfile.TemporaryDirectory() as temporary:
            consumer,lock=self.snapshot_fixture(Path(temporary))
            offline=runtime.load_models(root=consumer,online=False)
            future=next(row for row in offline['models'] if row['id']=='device:example-future')
            self.assertEqual(future['name'],{'en':'Future Demo','zh':'未来机型'})
            self.assertEqual(future['hardware_matching']['pairs'],[dict(manufacturer='EXAMPLE INC',model='FUTURE 1')])
            self.assertFalse(future['installable']); self.assertEqual(offline['bundled_packages'],[])
            from maintenance.models import catalog as maintenance_catalog
            self.assertEqual(maintenance_catalog(runtime_root=consumer,online=False)['models'],offline['models'])
            self.assertNotIn('device:example-future',runtime.MODELS)  # legacy executable facade is deliberately unchanged
            self.assertIn('/'+lock['source']['commit']+'/brands/example/future/model.md',future['guide_url'])
            def reader(url,maximum):
                member=url.split('/'+lock['source']['commit']+'/',1)[1]
                data=(consumer/'components/model-catalog/snapshot'/member).read_bytes()
                self.assertLessEqual(len(data),maximum); return data
            online=runtime.load_models(root=consumer,reader=reader)
            self.assertFalse(online['offline']); self.assertEqual(online['models'],offline['models'])
            # A displayed model cannot acquire an executable package or trust.
            with self.assertRaisesRegex(ProtocolError,'Explicit session trust'):
                runtime.bundled_model_package('device:example-future',root=consumer)
            with self.assertRaisesRegex(ProtocolError,'No bundled executable'):
                runtime.bundled_model_package('device:example-future',root=consumer,trust_local_test=True)

    def test_new_identity_bundle_preparation_keeps_hash_path_and_explicit_trust(self):
        with tempfile.TemporaryDirectory() as temporary:
            consumer,_=self.snapshot_fixture(Path(temporary))
            package=consumer/'cache/model-bundles/packages/example-future'; (package/'payload').mkdir(parents=True)
            payload=b'fixture\n'; (package/'payload/data').write_bytes(payload)
            value=dict(schema_version=1,kind='DevicePackage',id='device:example-future',version='0.1.0-test.1',
                source=dict(kind='local',path='fixture'),components=[],files=[dict(path='payload/data',size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest())])
            (package/'manifest.json').write_text(json.dumps(value),encoding='utf-8')
            result=bundles.prepare(consumer,[package]); self.assertEqual(result['packages'][0]['id'],'device:example-future')
            with self.assertRaisesRegex(ProtocolError,'Explicit session trust'):
                runtime.bundled_model_package('device:example-future',root=consumer)
            item=runtime.bundled_model_package('device:example-future',root=consumer,trust_local_test=True)
            self.assertEqual(item['package']['id'],'device:example-future')
            (package/'payload/data').write_bytes(b'tampered')
            with self.assertRaises(ProtocolError): runtime.bundled_model_package('device:example-future',root=consumer,trust_local_test=True)

    def test_other_brand_x2_mini_does_not_inherit_onexplayer_builder_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=self.source_fixture(Path(temporary)); parser=catalog.decoder(source)
            model=parser.loads((source/'devices/example/future.toon').read_bytes())
            model.update(id='guide:example-x2-mini',canonical_id='device:example-x2-mini',
                guide='brands/example/x2-mini/model.md',device_record_path='devices/example-x2-mini.json')
            model['runtime']['id']='device:example-x2-mini';model['resource_model']['id']='device:example-x2-mini'
            model['package']['id']='device:example-x2-mini';model['package']['components'][0]['id']='x2-mini:fixture'
            catalog.validate_model(model,Path('devices/example/x2-mini.toon'))
            onex=parser.loads((source/'devices/onexplayer/x2-mini.toon').read_bytes())
            model['build']=onex['build'];model['package']=onex['package']
            with self.assertRaisesRegex(catalog.CatalogError,'X2 adapter requires the ONEXPLAYER X2 Mini model'):
                catalog.validate_model(model,Path('devices/example/x2-mini.toon'))

    def test_cli_preflight_resolves_canonical_and_declared_legacy_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=self.source_fixture(Path(temporary)); results=[]
            for identity in ('device:onexplayer-oxp3','guide:oxp3','device:oxp3'):
                command=[sys.executable,'-B',str(ROOT/'scripts/toon_catalog.py'),'preflight',
                         '--catalog-root',str(source),'--model',identity]
                result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
                self.assertIn(result.returncode,(0,1),result.stderr)
                results.append(json.loads(result.stdout))
            self.assertEqual(results[0],results[1]);self.assertEqual(results[1],results[2])
            bad=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/toon_catalog.py'),'preflight',
                '--catalog-root',str(source),'--model','guide:onexplayer-oxp3'],capture_output=True,text=True,encoding='utf-8')
            self.assertNotEqual(bad.returncode,0);self.assertIn('uniquely known',bad.stderr)

    def test_runtime_metadata_rejects_identity_paths_names_dmi_and_trust_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=self.source_fixture(Path(temporary))
            baseline=json.loads((source/'adaptations.json').read_bytes())
            for mutate in (
                lambda value: value['runtime_models'].__setitem__(1,copy.deepcopy(value['runtime_models'][0])),
                lambda value: value['runtime_models'][0]['name'].update(en=''),
                lambda value: value['runtime_models'][0]['hardware_matching'].update(mode='automatic'),
                lambda value: value['runtime_models'][0]['hardware_matching']['pairs'][0].update(model='bad\n'),
                lambda value: value['runtime_models'][0].update(manifest='brands/example/unknown/manifest.json'),
                lambda value: value['runtime_models'][0].update(installable=True),
                lambda value: value['models'].__setitem__(0,'../private.json'),
            ):
                value=copy.deepcopy(baseline); mutate(value)
                with self.subTest(value=value['runtime_models'][0]),self.assertRaises(ProtocolError): runtime.catalog_records(value)
            value=copy.deepcopy(baseline); del value['runtime_models']
            with self.assertRaisesRegex(ProtocolError,'TOON-derived'): runtime.catalog_records(value)

    def test_unlisted_snapshot_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            consumer,_=self.snapshot_fixture(Path(temporary))
            destination=consumer/'components/model-catalog'; lock=json.loads((destination/'lock.json').read_bytes())
            name='brands/example/unlisted/manifest.json'; data=b'{}'
            path=destination/'snapshot'/name; path.parent.mkdir(parents=True); path.write_bytes(data)
            lock['files'].append(dict(path=name,size=len(data),sha256=hashlib.sha256(data).hexdigest()))
            (destination/'lock.json').write_text(json.dumps(lock))
            with self.assertRaisesRegex(ProtocolError,'catalog'): runtime.load_models(root=consumer,online=False)

    def test_legacy_three_file_catalog_is_still_displayed(self):
        with tempfile.TemporaryDirectory() as temporary:
            consumer=Path(temporary); destination=consumer/'components/model-catalog'
            shutil.copytree(ROOT/'components/model-catalog',destination)
            path=destination/'lock.json'; lock=json.loads(path.read_bytes())
            lock['source']=dict(repository='FlightDan/Boiler-installer',commit='a'*40,subtree='adaptations')
            path.write_text(json.dumps(lock))
            self.assertEqual(len(lock['files']),3)
            result=runtime.load_models(root=consumer,online=False)
            self.assertEqual({row['id'] for row in result['models']},{'device:oxp3','device:x2-mini'})

    def test_physical_model_mismatch_remains_advisory_while_system_requirements_stay_separate(self):
        from protocol.manifest import compatible, device_advisory
        package=dict(id='device:example-future',compatibility=dict(device_ids=['future'],architectures=['x86_64'],
            os_ids=['steamos'],os_versions=['3.10']))
        other=dict(device_id='a-different-physical-model',architecture='x86_64',os_id='steamos',os_version='3.10')
        self.assertTrue(compatible(package,other))
        warning=device_advisory(package,other)
        self.assertEqual(warning['actual_device_id'],'a-different-physical-model')
        self.assertFalse(compatible(package,dict(other,os_version='3.11')))

    def runtime_source_fixture(self,consumer):
        from maintenance.engine import RUNTIME_FILES
        names=set(RUNTIME_FILES)|{'LICENSE','scripts/package-maintenance.py','scripts/package-maintenance-bootstrap.py',
            'scripts/maintenance_bootstrap.py'}
        names.discard('components/model-catalog/lock.json')
        policy=json.loads((ROOT/'scripts/source-files.json').read_bytes())
        names.update(name for name in policy['files']
            if (name.startswith('components/') and not name.startswith('components/model-catalog/'))
            or name.startswith('adaptations/vendor/'))
        for name in names:
            target=consumer/name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(ROOT/name,target)
        files=sorted(p.relative_to(consumer).as_posix() for p in consumer.rglob('*') if p.is_file())
        (consumer/'scripts/source-files.json').write_text(json.dumps(dict(kind='BoilerSourceWhitelist',files=sorted(set(files)|{'scripts/source-files.json'}))))
        return files

    def test_third_model_is_copied_by_real_maintenance_component_windows_closure_and_bootstrap(self):
        with tempfile.TemporaryDirectory() as temporary:
            work=Path(temporary); consumer,_=self.snapshot_fixture(work)
            self.runtime_source_fixture(consumer)
            from maintenance.engine import runtime_files
            future='components/model-catalog/snapshot/brands/example/future/manifest.json'
            self.assertIn((consumer/future).resolve(),runtime_files(root=consumer))
            preparation=module('third_model_integration_prepare',ROOT/'scripts/prepare-integration.py')
            output=work/'integration'
            with patch.object(preparation,'ROOT',consumer.resolve()): metadata=preparation.prepare(output)
            component=metadata['components']['core:maintenance']['component']
            target='usr/local/lib/boiler/'+future
            selected=next(row for row in component['files'] if row['target']==target)
            self.assertEqual((output/selected['source']).read_bytes(),(consumer/future).read_bytes())
            self.assertEqual(selected['sha256'],hashlib.sha256((consumer/future).read_bytes()).hexdigest())
            retained=work/'retained-runtime'
            for row in component['files']:
                if row['target'].startswith('usr/local/lib/boiler/'):
                    path=retained/row['target'].removeprefix('usr/local/lib/boiler/'); path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes((output/row['source']).read_bytes())
            self.assertEqual(len(runtime.load_models(root=retained,online=False)['models']),3)
            windows=module('third_model_windows_package',ROOT/'scripts/package-installer.py')
            members=windows.model_catalog_package_files(consumer)
            self.assertIn(future,members)
            files=json.loads((consumer/'scripts/source-files.json').read_bytes())['files']
            with self.assertRaisesRegex(ValueError,'source whitelist'):
                windows.model_catalog_package_files(consumer,set(files)-{future})
            bootstrap=module('third_model_bootstrap_package',consumer/'scripts/package-maintenance-bootstrap.py')
            archive=work/'three-models.pyz'; bootstrap.package(consumer,archive)
            with zipfile.ZipFile(archive) as bundle: self.assertIn(future,bundle.namelist())
            # Missing locked members fail before any integration output is made.
            (consumer/future).unlink()
            with patch.object(preparation,'ROOT',consumer.resolve()),self.assertRaisesRegex(RuntimeError,'model catalog runtime snapshot'):
                preparation.prepare(work/'must-not-exist')
            self.assertFalse((work/'must-not-exist').exists())

    def test_third_model_is_in_real_unsigned_maintenance_update_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            work=Path(temporary); consumer,_=self.snapshot_fixture(work)
            self.runtime_source_fixture(consumer)
            def git(*args): return subprocess.check_output(['git','-C',str(consumer),*args],stderr=subprocess.PIPE)
            subprocess.check_output(['git','init','-q',str(consumer)],stderr=subprocess.PIPE)
            git('config','user.name','Runtime artifact fixture'); git('config','user.email','fixture@example.invalid')
            git('config','core.autocrlf','false'); git('add','.'); git('commit','-qm','Three-model retained metadata fixture')
            commit=git('rev-parse','HEAD').decode().strip()
            builder=module('third_model_maintenance_update_package',ROOT/'scripts/package-maintenance.py')
            archive=work/'maintenance.zip'
            builder.package(consumer,archive,commit,'https://github.com/FlightDan/Boiler-installer/releases/download/fixture/maintenance.zip')
            with zipfile.ZipFile(archive) as bundle:
                metadata=json.loads(bundle.read('component.json'))
                target='usr/local/lib/boiler/components/model-catalog/snapshot/brands/example/future/manifest.json'
                row=next(item for item in metadata['files'] if item['target']==target)
                self.assertEqual(bundle.read(row['source']),(consumer/'components/model-catalog/snapshot/brands/example/future/manifest.json').read_bytes())
                self.assertEqual(bundle.read('maintenance-runtime/protocol/trusted-public-key.pem'),(ROOT/'protocol/trusted-public-key.pem').read_bytes())


if __name__=='__main__': unittest.main()
