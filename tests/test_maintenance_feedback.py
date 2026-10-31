"""Consent, data minimization, and failure isolation of maintenance reporting."""
from concurrent.futures import TimeoutError
import json
import runpy
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance.feedback import FeedbackClient
from maintenance.model_ui import BackendError, KDialog, ModelUI
from maintenance.reporter import ReportResult, Reporter
from maintenance.resource_ui import ResourceUI


class Dialog:
    def __init__(self, *, confirmations=(), inputs=(), preview=True):
        self.confirmations, self.inputs = list(confirmations), list(inputs)
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
        return None


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

    def test_resource_failure_offers_consent_without_raw_error_fields(self):
        class Backend:
            def call(self,*args):raise BackendError('password=private /home/alice/secret')
        dialog=Dialog(confirmations=[True]);dialog.text=lambda zh,en:en
        self.assertEqual(ResourceUI(dialog,Backend(),failure=self.ui(dialog).offer_failure).run(),1)
        self.assertEqual(len(self.sent),1)
        body=self.sent[0]._body.decode()
        self.assertIn('MAINTENANCE_OPERATION_FAILED',body)
        self.assertNotIn('private',body);self.assertNotIn('alice',body)
        self.assertIn('/home/alice/secret',dialog.messages[0][0])

    def test_resource_existing_failure_and_decline_never_send(self):
        class Backend:
            def call(self,*args):return {'resources':{'resource:sample':{'status':'failed','error':'private'}}}
        dialog=Dialog(confirmations=[False]);dialog.text=lambda zh,en:en
        self.assertEqual(ResourceUI(dialog,Backend(),failure=self.ui(dialog).offer_failure).run(),0)
        self.assertEqual(len(dialog.prompts),1);self.assertEqual(self.sent,[])

    def test_resource_verification_failure_uses_fixed_verification_code(self):
        class Backend:
            def call(self,action,*args):return {'resources':{'resource:sample':{'ok':False,'error':'private'}}} if action=='verify' else {'resources':{'resource:sample':{'status':'active'}}}
        dialog=Dialog(confirmations=[True]);dialog.text=lambda zh,en:en
        choices=iter(['verify',None]);dialog.menu=lambda *args:next(choices)
        self.assertEqual(ResourceUI(dialog,Backend(),failure=self.ui(dialog).offer_failure).run(),0)
        self.assertEqual(json.loads(self.sent[0]._body)['error_code'],'MODEL_VERIFICATION_FAILED')
        self.assertTrue(dialog.messages[0][1]);self.assertIn('need attention',dialog.messages[0][0])

    def test_resource_reporting_failure_preserves_primary_result(self):
        class Backend:
            def call(self,*args):raise BackendError('primary operation failure')
        dialog=Dialog();dialog.text=lambda zh,en:en
        def broken(code):raise OSError('report failure')
        self.assertEqual(ResourceUI(dialog,Backend(),failure=broken).run(),1)
        self.assertIn('primary operation failure',dialog.messages[0][0])

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

    def test_manual_entry_does_not_require_catalog(self):
        class Backend:
            def call(self, *args):
                return {'models': []}
        dialog = Dialog(inputs=['Title', 'Description'], confirmations=[False])
        selections = iter(['feedback', None])
        dialog.menu = lambda text, rows: next(selections)
        self.assertEqual(ModelUI(dialog, Backend(), feedback=self.client).run(), 0)
        self.assertEqual(len(dialog.details_text), 1)

    def test_catalog_failure_keeps_manual_feedback_available(self):
        class Backend:
            def call(self, *args):
                raise BackendError('catalog unavailable')
        dialog = Dialog(inputs=['Title', 'Description'], confirmations=[False, True])
        dialog.menu = lambda text, rows: 'feedback'
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
