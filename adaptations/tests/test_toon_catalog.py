import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys

ADAPTATIONS=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('toon_catalog_tests',ADAPTATIONS/'scripts/toon_catalog.py')
catalog=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(catalog)


class ToonCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records=catalog.models(ADAPTATIONS)
        cls.model=cls.records[0][1]
        cls.parser=catalog.decoder(ADAPTATIONS)

    def test_official_parser_version_and_real_toon_shapes(self):
        self.assertEqual(self.parser.__version__,'1.2.0')
        self.assertEqual(self.parser.__toon_spec__,'4.4')
        text='items[2]{id,name}:\n  1,"a,b"\n  2,"c\\nd"'
        value=self.parser.loads(text,strict=True)
        self.assertEqual(value,{'items':[{'id':1,'name':'a,b'},{'id':2,'name':'c\nd'}]})
        self.assertEqual(self.parser.loads(self.parser.dumps(value)),value)

    def test_official_strict_errors_are_not_silently_accepted(self):
        for text in ('x: 1\nx: 2','x[2]: a','x[1]{a,a}:\n  1,2','x:\n   bad: true','x: "bad\\q"'):
            with self.subTest(text=text), self.assertRaises(ValueError): self.parser.loads(text,strict=True)

    def test_component_data_and_source_paths_survive_toon_roundtrip(self):
        baseline=json.loads((ADAPTATIONS/'tests/fixtures/legacy-model-manifest-hashes.json').read_bytes())
        pins={row['path']:row for row in baseline}
        updates=json.loads((ADAPTATIONS/'tests/fixtures/reviewed-guide-field-updates.json').read_bytes())
        for name,model in self.records:
            legacy={key:value for key,value in model.items() if key not in ('build','package','runtime')}
            relative='adaptations/'+Path(name).with_name('manifest.json').as_posix()
            if model['id']=='guide:x2-mini' and model['build']['adapter']=='x2-complete-v1':
                self.assertEqual(self.parser.loads(self.parser.dumps(legacy)),legacy)
                historical=(ADAPTATIONS/'tests/fixtures/legacy-x2-guide.json').read_bytes()
                self.assertEqual(hashlib.sha256(historical).hexdigest(),pins[relative]['sha256'])
                self.assertEqual(len(historical),pins[relative]['size'])
                continue  # Current guide intentionally covers the full OXP3 model.
            comparison=copy.deepcopy(legacy)
            for update in updates.get(relative,[]):
                component=next(row for row in comparison['components'] if row['id']==update['component'])
                self.assertEqual(component[update['field']],update['after'])
                component[update['field']]=update['before']
            # Reviewed guide status/note edits do not rewrite the historical
            # baseline. Every unrelated component/source byte stays identical.
            generated=catalog.json_bytes(comparison)
            pin=pins[relative]
            self.assertEqual(hashlib.sha256(generated).hexdigest(),pin['sha256'])
            self.assertEqual(len(generated),pin['size'])

    def test_generated_views_are_deterministic_and_detect_stale_files(self):
        self.assertEqual(catalog.views(ADAPTATIONS),catalog.views(ADAPTATIONS))
        outputs=catalog.views(ADAPTATIONS)
        self.assertEqual(self.parser.loads(outputs['models.toon']),json.loads(outputs['model-index.json']))
        catalog.generate(ADAPTATIONS,check=True)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); shutil.copytree(ADAPTATIONS/'brands',root/'brands')
            shutil.copytree(ADAPTATIONS/'vendor',root/'vendor')
            shutil.copyfile(ADAPTATIONS/'toon-parser-lock.json',root/'toon-parser-lock.json')
            # Keep the authenticated parser module rooted at the real vendor;
            # this test varies only the generated-output destination.
            with patch.object(catalog,'decoder',return_value=self.parser):
                catalog.generate(root)
                target=root/'model-index.json'; target.write_bytes(target.read_bytes()+b' ')
                with self.assertRaisesRegex(ValueError,'stale'): catalog.generate(root,check=True)

    def test_new_model_file_automatically_enters_toon_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); shutil.copytree(ADAPTATIONS/'brands',root/'brands')
            model=copy.deepcopy(self.model); model.update(id='guide:future',guide='brands/example/future/model.md')
            model['runtime'].update(id='device:future',name={'en':'Future','zh':'未来机型'})
            path=root/'brands/example/future/future.toon'; path.parent.mkdir(parents=True)
            path.write_text(self.parser.dumps(model),encoding='utf-8')
            with patch.object(catalog,'decoder',return_value=self.parser):
                outputs=catalog.views(root)
            index=self.parser.loads(outputs['models.toon'])
            self.assertEqual(len(index['models']),3)
            self.assertIn('guide:future',[row['id'] for row in index['models']])

    def test_dependency_cycle_unknown_component_and_defaults_fail(self):
        for change,message in (
            (lambda m: m['components'][0].update(dependencies=['missing']),'dependency'),
            (lambda m: m['components'][0].update(dependencies=[m['components'][0]['id']]),'Cyclic'),
            (lambda m: m['components'][0].update(default=True),'defaults'),
            (lambda m: m['components'].append(copy.deepcopy(m['components'][0])),'Duplicate'),
            (lambda m: m['components'][0].update(sources=['../private.py']),'relative'),
            (lambda m: m['build'].update(adapter='run-any-command'),'adapter'),
        ):
            model=copy.deepcopy(self.model); change(model)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError,message):
                catalog.validate_model(model,Path(self.records[0][0]))

    def test_complete_x2_requires_its_fixed_builder_and_explicit_inputs(self):
        name,model=next(row for row in self.records if row[1]['id']=='guide:x2-mini')
        self.assertTrue(model['build']['complete'])
        changed=copy.deepcopy(model); changed['build']['adapter']='oxp3-upstream-v1'
        with self.assertRaisesRegex(ValueError,'X2'): catalog.validate_model(changed,Path(name))
        with tempfile.TemporaryDirectory() as temporary, patch.object(catalog,'check_sources',return_value=True):
            _,errors=catalog.preflight(Path(temporary),model)
            self.assertEqual(len([error for error in errors if error.startswith('Explicit --input')]),3)

    def test_locked_source_bytes_not_only_lock_presence_are_verified(self):
        model=copy.deepcopy(self.model)
        model['package']['definitions']=[]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); adaptation=root/'adaptations'; adaptation.mkdir()
            (adaptation/'source.py').write_bytes(b'pass\n')
            lock=dict(source=model['source_revision'],files=[dict(path='source.py',size=5,
                sha256=hashlib.sha256(b'pass\n').hexdigest())])
            path=adaptation/'source-lock.json'; path.write_bytes(catalog.json_bytes(lock))
            model['build']['materials']=[dict(scope='common',lock='adaptations/source-lock.json',sha256=catalog.sha(path))]
            for component in model['components']: component['sources']=[]
            model['components'][0]['sources']=['source.py']
            self.assertTrue(catalog.check_sources(root,model))
            (adaptation/'source.py').write_bytes(b'evil\n')
            with self.assertRaisesRegex(ValueError,'Source differs'): catalog.check_sources(root,model)
            (adaptation/'source.py').write_bytes(b'pass\n'); path.write_bytes(path.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError,'changed source lock'): catalog.check_sources(root,model)

    def test_missing_inputs_prevent_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'output'
            with self.assertRaises(ValueError): catalog.build(Path(temporary),self.model,output)
            self.assertFalse(output.exists())

    def test_dispatch_uses_fixed_script_argv_and_propagates_real_process_failure(self):
        with tempfile.TemporaryDirectory(prefix='toon dispatch ') as temporary:
            root=Path(temporary); script=root/'adaptations/scripts/build-upstream-repairs.py'
            script.parent.mkdir(parents=True)
            script.write_text('import sys\nassert "--base" in sys.argv\nraise SystemExit(23)\n')
            resolved={key:root/(key+' ; no shell') for key in ('base','decky','xevma')}
            model=copy.deepcopy(self.model); model['build']['adapter']='oxp3-upstream-v1'
            with patch.object(catalog,'preflight',return_value=(resolved,[])):
                observed=[]
                catalog.build(root,model,root/'output',executor=lambda argv,cwd: observed.append((argv,cwd)))
                self.assertEqual(observed[0][0][3],str(script))
                self.assertIn(str(resolved['base']),observed[0][0])
                with self.assertRaises(subprocess.CalledProcessError) as context:
                    catalog.build(root,model,root/'output')
                self.assertEqual(context.exception.returncode,23)

    def test_unsafe_and_reserved_paths_fail(self):
        for value in ('../x','/x','C:/x','a\\b','a//b','a/CON.txt','a.','a\n','.local/secret.json','a/.git/config'):
            with self.subTest(value=value),self.assertRaises(ValueError): catalog.relative(value)

    def test_guest_desktop_local_target_does_not_allow_private_project_sources(self):
        self.assertEqual(catalog.guest_target('home/deck/.local/share/applications/example.desktop'),
                         'home/deck/.local/share/applications/example.desktop')
        with self.assertRaisesRegex(ValueError,'Private/internal'): catalog.relative('.local/private.py')
        with self.assertRaises(ValueError): catalog.guest_target('.local/private.py')

    def test_current_oxp_pipeline_requires_explicit_inputs_and_rejects_prebuilt_four(self):
        self.assertEqual(self.model['build']['adapter'],'oxp3-current-v1')
        with tempfile.TemporaryDirectory() as temporary,patch.object(catalog,'check_sources',return_value=True):
            _,errors=catalog.preflight(Path(temporary),self.model)
            self.assertEqual(len([e for e in errors if e.startswith('Explicit --input')]),9)
        wrong=copy.deepcopy(self.model)
        next(row for row in wrong['build']['inputs'] if row['name']=='base')['sha256']=catalog.OXP_CURRENT_ANCHOR
        with self.assertRaisesRegex(ValueError,'never a prebuilt .3 or .4'): catalog.validate_model(wrong,Path(self.records[0][0]))

    def test_raw_decky_directory_is_not_treated_as_a_prepared_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); lock=root/'lock.json'; lock.write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError,'Prepared Decky manifest missing'):
                catalog.check_directory_input(dict(name='decky'),root,lock,root)

    def test_current_oxp_pipeline_checks_intermediate_before_fixed_final_stage(self):
        sys.path.insert(0,str(ADAPTATIONS.parent))
        from protocol import manifest as protocol_manifest
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); output=root/'result'
            paths={row['name']:root/row['name'] for row in self.model['build']['inputs']}
            definition=self.model['package']
            final=dict(id='device:oxp3',version='0.1.0-localtest.20261030.8',
                components=[dict(id=name) for name in definition['inherited_components']+[row['id'] for row in definition['definitions']]],
                legacy_capabilities=[dict(id=name) for name in definition['inherited_legacy_capabilities']])
            calls=[]
            def execute(argv,**kwargs):
                calls.append(argv); stage=Path(argv[argv.index('--output')+1]); stage.mkdir(parents=True)
                (stage/'package').mkdir()
                if stage.name=='final':
                    archive=stage/'candidate.zip'; archive.write_bytes(b'synthetic-stage-test')
                    report=dict(directory=str(stage/'package'),path=str(archive),size=archive.stat().st_size,
                        sha256=catalog.sha(archive),manifest_sha256='0'*64)
                    (stage/'verification.json').write_bytes(catalog.json_bytes(report))
                return subprocess.CompletedProcess(argv,0)
            shared=dict(id='device:oxp3',version=catalog.OXP_SHARED_VERSION)
            intermediate=dict(id='device:oxp3',version=catalog.OXP_CURRENT_VERSION)
            with patch.object(catalog,'preflight',return_value=(paths,[])),patch.object(catalog.subprocess,'run',side_effect=execute), \
                    patch.object(protocol_manifest,'validate_package',side_effect=[shared,intermediate,final]) as validate:
                result=catalog.build(root,self.model,output,input_paths=paths)
            self.assertEqual(result['status'],'built'); self.assertEqual(len(calls),3)
            self.assertTrue(calls[0][3].replace('\\','/').endswith('adaptations/scripts/build-shared-base.py'))
            self.assertEqual(calls[0].count('--optiscaler-archive'),3)
            self.assertEqual(calls[1][calls[1].index('--base')+1],str(output/'shared/package'))
            self.assertEqual(calls[1][calls[1].index('--expected-plugin-sha256')+1],self.model['build']['plugin_pins']['sha256'])
            self.assertEqual(calls[2][calls[2].index('--base')+1],str(output/'opti/package'))
            self.assertEqual(validate.call_args_list[0].kwargs['expected_manifest_sha256'],catalog.OXP_SHARED_ANCHOR)
            self.assertEqual(validate.call_args_list[1].kwargs['expected_manifest_sha256'],catalog.OXP_CURRENT_ANCHOR)
            calls.clear()
            with patch.object(catalog,'preflight',return_value=(paths,[])),patch.object(catalog.subprocess,'run',side_effect=execute), \
                    patch.object(protocol_manifest,'validate_package',side_effect=ValueError('bad intermediate')):
                with self.assertRaisesRegex(ValueError,'bad intermediate'): catalog.build(root,self.model,root/'bad',input_paths=paths)
            self.assertEqual(len(calls),1)
            self.assertFalse((root/'bad/final').exists())
            self.assertFalse((root/'bad/model-build-result.json').exists())
            calls.clear()
            with patch.object(catalog,'preflight',return_value=(paths,[])),patch.object(catalog.subprocess,'run',side_effect=execute), \
                    patch.object(protocol_manifest,'validate_package',side_effect=[shared,ValueError('bad current Opti')]):
                with self.assertRaisesRegex(ValueError,'bad current Opti'): catalog.build(root,self.model,root/'bad-opti',input_paths=paths)
            self.assertEqual(len(calls),2)
            self.assertFalse((root/'bad-opti/final').exists())
            self.assertFalse((root/'bad-opti/model-build-result.json').exists())

    def test_historical_public_artifacts_are_checked_before_build_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            paths={row['name']:root/row['name'] for row in self.model['build']['inputs']}
            for name in ('historical_plugin_zip','optiscaler_094','optiscaler_079','optiscaler_nightly'):
                paths[name].write_bytes(b'wrong public artifact')
            _,errors=catalog.preflight(ADAPTATIONS.parent,self.model,input_paths=paths)
            self.assertTrue(any('Historical public artifact size/SHA-256 differs:' in e for e in errors))
            with self.assertRaisesRegex(ValueError,'Historical public artifact size/SHA-256 differs:'):
                catalog.build(ADAPTATIONS.parent,self.model,root/'output',input_paths=paths)
            self.assertFalse((root/'output').exists())


if __name__=='__main__': unittest.main()
