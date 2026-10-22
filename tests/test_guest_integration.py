import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'components'))
sys.path.insert(0, str(ROOT / 'guest'))
from integration import stage, validate_options, check_conflicts, verify_device_components, device_plan
from source_profile import inspect_source


class GenericIntegrationChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name) / 'root'; self.root.mkdir()
        (self.root / 'etc').mkdir(); (self.root / 'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.8.14\nBUILD_ID=20260707.10\n')
        (self.root / 'usr/lib/modules/existing-kernel').mkdir(parents=True)

    def tearDown(self): self.tmp.cleanup()

    def inventory(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

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
        self.assertFalse(manifest['components']['wifi:intel']['available'])
        with self.assertRaises(FileExistsError): prepare(output)
        self.assertFalse(any('oxp3' in p.as_posix() for p in output.rglob('*')))

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
