"""Isolated fixtures only: every subprocess is mocked and ROOT is temporary."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('battery_sleep_test', Path(__file__).with_name('sleep_control.py'))
sleep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sleep)


class SleepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(sleep, 'ROOT', self.root).start()
        if os.name != 'posix':
            stub = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8, flock=lambda *args: None)
            patch.dict(sys.modules, {'fcntl': stub}).start()
        self.commands = []
        patch.object(sleep.subprocess, 'run', side_effect=self.command).start()
        self.write('sys/power/state', 'freeze mem disk\n')
        self.write('sys/power/mem_sleep', 's2idle [deep]\n')
        self.write('sys/power/resume', '0:0\n')
        self.write('sys/power/resume_offset', '0\n')
        self.write(sleep.HELPER, '#!/bin/sh\n').chmod(0o755)
        self.write('usr/lib/initcpio/hooks/resume', '/usr/lib/systemd/systemd-hibernate-resume\n')
        self.write(sleep.ROUTER, sleep.ROUTER_TEXT)
        (self.root / 'sys/firmware/efi').mkdir(parents=True)
        (self.root / 'sys/class/rtc/rtc0').mkdir(parents=True)

    def write(self, relative, content):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        return target

    def command(self, args, **kwargs):
        self.commands.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    def policy(self, mode='auto', delay=120):
        self.write(sleep.POLICY, json.dumps({'mode': mode, 'delay_minutes': delay}))

    def test_default_is_inherited_with_two_hour_delay_and_no_writes(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = sleep.status()
        self.assertEqual((result['mode'], result['delay_minutes']), ('system', 120))
        self.assertEqual(result['available_modes'], list(sleep.MODES))
        self.assertEqual(result['delay_options'], [20, 60, 120, 720, 1440])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(self.commands, [])

    def test_invalid_preferences_never_write(self):
        for mode, delay in [('bad', 120), ('auto', True), ('auto', 0), ('auto', 121), ('auto', '120')]:
            with self.subTest(mode=mode, delay=delay), self.assertRaises(ValueError):
                sleep.save(mode, delay)
        self.assertFalse((self.root / sleep.POLICY).exists())
        self.assertEqual(self.commands, [])

    def test_save_and_reload_preserve_preference_without_sleep_or_sysfs_writes(self):
        with patch.object(sleep, 'run') as run:
            for mode in sleep.MODES:
                result = sleep.save(mode, 120)
                self.assertEqual(result['mode'], mode)
                self.assertEqual(sleep.read_policy(), {'mode': mode, 'delay_minutes': 120})
            run.assert_not_called()
        self.assertEqual(self.commands, [])
        self.assertEqual((self.root / 'sys/power/mem_sleep').read_text(), 's2idle [deep]\n')
        self.assertFalse((self.root / sleep.RUNTIME).exists())

    def test_save_requires_router_and_supported_mode(self):
        (self.root / sleep.ROUTER).unlink()
        with self.assertRaisesRegex(RuntimeError, 'integration'):
            sleep.save('auto', 120)
        self.write(sleep.ROUTER, sleep.ROUTER_TEXT)
        (self.root / sleep.HELPER).unlink()
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            sleep.save('hibernate', 120)
        self.assertFalse((self.root / sleep.POLICY).exists())

    def test_failed_atomic_replace_preserves_previous_preference(self):
        self.policy('system')
        with patch.object(sleep.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                sleep.save('auto', 120)
        self.assertEqual(sleep.read_policy()['mode'], 'system')
        self.assertEqual(list((self.root / sleep.POLICY).parent.glob('.sleep-policy-*')), [])

    def test_corrupt_policy_is_not_silently_replaced(self):
        self.write(sleep.POLICY, 'broken')
        with self.assertRaises(ValueError):
            sleep.save('auto', 120)
        self.assertEqual((self.root / sleep.POLICY).read_text(), 'broken')

    def test_default_run_only_passes_through_original_suspend(self):
        sleep.run()
        self.assertEqual([args for args, _ in self.commands], [[str(self.root / sleep.SLEEP), 'suspend']])
        self.assertFalse((self.root / sleep.RUNTIME).exists())

    def test_s2idle_runtime_config_and_previous_memory_mode_restored(self):
        self.policy('s2idle')
        def execute(args, **kwargs):
            self.assertIn('MemorySleepMode=s2idle', (self.root / sleep.RUNTIME).read_text())
            self.write('sys/power/mem_sleep', '[s2idle] deep\n')
            return self.command(args, **kwargs)
        sleep.subprocess.run.side_effect = execute
        sleep.run()
        self.assertEqual((self.root / 'sys/power/mem_sleep').read_text().strip(), 'deep')
        self.assertEqual(self.commands[0][0][-1], 'suspend')
        self.assertFalse((self.root / sleep.RUNTIME).exists())

    def test_direct_s4_prepares_and_cleans_up(self):
        self.policy('hibernate')
        sleep.run()
        self.assertEqual([args[-1] for args, _ in self.commands], ['create', 'hibernate', 'cleanup'])
        self.assertFalse((self.root / sleep.RUNTIME).exists())

    def test_auto_two_hours_uses_real_suspend_then_hibernate_and_cleans_early_wake(self):
        self.policy()
        def execute(args, **kwargs):
            text = (self.root / sleep.RUNTIME).read_text()
            self.assertIn('HibernateDelaySec=120min\n', text)
            self.assertIn('HibernateOnACPower=no\n', text)
            self.assertIn('MemorySleepMode=s2idle\n', text)
            self.assertGreater(Path(sleep.RUNTIME).name, 'steamos-suspend-then-hibernate.conf')
            return self.command(args, **kwargs)
        sleep.subprocess.run.side_effect = execute
        sleep.run()
        self.assertEqual([args[-1] for args, _ in self.commands], ['create', 'suspend-then-hibernate', 'cleanup'])
        self.assertFalse((self.root / sleep.RUNTIME).exists())

    def test_selected_delay_is_used(self):
        self.policy(delay=240)
        self.assertIn('HibernateDelaySec=240min', sleep.configuration(sleep.read_policy()))

    def test_new_delays_are_saved_and_used_in_systemd_configuration(self):
        for delay in sleep.DELAY_OPTIONS:
            with self.subTest(delay=delay):
                result = sleep.save('auto', delay)
                self.assertEqual(result['delay_minutes'], delay)
                self.assertIn(f'HibernateDelaySec={delay}min\n', sleep.configuration(sleep.read_policy()))
        self.assertEqual(self.commands, [])

    def test_legacy_delays_remain_readable_and_configurable(self):
        for delay in sleep.LEGACY_DELAYS:
            with self.subTest(delay=delay):
                self.policy(delay=delay)
                self.assertEqual(sleep.read_policy()['delay_minutes'], delay)
                self.assertIn(f'HibernateDelaySec={delay}min\n', sleep.configuration(sleep.read_policy()))
                self.assertEqual(sleep.save('auto', delay)['delay_minutes'], delay)

    def test_24_hour_delay_keeps_existing_systemd_suspend_then_hibernate_path(self):
        self.policy(delay=1440)
        def execute(args, **kwargs):
            if args[-1] == 'suspend-then-hibernate':
                text = (self.root / sleep.RUNTIME).read_text()
                self.assertIn('AllowSuspendThenHibernate=yes\n', text)
                self.assertIn('HibernateDelaySec=1440min\n', text)
                self.assertIn('HibernateOnACPower=no\n', text)
            return self.command(args, **kwargs)
        sleep.subprocess.run.side_effect = execute
        sleep.run()
        self.assertEqual([args[-1] for args, _ in self.commands], ['create', 'suspend-then-hibernate', 'cleanup'])
        self.assertFalse((self.root / sleep.RUNTIME).exists())

    def test_sleep_failure_restores_resume_and_cleans_runtime(self):
        self.policy()
        def execute(args, **kwargs):
            self.command(args, **kwargs)
            if args[-1] == 'create':
                self.write('sys/power/resume', '259:14')
                self.write('sys/power/resume_offset', '42')
            if args[-1] == 'suspend-then-hibernate':
                raise subprocess.CalledProcessError(1, args)
        sleep.subprocess.run.side_effect = execute
        with self.assertRaises(subprocess.CalledProcessError):
            sleep.run()
        self.assertEqual(self.commands[-1][0][-1], 'cleanup')
        self.assertEqual((self.root / 'sys/power/resume').read_text().strip(), '0:0')
        self.assertEqual((self.root / 'sys/power/resume_offset').read_text().strip(), '0')
        self.assertFalse((self.root / sleep.RUNTIME).exists())

    def test_swap_preparation_failure_never_sleeps_and_restores_resume(self):
        self.policy()
        def execute(args, **kwargs):
            self.command(args, **kwargs)
            self.write('sys/power/resume', '259:14')
            raise subprocess.CalledProcessError(1, args)
        sleep.subprocess.run.side_effect = execute
        with self.assertRaises(subprocess.CalledProcessError):
            sleep.run()
        self.assertEqual(len(self.commands), 1)
        self.assertEqual((self.root / 'sys/power/resume').read_text().strip(), '0:0')
        self.assertFalse((self.root / sleep.RUNTIME).exists())

    def test_cleanup_failure_still_removes_runtime(self):
        self.policy()
        def execute(args, **kwargs):
            self.command(args, **kwargs)
            if args[-1] == 'cleanup':
                raise subprocess.CalledProcessError(1, args)
        sleep.subprocess.run.side_effect = execute
        with self.assertRaises(subprocess.CalledProcessError):
            sleep.run()
        self.assertFalse((self.root / sleep.RUNTIME).exists())

    def test_existing_runtime_file_is_never_overwritten(self):
        self.policy()
        self.write(sleep.RUNTIME, 'another owner')
        with self.assertRaises(FileExistsError):
            sleep.run()
        self.assertEqual((self.root / sleep.RUNTIME).read_text(), 'another owner')
        self.assertEqual(self.commands, [])

    def test_runtime_write_or_close_failure_removes_only_our_file(self):
        self.policy()
        original_open = Path.open
        for failure in ('write', 'close'):
            with self.subTest(failure=failure):
                self.write('sys/power/mem_sleep', 's2idle [deep]\n')
                def injected_open(target, *args, **kwargs):
                    stream = original_open(target, *args, **kwargs)
                    if target != self.root / sleep.RUNTIME:
                        return stream
                    class BrokenStream:
                        def __enter__(self):
                            return self
                        def write(self, value):
                            if failure == 'write':
                                raise OSError('full tmpfs')
                            return stream.write(value)
                        def __exit__(self, *exc):
                            stream.close()
                            if failure == 'close':
                                raise OSError('close failed')
                    return BrokenStream()
                with patch.object(Path, 'open', injected_open), self.assertRaises(OSError):
                    sleep.run()
                self.assertFalse((self.root / sleep.RUNTIME).exists())
                self.assertEqual(self.commands, [])

    def test_router_template_matches_and_rpc_has_no_sleep_entrypoint(self):
        self.assertEqual(Path(__file__).with_name('sleep-router.conf').read_text(), sleep.ROUTER_TEXT)
        backend = Path(__file__).with_name('battery-plugin-main.py').read_text()
        self.assertIn('get_sleep_status', backend)
        self.assertIn('set_sleep_policy', backend)
        self.assertNotIn('sleep.run', backend)


if __name__ == '__main__':
    unittest.main()
