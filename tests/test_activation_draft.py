import sys
import unittest
import types
from pathlib import Path
from unittest.mock import patch
if sys.platform=='win32':
    pwd=types.ModuleType('pwd');pwd.getpwnam=lambda name:types.SimpleNamespace(pw_gid=1235,pw_dir='/home/deck');sys.modules['pwd']=pwd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance import desktop_hdr_activation as activation

class ActivationTests(unittest.TestCase):
    def test_bus_absence_is_configured_pending_without_reload(self):
        class System:
            def __init__(self,root): pass
            def user(self): return 1234,False
            def userctl(self,*args): raise AssertionError('no user manager should be called')
        records=[]
        binding=dict(account={'uid':1234,'gid':1235,'home':'/home/deck'},generation='a'*32,profile='x2-mini',bundle_sha256='b'*64)
        with patch.object(activation,'_bound',return_value=binding),patch.object(activation,'LiveSystem',System):
            result=activation.activate(activation.IDENTITY,root='/',pool=None,facts={},selection=[activation.IDENTITY],record_before_write=records.append)
        self.assertEqual(result['runtime'],'pending-user-session')
        self.assertFalse(records[0]['daemon_reload_pending'])

    def test_reload_records_before_write(self):
        events=[]
        class System:
            def __init__(self,root):pass
            def user(self):return 1234,True
            def userctl(self,*args):events.append(('reload',args));return (0,'')
        binding=dict(account={'uid':1234,'gid':1235,'home':'/home/deck'},generation='a'*32,profile='x2-mini',bundle_sha256='b'*64)
        with patch.object(activation,'_bound',return_value=binding),patch.object(activation,'LiveSystem',System):
            activation.activate(activation.IDENTITY,root='/',pool=None,facts={},selection=[activation.IDENTITY],record_before_write=lambda row:events.append(('record',row['daemon_reload_pending'])))
        self.assertEqual(events,[('record',True),('reload',('daemon-reload',)),('record',False)])

    def test_post_restore_reload_is_recorded_and_failure_propagates(self):
        events=[]
        class System:
            def __init__(self,root):pass
            def user(self):return 1234,True
            def userctl(self,*args):events.append('reload');return (1,'failed')
        state=dict(kind='BoilerDesktopHdrActivationV1',identity=activation.IDENTITY,generation='a'*32)
        with patch.object(activation,'LiveSystem',System):
            with self.assertRaises(activation.ResourceError):
                activation.resume_original(activation.IDENTITY,root='/',state=state,record_before_write=lambda row:events.append(row['reload_pending']))
        self.assertEqual(events,[True,'reload'])

    def test_post_restore_resumes_only_matching_recorded_generation(self):
        class System:
            def __init__(self,root):pass
            def user(self):return 1234,True
            def userctl(self,*args):return (0,'')
        state=dict(kind='BoilerDesktopHdrActivationV1',identity=activation.IDENTITY,generation='a'*32)
        prior=dict(kind='BoilerDesktopHdrPostRestoreV1',identity=activation.IDENTITY,generation='a'*32,reload_pending=True)
        history=[]
        with patch.object(activation,'LiveSystem',System):
            result=activation.resume_original(activation.IDENTITY,root='/',state=state,prior_resume=prior,record_before_write=lambda value:history.append(dict(value)))
            self.assertEqual(result['status'],'restored');self.assertFalse(history[-1]['reload_pending'])
            with self.assertRaises(activation.ResourceError):activation.resume_original(activation.IDENTITY,root='/',state=state,prior_resume=dict(prior,generation='b'*32),record_before_write=lambda _:None)

if __name__=='__main__':unittest.main()
