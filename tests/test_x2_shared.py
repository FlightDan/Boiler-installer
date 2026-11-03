"""Synthetic roots only: no service, device, shell hook or host install action."""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from x2_partial_fixture import copy_toon_sources

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, default=HERE.parent)
parser.add_argument('--candidate', type=Path)
parser.add_argument('--package', type=Path)
parser.add_argument('--builder', type=Path)
parser.add_argument('--adaptations-root', type=Path)
args, rest = parser.parse_known_args()
ROOT = args.root.resolve()
CANDIDATE = args.candidate.resolve() if args.candidate else ROOT
PACKAGE = args.package.resolve() if args.package else None
BUILDER = args.builder.resolve() if args.builder else ROOT/'adaptations/brands/onexplayer/x2-mini/bridge/scripts/build.py'
ADAPTATIONS = args.adaptations_root.resolve() if args.adaptations_root else ROOT/'adaptations'
sys.path[:0] = [str(ROOT), str(ROOT/'components'), str(ROOT/'guest')]
import maintenance
from maintenance.engine import Engine, MaintenanceError
from protocol.manifest import validate_package, ProtocolError

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded

shared = module('maintenance._x2_shared_candidate', CANDIDATE/'maintenance/shared_models.py')
installer = module('x2_candidate_integration', CANDIDATE/'components/integration.py')
builder = module('x2_shared_source_builder', BUILDER)
KERNEL = shared.GAMESCOPE_KERNEL
IDENTITY = shared.GAMESCOPE_COMPONENT
TARGET = shared.GAMESCOPE_TARGET
BASE_ANCHOR = '874ee0c4bf0f46ec5af94f7452ebe0d656b50bb2b5c673a42392c8ba9191db6c'

@contextlib.contextmanager
def candidate_shared_module():
    # Importing this test must not replace the product module retained by other
    # tests. Installer lazy imports need the candidate only within this fixture.
    name='maintenance.shared_models'
    missing=object()
    previous=sys.modules.get(name,missing)
    with patch.object(maintenance,'shared_models',shared,create=True):
        sys.modules[name]=shared
        try:
            yield
        finally:
            if previous is missing:
                sys.modules.pop(name,None)
            else:
                sys.modules[name]=previous

def sha(data):
    return hashlib.sha256(data).hexdigest()

def json_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value)+'\n', encoding='utf-8')

class CandidateFixture(unittest.TestCase):
    def setUp(self):
        if os.name != 'nt' and os.geteuid() != 0:
            self.skipTest('Linux fixture must run as root; no physical target is used')
        self.enterContext(candidate_shared_module())
        self.tmp = tempfile.TemporaryDirectory(prefix='x2-gamescope-synthetic-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.root = self.work/'image'; self.root.mkdir()
        (self.root/'etc').mkdir()
        (self.root/'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.10\nBUILD_ID=20260924.1000\n')
        (self.root/'etc/passwd').write_text('root:x:0:0:root:/root:/bin/sh\ndeck:x:1000:1000:deck:/home/deck:/bin/bash\n')
        (self.root/'home/deck').mkdir(parents=True)
        if os.name != 'nt':
            os.chown(self.root/'home/deck', 1000, 1000)
        (self.root/'usr/lib/modules'/KERNEL).mkdir(parents=True)
        (self.root/'usr/bin').mkdir(parents=True)
        elf = bytearray(20); elf[:4] = b'\x7fELF'; elf[5] = 1; elf[18:20] = (62).to_bytes(2,'little')
        (self.root/'usr/bin/env').write_bytes(elf)
        (self.root/'usr/bin/systemctl').write_bytes(b'synthetic-not-executable-systemctl')
        unit = self.root/'usr/lib/systemd/user/gamescope-session.service'
        unit.parent.mkdir(parents=True); unit.write_text('[Service]\nExecStart=/usr/bin/false\n')
        self.payload = self.work/'payload'
        self.package = self.payload/'device-packages/x2-mini'
        if PACKAGE is None:
            built=self.work/'initial-source-build'
            partial=self.work/'historical-partial-adaptations'
            copy_toon_sources(ADAPTATIONS,partial)
            builder.build(ROOT,built,partial)
            shutil.copytree(built/'package',self.package)
        else:
            shutil.copytree(PACKAGE, self.package)
        self.manifest = json.loads((self.package/'manifest.json').read_bytes())
        self.anchor = sha((self.package/'manifest.json').read_bytes())
        self.part = next(p for p in self.manifest['components'] if p['id'] == IDENTITY)
        # Public historical metadata plus the independently rebuilt current
        # source bytes reproduce the exact former .2 package for upgrade tests.
        self.baseline_source = self.work/'baseline'
        self.baseline_source.mkdir()
        raw=(HERE/'fixtures/x2/manifest-20261030.2.json').read_bytes()
        old=json.loads(raw)
        for row in old['files']:
            source=(HERE/'fixtures/x2/inputs-20261030.2.json') if row['path']=='shared-adaptations-inputs.json' else self.package/row['path']
            target=self.baseline_source/row['path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes())
        (self.baseline_source/'manifest.json').write_bytes(raw)
        validate_package(self.baseline_source,expected_manifest_sha256=BASE_ANCHOR)
        json_file(self.payload/'integration/manifest.json', dict(schema=1, components={}))
        self.runtime = self.work/'runtime'
        self.bundle(self.package, self.runtime)
        self.kw = dict(trust_local_test=True, runtime_root=self.runtime)
        self.options = dict(schema=1, repairs=[], plugins=[], device_components=[IDENTITY],
            device_package='x2-mini', device_trust='developer-session', device_manifest_sha256=self.anchor)
        self.target = self.root/TARGET
        patcher = patch('maintenance.models._privileged', lambda: None)
        patcher.start(); self.addCleanup(patcher.stop)
        # Any attempt to activate services or hooks is a test failure.
        patcher = patch('subprocess.run', side_effect=AssertionError('No external process is permitted'))
        patcher.start(); self.addCleanup(patcher.stop)

    def bundle(self, source, runtime):
        destination = runtime/'cache/model-bundles/packages/x2-mini'
        shutil.copytree(source, destination)
        value = json.loads((destination/'manifest.json').read_bytes())
        json_file(runtime/'cache/model-bundles/manifest.json', dict(schema_version=1,kind='LocalTestModelBundles',
            packages=[dict(id='device:x2-mini', version=value['version'], directory='cache/model-bundles/packages/x2-mini',
                           manifest_sha256=sha((destination/'manifest.json').read_bytes()))]))

    def plan(self, action='apply', repairs=None):
        if repairs is None: repairs=[IDENTITY] if action=='apply' else []
        return shared.plan(self.root, 'device:x2-mini', repairs, action=action, **self.kw)

    def execute(self, plan):
        return shared.execute(self.root, plan, reviewed_sha256=plan['review_sha256'], **self.kw)

    def install(self):
        return installer.stage(self.root, self.payload, self.options, 'A')

    def original(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'[Service]\n# existing user configuration\n')
        self.target.chmod(0o600)
        if os.name != 'nt': os.chown(self.target,1000,1000)
        return self.target.read_bytes()

class CandidateLifecycle(CandidateFixture):
    @unittest.skipIf(os.name == 'nt', 'Full installer result journals require genuine Linux fcntl')
    def test_new_install_verify_restore_exact_original(self):
        before = self.original()
        mode = self.target.stat().st_mode & 0o777
        parent_mode = self.target.parent.stat().st_mode & 0o777
        result = self.install()
        self.assertEqual('files_installed',result['components'][IDENTITY]['status'])
        installer.verify_device_components(self.root,self.payload,self.options,'A',result['components'])
        self.assertEqual(shared.GAMESCOPE_SHA256,sha(self.target.read_bytes()))
        self.assertEqual(parent_mode,self.target.parent.stat().st_mode & 0o777)
        if os.name != 'nt':
            self.assertEqual(0o644,self.target.stat().st_mode & 0o777)
            self.assertEqual(1000,self.target.stat().st_uid)
        self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
        self.execute(self.plan('restore'))
        self.assertEqual(before,self.target.read_bytes())
        if os.name != 'nt': self.assertEqual(mode,self.target.stat().st_mode & 0o777)

    @unittest.skipIf(os.name == 'nt', 'Full installer result journals require genuine Linux fcntl')
    def test_absent_original_restores_to_absence_and_safe_modes(self):
        self.install()
        self.assertTrue(self.target.is_file())
        if os.name != 'nt':
            self.assertEqual(0o755,self.target.parent.stat().st_mode & 0o777)
            self.assertEqual(1000,self.target.parent.stat().st_uid)
            self.assertEqual(1000,(self.root/'home/deck/.config').stat().st_uid)
            self.assertEqual(0o644,self.target.stat().st_mode & 0o777)
        self.execute(self.plan('restore'))
        self.assertFalse(self.target.exists())

    def test_maintenance_only_apply_verify_restore(self):
        before = self.original()
        self.execute(self.plan())
        self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
        self.execute(self.plan('restore'))
        self.assertEqual(before,self.target.read_bytes())

    @unittest.skipIf(os.name == 'nt', 'Full installer result journals require genuine Linux fcntl')
    def test_installed_target_change_blocks_restore_and_verify(self):
        self.install(); self.target.write_bytes(b'user changed')
        self.assertFalse(self.execute(self.plan('verify'))['result']['verified'])
        with self.assertRaisesRegex(MaintenanceError,'modified target'):
            self.execute(self.plan('restore'))
        self.assertEqual(b'user changed',self.target.read_bytes())

    def test_missing_dependency_refuses_before_installer_target_write(self):
        (self.root/'usr/lib/systemd/user/gamescope-session.service').unlink()
        with self.assertRaisesRegex(MaintenanceError,'dependency missing'):
            self.install()
        self.assertFalse((self.root/'var').exists())
        row = next(p for p in shared.options(self.root,'device:x2-mini',**self.kw)['capabilities'] if p['id']==IDENTITY)
        self.assertFalse(row['available'])

    @unittest.skipIf(os.name == 'nt', 'Full installer result journals require genuine Linux fcntl')
    def test_wrong_build_and_kernel_refuse_install_without_dropping_restore(self):
        self.install()
        (self.root/'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.10\nBUILD_ID=other\n')
        with self.assertRaisesRegex(MaintenanceError,'baseline'):
            self.install()
        with self.assertRaisesRegex(MaintenanceError,'unavailable|incompatible'):
            self.plan()
        self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
        self.execute(self.plan('restore'))
        self.assertFalse(self.target.exists())

    def test_missing_deck_account_refuses_before_generic_component_write(self):
        (self.root/'etc/passwd').write_text('root:x:0:0:root:/root:/bin/sh\n')
        raw=b'synthetic generic maintenance payload'
        name='review-maintenance-core.txt'
        (self.payload/'integration'/name).write_bytes(raw)
        row=dict(source=name,size=len(raw),sha256=sha(raw))
        component=dict(schema_version=1,kind='ComponentManifest',id='core:maintenance',
            version='0.1.0',files=[dict(source=name,target='usr/share/boiler/review-core.txt',
                size=len(raw),sha256=sha(raw),mode=0o644)])
        json_file(self.payload/'integration/manifest.json',dict(schema=1,components={
            'core:maintenance':dict(available=True,files=[row],component=component)}))
        self.options['repairs']=['core:maintenance']
        with self.assertRaisesRegex(MaintenanceError,'target deck account'):
            self.install()
        self.assertFalse((self.root/'usr/share/boiler/review-core.txt').exists())
        self.assertFalse((self.root/'var').exists())
        view=shared.options(self.root,'device:x2-mini',**self.kw)
        self.assertFalse(next(p for p in view['capabilities'] if p['id']==IDENTITY)['available'])

    def test_missing_deck_home_refuses_before_any_target_write(self):
        (self.root/'home/deck').rmdir()
        with self.assertRaises(FileNotFoundError):
            self.install()
        self.assertFalse((self.root/'var').exists())
        self.assertFalse((self.root/'home/deck').exists())

    def test_extra_installed_kernel_refuses(self):
        (self.root/'usr/lib/modules/unaccepted').mkdir()
        with self.assertRaises((ProtocolError,MaintenanceError)):
            self.install()
        self.assertFalse(self.target.exists())

    def test_exact_target_and_bytes_policy_cannot_be_broadened(self):
        for field,value in [('target','home/deck/.config/arbitrary'),('sha256','0'*64),('mode',0o755)]:
            part=copy.deepcopy(self.part); part['files'][0][field]=value
            with self.assertRaisesRegex(MaintenanceError,'Unexpected'):
                shared.prepare_component_dirs(Engine(self.root,read_only=True),part)
        part=copy.deepcopy(self.part);part['default']=True
        with self.assertRaisesRegex(MaintenanceError,'Unexpected'):
            shared.check_gamescope_component_shape(part)
        self.assertFalse((self.root/'home/deck/.config').exists())

    def test_redirected_parent_refused(self):
        outside=self.work/'outside';outside.mkdir()
        try: (self.root/'home/deck/.config').symlink_to(outside,target_is_directory=True)
        except OSError: self.skipTest('Windows symlink privilege is unavailable')
        with self.assertRaises((MaintenanceError,ProtocolError,ValueError,OSError)):
            self.install()
        self.assertEqual([],list(outside.iterdir()))

    def test_old_retained_package_adds_option_without_mutating_old_selection(self):
        oldruntime=self.work/'old-runtime'
        oldsource=self.baseline_source
        self.bundle(oldsource,oldruntime)
        oldkw=dict(trust_local_test=True,runtime_root=oldruntime)
        oldplan=shared.plan(self.root,'device:x2-mini',['x2-mini:microphone'],**oldkw)
        shared.execute(self.root,oldplan,reviewed_sha256=oldplan['review_sha256'],**oldkw)
        state=Engine(self.root)._state()
        oldtransaction=state['components']['x2-mini:microphone']['transaction']
        view=shared.options(self.root,'device:x2-mini',**self.kw)
        self.assertIn(IDENTITY,[p['id'] for p in view['capabilities']])
        self.assertNotIn('x2-mini:keyboard',[p['id'] for p in view['capabilities']])
        with self.assertRaisesRegex(MaintenanceError,'Unknown shared repair'):
            shared.plan(self.root,'device:x2-mini',['x2-mini:keyboard'],**self.kw)
        self.assertFalse((self.root/'home/.boiler/keyboard-v1').exists())
        self.assertEqual(['x2-mini:microphone'],view['current']['repairs'])
        self.execute(self.plan())
        self.assertEqual(oldtransaction,Engine(self.root)._state()['components']['x2-mini:microphone']['transaction'])
        self.assertTrue((self.root/'var/lib/boiler/maintenance/models/packages'/BASE_ANCHOR).is_dir())
        self.execute(self.plan('restore'))
        self.assertEqual({},Engine(self.root)._state()['components'])
        self.assertFalse(self.target.exists())

    def test_changed_old_component_definition_keeps_retained_recovery(self):
        oldruntime=self.work/'old-runtime';self.bundle(self.baseline_source,oldruntime)
        oldkw=dict(trust_local_test=True,runtime_root=oldruntime)
        plan=shared.plan(self.root,'device:x2-mini',['x2-mini:microphone'],**oldkw)
        shared.execute(self.root,plan,reviewed_sha256=plan['review_sha256'],**oldkw)
        bundled=self.runtime/'cache/model-bundles/packages/x2-mini/manifest.json'
        value=json.loads(bundled.read_bytes());value['components'][0]['description']['en']='changed'
        json_file(bundled,value)
        descriptor=self.runtime/'cache/model-bundles/manifest.json'
        value=json.loads(descriptor.read_bytes());value['packages'][0]['manifest_sha256']=sha(bundled.read_bytes());json_file(descriptor,value)
        view=shared.options(self.root,'device:x2-mini',**self.kw)
        self.assertEqual(BASE_ANCHOR,view['package']['manifest_sha256'])
        self.assertNotIn(IDENTITY,[p['id'] for p in view['capabilities']])
        self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
        self.execute(self.plan('restore'))
        self.assertEqual({},Engine(self.root)._state()['components'])

    def test_invalid_added_keyboard_contract_keeps_original_transaction_and_recovery(self):
        oldruntime=self.work/'old-runtime';self.bundle(self.baseline_source,oldruntime)
        oldkw=dict(trust_local_test=True,runtime_root=oldruntime)
        plan=shared.plan(self.root,'device:x2-mini',['x2-mini:microphone'],**oldkw)
        shared.execute(self.root,plan,reviewed_sha256=plan['review_sha256'],**oldkw)
        transaction=Engine(self.root)._state()['components']['x2-mini:microphone']['transaction']
        bundled=self.runtime/'cache/model-bundles/packages/x2-mini/manifest.json'
        descriptor=self.runtime/'cache/model-bundles/manifest.json'
        original=json.loads(bundled.read_bytes())
        variants=[('dynamic_handler','arbitrary-module'),('dynamic_targets',['home/deck/.config/other']),
                  ('default',True),('program','unreviewed.py'),
                  ('shared_kind','boiler-shared-static-v1'),('other_dynamic','arbitrary-module')]
        for field,invalid in variants:
            with self.subTest(field=field):
                value=copy.deepcopy(original)
                keyboard=next(part for part in value['components'] if part['id']=='x2-mini:keyboard')
                if field=='shared_kind':value['shared_adaptations']['kind']=invalid
                elif field=='other_dynamic':next(part for part in value['components'] if part['id']==IDENTITY)['dynamic_handler']=invalid
                else:keyboard[field]=invalid
                json_file(bundled,value)
                data=json.loads(descriptor.read_bytes());data['packages'][0]['manifest_sha256']=sha(bundled.read_bytes());json_file(descriptor,data)
                view=shared.options(self.root,'device:x2-mini',**self.kw)
                self.assertEqual(BASE_ANCHOR,view['package']['manifest_sha256'])
                self.assertNotIn(IDENTITY,[part['id'] for part in view['capabilities']])
                self.assertNotIn('x2-mini:keyboard',[part['id'] for part in view['capabilities']])
                self.assertEqual(transaction,Engine(self.root)._state()['components']['x2-mini:microphone']['transaction'])
                self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
        self.execute(self.plan('restore'))
        self.assertEqual({},Engine(self.root)._state()['components'])
        self.assertFalse((self.root/'home/.boiler/keyboard-v1').exists())

    def test_unavailable_new_host_contract_does_not_gate_retained_verify_restore(self):
        from maintenance import keyboard_contract
        oldruntime=self.work/'old-runtime';self.bundle(self.baseline_source,oldruntime)
        oldkw=dict(trust_local_test=True,runtime_root=oldruntime)
        plan=shared.plan(self.root,'device:x2-mini',['x2-mini:microphone'],**oldkw)
        shared.execute(self.root,plan,reviewed_sha256=plan['review_sha256'],**oldkw)
        with patch.object(keyboard_contract,'check',side_effect=ImportError('Unavailable optional host runtime')):
            view=shared.options(self.root,'device:x2-mini',**self.kw)
            self.assertEqual(BASE_ANCHOR,view['package']['manifest_sha256'])
            self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
            self.execute(self.plan('restore'))
        self.assertEqual({},Engine(self.root)._state()['components'])

    def test_missing_current_bundle_does_not_block_retained_verify_restore(self):
        oldruntime=self.work/'old-runtime';self.bundle(self.baseline_source,oldruntime)
        oldkw=dict(trust_local_test=True,runtime_root=oldruntime)
        plan=shared.plan(self.root,'device:x2-mini',['x2-mini:microphone'],**oldkw)
        shared.execute(self.root,plan,reviewed_sha256=plan['review_sha256'],**oldkw)
        shutil.rmtree(self.runtime/'cache')
        view=shared.options(self.root,'device:x2-mini',**self.kw)
        self.assertEqual(BASE_ANCHOR,view['package']['manifest_sha256'])
        self.assertNotIn(IDENTITY,[p['id'] for p in view['capabilities']])
        self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
        self.execute(self.plan('restore'))
        self.assertEqual({},Engine(self.root)._state()['components'])

    def test_corrupted_current_bundle_does_not_block_retained_verify_restore(self):
        oldruntime=self.work/'old-runtime';self.bundle(self.baseline_source,oldruntime)
        oldkw=dict(trust_local_test=True,runtime_root=oldruntime)
        plan=shared.plan(self.root,'device:x2-mini',['x2-mini:microphone'],**oldkw)
        shared.execute(self.root,plan,reviewed_sha256=plan['review_sha256'],**oldkw)
        bundled=self.runtime/'cache/model-bundles/packages/x2-mini/manifest.json'
        bundled.write_bytes(b'invalid current candidate manifest')
        self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
        self.execute(self.plan('restore'))
        self.assertEqual({},Engine(self.root)._state()['components'])

    def test_invalid_optional_upgrade_metadata_does_not_block_retained_recovery(self):
        oldruntime=self.work/'old-runtime';self.bundle(self.baseline_source,oldruntime)
        oldkw=dict(trust_local_test=True,runtime_root=oldruntime)
        plan=shared.plan(self.root,'device:x2-mini',['x2-mini:microphone'],**oldkw)
        shared.execute(self.root,plan,reviewed_sha256=plan['review_sha256'],**oldkw)
        bundled=self.runtime/'cache/model-bundles/packages/x2-mini/manifest.json'
        descriptor=self.runtime/'cache/model-bundles/manifest.json'
        original=json.loads(bundled.read_bytes())
        wrong_kind=copy.deepcopy(original['shared_adaptations']);wrong_kind['kind']='invalid-new-kind'
        duplicate=copy.deepcopy(original['shared_adaptations']);duplicate['components'].append(duplicate['components'][0])
        wrong_warnings=copy.deepcopy(original['shared_adaptations']);wrong_warnings['warnings']=True
        for invalid in ([], {'additive_upgrade_from':True}, wrong_kind, duplicate, wrong_warnings,
                        {'additive_upgrade_from':[BASE_ANCHOR],'components':[{}]}):
            value=copy.deepcopy(original);value['shared_adaptations']=invalid;json_file(bundled,value)
            metadata=json.loads(descriptor.read_bytes());metadata['packages'][0]['manifest_sha256']=sha(bundled.read_bytes());json_file(descriptor,metadata)
            view=shared.options(self.root,'device:x2-mini',**self.kw)
            self.assertEqual(BASE_ANCHOR,view['package']['manifest_sha256'])
            self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
        self.execute(self.plan('restore'))
        self.assertEqual({},Engine(self.root)._state()['components'])

    def test_unlisted_old_package_anchor_is_not_automatically_upgraded(self):
        unknown=self.work/'unknown';shutil.copytree(self.baseline_source,unknown)
        manifest=unknown/'manifest.json';value=json.loads(manifest.read_bytes());value['version']='0.1.0-localtest.unlisted';json_file(manifest,value)
        oldruntime=self.work/'unknown-runtime';self.bundle(unknown,oldruntime)
        oldkw=dict(trust_local_test=True,runtime_root=oldruntime)
        plan=shared.plan(self.root,'device:x2-mini',['x2-mini:microphone'],**oldkw)
        shared.execute(self.root,plan,reviewed_sha256=plan['review_sha256'],**oldkw)
        view=shared.options(self.root,'device:x2-mini',**self.kw)
        self.assertNotIn(IDENTITY,[p['id'] for p in view['capabilities']])

    def test_maintenance_wrong_build_blocks_apply_but_allows_original_restore(self):
        before=self.original();self.execute(self.plan())
        (self.root/'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.11\nBUILD_ID=other\n')
        with self.assertRaisesRegex(MaintenanceError,'unavailable|incompatible'):
            self.plan()
        self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
        self.execute(self.plan('restore'));self.assertEqual(before,self.target.read_bytes())

    def test_maintenance_modified_file_is_preserved(self):
        self.execute(self.plan());self.target.write_bytes(b'custom edits')
        self.assertFalse(self.execute(self.plan('verify'))['result']['verified'])
        with self.assertRaisesRegex(MaintenanceError,'modified target'):
            self.execute(self.plan('restore'))
        self.assertEqual(b'custom edits',self.target.read_bytes())

    @unittest.skipIf(os.name == 'nt', 'Requires genuine Linux uid and permission checks')
    def test_world_writable_user_ancestor_rejected(self):
        config=self.root/'home/deck/.config';config.mkdir();config.chmod(0o777)
        def snapshot():
            return {path.relative_to(self.root).as_posix():
                    (path.stat().st_mode, path.stat().st_uid, path.stat().st_gid,
                     sha(path.read_bytes()) if path.is_file() else None)
                    for path in self.root.rglob('*')}
        before=snapshot()
        options=shared.options(self.root,'device:x2-mini',**self.kw)
        capability=next(row for row in options['capabilities'] if row['id']==IDENTITY)
        self.assertFalse(capability['available'])
        self.assertIn('Writable',capability['reason'])
        self.assertEqual(before,snapshot())
        # The plan intentionally exposes the common unavailable-selection error;
        # the per-option capability above carries the actionable path reason.
        with self.assertRaisesRegex(MaintenanceError,'unavailable'):
            self.plan()
        self.assertEqual(before,snapshot())
        with self.assertRaisesRegex(MaintenanceError,'Writable'):
            self.install()
        self.assertEqual(before,snapshot())
        self.assertFalse(self.target.exists())

    def test_explicit_empty_selection_and_manifest_public_inventory(self):
        manifest=validate_package(self.package,expected_manifest_sha256=self.anchor)
        self.assertFalse(self.part['default']);self.assertEqual('explicit-opt-in',self.part['selection_policy'])
        choices=dict(self.options,device_components=[])
        result=installer.stage(self.root,self.payload,choices,'A')
        self.assertEqual('no-components-selected',result['status'])
        self.assertFalse(self.target.exists())
        # An explicit empty selection must still skip staging. The Windows import
        # UI defaults to all items; its selection/opt-out behavior is exercised by
        # device-choice-checks rather than an implementation-text assertion.
        self.assertEqual({'x2-mini:battery','x2-mini:microphone',IDENTITY,'x2-mini:slot-switch','x2-mini:keyboard'},{p['id'] for p in manifest['components']})
        for path in [p['path'] for p in manifest['files']]:
            self.assertNotIn('.local/',path);self.assertNotIn('research/',path)
        for component in manifest['components']:
            self.assertFalse(component.get('hooks'))
            for row in component['files']:
                self.assertNotIn('oxpec',row['target']);self.assertNotIn('fan',row['target'])

    def test_public_builder_uses_only_locked_source_inputs_and_is_reproducible(self):
        lock=json.loads((builder.BRIDGE/'source-lock.json').read_bytes())
        isolated=self.work/'source-only';isolated.mkdir()
        copy_toon_sources(ADAPTATIONS,isolated/'adaptations')
        for row in lock['inputs']:
            if row['location'] != 'project': continue
            path=isolated/row['source'];path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes((ROOT/row['source']).read_bytes())
        self.assertFalse((isolated/'cache').exists());self.assertFalse((isolated/'.local').exists())
        first=builder.build(isolated,self.work/'build1')
        second=builder.build(isolated,self.work/'build2')
        self.assertEqual(first['manifest_sha256'],second['manifest_sha256'])
        self.assertEqual(first['sha256'],second['sha256'])
        self.assertFalse(first['full_x2_adaptation_complete'])
        self.assertEqual('partial-shared-adaptations',first['delivery_scope'])
        self.assertEqual(self.anchor,first['manifest_sha256'])

    def test_public_builder_rejects_changed_locked_source_before_output(self):
        lock=json.loads((builder.BRIDGE/'source-lock.json').read_bytes())
        isolated=self.work/'source-only';isolated.mkdir()
        copy_toon_sources(ADAPTATIONS,isolated/'adaptations')
        for row in lock['inputs']:
            if row['location'] != 'project': continue
            path=isolated/row['source'];path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes((ROOT/row['source']).read_bytes())
        (isolated/'adaptations/brands/onexplayer/x2-mini/current/battery_control.py').write_bytes(b'not reviewed')
        with self.assertRaisesRegex(ValueError,'differs from reviewed'):
            builder.build(isolated,self.work/'rejected-build')
        self.assertFalse((self.work/'rejected-build').exists())


class SlotSwitchLifecycle(CandidateFixture):
    def setUp(self):
        super().setUp()
        for name in shared.SLOT_SWITCH_TOOLS:
            path=self.root/'usr/bin'/name
            path.write_bytes(b'fixed synthetic executable, never run');path.chmod(0o755)
        self.slot=next(p for p in self.manifest['components'] if p['id']==shared.SLOT_SWITCH_COMPONENT)
        self.options['device_components']=[shared.SLOT_SWITCH_COMPONENT]
        self.boot_config=self.root/'esp/SteamOS/conf/A.conf'
        self.boot_config.parent.mkdir(parents=True);self.boot_config.write_bytes(b'unchanged A boot configuration')

    def plan(self, action='apply', repairs=None):
        if repairs is None and action=='apply': repairs=[shared.SLOT_SWITCH_COMPONENT]
        return super().plan(action,repairs)

    def originals(self):
        result={}
        for row in self.slot['files']:
            path=self.root/row['target'];path.parent.mkdir(parents=True,exist_ok=True)
            raw=('original '+row['target']).encode();path.write_bytes(raw)
            mode=0o600 if row['target'].startswith('home/deck/') else row['mode']
            path.chmod(mode)
            if os.name != 'nt' and row['target'].startswith('home/deck/'):os.chown(path,1000,1000)
            result[row['target']]=(raw,mode)
        return result

    def test_all_static_files_install_verify_and_restore_originals(self):
        before=self.originals();self.execute(self.plan())
        self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
        self.assertEqual(b'unchanged A boot configuration',self.boot_config.read_bytes())
        self.execute(self.plan('restore'))
        for target,(raw,mode) in before.items():
            path=self.root/target;self.assertEqual(raw,path.read_bytes())
            if os.name != 'nt':self.assertEqual(mode,path.stat().st_mode & 0o777)

    def test_absent_files_restore_without_touching_boot_configuration(self):
        self.execute(self.plan())
        for row in self.slot['files']:
            path=self.root/row['target'];self.assertTrue(path.is_file())
            if os.name != 'nt':
                self.assertEqual(row['mode'],path.stat().st_mode & 0o777)
                if row['target'].startswith('home/deck/'):
                    self.assertEqual(1000,path.parent.stat().st_uid)
                else:self.assertEqual(0,path.stat().st_uid)
        self.execute(self.plan('restore'))
        self.assertTrue(all(not (self.root/row['target']).exists() for row in self.slot['files']))
        self.assertEqual(b'unchanged A boot configuration',self.boot_config.read_bytes())

    def test_each_missing_tool_refuses_before_generic_core_write(self):
        raw=b'generic payload';name='slot-review-core.bin';(self.payload/'integration'/name).write_bytes(raw)
        record=dict(source=name,size=len(raw),sha256=sha(raw))
        component=dict(schema_version=1,kind='ComponentManifest',id='core:maintenance',version='0.1.0',files=[dict(record,target='usr/share/slot-review-core',mode=0o644)])
        json_file(self.payload/'integration/manifest.json',dict(schema=1,components={'core:maintenance':dict(available=True,files=[record],component=component)}))
        self.options['repairs']=['core:maintenance']
        for tool in shared.SLOT_SWITCH_TOOLS:
            with self.subTest(tool=tool):
                path=self.root/'usr/bin'/tool;raw=path.read_bytes();path.unlink()
                try:
                    with self.assertRaisesRegex(MaintenanceError,'dependency missing'):
                        self.install()
                    self.assertFalse((self.root/'usr/share/slot-review-core').exists())
                    self.assertFalse((self.root/'var').exists())
                finally:path.write_bytes(raw);path.chmod(0o755)

    def test_missing_home_is_read_only_preflight_refusal(self):
        (self.root/'home/deck').rmdir()
        with self.assertRaises(FileNotFoundError):self.install()
        self.assertFalse((self.root/'var').exists())
        self.assertFalse((self.root/'home/.oxp3-slot-switch').exists())

    def test_default_off_shape_and_exact_baseline(self):
        self.assertFalse(self.slot['default']);self.assertEqual('explicit-opt-in',self.slot['selection_policy'])
        changed=copy.deepcopy(self.slot);changed['files'][0]['target']='home/.oxp3-slot-switch/arbitrary.py'
        with self.assertRaisesRegex(MaintenanceError,'Unexpected'):
            shared.prepare_component_dirs(Engine(self.root,read_only=True),changed)
        (self.root/'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.10\nBUILD_ID=unaccepted\n')
        with self.assertRaisesRegex(MaintenanceError,'baseline'):self.install()
        self.assertFalse((self.root/'var').exists())

    def test_changed_managed_file_prevents_partial_restore(self):
        self.execute(self.plan());target=self.root/self.slot['files'][0]['target'];target.write_bytes(b'external code edit')
        snapshot={row['target']:(self.root/row['target']).read_bytes() for row in self.slot['files']}
        self.assertFalse(self.execute(self.plan('verify'))['result']['verified'])
        with self.assertRaisesRegex(MaintenanceError,'modified target'):self.execute(self.plan('restore'))
        self.assertEqual(snapshot,{name:(self.root/name).read_bytes() for name in snapshot})

    def test_os_change_and_missing_current_bundle_still_allow_retained_restore(self):
        self.execute(self.plan());shutil.rmtree(self.runtime/'cache')
        (self.root/'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.11\nBUILD_ID=other\n')
        self.assertTrue(self.execute(self.plan('verify'))['result']['verified'])
        self.execute(self.plan('restore'))
        self.assertFalse((self.root/'home/.oxp3-slot-switch/boot.py').exists())

    def test_known_gamescope_candidate_adds_slot_without_rewriting_prior_transaction(self):
        previous=self.work/'gamescope-previous';previous.mkdir()
        raw=(HERE/'fixtures/x2/manifest-gamescope.2.json').read_bytes();value=json.loads(raw)
        for row in value['files']:
            target=previous/row['path'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((self.package/row['path']).read_bytes())
        (previous/'manifest.json').write_bytes(raw)
        anchor='4765603630661f26e925be214d5da684bae81109240a23aa306333a2d99459e6'
        validate_package(previous,expected_manifest_sha256=anchor)
        runtime=self.work/'previous-runtime';self.bundle(previous,runtime)
        oldkw=dict(trust_local_test=True,runtime_root=runtime)
        plan=shared.plan(self.root,'device:x2-mini',[IDENTITY],**oldkw)
        shared.execute(self.root,plan,reviewed_sha256=plan['review_sha256'],**oldkw)
        transaction=Engine(self.root)._state()['components'][IDENTITY]['transaction']
        view=shared.options(self.root,'device:x2-mini',**self.kw)
        self.assertEqual([IDENTITY],view['current']['repairs'])
        self.assertIn(shared.SLOT_SWITCH_COMPONENT,[row['id'] for row in view['capabilities']])
        self.execute(self.plan())
        self.assertEqual(transaction,Engine(self.root)._state()['components'][IDENTITY]['transaction'])
        self.execute(self.plan('restore'));self.assertEqual({},Engine(self.root)._state()['components'])

    @unittest.skipIf(os.name == 'nt', 'Requires genuine Linux executable and permission checks')
    def test_nonexecutable_tool_and_unsafe_existing_privileged_mode_refuse(self):
        dependency=self.root/'usr/bin/pkexec';dependency.chmod(0o644)
        with self.assertRaisesRegex(MaintenanceError,'not executable'):self.install()
        dependency.chmod(0o755)
        privileged=self.root/'home/.oxp3-slot-switch/boot.py';privileged.parent.mkdir(parents=True)
        privileged.write_bytes(b'existing protected original');privileged.chmod(0o600)
        with self.assertRaisesRegex(MaintenanceError,'file permissions'):self.install()
        self.assertFalse((self.root/'var').exists())
        self.assertEqual(b'existing protected original',privileged.read_bytes())

    @unittest.skipIf(os.name == 'nt', 'Requires genuine Linux guest symlink and ownership checks')
    def test_tool_absolute_link_resolves_inside_image_and_missing_target_refuses(self):
        dependency=self.root/'usr/bin/python3';dependency.unlink()
        target=self.root/'usr/lib/x2-python';target.write_bytes(b'guest interpreter fixture');target.chmod(0o755)
        dependency.symlink_to('/usr/lib/x2-python')
        self.assertTrue(shared.check_slot_switch_component(Engine(self.root,read_only=True),self.slot))
        target.unlink()
        with self.assertRaisesRegex(MaintenanceError,'dependency missing'):self.install()
        self.assertFalse((self.root/'var').exists())

    @unittest.skipIf(os.name == 'nt', 'Full installer result journals require genuine Linux fcntl')
    def test_full_installer_static_tool_lifecycle(self):
        before=self.originals();result=self.install()
        installer.verify_device_components(self.root,self.payload,self.options,'A',result['components'])
        self.execute(self.plan('restore'))
        self.assertEqual({name:raw for name,(raw,_) in before.items()},{name:(self.root/name).read_bytes() for name in before})
        self.assertEqual(b'unchanged A boot configuration',self.boot_config.read_bytes())

if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0],*rest],verbosity=2)
