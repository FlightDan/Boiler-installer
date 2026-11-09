"""Privilege and argument boundaries used by the GTK maintenance frontend."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance import project
from maintenance.model_ui import Backend, GtkMaintenance, MAINTENANCE, PKEXEC


class GtkBackendTests(unittest.TestCase):
    def test_update_copy_distinguishes_empty_offline_and_reviewable_versions(self):
        ui = GtkMaintenance.__new__(GtkMaintenance)
        ui.lang = 'zh-CN'
        self.assertEqual(ui.stream_status({'status': 'ready', 'candidates': []}), '暂无更新')
        self.assertEqual(ui.stream_status({'status': 'empty'}), '暂无更新')
        self.assertEqual(ui.stream_status({'status': 'offline'}), '离线')
        self.assertEqual(ui.stream_status({'status': 'error'}), '检查失败')
        candidate = {'compatible': True, 'update_available': True}
        self.assertEqual(ui.stream_status({'status': 'ready', 'candidates': [candidate]}), '有 1 个可审阅版本')
        candidate['installed_version'] = '0.2.0+maintenance.19'
        self.assertEqual(ui.stream_status({'status': 'ready', 'candidates': [candidate]}),
                         '发现更新 · 1 个可审阅版本')

    def test_overview_is_fixed_and_omits_catalog_and_raw_errors(self):
        class Widget:
            def __init__(self, **kwargs): pass
            def set_border_width(self, *args): pass
            def get_style_context(self): return self
            def add_class(self, *args): pass
            def pack_start(self, *args): pass
            def set_size_request(self, *args): pass
            def set_sensitive(self, *args): pass
            def set_shadow_type(self, *args): pass
            def add(self, *args): pass
            def connect(self, *args): pass
        class Gtk:
            Box = Button = Frame = Widget
            Orientation = type('Orientation', (), {'VERTICAL': 0, 'HORIZONTAL': 1})
            ShadowType = type('ShadowType', (), {'NONE': 0})
        ui = GtkMaintenance.__new__(GtkMaintenance)
        ui.gtk, ui.body, ui.lang = Gtk(), Widget(), 'en-US'
        ui.busy, ui.check_error = False, None
        ui.result = {
            'match': {'name': 'X2 Mini', 'status': 'matched'},
            'streams': [{'id': 'device', 'status': 'ready', 'candidates': [
                {'compatible': True, 'update_available': True},
                {'compatible': False, 'update_available': True}]},
                {'id': 'maintenance', 'status': 'error', 'message': 'network details'}],
            'components': [{'name': 'Catalog row', 'notes': 'Long instructions'}] * 34,
        }
        layout, lines = [], []
        ui.clear = lambda title, *, scrollable: layout.append((title, scrollable))
        ui.line = lambda _box, value, *_style: lines.append(value)
        ui.overview()
        self.assertEqual(layout, [('Overview', False)])
        self.assertEqual(lines, ['X2 Mini', 'Model matched', 'Update status',
                                 'Device adaptations  ·  1 available to review',
                                 'Boiler Maintenance  ·  Check failed'])

    def test_project_commands_use_expected_privilege_boundary(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps({'ok': True, 'result': {}}), '')
        backend = Backend(runner)
        backend.project('check', '/tmp/session', '--offline')
        backend.project('acquire', '/tmp/session', 'device', 'package', '1')
        backend.project('inventory')
        backend.project('plan', '/tmp/session', 'device', 'package', '1')
        backend.project('execute', '/tmp/session', 'device', '/tmp/plan', '--reviewed-sha256', 'a' * 64)
        backend.recovery('restore', 'transaction')
        self.assertEqual(calls[0], [MAINTENANCE, '--root', '/', 'project', 'check', '/tmp/session', '--offline'])
        self.assertEqual(calls[1][0], MAINTENANCE)
        self.assertTrue(all(command[0] == PKEXEC for command in calls[2:]))
        self.assertEqual(calls[-1], [PKEXEC, MAINTENANCE, '--root', '/', 'restore', 'transaction'])

    def test_empty_selection_does_not_request_default_plan(self):
        class Check:
            def __init__(self, label):
                self.active = False
                self.sensitive = True
            def set_size_request(self, *args): pass
            def set_active(self, value): self.active = value
            def set_sensitive(self, value): self.sensitive = value
            def get_active(self): return self.active
            def get_sensitive(self): return self.sensitive
        class Body:
            def pack_start(self, *args): pass
        class Gtk:
            CheckButton = Check
        ui = GtkMaintenance.__new__(GtkMaintenance)
        ui.gtk, ui.body, ui.lang = Gtk(), Body(), 'en-US'
        ui.session = type('Session', (), {'name': '/tmp/session'})()
        ui.clear = lambda *args: None
        ui.refresh = lambda: None
        messages, buttons, operations = [], {}, []
        ui.text = messages.append
        ui.button = lambda label, callback, *args: buttons.update({label: callback})
        ui.operation = lambda *args, **kwargs: operations.append(args)
        ui.select_components('device', 'device:test', '2',
                             {'package': {'components': [{'id': 'test:part', 'version': '2'}]}},
                             {'components': {}})
        buttons['Review plan']()
        self.assertFalse(operations)
        self.assertIn('Select at least one component to update.', messages)

    def test_scripted_components_do_not_offer_generic_recovery(self):
        ui = GtkMaintenance.__new__(GtkMaintenance)
        ui.lang = 'en-US'
        ui.clear = lambda *args: None
        ui.refresh = lambda: None
        ui.text = lambda *args: None
        buttons = []
        ui.button = lambda label, callback, *args: buttons.append(label)
        ui.recovery_options({'components': {'scripted': {'version': '1', 'verified': True,
                                                          'transaction': 'txn', 'scripted': True},
                                             'plain': {'version': '1', 'verified': True,
                                                       'transaction': 'txn2'}}})
        self.assertNotIn('Verify: scripted', buttons)
        self.assertNotIn('Restore: scripted', buttons)
        self.assertIn('Verify: plain', buttons)

    def _project_details(self, upstream_plugin):
        class Widget:
            def __init__(self, **kwargs): pass
            def set_size_request(self, *args): pass
            def set_shadow_type(self, *args): pass
            def get_style_context(self): return self
            def add_class(self, *args): pass
            def pack_start(self, *args): pass
        class Gtk:
            Box = Frame = Widget
            Orientation = type('Orientation', (), {'VERTICAL': 0, 'HORIZONTAL': 1})
            ShadowType = type('ShadowType', (), {'NONE': 0})
        ui = GtkMaintenance.__new__(GtkMaintenance)
        ui.gtk, ui.body, ui.lang = Gtk(), Widget(), 'en-US'
        ui.check_error = None
        ui.result = {'match': {'status': 'matched'}, 'streams': [], 'upstream_plugin': upstream_plugin}
        messages = []
        ui.text = messages.append
        ui.project_details()
        return messages

    def test_upstream_plugin_shows_installed_to_available_version(self):
        messages = self._project_details({'id': 'opti2xmx', 'repository': 'FlightDan/Opti2XMX',
                                          'installed_version': '0.3.1', 'version': '0.3.2',
                                          'newer': True})
        self.assertTrue(any('FlightDan/Opti2XMX · 0.3.1 → 0.3.2' in message for message in messages))

    def test_uninstalled_plugin_render_survives_the_real_uninstalled_result(self):
        # The renderer concatenates installed_version unconditionally, so it raises TypeError
        # when newer is true and installed_version is None. Only the producer's early return
        # keeps that unreachable; render its actual output rather than a hand-built dict.
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(project, '_releases') as releases:
            upstream = project._upstream_plugin(
                temporary, {'components': [{'id': 'opti2xmx'}]})
        releases.assert_not_called()
        self.assertIsNone(upstream['installed_version'])
        self.assertFalse(upstream['newer'])
        messages = self._project_details(upstream)
        self.assertTrue(any('no version comparison is available' in message for message in messages))
        self.assertFalse(any('→' in message for message in messages))


if __name__ == '__main__':
    unittest.main()
