"""Desktop firstboot failures require fresh report consent and contain no logs."""
import copy,contextlib,io,json,sys,tempfile,unittest
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from maintenance import firstboot_notice as notice
from maintenance.model_ui import ModelUI
from maintenance.feedback import FeedbackClient
from maintenance.reporter import Reporter,ReportResult
from test_maintenance_feedback import Dialog
from unittest.mock import patch

class NoticeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cache=self.root/'user-cache/ack'
  self.state=dict(generation='a'*32,status='attention_required',resources={'resource:sample':{'status':'failed','attempts':1,'error':'password=private /home/alice/file'}})
  self.sent=[];client=FeedbackClient(Reporter('https://example.test/api/v1/reports',source='steamos-maintenance',app_version='test',os='steamos',component='maintenance',enabled=True))
  client.send=lambda report:self.sent.append(report) or ReportResult('sent',report.report_id)
  self.dialog=Dialog(confirmations=[False,True]);self.ui=ModelUI(self.dialog,None,feedback=client)
 def tearDown(self):self.temp.cleanup()
 def test_initial_popup_decline_and_later_new_failure_require_consent(self):
  notice.publish(self.state,root=self.root)
  self.assertTrue(notice.show_pending(self.ui,root=self.root,cache=self.cache))
  self.assertEqual(len(self.dialog.prompts),1);self.assertEqual(self.sent,[])
  notice.publish(self.state,root=self.root)
  self.assertFalse(notice.show_pending(self.ui,root=self.root,cache=self.cache))
  self.state['resources']['resource:sample']['attempts']=2;notice.publish(self.state,root=self.root)
  self.assertTrue(notice.show_pending(self.ui,root=self.root,cache=self.cache));self.assertEqual(len(self.sent),1)
  self.assertNotIn('alice',self.sent[0]._body.decode())
 def test_public_marker_is_minimal_and_excludes_original_error(self):
  notice.publish(self.state,root=self.root);raw=(self.root/notice.MARKER).read_text()
  self.assertEqual(set(json.loads(raw)),{'kind','schema_version','generation','status','code','fingerprint'})
  for secret in ('private','alice','password','resource:sample'):self.assertNotIn(secret,raw)
 def test_intentional_restore_is_not_a_failure(self):
  self.state['resources']['resource:sample']['status']='restored';notice.publish(self.state,root=self.root)
  self.assertFalse(notice.show_pending(self.ui,root=self.root,cache=self.cache));self.assertEqual(self.dialog.prompts,[])
 def test_early_fatal_error_has_a_fixed_report_code(self):
  notice.publish({},root=self.root,fatal=True)
  self.assertEqual(notice.read_notice(root=self.root)['code'],'MAINTENANCE_OPERATION_FAILED')
  self.assertTrue(notice.show_pending(self.ui,root=self.root,cache=self.cache))
 def test_unknown_code_in_marker_is_refused_before_any_prompt(self):
  notice.publish(self.state,root=self.root);path=self.root/notice.MARKER
  value=json.loads(path.read_text());value['code']='private free text';path.write_text(json.dumps(value))
  with self.assertRaises(ValueError):notice.show_pending(self.ui,root=self.root,cache=self.cache)
  self.assertEqual(self.dialog.prompts,[])
 def test_periodic_lock_contention_does_not_publish_failure(self):
  from maintenance import resource_firstboot as firstboot
  output=io.StringIO()
  with patch.object(sys,'argv',['firstboot','run']),patch.object(firstboot,'run',side_effect=firstboot.ResourceBusy('busy')),patch.object(notice,'publish') as publish,contextlib.redirect_stdout(output):
   firstboot.main()
  publish.assert_not_called();self.assertEqual(json.loads(output.getvalue()),{'status':'busy'})

if __name__=='__main__':unittest.main()
