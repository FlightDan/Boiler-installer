import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'components'))
sys.path.insert(0, str(ROOT / 'guest'))
from integration import stage, validate_options, check_conflicts, verify_device_components, device_plan, device_legacy_selection, device_hook_environment, authenticate_oxp3_efi, authenticate_x2_efi, authenticate_oxp3_full_efi
from source_profile import inspect_source


class GenericIntegrationChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name) / 'root'; self.root.mkdir()
        (self.root / 'etc').mkdir(); (self.root / 'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.8.14\nBUILD_ID=20260707.10\n')
        (self.root / 'usr/lib/modules/existing-kernel').mkdir(parents=True)

    def tearDown(self): self.tmp.cleanup()

    def inventory(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_capacity_fixture_import_does_not_probe_or_modify_devices(self):
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('Import must not probe filesystems')):
            fixture = runpy.run_path(str(ROOT / 'guest/test_prepared_capacity.py'))
        self.assertTrue(callable(fixture['main']))

    def test_no_upgrade_accepts_existing_actual_source(self):
        profile = inspect_source(self.root)
        self.assertEqual('3.8.14', profile['version']); self.assertEqual(['existing-kernel'], profile['kernels'])

    def test_source_metadata_has_bounded_unique_fields(self):
        metadata = self.root / 'etc/os-release'
        metadata.write_text('X=' + 'x'*65536)
        with self.assertRaisesRegex(ValueError, 'limit'): inspect_source(self.root)
        metadata.write_text('ID=steamos\nID=holo\nVERSION_ID=3.8.14\nBUILD_ID=test\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate'): inspect_source(self.root)

    def test_absolute_source_metadata_link_stays_inside_image(self):
        metadata = self.root / 'etc/os-release'
        target = self.root / 'usr/lib/os-release'; target.write_bytes(metadata.read_bytes()); metadata.unlink()
        try: metadata.symlink_to('/usr/lib/os-release')
        except OSError: self.skipTest('Native symlink creation is unavailable')
        self.assertEqual('3.8.14', inspect_source(self.root)['version'])
        result = stage(self.root, '/missing', dict(schema=1, repairs=[], plugins=[], install_mode='vanilla'), 'A')
        self.assertEqual('3.8.14', result['source']['VERSION_ID'])
        metadata.unlink(); metadata.symlink_to('../../outside')
        with self.assertRaisesRegex(ValueError, 'escapes'): inspect_source(self.root)

    def test_kernel_metadata_ancestor_links_use_the_image_namespace(self):
        import shutil
        library = self.root / 'usr/lib'
        shutil.rmtree(library)
        guest_library = self.root / 'guest-library'
        (guest_library / 'modules/guest-kernel').mkdir(parents=True)
        try: library.symlink_to('/guest-library', target_is_directory=True)
        except OSError: self.skipTest('Native symlink creation is unavailable')
        self.assertEqual(inspect_source(self.root)['kernels'], ['guest-kernel'])
        library.unlink(); library.symlink_to('../../outside', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'escapes'): inspect_source(self.root)

    def test_vanilla_empty_performs_no_device_or_updater_writes(self):
        before = self.inventory()
        result = stage(self.root, Path(self.tmp.name) / 'missing-payload', dict(schema=1, repairs=[], plugins=[], install_mode='vanilla'), 'A')
        self.assertEqual('no-components-selected', result['status']); self.assertEqual(before, self.inventory())
        self.assertFalse((self.root / 'var').exists())
        sys.path.insert(0, str(ROOT))
        from protocol import validate_manifest
        self.assertIs(result['install_plan'], validate_manifest(result['install_plan'], 'InstallPlan'))
        self.assertEqual('3.8.14', result['install_plan']['source']['version'])

    def test_imported_does_not_reinject_even_if_stale_choices_exist(self):
        before = self.inventory()
        result = stage(self.root, '/missing', dict(schema=1, repairs=['core:decky'], plugins=[], install_mode='imported'), 'B')
        self.assertEqual('preserved-imported-image', result['status']); self.assertEqual(before, self.inventory())

    def test_vanilla_explicit_selection_is_not_ignored(self):
        with self.assertRaises(FileNotFoundError):
            stage(self.root, '/missing', dict(schema=1, repairs=['core:maintenance'], plugins=[], install_mode='vanilla'), 'A')

    def test_untrusted_device_package_fails_before_any_write(self):
        before = self.inventory()
        with self.assertRaisesRegex(ValueError, 'trust'):
            stage(self.root, '/missing', dict(schema=1, repairs=[], plugins=[], device_package='oxp3'), 'A')
        self.assertEqual(before, self.inventory())

    def test_no_generic_legacy_device_policy(self):
        for component in ('system-update-hold', 'input', 'fan-driver'):
            with self.assertRaisesRegex(ValueError, 'explicitly trusted'):
                stage(self.root, '/missing', dict(schema=1, repairs=[component], plugins=[]), 'A')

    def test_options_fail_closed(self):
        validate_options(dict(schema=1, repairs=[], plugins=[], wireless=None, device_components=None, device_package=None))
        with self.assertRaises(ValueError): validate_options(dict(schema=1, repairs=['core:decky'] * 2))
        with self.assertRaises(ValueError): validate_options(dict(schema=1, plugins=['unknown']))
        with self.assertRaises(ValueError): validate_options(dict(schema=1, repairs=[], install_mode='unknown'))

    def legacy_fixture(self):
        component = dict(id='oxp3:legacy-adaptation')
        package = dict(id='device:oxp3', selection_contract=dict(kind='oxp3-legacy-options-v1', component=component['id']),
                       legacy_kde_languages=['system'], legacy_capabilities=[
                           dict(id='oxp3:input', category='repairs', available=True, dependencies=[]),
                           dict(id='oxp3:gyro', category='repairs', available=True, dependencies=['oxp3:input']),
                           dict(id='oxp3:decky', category='plugins', available=True, dependencies=[]),
                           dict(id='oxp3:refind', category='repairs', available=False, dependencies=[]),
                           dict(id='oxp3:gm7-resume', category='repairs', available=False, dependencies=[])])
        options = dict(schema=1, repairs=[], plugins=[], device_package='oxp3', device_manifest_sha256='a'*64,
                       device_trust='developer-session', installer_language='en-US', kde_language='system',
                       device_legacy_options=dict(schema_version=1, repairs=['oxp3:input','oxp3:gyro'], plugins=[],
                                                  acknowledge_legacy_update_hold=True))
        return package, component, options

    def test_dynamic_legacy_selection_only_uses_authenticated_capability_contract(self):
        package, component, options = self.legacy_fixture()
        validate_options(options)
        choice = device_legacy_selection(package, options, [component])
        self.assertEqual(['oxp3:input','oxp3:gyro'], choice['repairs'])
        self.assertEqual('guest', choice['slot'])
        descriptor = dict(legacy_selection=choice)
        self.assertEqual(choice, json.loads(device_hook_environment(descriptor, component)['BOILER_OXP3_SELECTION']))
        self.assertIsNone(device_hook_environment(descriptor, dict(id='other:component')))
        for repairs in (['oxp3:gyro'], ['oxp3:unknown'], ['oxp3:refind'], ['oxp3:gm7-resume'], ['oxp3:decky'], []):
            altered = dict(options, device_legacy_options=dict(options['device_legacy_options'], repairs=repairs))
            with self.subTest(repairs=repairs), self.assertRaises(ValueError):
                device_legacy_selection(package, altered, [component])
        with self.assertRaises(ValueError): device_legacy_selection(dict(package, selection_contract=None), options, [component])
        with self.assertRaises(ValueError): device_legacy_selection(package, options, [])
        self.assertIsNone(device_legacy_selection({}, {}, []))

    def test_oxp3_efi_authenticator_does_not_accept_x2_identity(self):
        package, component, options = self.legacy_fixture()
        package['legacy_capabilities'].append(dict(id='oxp3:split-lock', category='repairs', available=True, dependencies=[]))
        options['device_legacy_options']['repairs'] = ['oxp3:split-lock']
        choice = device_legacy_selection(package, options, [component])
        descriptor = dict(id='device:x2-mini', legacy_selection=choice,
                          oxp3_efi_contract=dict(kind='oxp3-split-lock-efi-v1', component=component['id']))
        with mock.patch('integration.device_plan', return_value=(Path('/package'), [component], {}, descriptor)), \
             mock.patch('integration.release', return_value={}), \
             mock.patch('protocol.validate_package') as validate, \
             mock.patch('oxp3_efi_provider.verify') as verify:
            with self.assertRaisesRegex(ValueError, 'reviewed fixed OXP3'):
                authenticate_oxp3_efi(self.root, '/payload', options)
            validate.assert_not_called()
            verify.assert_not_called()

    def test_x2_authenticator_binds_reviewed_x2_package_and_choice(self):
        package, component, options = self.legacy_fixture()
        package['id'] = 'device:x2-mini'
        package['legacy_capabilities'].append(dict(id='oxp3:split-lock', category='repairs', available=True, dependencies=[]))
        options['device_legacy_options']['repairs'] = ['oxp3:split-lock']
        choice = device_legacy_selection(package, options, [component])
        descriptor = dict(id='device:x2-mini', legacy_selection=choice,
                          x2_efi_contract=dict(kind='x2-split-lock-efi-v1', component=component['id']))
        with mock.patch('integration.device_plan', return_value=(Path('/package'), [component], {}, descriptor)), \
             mock.patch('integration.release', return_value={}), \
             mock.patch('protocol.validate_package', return_value=package), \
             mock.patch('x2_efi_provider.verify') as verify:
            result = authenticate_x2_efi(self.root, '/payload', options)
            self.assertEqual(result['source']['package_id'], 'device:x2-mini')
            self.assertEqual(result['selection'], choice)
            verify.assert_called_once()
            descriptor['id'] = 'device:oxp3'
            with self.assertRaisesRegex(ValueError, 'reviewed fixed X2'):
                authenticate_x2_efi(self.root, '/payload', options)

    def test_x2_fixed_efi_package_requires_new_engine(self):
        from protocol import manifest
        package = {'minimum_engine_version': '0.1.4', 'compatibility': {}}
        with mock.patch.object(manifest, 'ENGINE_VERSION', '0.1.3'):
            self.assertFalse(manifest.compatible(package, {}))
        with mock.patch.object(manifest, 'ENGINE_VERSION', '0.1.4'):
            self.assertTrue(manifest.compatible(package, {}))

    def test_full_oxp3_authenticator_requires_its_own_contract(self):
        package, component, options=self.legacy_fixture()
        package['legacy_capabilities'].append(dict(id='oxp3:split-lock',category='repairs',available=True,dependencies=[]))
        options['device_legacy_options']['repairs']=['oxp3:split-lock']
        choice=device_legacy_selection(package,options,[component])
        descriptor=dict(id='device:oxp3',legacy_selection=choice,
                        oxp3_full_efi_contract=dict(kind='oxp3-full-split-lock-efi-v1',component=component['id']))
        with mock.patch('integration.device_plan',return_value=(Path('/package'),[component],{},descriptor)), \
             mock.patch('integration.release',return_value={}), \
             mock.patch('protocol.validate_package',return_value=package), \
             mock.patch('oxp3_full_efi_provider.verify') as verify:
            result=authenticate_oxp3_full_efi(self.root,'/payload',options)
            self.assertEqual(result['source']['package_id'],'device:oxp3');verify.assert_called_once()
            with self.assertRaisesRegex(ValueError,'reviewed fixed OXP3'):
                authenticate_oxp3_efi(self.root,'/payload',options)
            descriptor['id']='device:x2-mini'
            with self.assertRaisesRegex(ValueError,'reviewed full OXP3'):
                authenticate_oxp3_full_efi(self.root,'/payload',options)

    def test_dispatcher_uses_full_oxp3_authenticator_only_for_full_contract(self):
        from integration import authenticate_model_efi
        descriptor={'id':'device:oxp3','oxp3_full_efi_contract':{'kind':'oxp3-full-split-lock-efi-v1','component':'oxp3:legacy-adaptation'}}
        with mock.patch('integration.device_plan',return_value=(Path('/package'),[],{},descriptor)), \
             mock.patch('integration.release',return_value={}), \
             mock.patch('integration.authenticate_oxp3_full_efi',return_value={'full':True}) as full, \
             mock.patch('integration.authenticate_oxp3_efi',return_value={'old':True}) as old:
            self.assertEqual(authenticate_model_efi(self.root,'/payload',{}),{'full':True})
            full.assert_called_once();old.assert_not_called()
            descriptor.pop('oxp3_full_efi_contract')
            self.assertEqual(authenticate_model_efi(self.root,'/payload',{}),{'old':True})
            old.assert_called_once()

    def test_stage_records_authenticated_full_oxp3_contract_kind(self):
        _,component,options=self.legacy_fixture()
        options['device_components']=[component['id']]
        options['device_legacy_options']['repairs']=['oxp3:split-lock']
        descriptor=dict(id='device:oxp3',device_warnings=[],
            oxp3_full_efi_contract=dict(kind='oxp3-full-split-lock-efi-v1',component=component['id']))
        plan={'components':[]}
        class StopAfterPlan(Exception): pass
        with mock.patch('integration.device_plan',return_value=(Path('/verified'),[component],{},descriptor)), \
             mock.patch('integration.install_plan',return_value=plan), \
             mock.patch('integration.authenticate_model_efi',return_value={'source':{'package_id':'device:oxp3'}}), \
             mock.patch('integration.load_manifest',side_effect=StopAfterPlan):
            with self.assertRaises(StopAfterPlan):
                stage(self.root,Path('/payload'),options,'A',model_review_path='/run/boiler-oxp3-efi-A.json')
        self.assertEqual(plan['model_efi'],{'kind':'oxp3-full-split-lock-efi-v1'})

    def test_installer_rejects_explicitly_unavailable_component(self):
        from protocol import resolve_plan
        part=dict(schema_version=1,kind='ComponentManifest',id='x2-mini:fixture',version='1.0.0',files=[])
        self.assertEqual(resolve_plan([part],[part['id']],{})['components'],[part])
        part['available']=True
        self.assertEqual(resolve_plan([part],[part['id']],{})['components'],[part])
        part['available']=False
        with self.assertRaisesRegex(ValueError,'Incompatible component'):
            resolve_plan([part],[part['id']],{})

    def test_dynamic_legacy_selection_requires_explicit_policy_and_package(self):
        _, _, options = self.legacy_fixture()
        for patch in ({'schema_version':True}, {'acknowledge_legacy_update_hold':False}, {'repairs':['oxp3:input']*2}, {'extra':'command'}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_options(dict(options, device_legacy_options=dict(options['device_legacy_options'], **patch)))
        with self.assertRaises(ValueError): validate_options(dict(options, device_package=None))

    def test_dynamic_legacy_readonly_verification_receives_same_reviewed_choices(self):
        package, component, options = self.legacy_fixture()
        component['hooks'] = dict(verify='scripts/verify.sh')
        choice = device_legacy_selection(package, options, [component])
        folder = Path(self.tmp.name)/'package'; (folder/'scripts').mkdir(parents=True)
        (folder/'scripts/verify.sh').write_text('exit 0\n')
        engine = mock.Mock(); engine.verify_readonly.return_value = dict(ok=True)
        with mock.patch('integration.device_plan', return_value=(folder,[component],{},dict(legacy_selection=choice))), \
             mock.patch('integration.maintenance_api', return_value=mock.Mock(return_value=engine)), mock.patch('subprocess.run') as execute:
            verify_device_components(self.root, '/payload', options, 'B', {component['id']:{}})
        self.assertEqual(choice, json.loads(execute.call_args.kwargs['env']['BOILER_OXP3_SELECTION']))

    def test_dynamic_legacy_apply_receives_reviewed_choices_in_both_slots(self):
        package, component, options = self.legacy_fixture()
        options['device_components'] = [component['id']]
        choice = device_legacy_selection(package, options, [component])
        descriptor = dict(legacy_selection=choice, device_warnings=[])
        class ReachedHook(Exception): pass
        for slot in ('A', 'B'):
            engine = mock.Mock(); engine.apply.side_effect = ReachedHook
            before = self.inventory()
            with mock.patch('integration.device_plan', return_value=(Path('/verified'), [component], {}, descriptor)), \
                 mock.patch('integration.load_manifest', return_value={}), \
                 mock.patch('integration.merged_firmware_plan', return_value=({}, {})), \
                 mock.patch('integration.maintenance_api', return_value=mock.Mock(return_value=engine)):
                with self.assertRaises(ReachedHook): stage(self.root, Path('/payload'), options, slot)
            self.assertEqual(choice, json.loads(engine.apply.call_args.kwargs['hook_environment']['BOILER_OXP3_SELECTION']))
            self.assertTrue(engine.apply.call_args.kwargs['allow_hooks'])
            self.assertEqual(before, self.inventory())

    def test_x2_stage_dispatches_to_its_own_fixed_efi_authenticator(self):
        package, component, options = self.legacy_fixture()
        package['id'] = 'device:x2-mini'
        package['legacy_capabilities'].append(dict(id='oxp3:split-lock', category='repairs', available=True, dependencies=[]))
        options['device_legacy_options']['repairs'] = ['oxp3:split-lock']
        options['device_components'] = [component['id']]
        choice = device_legacy_selection(package, options, [component])
        descriptor = dict(id='device:x2-mini', legacy_selection=choice, device_warnings=[],
                          x2_efi_contract=dict(kind='x2-split-lock-efi-v1',component=component['id']))
        reviewed = dict(source=dict(package_id='device:x2-mini'), selection=choice)
        class ReachedHook(Exception): pass
        engine = mock.Mock(); engine.apply.side_effect = ReachedHook
        before = self.inventory()
        with mock.patch('integration.device_plan', return_value=(Path('/verified'), [component], {}, descriptor)), \
             mock.patch('integration.authenticate_x2_efi', return_value=reviewed) as x2, \
             mock.patch('integration.authenticate_oxp3_efi', side_effect=AssertionError('wrong identity')) as oxp3, \
             mock.patch('integration.load_manifest', return_value={}), \
             mock.patch('integration.merged_firmware_plan', return_value=({}, {})), \
             mock.patch('integration.maintenance_api', return_value=mock.Mock(return_value=engine)):
            with self.assertRaises(ReachedHook):
                stage(self.root, Path('/payload'), options, 'A', model_review_path='/run/boiler-oxp3-efi-A.json')
        x2.assert_called_once(); oxp3.assert_not_called()
        self.assertEqual(before, self.inventory())

    def test_cross_model_generic_conflicts_are_rejected_before_apply(self):
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            check_conflicts([dict(id='oxp3:decky', conflicts=['core:decky'])], ['oxp3:decky','core:decky'])
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            check_conflicts([dict(id='core:decky', conflicts=['oxp3:decky'])], ['oxp3:decky','core:decky'])
        check_conflicts([dict(id='oxp3:decky', conflicts=['core:decky'])], ['oxp3:decky'])

    def test_preparation_reports_missing_inputs_unavailable(self):
        prepare = runpy.run_path(str(ROOT / 'scripts/prepare-integration.py'))['prepare']
        output = Path(self.tmp.name) / 'prepared'
        manifest = prepare(output)
        self.assertTrue(manifest['components']['core:maintenance']['available'])
        self.assertFalse(manifest['components']['core:decky']['available'])
        self.assertTrue(manifest['components']['wifi:intel']['available'])
        self.assertFalse(manifest['components']['wifi:intel']['payload_cached'])
        with self.assertRaises(FileExistsError): prepare(output)
        # Model guide JSON belongs to the generic runtime; executable device
        # payloads still require the explicit local-test preparation switch.
        self.assertFalse((output/'maintenance-runtime/cache/model-bundles').exists())
        self.assertTrue((output/'maintenance-runtime/components/model-catalog/lock.json').is_file())

    def test_prepared_maintenance_runtime_loads_full_oxp3_provider_and_lock(self):
        import hashlib
        import subprocess
        prepare = runpy.run_path(str(ROOT/'scripts/prepare-integration.py'))['prepare']
        output=Path(self.tmp.name)/'prepared-full-efi'
        prepare(output)
        runtime=output/'maintenance-runtime'
        provider=runtime/'components/oxp3_full_efi_provider.py'
        lock=runtime/'components/oxp3_full_efi_provider.lock.json'
        self.assertTrue(provider.is_file());self.assertTrue(lock.is_file())
        self.assertEqual(hashlib.sha256(lock.read_bytes()).digest(),
                         hashlib.sha256((ROOT/'components/oxp3_full_efi_provider.lock.json').read_bytes()).digest())
        code=("import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
              "from components.oxp3_full_efi_provider import verify; "
              "p=Path(sys.argv[1]); "
              "exec(\"try:\\n verify(p/'missing-package',p/'components/oxp3_full_efi_provider.lock.json')\\n"
              "except ValueError as e:\\n assert 'manifest' in str(e)\\nelse:\\n raise AssertionError('unverified package accepted')\")")
        subprocess.run([sys.executable,'-I','-B','-c',code,str(runtime)],check=True)

    def test_maintenance_package_role_is_refused_before_install_plan(self):
        package = dict(id='device:oxp3', device_id='oxp3', purpose='maintenance',
                       version='1.0.6', components=[], compatibility={})
        options = dict(device_package='trusted-cache-name', device_manifest_sha256='a'*64,
                       device_trust='developer-session', device_components=[])
        source = dict(ID='steamos', VERSION_ID='3.10', KERNELS=['existing-kernel'])
        with mock.patch('protocol.validate_package', return_value=package) as validate, \
             mock.patch('protocol.resolve_plan') as resolve:
            with self.assertRaisesRegex(ValueError, 'Maintenance-only'):
                device_plan(self.root, options, source)
            validate.assert_called_once()
            resolve.assert_not_called()

    def test_device_mismatch_is_reported_without_rejecting_install_plan(self):
        item = dict(schema_version=1, kind='ComponentManifest', id='fixture:feature', version='1.0.0',
                    compatibility={'device_ids':['another']}, files=[])
        package = dict(id='device:fixture', device_id='fixture', version='1.0.0', components=[item],
                       compatibility={'device_ids':['another']})
        options = dict(device_package='fixture', device_manifest_sha256='a'*64,
                       device_trust='developer-session', device_components=[item['id']], repairs=[])
        source = dict(ID='steamos', VERSION_ID='3.10', KERNELS=['existing-kernel'])
        with mock.patch('protocol.validate_package', return_value=package):
            _, components, _, descriptor = device_plan(self.root, options, source)
        self.assertEqual([item['id']], [component['id'] for component in components])
        self.assertEqual([package['id'], item['id']], [warning['id'] for warning in descriptor['device_warnings']])

    def test_readonly_device_verification_executes_only_declared_verify_with_clean_environment(self):
        component = dict(id='oxp3:legacy-adaptation', hooks=dict(apply='scripts/apply.sh', verify='scripts/verify.sh'))
        package = Path(self.tmp.name) / 'package'; (package / 'scripts').mkdir(parents=True)
        (package / 'scripts/verify.sh').write_text('exit 0\n')
        options = dict(schema=1, repairs=[], plugins=[], device_package='oxp3', device_manifest_sha256='a'*64,
                       device_trust='developer-session', device_components=[component['id']])
        engine = mock.Mock(); engine.verify_readonly.return_value = dict(ok=True)
        factory = mock.Mock(return_value=engine)
        with mock.patch('integration.device_plan', return_value=(package, [component], {}, {})) as plan, \
             mock.patch('integration.maintenance_api', return_value=factory), mock.patch('subprocess.run') as execute, \
             mock.patch.dict('os.environ', dict(BASH_ENV='/untrusted', LD_PRELOAD='/untrusted', PYTHONPATH='/untrusted')):
            self.assertEqual({component['id']}, verify_device_components(self.root, '/payload', options, 'A', {component['id']: {}}))
        self.assertEqual(Path('/payload'), plan.call_args.args[0])
        command = execute.call_args.args[0]; environment = execute.call_args.kwargs['env']
        self.assertEqual(['/bin/bash', str(package.resolve()/'scripts/verify.sh'), str(self.root)], command)
        self.assertEqual(str(self.root), environment['BOILER_TARGET_ROOT'])
        self.assertFalse(set(environment) & {'BASH_ENV', 'LD_PRELOAD', 'PYTHONPATH'})
        self.assertTrue(factory.call_args.kwargs['read_only']); engine.apply.assert_not_called(); engine.recover.assert_not_called()

    def test_scripted_device_without_verify_hook_is_not_silently_skipped(self):
        component = dict(id='oxp3:legacy-adaptation', hooks=dict(apply='scripts/apply.sh'))
        options = dict(schema=1, repairs=[], plugins=[], device_package='oxp3', device_manifest_sha256='a'*64,
                       device_trust='developer-session', device_components=[component['id']])
        engine = mock.Mock(); engine.verify_readonly.return_value = dict(ok=True)
        with mock.patch('integration.device_plan', return_value=(Path(self.tmp.name), [component], {}, {})), \
             mock.patch('integration.maintenance_api', return_value=mock.Mock(return_value=engine)), mock.patch('subprocess.run') as execute:
            with self.assertRaisesRegex(ValueError, 'no readonly verification'):
                verify_device_components(self.root, '/payload', options, 'A', {component['id']: {}})
            execute.assert_not_called()


if __name__ == '__main__': unittest.main()
