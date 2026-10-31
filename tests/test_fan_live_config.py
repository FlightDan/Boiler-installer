import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import copy, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from maintenance import fan_live_config as fan
from maintenance.engine import MaintenanceError

GEN = 'a' * 32
class FanConfigTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name);self.states=[]
    def tearDown(self): self.tmp.cleanup()
    def record(self,state): self.states.append(copy.deepcopy(state))
    @property
    def path(self): return self.root/fan.TARGET
    def write(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.write_text(json.dumps(data));self.path.chmod(0o600)
    def test_absent_create_edit_preserve_and_restore(self):
        state=fan.ensure(self.root,generation=GEN,state=None,record_before_write=self.record)
        self.assertEqual([s['phase'] for s in self.states],['prepared-create','created'])
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.write({'mode':'custom','points':[[30,35],[85,100]]})
        self.assertTrue(fan.verify(self.root,generation=GEN,state=state)['valid'])
        result=fan.restore(self.root,generation=GEN,state=state,record_before_write=self.record)
        self.assertTrue(result['preserved']);self.assertTrue(self.path.exists())
    def test_existing_and_deleted_user_config(self):
        self.write({'mode':'performance'}); before=self.path.read_bytes()
        state=fan.ensure(self.root,generation=GEN,state=None,record_before_write=self.record)
        self.assertEqual(self.path.read_bytes(),before)
        self.path.unlink()
        with self.assertRaises(MaintenanceError):fan.ensure(self.root,generation=GEN,state=state,record_before_write=self.record)
        self.assertFalse(self.path.exists())
    def test_restore_created_default(self):
        state=fan.ensure(self.root,generation=GEN,state=None,record_before_write=self.record)
        restored=fan.restore(self.root,generation=GEN,state=state,record_before_write=self.record)
        self.assertFalse(self.path.exists());self.assertFalse(restored['preserved'])
    def test_gap_after_creation_can_resume(self):
        def record(state):
            if state['phase']=='created': raise RuntimeError('simulated receipt loss')
            self.record(state)
        with self.assertRaises(RuntimeError):fan.ensure(self.root,generation=GEN,state=None,record_before_write=record)
        self.assertTrue(self.path.exists())
        state=fan.ensure(self.root,generation=GEN,state=self.states[-1],record_before_write=self.record)
        self.assertEqual(state['phase'],'created')
    def test_gap_with_user_change_rejects(self):
        def record(state):
            if state['phase']=='created': raise RuntimeError('receipt loss')
            self.record(state)
        with self.assertRaises(RuntimeError):fan.ensure(self.root,generation=GEN,state=None,record_before_write=record)
        self.write({'mode':'balanced'})
        with self.assertRaises(MaintenanceError):fan.ensure(self.root,generation=GEN,state=self.states[-1],record_before_write=self.record)
        self.assertEqual(json.loads(self.path.read_text())['mode'],'balanced')
    def test_temporary_and_link_gap_recovery(self):
        def stop(state):
            self.record(state)
            raise RuntimeError('stopped after prepared WAL')
        with self.assertRaises(RuntimeError):fan.ensure(self.root,generation=GEN,state=None,record_before_write=stop)
        self.path.parent.mkdir(parents=True)
        temporary=self.path.parent/('.boiler-config-'+GEN)
        temporary.write_bytes(b'{partial');temporary.chmod(0o600)
        state=fan.ensure(self.root,generation=GEN,state=self.states[-1],record_before_write=self.record)
        self.assertEqual(state['phase'],'created');self.assertFalse(temporary.exists())
        self.path.unlink()
        temporary.write_bytes(fan.DEFAULT);temporary.chmod(0o600)
        os.link(temporary,self.path)
        state=fan.ensure(self.root,generation=GEN,state=self.states[0],record_before_write=self.record)
        self.assertEqual(state['phase'],'created');self.assertFalse(temporary.exists())
    def test_process_exit_after_file_before_receipt(self):
        ledger = self.root/'external-ledger.json'
        script = r"""
import json, os, sys
from pathlib import Path
from maintenance import fan_live_config as fan
root=Path(sys.argv[1]);ledger=Path(sys.argv[2]);generation=sys.argv[3]
def persist(state):
    if state['phase']=='created': os._exit(77)
    with ledger.open('w') as f:
        json.dump(state,f);f.flush();os.fsync(f.fileno())
fan.ensure(root,generation=generation,state=None,record_before_write=persist)
"""
        result=subprocess.run([sys.executable,'-B','-c',script,str(self.root),str(ledger),GEN],check=False)
        self.assertEqual(result.returncode,77)
        self.assertTrue(self.path.exists())
        state=fan.ensure(self.root,generation=GEN,state=json.loads(ledger.read_text()),record_before_write=self.record)
        self.assertEqual(state['phase'],'created')
    def test_process_exit_during_temporary_creation_and_link(self):
        for phase in ('temporary','linked'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as folder:
                root=Path(folder); ledger=root/'wal.json'
                script=r"""
import json, os, sys
from pathlib import Path
from maintenance import fan_live_config as fan
root=Path(sys.argv[1]);ledger=Path(sys.argv[2]);generation=sys.argv[3];phase=sys.argv[4]
def persist(state):
    with ledger.open('w') as f:
        json.dump(state,f);f.flush();os.fsync(f.fileno())
if phase=='temporary':
    original=os.fchmod
    def fault(fd,mode): original(fd,mode);os._exit(78)
    fan.os.fchmod=fault
else:
    original=os.link
    def fault(*a,**k): original(*a,**k);os._exit(79)
    fan.os.link=fault
fan.ensure(root,generation=generation,state=None,record_before_write=persist)
"""
                process=subprocess.run([sys.executable,'-B','-c',script,str(root),str(ledger),GEN,phase],check=False)
                self.assertEqual(process.returncode,78 if phase=='temporary' else 79)
                state=fan.ensure(root,generation=GEN,state=json.loads(ledger.read_text()),record_before_write=self.record)
                self.assertEqual(state['phase'],'created')
                self.assertFalse((root/fan.TARGET).with_name('.boiler-config-'+GEN).exists())
    def test_invalid_and_wrong_mode_no_write(self):
        self.write({'mode':'bad'});before=self.path.read_bytes()
        with self.assertRaises(MaintenanceError):fan.ensure(self.root,generation=GEN,state=None,record_before_write=self.record)
        self.assertEqual(before,self.path.read_bytes());self.assertFalse(self.states)
        self.write({'mode':'auto'});self.path.chmod(0o644)
        with self.assertRaises(MaintenanceError):fan.ensure(self.root,generation=GEN,state=None,record_before_write=self.record)
        self.assertFalse(self.states)

if __name__=='__main__':unittest.main()



