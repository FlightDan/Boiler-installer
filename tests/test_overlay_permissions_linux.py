import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import os, stat, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from maintenance import overlay_permissions as p

@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'requires Linux root')
class PermissionsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.node=self.root/'energy_uj';self.node.write_text('123')
        os.chown(self.node,0,0);os.chmod(self.node,0o600)
        self.state=self.root/'private/state.json';self.account=SimpleNamespace(pw_uid=65534,pw_gid=65534)
        self.targets=patch.object(p,'_targets',return_value=[self.node]);self.targets.start()
    def tearDown(self):self.targets.stop();self.temp.cleanup()
    def grant(self):return p.grant(state_path=self.state,boot='test-boot',account=self.account)
    def restore(self):return p.restore(state_path=self.state,boot='test-boot')
    def can_read_as_deck(self):
        pid=os.fork()
        if not pid:
            try:
                os.setgroups([]);os.setgid(65534);os.setuid(65534);self.node.read_text();os._exit(0)
            except OSError:os._exit(1)
        return os.waitpid(pid,0)[1]==0
    def test_real_permissions_and_restore(self):
        os.chmod(self.root,0o755)
        self.assertFalse(self.can_read_as_deck());self.assertEqual(self.grant(),[]);self.assertTrue(self.can_read_as_deck())
        self.assertEqual(self.restore(),[]);self.assertFalse(self.can_read_as_deck());self.assertEqual(stat.S_IMODE(self.node.stat().st_mode),0o600)
        self.assertEqual(self.restore(),[])
    def test_conflict_and_inode_preserved(self):
        self.grant();os.chmod(self.node,0o640|0o001)
        self.assertEqual(self.restore(),[str(self.node)]);self.assertEqual(stat.S_IMODE(self.node.stat().st_mode),0o641)
        self.node.unlink();self.node.write_text('new');os.chmod(self.node,0o600)
        self.assertEqual(self.restore(),[]);self.assertEqual(stat.S_IMODE(self.node.stat().st_mode),0o600)
    def test_intent_ack_and_restore_crashes(self):
        for phase in ('intent','ack','restore'):
            with self.subTest(phase=phase):
                if phase=='restore':self.grant()
                pid=os.fork()
                if not pid:
                    real=p._save
                    def save(path,state):
                        real(path,state)
                        rows=state.get('rows',[])
                        if rows:
                            row=rows[0]
                            if phase=='intent' and row.get('planned') is not None:os._exit(71)
                            if phase=='ack' and row.get('planned') is None and row['ack']!=row['original']:os._exit(71)
                        return None
                    p._save=save
                    if phase=='restore':
                        original=p.os.chmod
                        def crash(path,*args):
                            result=original(path,*args)
                            if Path(path)==self.node:os._exit(71)
                            return result
                        p.os.chmod=crash;self.restore()
                    else:self.grant()
                    os._exit(0)
                self.assertEqual(os.waitstatus_to_exitcode(os.waitpid(pid,0)[1]),71)
                if phase!='restore':self.assertEqual(self.grant(),[])
                self.assertEqual(self.restore(),[])
                self.assertEqual((self.node.stat().st_gid,stat.S_IMODE(self.node.stat().st_mode)),(0,0o600))
                self.state.unlink()
    def test_unavailable_metadata_leaves_sensor_unreadable(self):
        with patch.object(p.os,'chown',side_effect=PermissionError('read-only sysfs')):
            self.assertEqual(self.grant(),[])
        self.assertEqual((self.node.stat().st_gid,stat.S_IMODE(self.node.stat().st_mode)),(0,0o600))
        self.assertEqual(self.restore(),[])
    def test_other_boot_is_not_restored(self):
        self.grant()
        with self.assertRaises(ValueError):p.restore(state_path=self.state,boot='other')
        self.assertEqual(self.node.stat().st_gid,65534)
        self.assertEqual(self.restore(),[])
    def test_crash_after_chown_and_chmod(self):
        for operation in ('chown','chmod'):
            with self.subTest(operation=operation):
                pid=os.fork()
                if not pid:
                    real=getattr(p.os,operation)
                    def crash(path,*args):
                        result=real(path,*args)
                        if Path(path)==self.node:os._exit(73)
                        return result
                    setattr(p.os,operation,crash);self.grant();os._exit(0)
                self.assertEqual(os.waitstatus_to_exitcode(os.waitpid(pid,0)[1]),73)
                self.assertEqual(self.grant(),[]);self.assertEqual(self.restore(),[])
                self.assertEqual((self.node.stat().st_uid,self.node.stat().st_gid,stat.S_IMODE(self.node.stat().st_mode)),(0,0,0o600))
                self.state.unlink()
if __name__=='__main__':unittest.main()
