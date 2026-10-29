"""The KDE wrapper preserves consent, backend review and argv boundaries."""
import copy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance.model_ui import Backend, BackendError, KDialog, ModelUI, main


MODEL = {'id': 'device:oxp3', 'name': {'zh': 'ONEXPLAYER 3', 'en': 'ONEXPLAYER 3'},
         'guide_url': 'https://example.test/pinned', 'components': [
             {'id': 'pl4', 'name': {'zh': 'PL4 待实现', 'en': 'PL4 pending'},
              'notes': {'zh': '尚未实现', 'en': 'Implementation pending'}}]}
CATALOG = {'source': {'repository': 'FlightDan/Boiler-devices', 'commit': 'a' * 40},
           'offline': True, 'models': [MODEL, dict(MODEL, id='device:x2-mini', name='X2 Mini')],
           'bundled_packages': [{'id': 'device:oxp3', 'version': 'test', 'manifest_sha256': 'b' * 64}]}
OPTIONS = {'executable': True, 'current': None, 'reasons': [], 'capabilities': [
    {'id': 'base', 'category': 'repairs', 'name': {'en': 'Base', 'zh': '基础'},
     'available': True, 'default': True, 'dependencies': []},
    {'id': 'fan', 'category': 'repairs', 'name': {'en': 'Fan', 'zh': '风扇'},
     'available': True, 'default': False, 'dependencies': ['base']},
    {'id': 'decky', 'category': 'plugins', 'name': {'en': 'Decky', 'zh': 'Decky'},
     'available': True, 'default': False, 'dependencies': ['base']},
    {'id': 'pl4', 'category': 'repairs', 'name': {'en': 'PL4 pending', 'zh': 'PL4 待实现'},
     'available': False, 'default': False, 'reason': {'en': 'Implementation pending', 'zh': '尚未实现'},
     'dependencies': []}]}
PLAN = {'review_sha256': 'c' * 64, 'model_id': MODEL['id'], 'action': 'apply',
        'selection': {'repairs': ['base', 'fan'], 'plugins': []},
        'scope': 'active-root-and-shared-home', 'target': {'slot': 'A'},
        'warnings': ['Local test only'], 'summary': {'added': ['fan'], 'removed': [], 'file_count': 4},
        'package': {'version': 'test'}}


class Dialog:
    def __init__(self, menus=(), checklists=(), confirms=(), review=True):
        self.menus, self.checklists, self.confirms = list(menus), list(checklists), list(confirms)
        self.menu_rows, self.checklist_rows, self.confirm_texts = [], [], []
        self.messages, self.detail_texts = [], []
        self.review = review

    def menu(self, text, rows):
        self.menu_rows.append((text, rows))
        return self.menus.pop(0) if self.menus else None

    def checklist(self, text, rows):
        self.checklist_rows.append((text, rows))
        return self.checklists.pop(0)

    def confirm(self, text):
        self.confirm_texts.append(text)
        return self.confirms.pop(0)

    def message(self, text, *, error=False):
        self.messages.append((text, error))

    def details(self, text):
        self.detail_texts.append(text)
        return self.review if 'Review this operation' in text else True


class FakeBackend:
    def __init__(self, options=None, plan=None):
        self.options = copy.deepcopy(OPTIONS if options is None else options)
        self.plan = copy.deepcopy(PLAN if plan is None else plan)
        self.calls, self.saved_plan, self.saved_path = [], None, None

    def call(self, *args):
        self.calls.append(args)
        if args[0] == 'list':
            return copy.deepcopy(CATALOG)
        if args[0] == 'options':
            return self.options
        if args[0] == 'plan':
            return self.plan
        if args[0] == 'execute':
            path = Path(args[1])
            self.saved_plan, self.saved_path = json.loads(path.read_text(encoding='utf-8')), path
            if os.name != 'nt':
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
                assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
            return {'receipt_path': '/var/lib/boiler/receipt.json', 'log': '/var/log/boiler/test.log'}
        raise AssertionError(args)


class ModelUITests(unittest.TestCase):
    def ui(self, dialog, backend=None):
        backend = backend or FakeBackend()
        return ModelUI(dialog, backend, offline=True), backend

    def test_model_cancel_only_reads_catalog(self):
        dialog = Dialog()
        ui, backend = self.ui(dialog)
        self.assertEqual(ui.run(), 0)
        self.assertEqual(backend.calls, [('list', '--offline')])
        self.assertEqual(len(dialog.menu_rows[0][1]), 2)
        self.assertIn('Pinned commit', dialog.menu_rows[0][0])

    def test_trust_cancel_never_inspects_private_target(self):
        dialog = Dialog(['device:oxp3', 'options'], confirms=[False])
        ui, backend = self.ui(dialog)
        self.assertEqual(ui.run(), 0)
        self.assertEqual(backend.calls, [('list', '--offline')])
        self.assertIn('unpublished local test', dialog.confirm_texts[0])

    def test_apply_closes_dependencies_reviews_and_executes_exact_plan(self):
        dialog = Dialog(['device:oxp3', 'options', 'apply', 'back'],
                        [{'fan'}, set(), {'update-hold'}], [True, True])
        ui, backend = self.ui(dialog)
        self.assertEqual(ui.run(), 0)
        self.assertEqual(backend.saved_plan, PLAN)
        self.assertFalse(backend.saved_path.exists())
        plan_args = next(args for args in backend.calls if args[0] == 'plan')
        self.assertIn('--acknowledge-update-hold', plan_args)
        self.assertIn('--installer-language', plan_args)
        self.assertIn(('base', '--repair', 'fan'), tuple(plan_args[index:index + 3]
                                                       for index in range(len(plan_args) - 2)))
        self.assertFalse(dialog.checklist_rows[2][1][0][2])
        self.assertNotIn('pl4', {row[0] for row in dialog.checklist_rows[0][1]})
        self.assertIn('Implementation pending', dialog.detail_texts[0])
        self.assertIn('Base', dialog.detail_texts[1])
        self.assertIn('shared /home', dialog.detail_texts[1])
        self.assertIn('Files affected: 4', dialog.detail_texts[1])
        self.assertIn('/var/log/boiler/test.log', dialog.messages[-1][0])
        execute = next(args for args in backend.calls if args[0] == 'execute')
        self.assertEqual(execute[2:], ('--reviewed-sha256', 'c' * 64, '--trust-local-test'))

    def test_unchecked_hold_stops_before_plan(self):
        dialog = Dialog(['device:oxp3', 'options', 'apply', 'back'],
                        [{'base'}, set(), set()], [True])
        ui, backend = self.ui(dialog)
        self.assertEqual(ui.run(), 0)
        self.assertEqual([args[0] for args in backend.calls], ['list', 'options'])

    def test_cancel_in_each_checklist_stops_before_plan(self):
        for choices in ([None], [{'base'}, None], [{'base'}, set(), None]):
            with self.subTest(choices=choices):
                dialog = Dialog(['device:oxp3', 'options', 'apply', 'back'], choices, [True])
                ui, backend = self.ui(dialog)
                self.assertEqual(ui.run(), 0)
                self.assertNotIn('plan', [args[0] for args in backend.calls])

    def test_cancel_review_textbox_or_confirmation_never_executes(self):
        for review, confirms in ((False, [True]), (True, [True, False])):
            dialog = Dialog(['device:oxp3', 'options', 'verify', 'back'],
                            [], confirms, review=review)
            options = dict(OPTIONS, current={'repairs': ['base'], 'plugins': []})
            ui, backend = self.ui(dialog, FakeBackend(options))
            self.assertEqual(ui.run(), 0)
            self.assertNotIn('execute', [args[0] for args in backend.calls])
            self.assertFalse(dialog.checklist_rows)

    def test_verify_reviews_backend_selection_without_editable_checklists(self):
        dialog = Dialog(['device:oxp3', 'options', 'verify', 'back'], confirms=[True, False])
        options = dict(OPTIONS, current={'repairs': ['base'], 'plugins': []})
        plan = dict(PLAN, action='verify', selection={'repairs': ['base'], 'plugins': []})
        ui, backend = self.ui(dialog, FakeBackend(options, plan))
        self.assertEqual(ui.run(), 0)
        self.assertFalse(dialog.checklist_rows)
        args = next(args for args in backend.calls if args[0] == 'plan')
        self.assertNotIn('--repair', args)
        self.assertNotIn('--plugin', args)
        self.assertIn('Base', dialog.detail_texts[1])
        self.assertNotIn('Fan', dialog.detail_texts[1].split('Items (including required dependencies):')[1].split('Add:')[0])

    def test_no_current_hides_verify_and_restore_and_pending_allows_restore_only(self):
        for current, pending, expected in ((None, None, {'apply', 'guide', 'back'}),
                                           (None, {'id': 'pending'}, {'restore', 'guide', 'back'}),
                                           ({'repairs': ['base'], 'plugins': []}, None,
                                            {'apply', 'verify', 'restore', 'guide', 'back'})):
            dialog = Dialog(['device:oxp3', 'options', 'back', 'back'], confirms=[True])
            ui, _ = self.ui(dialog, FakeBackend(dict(OPTIONS, current=current, pending=pending)))
            self.assertEqual(ui.run(), 0)
            self.assertEqual({row[0] for row in dialog.menu_rows[2][1]}, expected)

    def test_legacy_installer_current_is_informational_until_boiler_installation(self):
        for lang, expected in (('zh-CN', '安装器已有配置，安装后才纳入 Boiler 维护'),
                               ('en-US', 'Install it to bring it under Boiler maintenance')):
            dialog = Dialog(['back'])
            ui = ModelUI(dialog, FakeBackend(), lang=lang)
            options = dict(OPTIONS, current={'repairs': ['base'], 'plugins': []}, current_source='legacy-installer')
            ui.actions(MODEL, options)
            text, rows = dialog.menu_rows[0]
            self.assertEqual({row[0] for row in rows}, {'apply', 'guide', 'back'})
            self.assertIn(expected, text)
        dialog = Dialog(['back'])
        ui, _ = self.ui(dialog)
        ui.actions(MODEL, dict(options, pending={'id': 'pending'}))
        self.assertEqual({row[0] for row in dialog.menu_rows[0][1]}, {'restore', 'guide', 'back'})

    def test_review_displays_actual_device_target_shape_and_files_summary(self):
        ui, _ = self.ui(Dialog())
        plan = dict(PLAN, action='restore', target={'root': '/', 'id': 'rootfs-a', 'label': 'SteamOS A',
                                                  'partuuid': 'deadbeef-1234-5678-9012-abcdef012345'},
                    summary={'files': 72})
        text = ui.review(MODEL, OPTIONS, plan, 'Restore installed adaptations')
        self.assertIn('Active slot: SteamOS A', text)
        self.assertIn('Partition PARTUUID: deadbeef-1234-5678-9012-abcdef012345', text)
        self.assertIn('Files affected: 72', text)
        self.assertNotIn('Active slot: rootfs-a', text)

    def test_review_target_label_slot_and_id_precedence(self):
        ui, _ = self.ui(Dialog())
        for target, expected in (({'label': 'Root A', 'slot': 'A', 'id': 'rootfs-a'}, 'Root A'),
                                 ({'slot': 'A', 'id': 'rootfs-a'}, 'A'), ({'id': 'rootfs-a'}, 'rootfs-a')):
            plan = dict(PLAN, target=target, summary={'slot': 'old-slot', 'files': 9, 'file_count': 10})
            text = ui.review(MODEL, OPTIONS, plan, 'Verify')
            self.assertIn('Active slot: ' + expected, text)
            self.assertIn('Files affected: 10', text)

    def test_trust_not_bypassed_when_public_catalog_omits_bundles(self):
        backend = FakeBackend()
        original = backend.call
        def call(*args):
            result = original(*args)
            if args[0] == 'list':
                result['bundled_packages'] = []
            return result
        backend.call = call
        dialog = Dialog(['device:oxp3', 'options'], confirms=[False])
        ui, _ = self.ui(dialog, backend)
        self.assertEqual(ui.run(), 0)
        self.assertEqual([args[0] for args in backend.calls], ['list'])

    def test_saved_empty_selection_does_not_reenable_defaults(self):
        options = copy.deepcopy(OPTIONS)
        options['current'] = {'repairs': [], 'plugins': ['decky']}
        dialog = Dialog(checklists=[set(), {'decky'}])
        ui, _ = self.ui(dialog)
        selection = ui.choose(options)
        self.assertFalse(dialog.checklist_rows[0][1][0][2])
        self.assertTrue(dialog.checklist_rows[1][1][0][2])
        self.assertEqual(selection, {'repairs': {'base'}, 'plugins': {'decky'}})

    def test_new_selection_uses_package_defaults(self):
        dialog = Dialog(checklists=[{'base'}, set()])
        ui, _ = self.ui(dialog)
        ui.choose(OPTIONS)
        self.assertTrue(dialog.checklist_rows[0][1][0][2])

    def test_unavailable_injected_choice_and_dependency_are_rejected(self):
        for choice, dependency in (({'pl4'}, False), ({'fan'}, True)):
            options = copy.deepcopy(OPTIONS)
            if dependency:
                options['capabilities'][1]['dependencies'] = ['pl4']
            ui, _ = self.ui(Dialog(checklists=[choice, set()]))
            with self.assertRaises(ValueError):
                ui.choose(options)

    def test_x2_guide_keeps_unavailable_rows_without_execution_actions(self):
        options = copy.deepcopy(OPTIONS)
        options['executable'] = False
        for row in options['capabilities']:
            row['available'] = False
        dialog = Dialog(['device:x2-mini', 'options', 'guide', 'back'])
        ui, backend = self.ui(dialog, FakeBackend(options))
        self.assertEqual(ui.run(), 0)
        self.assertEqual([args[0] for args in backend.calls], ['list', 'options'])
        self.assertEqual(backend.calls[1], ('options', 'device:x2-mini'))
        self.assertFalse(dialog.confirm_texts)
        self.assertIn('PL4 pending', '\n'.join(dialog.detail_texts))

    def test_unmapped_guide_entry_stays_visible(self):
        dialog = Dialog()
        ui, _ = self.ui(dialog)
        model = dict(MODEL, components=MODEL['components'] + [
            {'id': 'guide-only', 'name': 'Guide-only repair', 'notes': 'Hardware limitation'}])
        ui.unavailable(OPTIONS, model)
        self.assertIn('Guide-only repair', dialog.detail_texts[0])
        self.assertIn('no executable option', dialog.detail_texts[0])
        self.assertIn('Hardware limitation', dialog.detail_texts[0])

    def test_invalid_backend_hash_cannot_execute(self):
        dialog = Dialog(['device:oxp3', 'options', 'verify'], confirms=[True])
        ui, backend = self.ui(dialog, FakeBackend(dict(OPTIONS, current={'repairs': ['base'], 'plugins': []}),
                                                plan=dict(PLAN, review_sha256='invalid')))
        self.assertEqual(ui.run(), 1)
        self.assertNotIn('execute', [args[0] for args in backend.calls])
        self.assertTrue(dialog.messages[-1][1])

    def test_chinese_scope_and_consent(self):
        dialog = Dialog(['device:oxp3', 'options', 'apply', 'back'],
                        [{'base'}, set(), set()], [True])
        ui = ModelUI(dialog, FakeBackend(), lang='zh-CN')
        self.assertEqual(ui.run(), 0)
        self.assertIn('本地测试包', dialog.confirm_texts[0])
        self.assertIn('系统更新暂停', dialog.checklist_rows[-1][0])

    def test_verification_failure_shows_changed_paths_and_never_success(self):
        dialog = Dialog()
        ui, _ = self.ui(dialog)
        ui.receipt({'status': 'verification-failed', 'receipt_path': '/var/lib/boiler/failed.json',
                    'restart_required': False, 'result': {'ok': True, 'verified': False, 'status': 'modified',
                    'modified': ['/etc/changed.conf', {'path': '/home/deck/changed.conf'}]}}, 'verify')
        text, error = dialog.messages[-1]
        self.assertTrue(error)
        self.assertIn('Verification failed', text)
        self.assertIn('/etc/changed.conf', text)
        self.assertIn('/home/deck/changed.conf', text)
        self.assertIn('/var/lib/boiler/failed.json', text)
        self.assertNotIn('Operation completed', text)

    def test_verify_result_false_overrides_successful_envelope_status(self):
        dialog = Dialog()
        ui, _ = self.ui(dialog)
        ui.receipt({'status': 'verified', 'result': {'ok': True, 'verified': False}}, 'verify')
        self.assertTrue(dialog.messages[-1][1])
        ui.receipt({'status': 'verified', 'result': {'ok': True, 'verified': True}}, 'verify')
        self.assertFalse(dialog.messages[-1][1])

    def test_replacement_error_explains_full_restore_and_keeps_backend_reason(self):
        backend = FakeBackend()
        original = backend.call
        def call(*args):
            if args[0] == 'plan':
                raise BackendError('replacement_requires_restore: current generation is owned')
            return original(*args)
        backend.call = call
        dialog = Dialog(['device:oxp3', 'options', 'apply'], [{'fan'}, set(), {'update-hold'}], [True])
        ui, _ = self.ui(dialog, backend)
        self.assertEqual(ui.run(), 1)
        text, error = dialog.messages[-1]
        self.assertTrue(error)
        self.assertIn('fully restore the current adaptation', text)
        self.assertIn('replacement_requires_restore', text)
        self.assertIn('current generation is owned', text)


class InvocationTests(unittest.TestCase):
    def test_privilege_boundary_uses_fixed_argv_and_no_shell(self):
        calls = []
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, '{"ok":true,"result":{}}', '')
        backend = Backend(runner)
        backend.call('list')
        for action in ('options', 'plan', 'execute'):
            backend.call(action, 'file;$(touch /tmp/bad)')
        self.assertEqual(calls[0][0], ['/usr/local/bin/boiler-maintenance', '--root', '/', 'models', 'list'])
        for args, kwargs in calls[1:]:
            self.assertEqual(args[:2], ['/usr/bin/pkexec', '/usr/local/bin/boiler-maintenance'])
            self.assertEqual(args[-1], 'file;$(touch /tmp/bad)')
            self.assertNotIn('shell', kwargs)
            self.assertNotIn('env', kwargs)

    def test_error_retains_receipt_and_log_location(self):
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 2, json.dumps(
                {'ok': False, 'error': 'failed', 'log': '/var/log/boiler/failure.log'}), '')
        with self.assertRaisesRegex(BackendError, '/var/log/boiler/failure.log'):
            Backend(runner).call('execute', '/tmp/plan')

    def test_kdialog_confirm_defaults_cancel_and_markup_is_escaped(self):
        calls = []
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 1, '', '')
        dialog = KDialog('en-US', runner)
        self.assertFalse(dialog.confirm('<b>untrusted</b> $(command)'))
        args, kwargs = calls[0]
        self.assertEqual(args[args.index('--default') + 1], 'cancel')
        self.assertIn('&lt;b&gt;untrusted&lt;/b&gt;', args[args.index('--menu') + 1])
        self.assertNotIn('shell', kwargs)

    def test_kdialog_checklist_rejects_unknown_rows(self):
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, 'hidden\n', '')
        with self.assertRaises(ValueError):
            KDialog('en-US', runner).checklist('Choose', [('safe', 'Safe', False)])

    def test_confirm_only_accepts_explicit_continue(self):
        for output, expected in (('cancel\n', False), ('continue\n', True), ('', False)):
            def runner(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, output, '')
            self.assertEqual(KDialog('en-US', runner).confirm('Review'), expected)

    def test_textbox_escapes_rich_text_content(self):
        def runner(args, **kwargs):
            path = Path(args[args.index('--textbox') + 1])
            text = path.read_text(encoding='utf-8')
            self.assertIn('&lt;a href=', text)
            self.assertNotIn('<a href=', text)
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            return subprocess.CompletedProcess(args, 0, '', '')
        self.assertTrue(KDialog('en-US', runner).details('<a href="exec:bad">source</a>'))

    @patch('maintenance.model_ui.Path.is_file', return_value=False)
    def test_missing_kdialog_gives_terminal_instructions(self, exists):
        with patch('sys.stderr') as stderr:
            self.assertEqual(main([]), 1)
        self.assertIn('models list --offline', ''.join(str(call) for call in stderr.write.call_args_list))


if __name__ == '__main__':
    unittest.main()
