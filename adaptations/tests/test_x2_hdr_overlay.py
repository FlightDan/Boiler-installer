import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
import types
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'brands' / 'onexplayer' / 'x2-mini' / 'scripts' / 'build-x2-hdr-overlay.py'
spec = importlib.util.spec_from_file_location('x2_overlay', SCRIPT)
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)

class OverlayRegression(unittest.TestCase):
    def test_real_pinned_base(self):
        base = os.environ.get('BOILER_X2_OVERLAY_BASE')
        if not base:
            self.skipTest('Set BOILER_X2_OVERLAY_BASE to the accepted .8 package')
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'overlay'
            lock = overlay.build(base, output)
            self.assertEqual(lock['mapping'], {'game': {'peak': 1100, 'full_frame': 472}, 'desktop': {'peak': 1100, 'full_frame': 472}})
            files = {entry['path']: (output / entry['source']).read_bytes() for entry in lock['files']}
            launch = files[overlay.LEGACY + 'components/desktop-hdr/launch.sh']
            self.assertIn(b'--recover-startup', launch)
            self.assertNotIn(b'--install-launcher', launch)
            self.assertNotIn(b'export KWIN_X2MINI_HDR_PROFILE', launch)
            for name in ('components/hdr/upstream.lua', 'components/hdr/generate.py', 'maintenance/app/oxp3_maintenance/components.py'):
                self.assertNotIn(overlay.LEGACY + name, files)
            options_name = overlay.LEGACY + 'components/options.json'
            original_options = json.loads((Path(base) / options_name).read_bytes())
            changed_options = json.loads(files[options_name])
            old_row = next(row for row in original_options['repairs'] if row['id'] == 'desktop-hdr')
            new_row = next(row for row in changed_options['repairs'] if row['id'] == 'desktop-hdr')
            self.assertEqual(new_row['en'], 'KDE desktop HDR mapping and recovery')
            old_row['en'] = new_row['en']; old_row['zh'] = new_row['zh']
            self.assertEqual(original_options, changed_options)
            trial = files[overlay.LEGACY + 'components/desktop-hdr/hdr_trial.py']
            self.assertIn(b"args.install_launcher or not any((args.device_check, args.uninstall_launcher, args.recover_startup, args.watchdog))", trial)
            self.assertIn(b"recover_startup(journal, home / '.config/kwinoutputconfig.json')", trial)
            fake = types.ModuleType('x2mini_device')
            fake.is_x2mini = lambda: True
            fake.install_launchers = mock.Mock()
            fake.cleanup_launchers = mock.Mock()
            namespace = {'__name__': 'trial_fixture', '__file__': str(output / 'hdr_trial.py')}
            old_path = sys.path[:]
            try:
                with mock.patch.dict(sys.modules, {'x2mini_device': fake}):
                    exec(compile(trial, namespace['__file__'], 'exec'), namespace)
                for args in ([], ['--install-launcher']):
                    with mock.patch.object(sys, 'argv', ['hdr_trial.py', *args]):
                        with self.assertRaisesRegex(RuntimeError, 'trial is retired'):
                            namespace['main']()
                namespace['uid'] = lambda: 1000
                namespace['Journal'] = mock.Mock(return_value=object())
                namespace['recover_startup'] = mock.Mock()
                namespace['os'] = types.SimpleNamespace(name='posix')
                with mock.patch.object(sys, 'argv', ['hdr_trial.py', '--recover-startup']):
                    self.assertEqual(namespace['main'](), 0)
                namespace['recover_startup'].assert_called_once()
            finally:
                sys.path[:] = old_path
            device = files[overlay.LEGACY + 'components/desktop-hdr/x2mini_device.py']
            self.assertIn(b"frozenset(('ONEXPLAYERX2MINI',))", device)
            self.assertIn(b'def cleanup_launchers(', device)
            self.assertIn(b'_assert_trial_is_terminal(home_path, owner)', device)
            self.assertIn(b'_assert_persisted_hdr_is_off(home_path, owner)', device)

if __name__ == '__main__':
    unittest.main()
