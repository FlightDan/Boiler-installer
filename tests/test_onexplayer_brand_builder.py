import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('brand_builder', ROOT / 'scripts/build-onexplayer-brand.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class Encoder:
    def dumps(self, value):
        return json.dumps(value)


class BrandBuilderTests(unittest.TestCase):
    def test_candidate_version_is_explicit_and_newer(self):
        old = '0.1.0-localtest.20261104.fingerprint.10'
        new = '0.1.0-localtest.20261104.toonbrand.1'
        self.assertEqual(builder.require_new_version(new, old), new)
        with self.assertRaises(ValueError):
            builder.require_new_version(old, old)
        with self.assertRaises(ValueError):
            builder.require_new_version(None, old)
        with self.assertRaises(ValueError):
            builder.require_new_version('0.1.0-localtest.20261104.brand-preview.1', old)

    def test_version_argument_is_required_once(self):
        base = ['builder', '--oxp3-package', 'one', '--x2-package', 'two', '--report']
        with contextlib.redirect_stderr(io.StringIO()), patch.object(sys, 'argv', base):
            with self.assertRaises(SystemExit) as error:
                builder.main()
            self.assertEqual(error.exception.code, 2)
        duplicate = base + ['--version', '0.1.0-localtest.20261104.toonbrand.1',
                            '--version', '0.1.0-localtest.20261104.toonbrand.1']
        with contextlib.redirect_stderr(io.StringIO()), patch.object(sys, 'argv', duplicate):
            with self.assertRaises(SystemExit) as error:
                builder.main()
            self.assertEqual(error.exception.code, 2)

    def test_components_follow_requested_toon(self):
        guide = {'package': {'inherited_components': ['oxp3:microphone'],
                             'definitions': [{'id': 'oxp3:speaker-route'}]},
                 'components': [{'id': 'fingerprint-unlock'}],
                 'resource_model': {'common_references': {'boot_policies': [{'id': 'ssd:gm7-boot-repair'}]}}}
        manifest = {'components': [{'id': 'oxp3:microphone'}, {'id': 'oxp3:speaker-route'},
                                   {'id': 'oxp3:fingerprint-unlock'}, {'id': 'oxp3:gm7', 'ssd_boot_policy': {}},
                                   {'id': 'oxp3:unrequested'}]}
        selected, missing = builder.selected_components(guide, manifest)
        self.assertEqual([c['id'] for c in selected],
                         ['oxp3:microphone', 'oxp3:speaker-route', 'oxp3:fingerprint-unlock', 'oxp3:gm7'])
        self.assertEqual(missing, [])

    def test_missing_requested_component_is_reported(self):
        guide = {'package': {'inherited_components': ['oxp3:microphone'], 'definitions': []},
                 'components': [], 'resource_model': {}}
        selected, missing = builder.selected_components(guide, {'components': []})
        self.assertEqual(selected, [])
        self.assertEqual(missing, ['oxp3:microphone'])

    def test_external_source_is_included_when_snapshot_contains_it(self):
        class Parser:
            def loads(self, text, strict=True):
                return {'id': 'resource:wifi', 'files': [
                    {'path': 'inputs/firmware.bin', 'size': 3, 'sha256': 'b' * 64,
                     'availability': 'external-locked'}], 'licenses': []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guide_path = 'brands/onexplayer/oxp3/oxp3.toon'
            source = 'common/network/intel/be201/source.toon'
            firmware = 'common/network/intel/be201/inputs/firmware.bin'
            for name in (guide_path, source, firmware):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('abc')
            guide = {'id': 'guide:oxp3', 'components': [],
                     'resource_model': {'common_references': {
                         'resources': [{'id': 'resource:wifi', 'source': source}], 'boot_policies': []}}}
            inventory = {'adaptations/' + firmware: {'size': 3, 'sha256': 'b' * 64}}
            selected, unresolved, pending = builder.collect_sources(root, guide, guide_path, Parser(), inventory)
            self.assertIn(firmware, selected)
            self.assertEqual(unresolved, [])
            self.assertEqual(pending, [])

    def test_common_fingerprint_profile_uses_canonical_physical_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            guide = 'brands/onexplayer/oxp3/oxp3.toon'
            original = 'common/fingerprint/runtime-profile/profile.json'
            for name in (guide, original):
                path = project / 'adaptations' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('hello')
            package = root / 'package'
            logical = 'fingerprint/profile/' + original
            path = package / logical
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('hello')
            row = {'path': logical, 'size': 5, 'sha256': 'a' * 64}
            model = {'id': 'device:oxp3', 'guide': guide,
                     'generated': {'files': [row], 'components': []}, 'package_dir': package}
            lock = {'repository': 'FlightDan/Boiler-adaptations', 'commit': 'abcd',
                    'files': [{'path': 'adaptations/' + original, 'size': 5, 'sha256': 'a' * 64}]}
            prepared = (lock, Encoder(), {guide, original}, [model],
                        [{'reason': 'test unresolved'}], [])
            with patch.object(builder, 'prepare_models', return_value=prepared):
                out = root / 'brand.zip'
                builder.build(project, {}, out, version='0.1.0-localtest.20261104.toonbrand.1', allow_incomplete=True)
            with zipfile.ZipFile(out) as archive:
                index = json.loads(archive.read('brand.toon'))
                self.assertEqual(index['models'][0]['files'][0]['member'], original)
                self.assertEqual(archive.namelist().count(original), 1)

    def test_equivalent_resource_checks_content_and_missing_only(self):
        resource = {'payload': {'files': [{'path': 'common/fw/input', 'size': 3, 'sha256': 'a' * 64}]},
                    'install': {'files': [{'source': 'common/fw/input', 'target': 'usr/lib/fw',
                                           'mode': 420, 'missing_only': True}]}}
        rows = {'legacy/fw': {'path': 'legacy/fw', 'size': 3, 'sha256': 'a' * 64}}
        source = [{'source': 'legacy/fw', 'target': 'usr/lib/fw', 'mode': 420, 'missing_only': True}]
        self.assertEqual(builder.equivalent_resource(resource, rows, source),
                         {'legacy/fw': ('common/fw/input', 'common/fw/input')})
        source[0]['missing_only'] = False
        self.assertIsNone(builder.equivalent_resource(resource, rows, source))

    def test_microphone_resource_is_placed_in_brand_common(self):
        resource = {'payload': {'files': [{'path': 'devices/Audio/microphone-mute/files/input',
                                           'size': 3, 'sha256': 'a' * 64}]},
                    'install': {'files': [{'source': 'devices/Audio/microphone-mute/files/input',
                                           'target': 'usr/lib/mic'}]}}
        rows = {'legacy/mic': {'path': 'legacy/mic', 'size': 3, 'sha256': 'a' * 64}}
        source = [{'source': 'legacy/mic', 'target': 'usr/lib/mic'}]
        result = builder.equivalent_resource(resource, rows, source)
        self.assertEqual(result['legacy/mic'][1], 'brands/onexplayer/common/microphone/files/input')

    def test_decky_transformation_requires_existing_locked_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / 'adaptations'
            project_lock = root / 'components/locks/decky.json'
            project_lock.parent.mkdir(parents=True)
            binary = {'size': 3, 'sha256': 'b' * 64}
            notice = {'source': 'source.zip', 'size': 4, 'sha256': 'c' * 64}
            project_lock.write_text(json.dumps({'version': '3.3.1', 'file': binary, 'notices': [notice]}))
            source = 'brands/onexplayer/oxp3/scripts/plugin_installer.py'
            old = 'payload/legacy/components/plugin_installer.py'
            (snapshot / source).parent.mkdir(parents=True)
            (snapshot / source).write_bytes(b"marker = b'3.2.10\\n'")
            package = root / 'package'
            (package / old).parent.mkdir(parents=True)
            (package / old).write_bytes(b"marker = b'3.3.1\\n'")
            rows = {'payload/legacy/plugins/decky/PluginLoader': binary,
                    'payload/legacy/plugins/decky/source.zip': notice}
            self.assertTrue(builder.matches_reviewed_decky_transform(root, snapshot, package, source, old, rows))
            rows['payload/legacy/plugins/decky/PluginLoader'] = {'size': 3, 'sha256': 'd' * 64}
            self.assertFalse(builder.matches_reviewed_decky_transform(root, snapshot, package, source, old, rows))

    def test_only_explicitly_disabled_new_resource_is_pending(self):
        unresolved, pending = [], []
        builder.add_resource_gap(unresolved, pending, 'device:oxp3', 'resource:test',
                                 {'enabled': False}, None)
        self.assertEqual(unresolved, [])
        self.assertEqual(pending[0]['status'], 'disabled-resource-not-integrated')
        self.assertFalse(pending[0]['payload_included'])
        for row in ({'enabled': True}, {}, None):
            errors, notices = [], []
            builder.add_resource_gap(errors, notices, 'device:oxp3', 'resource:test', row, None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(notices, [])

    def test_provider_inventory_uses_existing_verifier_and_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / 'components/oxp3_full_efi_provider.lock.json'
            lock.parent.mkdir(parents=True)
            entry = {'path': 'payload/a.py', 'size': 3, 'sha256': 'a' * 64}
            lock.write_text(json.dumps({'files': [entry]}))
            with patch.object(builder.oxp3_full_efi_provider, 'verify') as verify:
                self.assertEqual(builder.fixed_provider_files(root, root, 'device:oxp3',
                                                             {'payload/a.py': entry}), {'payload/a.py'})
                verify.assert_called_once()
                with self.assertRaises(ValueError):
                    builder.fixed_provider_files(root, root, 'device:oxp3', {})

    def test_serial_policy_matches_without_executing_old_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / 'package'
            helper = 'payload/legacy/components/serial_console.py'
            path = package / helper
            path.parent.mkdir(parents=True)
            path.write_text("DROPIN = 'etc/systemd/policy.conf'\nDATA = b'policy'\n")
            pool = root / 'pool'
            (pool / 'common').mkdir(parents=True)
            (pool / 'common/policy').write_bytes(b'policy')
            resource = {'install': {'files': [{'source': 'common/policy',
                                               'target': 'etc/systemd/policy.conf', 'mode': 420}]}}
            self.assertEqual(builder.matches_serial_policy(resource, pool, package, helper), 'common/policy')
            (pool / 'common/policy').write_bytes(b'changed')
            self.assertIsNone(builder.matches_serial_policy(resource, pool, package, helper))

    def test_dynamic_assets_must_match_requested_component_directory(self):
        resource = {'payload': {'files': [{'path': 'common/boot/slot-switch/files/boot',
                                          'size': 3, 'sha256': 'a' * 64}]}}
        rows = {'legacy/slot-switch/boot.py': {'size': 3, 'sha256': 'a' * 64},
                'legacy/unrelated/boot.py': {'size': 3, 'sha256': 'a' * 64}}
        self.assertEqual(builder.mapped_resource_assets(resource, rows, 'legacy/slot-switch/'),
                         {'legacy/slot-switch/boot.py': ('common/boot/slot-switch/files/boot',
                                                          'common/boot/slot-switch/files/boot')})
        self.assertIsNone(builder.mapped_resource_assets(resource, rows, 'legacy/missing/'))

    def test_available_capabilities_map_to_explicit_legacy_inputs(self):
        guide = {'package': {'inherited_legacy_capabilities':
                 ['oxp3:input', 'oxp3:slot-switch', 'oxp3:refind', 'oxp3:future', 'oxp3:unspecified']}}
        manifest = {'legacy_capabilities': [
            {'id': 'oxp3:input', 'available': True},
            {'id': 'oxp3:slot-switch', 'available': True},
            {'id': 'oxp3:refind', 'available': False},
            {'id': 'oxp3:future', 'available': True}, {'id': 'oxp3:unspecified'}]}
        integration = {'input': {'files': [{'source': 'input/000.bin'}],
                                 'runtime_archive': 'input/runtime.tar'}}
        roots, unknown, available = builder.legacy_capability_roots(guide, manifest, integration, Path('.'))
        self.assertIn('payload/legacy/integration/input/000.bin', roots)
        self.assertIn('payload/legacy/integration/input/runtime.tar', roots)
        self.assertIn('payload/legacy/components/slot-switch/boot.py', roots)
        self.assertIn('selection.json', roots)
        self.assertEqual(unknown, ['future'])
        self.assertNotIn('refind', available)
        self.assertNotIn('unspecified', available)

    def test_legacy_runtime_roots_follow_fixed_hook_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / 'payload/legacy/components/integration.py'
            helper.parent.mkdir(parents=True)
            helper.write_text("for name in ('integration_runtime.py', 'release_notes.py'):\n    pass\n"
                              "for name in ('installer.py', 'generate.py', 'upstream.lua', 'UPSTREAM_LICENSE'):\n    pass\n")
            files = builder.legacy_runtime_roots(root)
            self.assertIn('payload/legacy/components/integration_runtime.py', files)
            self.assertIn('payload/legacy/components/hdr/upstream.lua', files)
            self.assertIn('payload/legacy/components/split-lock/UPSTREAM_LICENSE', files)
            helper.write_text('pass\n')
            with self.assertRaises(ValueError):
                builder.legacy_runtime_roots(root)

    def test_static_python_import_closure_reads_known_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'payload/legacy/components'
            base.mkdir(parents=True)
            (base / 'integration.py').write_text('from helper import run\n')
            (base / 'helper.py').write_text('import json\n')
            names = ['payload/legacy/components/integration.py', 'payload/legacy/components/helper.py']
            self.assertEqual(builder.legacy_python_closure(root, dict.fromkeys(names), {names[0]}), set(names))

    def test_archive_shared_paths_are_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            guides = ('brands/onexplayer/oxp3/oxp3.toon', 'brands/onexplayer/x2-mini/x2-mini.toon')
            for guide in guides:
                path = project / 'adaptations' / guide
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('model')
            packages = []
            rows = [{'path': 'payload/shared', 'size': 5, 'sha256': 'a' * 64}]
            for name, guide in zip(('oxp3', 'x2-mini'), guides):
                package = root / name
                (package / 'payload').mkdir(parents=True)
                (package / 'payload/shared').write_text('hello')
                packages.append({'id': 'device:' + name, 'guide': guide,
                    'generated': {'files': rows, 'components': []}, 'package_dir': package})
            result = ({'repository': 'FlightDan/Boiler-adaptations', 'commit': 'abcd'},
                      Encoder(), set(guides), packages,
                      [{'reason': 'test unresolved'}], [])
            with patch.object(builder, 'prepare_models', return_value=result):
                out = root / 'brand.zip'
                info = builder.build(project, {}, out, version='0.1.0-localtest.20261104.toonbrand.1', allow_incomplete=True)
            with zipfile.ZipFile(out) as archive:
                index = json.loads(archive.read('brand.toon'))
                first, second = (model['files'][0]['member'] for model in index['models'])
                self.assertEqual(first, second)
                self.assertEqual(archive.namelist().count(first), 1)
                self.assertEqual(archive.read(first), b'hello')
                self.assertTrue(index['unresolved'])
                self.assertEqual(index['version'], '0.1.0-localtest.20261104.toonbrand.1')
            self.assertFalse(info['importable'])
            self.assertFalse(info['linux_lifecycle_accepted'])


@unittest.skipUnless(os.environ.get('BOILER_BRAND_OXP_PACKAGE') and os.environ.get('BOILER_BRAND_X2_PACKAGE')
                     and os.environ.get('BOILER_BRAND_RESOURCE_POOL'),
                     'explicit reviewed model packages and resource pool required')
class ActualBrandSelectionTests(unittest.TestCase):
    def test_actual_selection_preserves_defaults_and_required_roots(self):
        packages = {'device:oxp3': os.environ['BOILER_BRAND_OXP_PACKAGE'],
                    'device:x2-mini': os.environ['BOILER_BRAND_X2_PACKAGE']}
        _, _, _, models, unresolved, pending = builder.prepare_models(
            ROOT, packages, version='0.1.0-localtest.20261104.toonbrand.1', resource_pool=os.environ['BOILER_BRAND_RESOURCE_POOL'], allow_incomplete=True)
        self.assertEqual(unresolved, [])
        for model in models:
            old = json.loads((Path(packages[model['id']]) / 'manifest.json').read_text(encoding='utf-8'))
            generated = model['generated']
            self.assertEqual(generated['legacy_capabilities'], old['legacy_capabilities'])
            old_versions = {row['id']: row['version'] for row in old['components']}
            self.assertTrue(all(old_versions[row['id']] == row['version'] for row in generated['components']))
            self.assertEqual(generated['version'], '0.1.0-localtest.20261104.toonbrand.1')
            selected = {row['path'] for row in generated['files']}
            toon = builder.decoder(ROOT / 'adaptations')
            guide = toon.loads((ROOT / 'adaptations' / model['guide']).read_text(encoding='utf-8'), strict=True)
            package = Path(packages[model['id']])
            integration = json.loads((package / 'payload/legacy/integration/manifest.json').read_text(encoding='utf-8'))
            roots, unknown, available = builder.legacy_capability_roots(guide, old, integration, package)
            self.assertEqual(unknown, [])
            self.assertEqual(len(available), 25)
            self.assertTrue(roots <= selected)
            self.assertTrue(builder.legacy_runtime_roots(package) <= selected)
            self.assertIn('selection.json', selected)
            original_selection = next(row for row in old['files'] if row['path'] == 'selection.json')
            selected_selection = next(row for row in generated['files'] if row['path'] == 'selection.json')
            self.assertEqual(selected_selection, original_selection)
            required_licenses = {row['path'] for row in old['files'] if row['path'].startswith('fingerprint/licenses/')}
            self.assertTrue(required_licenses)
            self.assertTrue(required_licenses <= selected)
            provider = ('components/oxp3_full_efi_provider.lock.json' if model['id'] == 'device:oxp3'
                        else 'components/x2_efi_provider.lock.json')
            fixed = json.loads((ROOT / provider).read_text(encoding='utf-8'))
            provider_roots = {row['path'] for row in fixed['files']}
            self.assertTrue(provider_roots <= selected)
            rows = {row['path']: row for row in old['files']}
            self.assertEqual(builder.legacy_closure_issues(guide, old, package, selected,
                                                           provider_roots, rows, integration), [])
            for missing in ('selection.json', fixed['files'][0]['path'], 'scripts/apply.sh',
                            'payload/legacy/components/slot-switch/boot.py'):
                issues = builder.legacy_closure_issues(guide, old, package, selected - {missing},
                                                       provider_roots, rows, integration)
                self.assertTrue(any(missing in issue.get('paths', []) for issue in issues), missing)
            unknown_guide, unknown_manifest = copy.deepcopy(guide), copy.deepcopy(old)
            unknown_guide['package']['inherited_legacy_capabilities'].append('oxp3:future-unknown')
            unknown_manifest['legacy_capabilities'].append({'id': 'oxp3:future-unknown', 'available': True})
            issues = builder.legacy_closure_issues(unknown_guide, unknown_manifest, package,
                                                   selected, provider_roots, rows, integration)
            self.assertTrue(any('future-unknown' in issue.get('capabilities', []) for issue in issues))
            be201 = [row for row in pending if row['model'] == model['id']
                     and row.get('resource') == 'resource:intel-be201-firmware']
            self.assertEqual(len(be201), 1)
            self.assertEqual(be201[0]['status'], 'disabled-resource-not-integrated')
            self.assertFalse(be201[0]['payload_included'])
            for row in pending:
                if row['model'] == model['id'] and 'resource' in row:
                    self.assertIn(row['definition'], model['resource_records'])
                    self.assertTrue(set(row['licenses']) <= set(model['resource_records']))



if __name__ == '__main__':
    unittest.main()
