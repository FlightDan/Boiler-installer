import copy
import unittest
from maintenance.resource_ui import ResourceUI

ID='resource:common-waydroid'
class Dialog:
 def __init__(self,confirm):self.confirmed=confirm;self.choices=iter(['retry',None]);self.reviews=[]
 def text(self,zh,en):return en
 def menu(self,*args):return next(self.choices)
 def confirm(self,text):self.reviews.append(text);return self.confirmed
 def message(self,*args,**kwargs):pass
class Backend:
 def __init__(self,state):self.state=state;self.calls=[]
 def call(self,action,identity=None,**kwargs):
  self.calls.append((action,identity,kwargs))
  if action=='status':return copy.deepcopy(self.state)
  if action=='plan-restore':return {'plan':{'summary':'Resume reviewed recovery'},'sha256':'a'*64}
  if action=='restore':return {'status':'restored'}
  raise AssertionError('Interrupted restore must not enter install retry')
class ResourceUiTests(unittest.TestCase):
 def run_case(self,confirm,top_status='restoring-packages'):
  state={'resources':{ID:{'status':'restore_failed'}},'maintenance_restore':{'identity':ID,'status':top_status}}
  backend=Backend(state);dialog=Dialog(confirm)
  self.assertEqual(ResourceUI(dialog,backend).run(),0)
  self.assertEqual(len(dialog.reviews),1)
  return backend.calls
 def test_retry_reviews_current_plan_then_restores(self):
  calls=self.run_case(True)
  self.assertIn(('restore',ID,{'reviewed_sha256':'a'*64}),calls)
  self.assertNotIn('retry',[c[0] for c in calls])
 def test_declined_resume_does_not_mutate(self):
  calls=self.run_case(False)
  self.assertEqual([c[0] for c in calls],['status','plan-restore','status'])
 def test_restore_failed_row_routes_without_top_pending_marker(self):self.run_case(True,'restored')
 def test_post_file_callback_failure_reopens_restore_review_instead_of_reinstall(self):
  state={'resources':{ID:{'status':'restored','resume_original_pending':True}},'maintenance_restore':{'identity':ID,'status':'restored'}}
  backend=Backend(state);dialog=Dialog(True);ui=ResourceUI(dialog,backend)
  self.assertEqual(ui.describe(state['resources'][ID]),'Files restored; recovery still needs to finish')
  self.assertEqual(ui.run(),0);self.assertIn(('restore',ID,{'reviewed_sha256':'a'*64}),backend.calls)
  self.assertNotIn('retry',[c[0] for c in backend.calls])
 def test_initialized_status_does_not_claim_container_running(self):
  self.assertEqual(ResourceUI(Dialog(False)).describe({'status':'configured','runtime':'initialized-awaiting-user-launch'}),'Android images initialized; launch Waydroid to continue')
