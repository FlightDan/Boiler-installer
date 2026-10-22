"""Root protection policy uses fake state or isolated native command fixtures."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from maintenance import Engine,MaintenanceError
from maintenance import live_root as live
from maintenance import adoption_blobs as blobs
from maintenance import __main__ as cli

IDENTITY={'partuuid':'12345678-abcd-1234-abcd-123456789abc','uuid':'abcdef01-2345-6789-abcd-0123456789ab'}


class FakeCommands:
    def __init__(self,status='enabled'):
        self.value=status;self.calls=[];self.root_identity=dict(IDENTITY);self.fail=None
    def identity(self): return copy.deepcopy(self.root_identity)
    def status(self): return self.value
    def set(self,value):
        self.calls.append(value)
        if self.fail == value: raise RuntimeError('fixture command failure')
        self.value=value
    def confirm_writable(self):
        if self.value != 'disabled': raise MaintenanceError('fixture remains read-only')


class LiveRootTests(unittest.TestCase):
    def setUp(self):
        if os.name != 'nt' and os.geteuid()!=0: self.skipTest('Protected root fixtures require root')
        work=tempfile.TemporaryDirectory();self.addCleanup(work.cleanup)
        self.work=Path(work.name).resolve();self.state=self.work/'state'
        self.commands=FakeCommands();self.window=live.ProtectionWindow(self.state,self.commands)

    def intent(self,original='enabled',phase='writable'):
        self.window.directory.mkdir(parents=True,mode=0o700,exist_ok=True)
        value=dict(schema_version=1,kind='SteamOSRootWriteIntent',id='a'*32,
                   root_identity=dict(IDENTITY),original=original,phase=phase)
        live._json_write(self.window.path,value)
        return value

    def test_enabled_root_has_durable_intent_before_disable_and_restores(self):
        old_set=self.commands.set
        def setting(value):
            self.assertTrue(self.window.path.is_file())
            record=live._intent(self.window.path)
            self.assertEqual(record['original'],'enabled')
            if value=='disabled': self.assertEqual(record['phase'],'prepared')
            old_set(value)
        with mock.patch.object(self.commands,'set',side_effect=setting):
            with self.window.open():
                self.assertEqual(self.commands.value,'disabled')
                self.assertEqual(live._intent(self.window.path)['phase'],'writable')
        self.assertEqual(self.commands.calls,['disabled','enabled'])
        self.assertFalse(self.window.path.exists())

    def test_initially_disabled_root_remains_disabled_even_on_body_failure(self):
        self.commands.value='disabled'
        with self.assertRaisesRegex(RuntimeError,'operation failed'):
            with self.window.open(): raise RuntimeError('operation failed')
        self.assertEqual(self.commands.value,'disabled')
        self.assertEqual(self.commands.calls,[])
        self.assertFalse(self.window.path.exists())

    def test_disable_failure_restores_original_state_and_never_yields(self):
        self.commands.fail='disabled'
        with self.assertRaisesRegex(RuntimeError,'command failure'):
            with self.window.open(): self.fail('must not yield')
        self.assertEqual(self.commands.value,'enabled')
        self.assertFalse(self.window.path.exists())

    def test_disable_failure_after_changing_protection_still_restores(self):
        original=self.commands.set
        def partial(value):
            original(value)
            if value=='disabled': raise RuntimeError('OS command failed after changing protection')
        with mock.patch.object(self.commands,'set',side_effect=partial):
            with self.assertRaisesRegex(RuntimeError,'after changing protection'):
                with self.window.open(): self.fail('must not yield')
        self.assertEqual(self.commands.calls,['disabled','enabled'])
        self.assertEqual(self.commands.value,'enabled')
        self.assertFalse(self.window.path.exists())

    def test_unknown_initial_status_or_identity_never_changes_protection(self):
        self.commands.value='unknown'
        with self.assertRaises(MaintenanceError):
            with self.window.open(): self.fail('must not yield')
        self.assertEqual(self.commands.calls,[])
        self.assertFalse(self.window.path.exists())
        self.commands.value='enabled';self.commands.root_identity={'partuuid':'','uuid':''}
        with self.assertRaises(MaintenanceError):
            with self.window.open(): self.fail('must not yield')
        self.assertEqual(self.commands.calls,[])

    def test_official_status_exit_codes_and_sysext_warning_are_exact(self):
        commands=live.RootCommands()
        for code,text,expected in ((0,'enabled','enabled'),(1,'disabled','disabled'),(1,live.SYSEXT_DISABLED,'disabled')):
            with self.subTest(code=code,text=text),mock.patch.object(commands,'_run',return_value=(code,text)):
                self.assertEqual(commands.status(),expected)
        for value in ((0,'disabled'),(1,'enabled'),(1,'unknown')):
            with mock.patch.object(commands,'_run',return_value=value),self.assertRaises(MaintenanceError):
                commands.status()

    def test_readonly_overlay_is_refused_and_original_protection_restored(self):
        with mock.patch.object(self.commands,'confirm_writable',side_effect=MaintenanceError('/usr/local still read-only')):
            with self.assertRaisesRegex(MaintenanceError,'still read-only'):
                with self.window.open(): self.fail('must not yield')
        self.assertEqual(self.commands.value,'enabled')
        self.assertFalse(self.window.path.exists())
        self.assertEqual(self.commands.calls,['disabled','enabled'])

    def test_restoration_still_runs_when_restoring_phase_cannot_be_persisted(self):
        writing=live._json_write
        def full(path,value):
            if value['phase']=='restoring': raise OSError('state filesystem full')
            return writing(path,value)
        with mock.patch.object(live,'_json_write',side_effect=full):
            with self.assertRaisesRegex(RuntimeError,'operation failed'):
                with self.window.open(): raise RuntimeError('operation failed')
        self.assertEqual(self.commands.value,'enabled')
        self.assertFalse(self.window.path.exists())

    def test_failed_restoration_preserves_intent_and_blocks_new_mutations(self):
        self.commands.fail='enabled'
        with self.assertRaisesRegex(MaintenanceError,'restoration failed'):
            with self.window.open(): pass
        self.assertEqual(self.commands.value,'disabled')
        self.assertEqual(live._intent(self.window.path)['phase'],'restore-failed')
        with self.assertRaisesRegex(MaintenanceError,'explicit recover'):
            with self.window.open(): self.fail('must not yield')
        self.commands.fail=None
        with self.window.open(recover=True): self.assertEqual(self.commands.value,'disabled')
        self.assertEqual(self.commands.value,'enabled')
        self.assertFalse(self.window.path.exists())

    def test_other_root_partition_cannot_recover_saved_intent(self):
        self.intent();self.commands.value='disabled'
        self.commands.root_identity['partuuid']='ffffffff-abcd-1234-abcd-123456789abc'
        with self.assertRaisesRegex(MaintenanceError,'original SteamOS root'):
            with self.window.open(recover=True): self.fail('must not yield')
        self.assertEqual(self.commands.calls,[])
        self.assertTrue(self.window.path.exists())

    def test_post_disable_identity_probe_failure_still_attempts_original_protection(self):
        def identity():
            if self.commands.calls: raise OSError('findmnt temporarily unavailable')
            return dict(IDENTITY)
        with mock.patch.object(self.commands,'identity',side_effect=identity):
            with self.assertRaisesRegex(MaintenanceError,'restoration failed'):
                with self.window.open(): self.fail('must not yield')
        self.assertEqual(self.commands.calls,['disabled','enabled'])
        self.assertEqual(self.commands.value,'enabled')
        self.assertEqual(live._intent(self.window.path)['phase'],'restore-failed')
        with self.window.open(recover=True): pass
        self.assertFalse(self.window.path.exists())

    def test_known_different_partition_never_uses_current_window_fallback(self):
        with self.assertRaisesRegex(MaintenanceError,'original SteamOS root'):
            with self.window.open():
                self.commands.root_identity['uuid']='ffffffff-2345-6789-abcd-0123456789ab'
        self.assertEqual(self.commands.calls,['disabled'])
        self.assertTrue(self.window.path.exists())

    @unittest.skipIf(os.name=='nt','POSIX live root aliases')
    def test_live_root_aliases_cannot_bypass_state_fence_before_mkdir(self):
        aliases=('/tmp/..','//',self.work/'live-link')
        aliases[-1].symlink_to('/',target_is_directory=True)
        for root in aliases:
            with self.subTest(root=str(root)):
                self.assertTrue(live.is_live_root(root))
                with self.assertRaisesRegex(MaintenanceError,'canonical state'):
                    Engine(root,self.state/'must-not-exist')
                self.assertFalse(self.state.exists())

    @unittest.skipIf(os.name=='nt','POSIX live root aliases')
    def test_root_alias_cli_verify_refuses_pending_intent_before_engine_construction(self):
        record=dict(schema_version=1,kind='SteamOSRootWriteIntent',id='e'*32,
                    root_identity=IDENTITY,original='enabled',phase='writable')
        for alias in ('/tmp/..','//'):
            output=io.StringIO()
            with mock.patch.object(live.os.path,'lexists',return_value=True),mock.patch.object(live,'_intent',return_value=record) as read_intent,mock.patch.object(cli,'Engine',side_effect=AssertionError('must refuse before Engine')),contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(['--root',alias,'verify','core:test']),2)
            self.assertIn('explicit recover',output.getvalue())
            read_intent.assert_called_once_with(Path('/var/lib/boiler/maintenance/platform/intent.json'))

    def test_recovery_clears_only_old_platform_intent_before_engine_barrier(self):
        old=self.intent();self.commands.value='disabled'
        barrier=self.state/'adoption-pending.json';barrier.write_bytes(b'protected adoption receipt required')
        with self.assertRaisesRegex(MaintenanceError,'exact receipt'):
            with self.window.open(recover=True):
                self.assertNotEqual(live._intent(self.window.path)['id'],old['id'])
                raise MaintenanceError('Resume adoption with exact receipt')
        self.assertEqual(barrier.read_bytes(),b'protected adoption receipt required')
        self.assertFalse(self.window.path.exists())
        self.assertEqual(self.commands.value,'enabled')
        with self.window.open(): pass  # Exact adoption resume is no longer trapped.

    def test_crash_between_old_intent_clear_and_new_window_leaves_root_protected(self):
        old=self.intent();self.commands.value='disabled'
        original=live._json_write
        def writing(path,value):
            if value['id'] != old['id']: raise OSError('crash before next intent')
            return original(path,value)
        with mock.patch.object(live,'_json_write',side_effect=writing):
            with self.assertRaisesRegex(OSError,'next intent'):
                with self.window.open(recover=True): self.fail('must not yield')
        self.assertEqual(self.commands.value,'enabled')
        self.assertFalse(self.window.path.exists())
        self.assertEqual(self.commands.calls,['enabled'])

    def test_initial_intent_write_failure_never_disables_root(self):
        with mock.patch.object(live,'_json_write',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                with self.window.open(): self.fail('must not yield')
        self.assertEqual(self.commands.calls,[])

    def test_platform_lock_refuses_concurrent_operation(self):
        with live._platform_lock(self.window.directory):
            with self.assertRaisesRegex(MaintenanceError,'operation is active'):
                with live._platform_lock(self.window.directory): self.fail('must not acquire')

    def test_pending_intent_inspection_is_readonly_and_uses_no_commands(self):
        self.intent();before={path:path.read_bytes() for path in self.state.rglob('*') if path.is_file()}
        with mock.patch.object(live,'is_live_root',return_value=True),mock.patch.object(live,'check_state_directory'),mock.patch.object(live,'RootCommands',side_effect=AssertionError('readonly')):
            result=live.pending(self.work,self.state)
        self.assertTrue(result['recovery_required'])
        self.assertEqual(before,{path:path.read_bytes() for path in self.state.rglob('*') if path.is_file()})

    def test_absent_platform_state_readonly_inspection_creates_nothing(self):
        with mock.patch.object(live,'is_live_root',return_value=True),mock.patch.object(live,'check_state_directory'):
            self.assertIsNone(live.pending(self.work,self.state))
        self.assertFalse(self.state.exists())

    def test_custom_live_state_is_refused_and_mounted_images_bypass_tools(self):
        with mock.patch.object(live,'is_live_root',return_value=True):
            with self.assertRaisesRegex(MaintenanceError,'canonical state'):
                live.check_state_directory('/',self.state)
        root=self.work/'mounted';root.mkdir();engine=Engine(root)
        with mock.patch.object(live,'RootCommands',side_effect=AssertionError('mounted target must not invoke root tools')):
            self.assertEqual(engine.recover(),[])
        self.assertFalse((engine.state_dir/'platform').exists())

    def engine_fixture(self):
        root=self.work/'target';(root/'etc').mkdir(parents=True)
        payload=self.work/'payload';payload.mkdir();(payload/'source').write_bytes(b'applied')
        component=dict(schema_version=1,kind='ComponentManifest',id='core:test',version='1.0.0',files=[dict(
            source='source',target='etc/test',size=7,sha256=hashlib.sha256(b'applied').hexdigest())])
        engine=Engine(root);engine.apply(component,payload)
        return root,engine

    @contextlib.contextmanager
    def fake_live(self):
        with mock.patch.object(live,'is_live_root',return_value=True),mock.patch.object(live,'check_state_directory'),mock.patch.object(live,'_check_host'),mock.patch.object(live,'RootCommands',return_value=self.commands):
            yield

    def test_cli_verify_is_readonly_and_rejects_pending_platform_recovery(self):
        root,engine=self.engine_fixture()
        with self.fake_live(),mock.patch.object(live,'RootCommands',side_effect=AssertionError('readonly')),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['--root',str(root),'verify','core:test']),0)
            window=live.ProtectionWindow(engine.state_dir,self.commands)
            window.directory.mkdir(mode=0o700)
            value=dict(schema_version=1,kind='SteamOSRootWriteIntent',id='b'*32,root_identity=IDENTITY,
                       original='enabled',phase='prepared')
            live._json_write(window.path,value)
            self.assertEqual(cli.main(['--root',str(root),'verify','core:test']),2)
        self.assertEqual(self.commands.calls,[])

    def test_inventory_reports_platform_barrier_without_verifying_or_writing(self):
        from maintenance import updates
        root,engine=self.engine_fixture()
        window=live.ProtectionWindow(engine.state_dir,self.commands)
        window.directory.mkdir(mode=0o700)
        live._json_write(window.path,dict(schema_version=1,kind='SteamOSRootWriteIntent',id='c'*32,
            root_identity=IDENTITY,original='enabled',phase='writable'))
        before={path:path.read_bytes() for path in engine.state_dir.rglob('*') if path.is_file()}
        with self.fake_live(),mock.patch.object(updates,'target_environment',return_value={}),mock.patch.object(Engine,'verify_readonly',side_effect=AssertionError('pending protection must block verification')):
            current=updates.inventory(root)
            self.assertTrue(current['pending_platform_recovery']['recovery_required'])
            self.assertFalse(current['components']['core:test']['verified'])
            with self.assertRaisesRegex(updates.UpdateError,'pending maintenance'):
                updates.plan_update(root,'unused-cache','unused-package','core','1.0.0')
        self.assertEqual(before,{path:path.read_bytes() for path in engine.state_dir.rglob('*') if path.is_file()})
        self.assertEqual(self.commands.calls,[])

    @unittest.skipIf(os.name=='nt','Live guard is Linux-only')
    def test_explicit_platform_recovery_preserves_actual_manual_hook_barrier(self):
        root,engine=self.engine_fixture()
        path=next(engine.journals.glob('*.json'));journal=json.loads(path.read_text())
        journal['status']='manual_recovery_required';live._json_write(path,journal)
        before=path.read_bytes()
        window=live.ProtectionWindow(engine.state_dir,self.commands)
        window.directory.mkdir(mode=0o700)
        live._json_write(window.path,dict(schema_version=1,kind='SteamOSRootWriteIntent',id='d'*32,
            root_identity=IDENTITY,original='enabled',phase='writable'))
        self.commands.value='disabled'
        with self.fake_live(),self.assertRaisesRegex(MaintenanceError,'manual recovery'):
            engine.recover()
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.commands.value,'enabled')
        self.assertFalse(window.path.exists())

    def test_plugin_restore_respects_engine_recovery_barrier_inside_guard(self):
        root,engine=self.engine_fixture()
        adapter=mock.Mock()
        with self.fake_live(),mock.patch('maintenance.plugins.PluginMaintenance',return_value=adapter),mock.patch.object(Engine,'_recover',side_effect=MaintenanceError('pending adoption requires receipt')),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['--root',str(root),'plugins','restore','decky']),2)
        adapter.restore.assert_not_called()
        self.assertEqual(self.commands.value,'enabled')
        self.assertFalse((engine.state_dir/'platform/intent.json').exists())

    @unittest.skipIf(os.name=='nt','Native descriptor flock borrowing')
    def test_nested_legacy_borrow_keeps_outer_lock_held(self):
        import fcntl
        root=self.work/'target';root.mkdir();store=root/'home/.oxp3-maintenance';store.mkdir(parents=True,mode=0o700)
        lock=store/'lock';lock.write_bytes(b'');lock.chmod(0o600);engine=Engine(root)
        with self.fake_live(),engine._lock():
            with blobs._legacy_lock(engine):
                with lock.open('rb') as contender:
                    with self.assertRaises(BlockingIOError): fcntl.flock(contender.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            with lock.open('rb') as contender:
                with self.assertRaises(BlockingIOError): fcntl.flock(contender.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.assertEqual(self.commands.value,'enabled')


@unittest.skipIf(os.name=='nt','Native trusted OS-command fixtures and abrupt child exit')
class NativeRootCommandTests(unittest.TestCase):
    def setUp(self):
        LiveRootTests.setUp(self)
        self.status_file=self.work/'readonly-state';self.status_file.write_text('enabled')
        self.program=self.work/'root-tool'
        script='''#!/bin/bash
set -eu
if [[ -n ${BASH_ENV-} || -n ${ENV-} || -n ${PYTHONPATH-} || -n ${LD_PRELOAD-} ]]; then exit 84; fi
if [[ $1 == -n ]]; then
  if [[ $3 == PARTUUID ]]; then printf '%s\\n' PART_ID; else printf '%s\\n' FS_ID; fi
elif [[ $1 == status ]]; then
  value=$(/bin/cat STATE_FILE);printf '%s\\n' "$value"
  if [[ $value == enabled ]]; then exit 0; else exit 1; fi
elif [[ $1 == enable ]]; then printf enabled > STATE_FILE
elif [[ $1 == disable ]]; then printf disabled > STATE_FILE
else exit 85; fi
'''.replace('PART_ID',shlex.quote(IDENTITY['partuuid'])).replace('FS_ID',shlex.quote(IDENTITY['uuid'])).replace('STATE_FILE',shlex.quote(str(self.status_file)))
        self.program.write_text(script);self.program.chmod(0o700)

    def test_native_commands_ignore_ambient_injection(self):
        marker=self.work/'injected';evil=self.work/'evil-env';evil.write_text('touch '+shlex.quote(str(marker)))
        alias=self.work/'steamos-readonly';alias.symlink_to(self.program)
        with mock.patch.object(live,'READONLY_PROGRAM',alias),mock.patch.object(live,'FINDMNT_PROGRAM',self.program),mock.patch.object(live,'PROTECTED_PREFIX',self.work),mock.patch.dict(os.environ,{'BASH_ENV':str(evil),'ENV':str(evil),'LD_PRELOAD':'/invalid/injection','PYTHONPATH':'/invalid/injection'}):
            commands=live.RootCommands();window=live.ProtectionWindow(self.state,commands)
            with window.open(): self.assertEqual(self.status_file.read_text(),'disabled')
        self.assertEqual(self.status_file.read_text(),'enabled');self.assertFalse(marker.exists())

    def test_official_sysext_echo_continuation_returns_one_line_and_exit_one(self):
        warning=self.work/'sysext-tool'
        # This is the official 3.8/3.10 status echo: two quoted arguments in
        # one continued shell command, rather than two echo invocations.
        warning.write_text('\n'.join(('#!/bin/bash',
            'echo "disabled, but loaded sysexts may result in" '+chr(92),
            '    "some parts of the filesystem being read-only"','exit 1','')))
        warning.chmod(0o700)
        with mock.patch.object(live,'READONLY_PROGRAM',warning):
            self.assertEqual(live.RootCommands().status(),'disabled')

    def test_untrusted_command_link_is_refused_before_execution(self):
        alias=self.work/'user-link';alias.symlink_to(self.program);os.chown(alias,1000,1000,follow_symlinks=False)
        with mock.patch.object(live,'READONLY_PROGRAM',alias),self.assertRaisesRegex(MaintenanceError,'command link'):
            live.RootCommands().status()
        self.assertEqual(self.status_file.read_text(),'enabled')

    def test_abrupt_child_exit_preserves_intent_and_explicit_recovery_restores(self):
        code=("import sys,os;from pathlib import Path;sys.path.insert(0,"+repr(str(ROOT))+ ");"
              "from maintenance import live_root as m;"
              "m.READONLY_PROGRAM=Path("+repr(str(self.program))+");m.FINDMNT_PROGRAM=m.READONLY_PROGRAM;"
              "m.PROTECTED_PREFIX=Path("+repr(str(self.work))+");"
              "w=m.ProtectionWindow(Path("+repr(str(self.state))+"));\n"
              "with w.open(): os._exit(91)\n")
        result=subprocess.run([sys.executable,'-I','-B','-c',code],check=False,timeout=30)
        self.assertEqual(result.returncode,91)
        self.assertEqual(self.status_file.read_text(),'disabled')
        self.assertEqual(live._intent(self.window.path)['phase'],'writable')
        with mock.patch.object(live,'READONLY_PROGRAM',self.program),mock.patch.object(live,'FINDMNT_PROGRAM',self.program),mock.patch.object(live,'PROTECTED_PREFIX',self.work):
            with live.ProtectionWindow(self.state).open(recover=True): pass
        self.assertEqual(self.status_file.read_text(),'enabled');self.assertFalse(self.window.path.exists())


if __name__=='__main__': unittest.main()
