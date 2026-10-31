"""Fixed keyboard dispatch against disposable roots, never host/device settings.

Portable tests inject only ownership and topology fixtures. Native enforcement
and real A/B shared-home mounts are exercised separately by the Linux fixture.
"""
import contextlib
import copy
import functools
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from x2_partial_fixture import copy_toon_sources

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/'components'),str(ROOT/'guest')]
from maintenance import keyboard_models as api
from maintenance import keyboard_contract as contract
from maintenance.keyboard_runtime import service
from maintenance.keyboard_runtime.merge import merge
from maintenance.engine import Engine
from protocol import manifest as protocol_manifest
import integration


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def write(path,data,mode=0o644,uid=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data)
    if os.name == 'posix':
        path.chmod(mode)
        if uid is not None: os.chown(path,uid,uid)
    return path


def target(root):
    write(root/'etc/passwd',b'root:x:0:0:root:/root:/bin/sh\ndeck:x:1000:1000:deck:/home/deck:/bin/bash\n')
    write(root/'etc/os-release',b'ID=steamos\nVERSION_ID=3.10\nBUILD_ID=20260924.1000\n')
    elf=bytearray(20); elf[:4]=b'\x7fELF';elf[5]=1;elf[18:20]=(62).to_bytes(2,'little')
    write(root/'usr/bin/env',bytes(elf))
    write(root/service.DESKTOP,b'[Desktop Entry]\nName=Fixture keyboard\nExec=/bin/false\n')
    (root/'usr/lib/modules'/ '7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9').mkdir(parents=True,exist_ok=True)
    (root/'home/deck').mkdir(parents=True,exist_ok=True)
    if os.name == 'posix': os.chown(root/'home/deck',1000,1000)


def source_release(root):
    return {'ID':'steamos','VERSION_ID':'3.10','BUILD_ID':'20260924.1000',
            'KERNELS':['7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9']}


def bundle(package,runtime):
    destination=runtime/'cache/model-bundles/packages/x2-mini'
    shutil.copytree(package,destination)
    value=json.loads((package/'manifest.json').read_bytes())
    write(runtime/'cache/model-bundles/manifest.json',json.dumps(dict(schema_version=1,kind='LocalTestModelBundles',
        packages=[dict(id='device:x2-mini',directory='cache/model-bundles/packages/x2-mini',version=value['version'],
        manifest_sha256=hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest())])).encode())


class KeyboardPortable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='keyboard-portable-',dir=ROOT.parent)
        cls.work=Path(cls.temp.name)
        builder=module('keyboard_x2_builder',ROOT/'adaptations/brands/onexplayer/x2-mini/bridge/scripts/build.py')
        partial=cls.work/'historical-partial-adaptations'
        copy_toon_sources(ROOT/'adaptations',partial)
        builder.build(ROOT,cls.work/'build',partial)
        cls.package=cls.work/'build/package'
        cls.component=next(c for c in json.loads((cls.package/'manifest.json').read_bytes())['components'] if c['id']==contract.IDENTITY)
        cls.runtime=cls.work/'runtime';bundle(cls.package,cls.runtime)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def setUp(self):
        self.case=tempfile.TemporaryDirectory(prefix='case-',dir=self.work);self.addCleanup(self.case.cleanup)
        self.root=Path(self.case.name)/'image';self.root.mkdir();target(self.root)
        self.addCleanup(patch.stopall)
        real=service.Keyboard
        fake=functools.partial(real,enforce_owner=False)
        patch.object(api,'Keyboard',fake).start()
        self.keyboard=fake(self.root)
        def bound(root,expected_label=None):
            label=expected_label or 'A';info=(Path(root)/'home').stat()
            return dict(slot='partuuid-11111111-'+label.lower()*8,label=label,partuuid='11111111-'+label.lower()*8,
                home=dict(device=info.st_dev,inode=info.st_ino,uuid='fixture-home',source='fixture',major_minor='0:1'))
        patch.object(service,'binding',bound).start();patch.object(api,'binding',bound).start()
        patch.object(api.models,'_privileged',lambda:None).start()
        self.kw=dict(trust_local_test=True,runtime_root=self.runtime)

    def plan(self,action='apply'):
        return api.plan(self.root,'device:x2-mini',[contract.IDENTITY] if action=='apply' else [],action=action,**self.kw)

    def execute(self,action='apply'):
        plan=self.plan(action)
        return api.execute(self.root,plan,reviewed_sha256=plan['review_sha256'],**self.kw)

    def test_actual_authenticated_package_selects_only_fixed_host_handler(self):
        contract.check(self.component)
        bad=copy.deepcopy(self.component);bad['hooks']={'apply':'scripts/evil.sh'}
        with self.assertRaisesRegex(RuntimeError,'contract'): contract.check(bad)
        bad=copy.deepcopy(self.component);bad['program']='evil.py'
        with self.assertRaisesRegex(RuntimeError,'code'): contract.check(bad)
        bad=copy.deepcopy(self.component);bad['dynamic_handler']='arbitrary-module'
        with self.assertRaises(RuntimeError): contract.check(bad)
        with patch.object(protocol_manifest,'ENGINE_VERSION','0.1.0'):
            self.assertFalse(protocol_manifest.compatible(self.component,service.BASELINE))

    def test_missing_default_and_trust_never_authorize_a_write(self):
        self.assertFalse(api.options(self.root,'device:x2-mini',**self.kw)['capabilities'][0]['default'])
        with self.assertRaisesRegex(RuntimeError,'trust'):
            api.plan(self.root,'device:x2-mini',[contract.IDENTITY])
        with self.assertRaisesRegex(RuntimeError,'Select only'):
            api.plan(self.root,'device:x2-mini',[],**self.kw)
        self.assertFalse((self.root/service.TARGET).exists())

    def test_maintenance_apply_verify_and_restore_exact_original(self):
        original=b'# original\r\n[Wayland]\r\nInputMethod=custom\r\nScale=1.2\r\n'
        path=write(self.root/service.TARGET,original,0o600,1000)
        self.execute();self.assertTrue(self.execute('verify')['result']['verified'])
        self.execute('restore');self.assertEqual(original,path.read_bytes())

    def test_whole_edited_file_is_preserved_and_not_reported_restored(self):
        path=write(self.root/service.TARGET,b'[Wayland]\nScale=1\n',0o600,1000)
        self.execute();edited=path.read_bytes().replace(b'Scale=1',b'Scale=2');path.write_bytes(edited)
        self.assertFalse(self.execute('verify')['result']['verified'])
        result=self.execute('restore')['result']['components'][0]
        self.assertTrue(result['user_edits_preserved']);self.assertFalse(result['configuration_restored'])
        self.assertEqual(edited,path.read_bytes())

    def test_indented_next_section_never_receives_keyboard_keys(self):
        data=b'[Wayland]\nScale=1\n  [Unrelated]\nInputMethod=keep\n'
        result=merge(data,'Wayland',service.VALUES)
        before,after=result.split(b'  [Unrelated]')
        self.assertIn(b'VirtualKeyboardMode=1',before);self.assertIn(b'InputMethod=keep',after)
        self.assertNotIn(b'VirtualKeyboardMode',after)
        for data in (b'[Wayland]\n[Wayland]\n',b'[Wayland]\nInputMethod=a\nInputMethod=b\n',b'[Wayland][$i]\n'):
            with self.assertRaises(ValueError):merge(data,'Wayland',service.VALUES)

    def test_present_desktop_is_not_claimed_executable_or_activated(self):
        # The public contract is presence, not an invented exact Exec policy.
        self.assertTrue(api.options(self.root,'device:x2-mini',**self.kw)['install_available'])
        with patch('subprocess.run',side_effect=AssertionError('No guest program')):
            result=self.execute()['result']['components'][0]
        self.assertFalse(result['runtime_validated']);self.assertIn('not validated',result['dependency_scope'])
        (self.root/service.DESKTOP).unlink()
        self.assertFalse(api.options(self.root,'device:x2-mini',**self.kw)['install_available'])
        self.execute('restore')

    def test_keyboard_has_no_invented_release_build_gate_and_retained_lifecycle_needs_no_cache(self):
        write(self.root/'etc/os-release',b'ID=steamos\nVERSION_ID=3.11\nBUILD_ID=another\n')
        self.assertTrue(api.options(self.root,'device:x2-mini',**self.kw)['install_available'])
        self.execute()
        offline=dict(self.kw,runtime_root=Path(self.case.name)/'no-cache')
        self.assertTrue(api.options(self.root,'device:x2-mini',**offline)['install_available'])
        review=api.plan(self.root,'device:x2-mini',[contract.IDENTITY],**offline)
        api.execute(self.root,review,reviewed_sha256=review['review_sha256'],**offline)
        write(self.root/'etc/os-release',b'ID=other\nVERSION_ID=1\n')
        self.assertFalse(api.options(self.root,'device:x2-mini',**offline)['install_available'])
        review=api.plan(self.root,'device:x2-mini',[],action='restore',**offline)
        api.execute(self.root,review,reviewed_sha256=review['review_sha256'],**offline)

    def test_pending_source_partition_is_reported_and_other_slot_cannot_offer_recovery(self):
        review=self.plan()
        with patch.object(service.Store,'_after_write',side_effect=RuntimeError('power loss')):
            with self.assertRaisesRegex(RuntimeError,'power loss'):
                api.execute(self.root,review,reviewed_sha256=review['review_sha256'],**self.kw)
        pending=self.root/service.STATE/'pending.json';saved=pending.read_bytes()
        original=api.binding(self.root)
        other={**original,'slot':'partuuid-22222222-bbbbbbbb','label':'B','partuuid':'22222222-bbbbbbbb'}
        with patch.object(api,'binding',lambda root,expected_label=None:other):
            options=api.options(self.root,'device:x2-mini',**self.kw)
            self.assertEqual(original['slot'],options['pending']['slot'])
            self.assertFalse(options['pending']['recover_available'])
            with self.assertRaisesRegex(RuntimeError,'original slot'):
                api.plan(self.root,'device:x2-mini',[],action='recover',**self.kw)
        self.assertEqual(saved,pending.read_bytes())

    def test_corrupt_pending_slot_is_displayed_without_type_errors_or_recovery_writes(self):
        review=self.plan()
        with patch.object(service.Store,'_after_write',side_effect=RuntimeError('power loss')):
            with self.assertRaisesRegex(RuntimeError,'power loss'):
                api.execute(self.root,review,reviewed_sha256=review['review_sha256'],**self.kw)
        pending=self.root/service.STATE/'pending.json';original=json.loads(pending.read_bytes())
        target=(self.root/service.TARGET).read_bytes()
        for value in (None,42):
            with self.subTest(slot=value):
                damaged={**original,'slot':value};pending.write_text(json.dumps(damaged),encoding='utf-8');saved=pending.read_bytes()
                options=api.options(self.root,'device:x2-mini',**self.kw)
                self.assertFalse(options['pending']['recover_available'])
                self.assertIn('corrupt',options['pending']['reason']['en'])
                with self.assertRaisesRegex(RuntimeError,'corrupt'):
                    api.plan(self.root,'device:x2-mini',[],action='recover',**self.kw)
                self.assertEqual(saved,pending.read_bytes())
                self.assertEqual(target,(self.root/service.TARGET).read_bytes())

    def test_ui_displays_pending_origin_and_does_not_offer_wrong_slot_recovery(self):
        from maintenance.model_ui import ModelUI
        menus=[]
        class Dialog:
            def details(self,text):return True
            def menu(self,text,rows):menus.append((text,rows));return 'back'
        options=dict(executable=True,install_available=False,dynamic_adapter='keyboard',shared_adaptations=True,
            capabilities=[],current=None,pending=dict(source='keyboard-store',slot='partuuid-11111111-aaaaaaaa',
                recover_available=False,reason={'en':'Return to original slot partuuid-11111111-aaaaaaaa.'}))
        ModelUI(Dialog(),None).actions(dict(id='device:x2-mini',name={'en':'X2'},components=[]),options)
        self.assertIn('partuuid-11111111-aaaaaaaa',menus[0][0])
        self.assertNotIn('recover',{row[0] for row in menus[0][1]})

    def test_stale_target_and_tampered_plan_are_rejected(self):
        path=write(self.root/service.TARGET,b'[Wayland]\nScale=1\n',0o600,1000);review=self.plan()
        path.write_bytes(b'[Wayland]\nScale=2\n')
        with self.assertRaisesRegex(RuntimeError,'review again'):
            api.execute(self.root,review,reviewed_sha256=review['review_sha256'],**self.kw)
        review=self.plan();review['action']='restore'
        with self.assertRaisesRegex(RuntimeError,'digest'):
            api.execute(self.root,review,reviewed_sha256=review['review_sha256'],**self.kw)

    def test_first_merge_does_not_overwrite_a_desktop_edit_before_store_capture(self):
        path=write(self.root/service.TARGET,b'[Wayland]\nScale=1\n',0o600,1000)
        review=self.keyboard.plan('apply','A');real=service._merge_group;calls=[]
        edited=b'[Wayland]\nScale=2\n# concurrent desktop edit\n'
        def concurrently_edit(data,*args):
            merged=real(data,*args);calls.append(True)
            if len(calls)==2:path.write_bytes(edited)
            return merged
        with patch.object(service,'_merge_group',concurrently_edit):
            with self.assertRaisesRegex(RuntimeError,'before journal commit'):
                self.keyboard.execute(review,reviewed_sha256=review['review_sha256'])
        self.assertEqual(edited,path.read_bytes())
        self.assertFalse((self.root/service.STATE/'index.json').exists())
        self.assertFalse((self.root/service.STATE/'pending.json').exists())

    def test_desktop_edit_after_journal_before_execute_is_kept_with_pending_conflict(self):
        path=write(self.root/service.TARGET,b'[Wayland]\nScale=1\n',0o600,1000)
        review=self.keyboard.plan('apply','A');real=service.PublicStore._execute
        edited=b'[Wayland]\nScale=3\n# edit after durable journal\n'
        def concurrently_edit(store,pending,slot=None):
            self.assertTrue((self.root/service.STATE/'pending.json').is_file())
            path.write_bytes(edited)
            return real(store,pending,slot)
        with patch.object(service.PublicStore,'_execute',concurrently_edit):
            with self.assertRaisesRegex(RuntimeError,'Recovery conflict'):
                self.keyboard.execute(review,reviewed_sha256=review['review_sha256'])
        self.assertEqual(edited,path.read_bytes())
        self.assertTrue((self.root/service.STATE/'pending.json').exists())
        self.assertFalse((self.root/service.STATE/'index.json').exists())

    def test_interruption_needs_explicit_recover_and_preserves_conflicts(self):
        review=self.plan()
        with patch.object(service.Store,'_after_write',side_effect=RuntimeError('power loss')):
            with self.assertRaisesRegex(RuntimeError,'power loss'):
                api.execute(self.root,review,reviewed_sha256=review['review_sha256'],**self.kw)
        with self.assertRaisesRegex(RuntimeError,'recover'):
            self.plan()
        self.execute('recover');self.assertTrue(self.execute('verify')['result']['verified'])
        self.execute('restore')

    def test_home_only_handler_does_not_open_root_protection_window(self):
        with patch('maintenance.live_root.write_scope',side_effect=AssertionError('root window')):
            self.execute();self.execute('restore')
        opened=[]
        @contextlib.contextmanager
        def window(engine,**kwargs):opened.append(True);yield
        with patch('maintenance.live_root.write_scope',window):
            with Engine(self.root)._lock():pass
        self.assertEqual([True],opened)

    def test_generic_engine_cannot_create_a_second_keyboard_ledger(self):
        with self.assertRaisesRegex(RuntimeError,'fixed host'):
            Engine(self.root).apply(self.component,self.package,environment=service.BASELINE)
        part=dict(self.component,id='test:static',files=[dict(source='evil',target=service.TARGET,size=0,sha256='0'*64)],dynamic_handler=None)
        with self.assertRaisesRegex(RuntimeError,'unique recovery ledger'):
            Engine(self.root)._apply_locked(part,self.package,{},Engine(self.root)._state(),'1'*32,None,False)

    def test_fixed_cli_routes_real_review_and_restore(self):
        from maintenance.__main__ import main
        offered=api._offered
        with patch.object(api,'_offered',lambda runtime:offered(self.runtime)):
            for action in ('apply','verify','restore'):
                args=['--root',str(self.root),'models','keyboard-plan','device:x2-mini','--action',action,'--trust-local-test']
                if action=='apply':args+=['--repair',contract.IDENTITY]
                with patch.object(sys,'stdout',io.StringIO()) as output:
                    self.assertEqual(0,main(args));review=json.loads(output.getvalue())['result']
                file=Path(self.case.name)/'review.json';file.write_text(json.dumps(review),encoding='utf-8')
                with patch.object(sys,'stdout',io.StringIO()) as output:
                    self.assertEqual(0,main(['--root',str(self.root),'models','keyboard-execute',str(file),'--reviewed-sha256',review['review_sha256'],'--trust-local-test']))
                    self.assertTrue(json.loads(output.getvalue())['ok'])


class KeyboardSourceBuilder(unittest.TestCase):
    def test_original_four_component_definitions_keep_their_system_constraints(self):
        catalog=module('keyboard_static_constraint_catalog',ROOT/'adaptations/scripts/toon_catalog.py')
        current=catalog.load(ROOT/'adaptations/brands/onexplayer/common/components.toon',ROOT/'adaptations')['definitions']
        pins=json.loads((ROOT/'tests/fixtures/x2/keyboard-static-baseline.json').read_bytes())
        for name,pin in pins.items():
            with self.subTest(name=name):
                self.assertEqual(pin,hashlib.sha256(json.dumps(current[name],sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest())

    def test_fixed_inventory_is_reproducible_and_refuses_extra_or_changed_inputs(self):
        builder=module('keyboard_fixed_builder',ROOT/'adaptations/scripts/build-keyboard-runtime.py')
        lock=json.loads((ROOT/builder.LOCK).read_bytes())
        with tempfile.TemporaryDirectory(prefix='keyboard-build-',dir=ROOT.parent) as tmp:
            tmp=Path(tmp);root=tmp/'source'
            for row in lock['runtime_sources']:
                write(root/row['source'],(ROOT/row['source']).read_bytes())
            write(root/builder.LOCK,(ROOT/builder.LOCK).read_bytes())
            self.assertEqual(builder.build(root,tmp/'one'),builder.build(root,tmp/'two'))
            extra=write(root/'maintenance/keyboard_runtime/unreviewed.txt',b'unreviewed research must not enter')
            with self.assertRaisesRegex(ValueError,'Unreviewed extra'):builder.build(root,tmp/'bad-extra')
            self.assertFalse((tmp/'bad-extra').exists());extra.unlink()
            edited=copy.deepcopy(lock);edited['runtime_sources'][0]['source']='../outside-base'
            write(root/builder.LOCK,json.dumps(edited).encode())
            with self.assertRaisesRegex(ValueError,'independent pin'):builder.build(root,tmp/'bad-lock')
            self.assertFalse((tmp/'bad-lock').exists())

    def test_source_export_requires_the_entire_fixed_host_runtime(self):
        # The current full model no longer names the earlier keyboard material.
        # The fixed host runtime is still in maintenance: check that actual
        # payload inventory and its public sources independently of model TOON.
        exporter=module('keyboard_source_export',ROOT/'scripts/export-source.py')
        fixed=module('keyboard_fixed_export_inventory',ROOT/'adaptations/scripts/build-keyboard-runtime.py')
        package=module('keyboard_maintenance_export_inventory',ROOT/'scripts/package-maintenance.py')
        from maintenance import engine as runtime_engine
        files=set(exporter.reviewed_files(ROOT))
        inventory=json.loads((ROOT/fixed.LOCK).read_bytes())
        runtime={row['source'] for row in inventory['runtime_sources']}
        required=runtime|{fixed.LOCK,'adaptations/scripts/build-keyboard-runtime.py'}
        self.assertFalse(required-files,'Fixed keyboard public sources omitted: '+str(sorted(required-files)))
        installed={path.relative_to(ROOT.resolve()).as_posix() for path in runtime_engine.runtime_files(ROOT)}
        packaged=set(package.runtime_inventory(ROOT))
        self.assertFalse(runtime-installed,'Installer maintenance runtime omitted: '+str(sorted(runtime-installed)))
        self.assertFalse(runtime-packaged,'Maintenance package omitted: '+str(sorted(runtime-packaged)))


if __name__=='__main__':unittest.main()

