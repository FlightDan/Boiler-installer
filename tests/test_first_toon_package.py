"""A new model can build a first unsigned package directly from TOON."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
import test_toon_runtime_catalog as fixtures
from test_toon_runtime_catalog import catalog, ROOT

GIT=os.environ.get('BOILER_GIT_EXE','git')

class FirstPackageTests(unittest.TestCase):
    def test_new_model_builds_and_checks_selected_sources_and_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=fixtures.ToonRuntimeCatalogTests().source_fixture(root)
            parser=catalog.decoder(source); path=source/'devices/example/future.toon'
            model=parser.loads(path.read_bytes())
            attribution='brands/example/future/provenance.json'
            original_source=dict(repository='ExampleAuthor/original-driver',commit='b'*40,
                                 files=['driver.py'],license='MIT')
            (source/attribution).write_text(json.dumps(original_source),encoding='utf-8')
            model['components'][0].update(recommendation='optional',sources=[attribution])
            path.write_text(parser.dumps(model),encoding='utf-8')
            guide=json.loads(catalog.views(source)['brands/example/future/manifest.json'])
            self.assertIn(attribution,guide['components'][0]['sources'])
            self.assertEqual(json.loads((source/attribution).read_bytes()),original_source)
            shared='common/audio/realtek/alc245/speaker-route/source.toon'
            audio='common/audio/realtek/alc245/speaker-route/final/speaker_route.py'
            reference=dict(schema_version=1,kind='BoilerModelCommonReferences',model_id='device:example-future',
                source_only=True,resources=[dict(id='resource:oxp3-speaker-route',source=shared)],boot_policies=[dict(id='ssd:gm7-boot-repair',version=1,source='common/ssd/acer/gm7/components.toon',component_ref='ssd-gm7-boot-repair')])
            model['resource_model']['resources']=[dict(id='resource:fixture')]
            model['resource_model']['common_references']=reference
            ref=source/'brands/example/future/common-device-sources.toon'
            ref.write_text(parser.dumps(reference),encoding='utf-8')
            model['source_inventory']='model-source-files.json'
            model['source_revision']=dict(kind='github',repository='ExampleContributor/handheld-fix',commit='a'*40)
            definition='brands/example/future/shared-components.toon'
            license='brands/example/future/LICENSE'
            payload=b'fixture\n'
            component=dict(schema_version=1,kind='ComponentManifest',version='0.1.0',default=True,resource_id='resource:fixture',
                license_files=[license],files=[dict(source='payload/config',target='etc/future.conf',mode=420,
                    size=len(payload),sha256=hashlib.sha256(payload).hexdigest())],hooks=dict(verify='scripts/verify.sh'))
            (source/definition).write_text(parser.dumps(dict(schema_version=1,kind='BoilerComponentDefinitions',
                definitions=dict(fixture=component))),encoding='utf-8')
            (source/license).write_text('Synthetic test license',encoding='utf-8')
            gm7=parser.loads((source/'common/ssd/acer/gm7/components.toon').read_bytes())['definitions']['ssd-gm7-boot-repair']
            model['build']=dict(adapter='toon-package-v1',complete=True,missing=[],materials=[],inputs=[],sources=[
                dict(source='payload/config',path='brands/example/future/config'),
                dict(source='scripts/verify.sh',path='brands/example/future/verify.sh'),
                dict(source=attribution,path=attribution),dict(source=license,path=license)],
                resource_bindings=[dict(id='resource:fixture',definition=definition,key='fixture',component='future:fixture')])
            authored=dict(component,id='future:fixture')
            authored['files']=[{k:v for k,v in row.items() if k not in ('size','sha256')} for row in component['files']]
            model['package']=dict(schema_version=1,kind='DevicePackage',id='device:example-future',version='0.1.0-test.1',
                minimum_engine_version='0.1.3',components=[authored,dict(gm7,id='shared:gm7')])
            path.write_text(parser.dumps(model),encoding='utf-8')
            index_path=source/'model-source-files.json'; index=json.loads(index_path.read_bytes())
            for name,data in [('brands/example/future/config',b'fixture\n'),('brands/example/future/verify.sh',b'#!/bin/sh\nexit 0\n'),
                              (attribution,(source/attribution).read_bytes()),(definition,(source/definition).read_bytes()),
                              (license,(source/license).read_bytes())]:
                dest=source/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
                index['files'].append(dict(path=name,size=len(data),sha256=hashlib.sha256(data).hexdigest()))
            index_path.write_text(json.dumps(index),encoding='utf-8')
            def cli(action,output=None,extra=()):
                cmd=[sys.executable,'-B',str(ROOT/'scripts/toon_catalog.py'),action,'--catalog-root',str(source),'--model','device:example-future']
                if output is not None: cmd+=['--output',str(output)]
                return subprocess.run(cmd+list(extra),capture_output=True,text=True,encoding='utf-8')
            pre=cli('preflight');self.assertEqual(pre.returncode,0,pre.stderr+pre.stdout)
            output=root/'local'; result=cli('canonical-build',output);self.assertEqual(result.returncode,0,result.stderr)
            manifest=json.loads((output/'package/manifest.json').read_bytes())
            self.assertEqual(manifest['source']['kind'],'local')
            self.assertEqual(manifest['trust']['status'],'unsigned-development')
            self.assertEqual(manifest['components'][0]['files'][0]['size'],8)
            with zipfile.ZipFile(output/'package.zip') as archive:
                self.assertEqual(archive.read('scripts/verify.sh'),b'#!/bin/sh\nexit 0\n')
                self.assertNotIn(audio,archive.namelist())
                self.assertIn('common/audio/realtek/alc245/speaker-route/licenses/MIT.txt',archive.namelist())
                self.assertIn('common/audio/realtek/alc245/speaker-route/licenses/AGPL-3.0.txt',archive.namelist())
                self.assertEqual(manifest['components'][1]['ssd_boot_policy']['id'],'ssd:gm7-boot-repair')
            original=path.read_bytes()
            selected_model=json.loads(json.dumps(model))
            selected_model['build']['sources'].append(dict(source=audio,path=audio))
            path.write_text(parser.dumps(selected_model),encoding='utf-8')
            selected_output=root/'selected'; selected=cli('build',selected_output)
            self.assertEqual(selected.returncode,0,selected.stdout+selected.stderr)
            with zipfile.ZipFile(selected_output/'package.zip') as archive:
                self.assertEqual(archive.read(audio),(source/audio).read_bytes())
            private_model=json.loads(json.dumps(model))
            private_model['resource_model']['resources']=[]
            del private_model['build']['resource_bindings']
            path.write_text(parser.dumps(private_model),encoding='utf-8')
            private=cli('preflight');self.assertEqual(private.returncode,0,private.stdout+private.stderr)
            model['package']['components'][0]['files'][0]['target']='etc/wrong.conf'
            path.write_text(parser.dumps(model),encoding='utf-8')
            bad=cli('preflight');self.assertNotEqual(bad.returncode,0)
            self.assertIn('differs from complete locked definition',bad.stdout+bad.stderr)
            model['package']['components'][0]['files'][0]['target']='etc/future.conf'
            model['resource_model']['resources'].append(dict(id='resource:unimplemented'))
            path.write_text(parser.dumps(model),encoding='utf-8')
            bad=cli('preflight');self.assertNotEqual(bad.returncode,0)
            self.assertIn('Requested model resources are not all provided',bad.stdout+bad.stderr)
            path.write_bytes(original)
            def git(*args): return subprocess.check_output([GIT,'-C',str(source),*args],stderr=subprocess.STDOUT)
            git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
            git('remote','add','origin','https://github.com/FlightDan/Boiler-adaptations.git')
            git('add','devices/example/future.toon','model-source-files.json','brands/example/future/config','brands/example/future/verify.sh',attribution,definition,license,'common/audio/realtek/alc245/speaker-route','common/ssd/acer/gm7/components.toon')
            git('commit','-qm','Synthetic source');commit=git('rev-parse','HEAD').decode().strip()
            signed_candidate=root/'git-bound'; result=cli('build',signed_candidate,['--source-commit',commit])
            self.assertEqual(result.returncode,0,result.stderr)
            manifest=json.loads((signed_candidate/'package/manifest.json').read_bytes())
            self.assertEqual(manifest['source'],dict(kind='github',repository='FlightDan/Boiler-adaptations',commit=commit))
            self.assertEqual(manifest['trust'],dict(status='index-signature-required',official_release=False))
            path.write_text(path.read_text(encoding='utf-8')+'\n',encoding='utf-8')
            bad=cli('build',root/'changed',['--source-commit',commit]);self.assertNotEqual(bad.returncode,0)
            self.assertIn('differs from exact Git commit',bad.stderr)
            (source/'brands/example/future/config').write_bytes(b'tampered')
            bad=cli('preflight');self.assertNotEqual(bad.returncode,0)
            self.assertIn('differs from canonical inventory',bad.stdout+bad.stderr)

if __name__=='__main__':unittest.main()
