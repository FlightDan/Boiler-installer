"""Consent, data minimization, and failure isolation of maintenance reporting."""
from concurrent.futures import TimeoutError
import json
import os
import runpy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance.feedback import FeedbackClient
from maintenance.model_ui import BackendError, KDialog, ModelUI, read_selected_log
from maintenance.reporter import MAX_BODY_BYTES, MAX_LOG_BYTES, ReportResult, Reporter


class Dialog:
    def __init__(self, *, confirmations=(), inputs=(), preview=True, choices=(), files=()):
        self.confirmations, self.inputs = list(confirmations), list(inputs)
        self.choices, self.files = list(choices), list(files)
        self.preview = preview
        self.messages, self.details_text, self.prompts = [], [], []

    def confirm(self, text):
        self.prompts.append(text)
        return self.confirmations.pop(0) if self.confirmations else False

    def input(self, text, **kwargs):
        return self.inputs.pop(0)

    def details(self, text):
        self.details_text.append(text)
        return self.preview

    def message(self, text, *, error=False):
        self.messages.append((text, error))

    def menu(self, text, rows):
        if self.choices:
            return self.choices.pop(0)
        if {row[0] for row in rows} == {'log', 'write'}:
            return 'write'
        return None

    def open_file(self):
        return self.files.pop(0) if self.files else None


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.reporter = Reporter('https://example.test/api/v1/reports', source='steamos-maintenance',
                                 app_version='0.1.0+maintenance.11', os='steamos',
                                 component='maintenance', enabled=True)
        self.client = FeedbackClient(self.reporter)
        self.sent = []

        def send(report):
            self.sent.append(report)
            return ReportResult('sent', report.report_id)
        self.client.send = send

    def ui(self, dialog):
        return ModelUI(dialog, None, feedback=self.client)

    def test_declining_failure_sends_nothing_and_each_failure_asks_again(self):
        dialog = Dialog(confirmations=[False, True, False])
        ui = self.ui(dialog)
        for _ in range(3):
            ui.offer_failure('MAINTENANCE_OPERATION_FAILED')
        self.assertEqual(len(dialog.prompts), 3)
        self.assertEqual(len(self.sent), 1)

    def test_failure_body_is_fixed_machine_fields_only(self):
        dialog = Dialog(confirmations=[True])
        ui = self.ui(dialog)
        ui.receipt({'status': 'verification-failed', 'result': {'verified': False,
                    'reason': 'password=super-secret /home/alice/private.txt',
                    'modified': ['/home/alice/private.txt']}}, 'verify')
        body = json.loads(self.sent[0]._body)
        self.assertEqual(body['error_code'], 'MODEL_VERIFICATION_FAILED')
        self.assertEqual(set(body), {'schema_version', 'report_id', 'consent', 'source', 'app_version',
                                     'os', 'component', 'kind', 'mode', 'error_code', 'events'})
        self.assertEqual(body['events'], [{'level': 'error', 'code': 'MODEL_VERIFICATION_FAILED',
                                           'step': 'model-maintenance'}])
        self.assertNotIn('super-secret', self.sent[0]._body.decode())
        self.assertNotIn('alice', self.sent[0]._body.decode())
        self.assertIn('/home/alice/private.txt', dialog.messages[0][0])

    def test_failure_code_cannot_be_arbitrary_user_text(self):
        with self.assertRaises(ValueError):
            self.client.failure('my-user-name')

    def test_original_error_and_return_code_survive_report_failure(self):
        class Backend:
            def call(self, *args):
                raise BackendError('original error /home/alice/private')
        self.client.send = lambda report: ReportResult('error', report.report_id, 'network_error')
        dialog = Dialog(confirmations=[True])
        ui = ModelUI(dialog, Backend(), feedback=self.client)
        self.assertEqual(ui.run(), 1)
        self.assertIn('original error /home/alice/private', dialog.messages[0][0])
        self.assertTrue(dialog.messages[0][1])
        self.assertIn('not sent', dialog.messages[-1][0])

    def test_reporting_exception_cannot_mask_original_error(self):
        class Backend:
            def call(self, *args):
                raise BackendError('primary failure')
        def broken(report):
            raise OSError('secret transport detail')
        self.client.send = broken
        dialog = Dialog(confirmations=[True])
        self.assertEqual(ModelUI(dialog, Backend(), feedback=self.client).run(), 1)
        self.assertIn('primary failure', dialog.messages[0][0])
        self.assertIn('unavailable', dialog.messages[-1][0])
        self.assertNotIn('secret transport', str(dialog.messages))

    def test_manual_feedback_previews_redacted_exact_bytes_before_confirmation(self):
        dialog = Dialog(confirmations=[True], inputs=['Issue /home/alice/private',
                         'Open https://user:pass@private.test/api?token=secret then fail\npassword=secret'])
        self.ui(dialog).manual_feedback()
        self.assertEqual(len(self.sent), 1)
        body = json.loads(self.sent[0]._body)
        self.assertEqual(body['mode'], 'manual')
        self.assertEqual(body['kind'], 'feedback')
        for secret in ('alice', 'private.test', 'password=secret', 'user:pass'):
            self.assertNotIn(secret, dialog.details_text[0])
            self.assertNotIn(secret, self.sent[0]._body.decode())
        self.assertIn(self.client.preview(self.sent[0]), dialog.details_text[0])
        self.assertNotIn('logs', body)

    def test_manual_preview_and_final_confirmation_both_gate_sending(self):
        for preview, confirmations in ((False, []), (True, [False])):
            with self.subTest(preview=preview):
                dialog = Dialog(inputs=['A title', 'A description'], preview=preview,
                                confirmations=confirmations)
                self.ui(dialog).manual_feedback()
                self.assertFalse(self.sent)

    def test_chinese_credentials_are_removed_from_preview_and_actual_http_bytes(self):
        values = {
            '密码：': 'two word password',
            '口令=': 'several recovery phrase words',
            '访问令牌:': 'token value with spaces',
            '密钥为': 'private key words',
            '私钥是': 'secret signing words',
            '用戶名：': 'private account name',
            '主機名=': 'personal machine name',
            '序列号:': 'device serial value',
            'password：': 'english secret with spaces',
        }
        description = '\n'.join(key + value for key, value in values.items())
        dialog = Dialog(inputs=['安装问题，密码：title secret phrase', description], confirmations=[True])
        requests = []
        class Response:
            status = 201
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        class Opener:
            def open(self, request, *, timeout):
                requests.append(request)
                return Response()
        client = FeedbackClient(self.reporter)
        with patch('maintenance.reporter.build_opener', return_value=Opener()):
            ModelUI(dialog, None, feedback=client).manual_feedback()
        self.assertEqual(len(requests), 1)
        wire = requests[0].data.decode('utf-8')
        payload = json.loads(wire)
        for secret in list(values.values()) + ['title secret phrase']:
            self.assertNotIn(secret, wire)
            self.assertNotIn(secret, dialog.details_text[0])
        self.assertEqual(payload['message'].count('[redacted]'), len(values))
        self.assertIn(json.dumps(payload, ensure_ascii=False, indent=2), dialog.details_text[0])
        self.assertEqual(payload['title'], '安装问题，密码：[redacted]')
        self.assertEqual(dialog.messages[-1], ('Report sent.', False))

    def test_manual_cancel_and_invalid_text_never_send(self):
        for inputs in ([None], ['Title', None], ['', 'Description'], ['Title', 'x' * 2049]):
            with self.subTest(inputs=inputs):
                self.ui(Dialog(inputs=inputs, confirmations=[True])).manual_feedback()
                self.assertFalse(self.sent)

    def test_selected_log_uses_manual_error_shape_and_never_sends_its_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private-name.log'
            path.write_text('startup\npassword=hidden phrase\nfinished', encoding='utf-8')
            dialog = Dialog(choices=['log'], files=[str(path)], confirmations=[True])
            self.ui(dialog).manual_feedback()
        self.assertEqual(len(self.sent), 1)
        body = json.loads(self.sent[0]._body)
        self.assertEqual(body['kind'], 'error')
        self.assertEqual(body['mode'], 'manual')
        self.assertEqual(body['error_code'], 'MAINTENANCE_LOG_FEEDBACK')
        self.assertEqual(body['title'], 'Maintenance log feedback')
        self.assertEqual(body['message'], 'A maintenance log was selected for manual feedback.')
        self.assertEqual(body['logs'], 'startup\npassword=[redacted]\nfinished')
        self.assertNotIn('private-name', self.sent[0]._body.decode('utf-8'))
        self.assertIn(self.client.preview(self.sent[0]), dialog.details_text[0])
        self.assertIn('4 KiB', dialog.details_text[0])
        self.assertIn('64 KiB', dialog.messages[0][0])

    def test_selected_log_is_redacted_before_its_4kib_tail_is_applied(self):
        report = self.client.manual_log('password=secret\n' + 'x' * 5000)
        body = json.loads(report._body)
        self.assertEqual(body['logs'], 'x' * MAX_LOG_BYTES)
        self.assertLessEqual(len(report._body), MAX_BODY_BYTES)
        self.assertNotIn('secret', report._body.decode('utf-8'))
        ansi = self.client.manual_log('before \x1b[31mred\x1b[0m after')
        self.assertEqual(json.loads(ansi._body)['logs'], 'before red after')

    def test_selected_log_reader_supports_bom_text_and_rejects_binary_or_nonfiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = {
                'utf8.log': b'\xef\xbb\xbfhello\nworld',
                'utf16le.log': 'hello\nworld'.encode('utf-16'),
                'utf16be.log': b'\xfe\xff' + 'hello\nworld'.encode('utf-16-be'),
            }
            for name, raw in fixtures.items():
                path = root / name
                path.write_bytes(raw)
                with self.subTest(name=name):
                    self.assertEqual(read_selected_log(path), 'hello\nworld')
            for name, raw in (('binary.log', b'\xff\xfe\x00'), ('nul.log', b'hello\x00world'),
                              ('empty.log', b'')):
                path = root / name
                path.write_bytes(raw)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    read_selected_log(path)
            with self.assertRaises(ValueError):
                read_selected_log(root)
            if hasattr(os, 'mkfifo'):
                fifo = root / 'pipe.log'
                os.mkfifo(fifo)
                with self.assertRaises(ValueError):
                    read_selected_log(fifo)

    def test_selected_log_reader_caps_disk_read_to_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'large.log'
            path.write_bytes(b'a' * (64 * 1024) + b'b' * 128)
            selected = read_selected_log(path)
        self.assertEqual(selected, 'a' * (64 * 1024 - 4 - 128) + 'b' * 128)

    def test_selected_log_reader_handles_long_single_line_and_utf16_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            line = root / 'long-line.log'
            line.write_bytes(b'x' * (70 * 1024))
            self.assertEqual(len(read_selected_log(line)), 64 * 1024 - 4)

            # Position the tail boundary between the two UTF-16 surrogate
            # code units for the emoji; the incomplete leading half is omitted.
            utf16 = root / 'utf16-boundary.log'
            content = 'a' * 4000 + '\U0001f600' + 'b' * (64 * 1024 // 2 - 3)
            utf16.write_bytes(b'\xff\xfe' + content.encode('utf-16-le'))
            self.assertEqual(read_selected_log(utf16), 'b' * (64 * 1024 // 2 - 3))

    def test_manual_entry_does_not_require_catalog(self):
        class Backend:
            def call(self, *args):
                return {'models': []}
        dialog = Dialog(inputs=['Title', 'Description'], confirmations=[False])
        selections = iter(['feedback', 'write', None])
        dialog.menu = lambda text, rows: next(selections)
        self.assertEqual(ModelUI(dialog, Backend(), feedback=self.client).run(), 0)
        self.assertEqual(len(dialog.details_text), 1)

    def test_catalog_failure_keeps_manual_feedback_available(self):
        class Backend:
            def call(self, *args):
                raise BackendError('catalog unavailable')
        dialog = Dialog(inputs=['Title', 'Description'], confirmations=[False, True])
        selections = iter(['feedback', 'write'])
        dialog.menu = lambda text, rows: next(selections)
        self.assertEqual(ModelUI(dialog, Backend(), feedback=self.client).run(), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(json.loads(self.sent[0]._body)['kind'], 'feedback')

    def test_transport_wait_is_bounded_without_automatic_retry(self):
        report = self.client.failure('MAINTENANCE_OPERATION_FAILED')
        class Future:
            def result(self, *, timeout):
                self.timeout = timeout
                raise TimeoutError()
        future = Future()
        with patch.object(self.reporter, 'send_background', return_value=future) as send:
            result = FeedbackClient(self.reporter).send(report)
        self.assertEqual(result.code, 'response_timeout')
        self.assertEqual(future.timeout, 6)
        send.assert_called_once_with(report)
        dialog = Dialog()
        self.ui(dialog).report_result(result)
        self.assertIn('unconfirmed', dialog.messages[0][0])

    def test_input_uses_fixed_argv_and_cancellation_is_not_feedback(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 1, 'ignored', '')
        self.assertIsNone(KDialog('en-US', runner).input('Describe', multiline=True))
        self.assertIn('--textinputbox', calls[0])

    def test_file_picker_uses_fixed_argv_and_cancellation_is_not_selection(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 1, '', '')
        self.assertIsNone(KDialog('en-US', runner).open_file())
        self.assertIn('--getopenfilename', calls[0])

    def test_runtime_lists_are_synced_and_include_version_and_feedback(self):
        root = Path(__file__).resolve().parents[1]
        from maintenance.engine import RUNTIME_FILES
        package = runpy.run_path(str(root/'scripts/package-maintenance.py'))
        left = RUNTIME_FILES
        self.assertEqual(left, package['RUNTIME_FILES'])
        self.assertTrue(set(left) <= set(package['runtime_inventory'](root)))
        self.assertTrue({'maintenance/reporter.py', 'maintenance/feedback.py', 'maintenance/release.json'} <= set(left))


if __name__ == '__main__':
    unittest.main()
