import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from maintenance.overlay_home import Store

@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'requires Linux root')
class HomeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.root.chmod(0o755)
        (self.root/'etc').mkdir();(self.root/'etc/passwd').write_text('deck:x:65534:65534::/home/deck:/bin/sh\n')
        (self.root/'home/deck/.config/MangoHud').mkdir(parents=True)
        os.chown(self.root/'home/deck',65534,65534);os.chown(self.root/'home/deck/.config',65534,65534);os.chown(self.root/'home/deck/.config/MangoHud',65534,65534)
        self.target=self.root/'home/deck/.config/MangoHud/presets.conf';self.original=b'[preset 0]\nfoo\n[preset 2]\nbar\n';self.target.write_bytes(self.original);os.chown(self.target,65534,65534)
        self.store=Store(self.root);self.g='a'*32;self.a='11111111-1111-1111-1111-111111111111';self.b='22222222-2222-2222-2222-222222222222';self.account=SimpleNamespace(pw_uid=65534,pw_gid=65534)
    def tearDown(self):self.temp.cleanup()
    def acquire(self,owner):return self.store.acquire(self.g,owner,lambda x:None,self.account)
    def release(self,owner):return self.store.release(self.g,owner,lambda x:None,self.account)
    def test_two_holders_restore(self):
        self.acquire(self.a);owned=self.target.read_bytes();self.assertNotEqual(owned,self.original)
        self.acquire(self.b);self.assertEqual(self.target.read_bytes(),owned)
        self.assertEqual(self.release(self.a)['status'],'other-slot-retains');self.assertEqual(self.target.read_bytes(),owned)
        self.assertEqual(self.release(self.b)['status'],'restored');self.assertEqual(self.target.read_bytes(),self.original)
    def test_crash_after_write_retries_without_resnapshot(self):
        import maintenance.overlay_home as module
        pid=os.fork()
        if not pid:
            real=module.blobs.restore_blob
            def crash(*args):real(*args);os._exit(73)
            module.blobs.restore_blob=crash;self.acquire(self.a);os._exit(0)
        self.assertEqual(os.waitstatus_to_exitcode(os.waitpid(pid,0)[1]),73)
        self.acquire(self.a);self.acquire(self.b)
        self.assertEqual(self.release(self.a)['status'],'other-slot-retains')
        self.assertEqual(self.release(self.b)['status'],'restored');self.assertEqual(self.target.read_bytes(),self.original)
    def test_crash_after_restoring_bytes_retries(self):
        import maintenance.overlay_home as module
        self.acquire(self.a)
        pid=os.fork()
        if not pid:
            real=module.blobs.restore_blob
            def crash(*args):real(*args);os._exit(73)
            module.blobs.restore_blob=crash;self.release(self.a);os._exit(0)
        self.assertEqual(os.waitstatus_to_exitcode(os.waitpid(pid,0)[1]),73)
        self.assertEqual(self.release(self.a)['status'],'restored');self.assertEqual(self.target.read_bytes(),self.original)
    def test_external_edit_preserved(self):
        self.acquire(self.a);self.target.write_bytes(self.target.read_bytes()+b'# user edit\n')
        self.assertEqual(self.release(self.a)['status'],'user-changed-preserved');self.assertTrue(self.target.read_bytes().endswith(b'# user edit\n'))
    def test_never_acquired_is_optional_only_without_recorded_epoch(self):
        from maintenance.resource_runtime import ResourceError
        with self.assertRaisesRegex(ResourceError,'ownership missing'):self.release(self.a)
        self.assertEqual(self.store.release(self.g,self.a,lambda x:None,self.account,allow_unacquired=True)['status'],'never-acquired')
        self.assertEqual(self.target.read_bytes(),self.original)
        self.acquire(self.a)
        with self.assertRaisesRegex(ResourceError,'ownership missing'):
            self.store.release('b'*32,self.a,lambda x:None,self.account,allow_unacquired=True)
    def test_failed_before_or_after_config_write_can_restore_without_finishing_acquisition(self):
        import maintenance.overlay_home as module
        for after_write in (False,True):
            with self.subTest(after_write=after_write):
                pid=os.fork()
                if not pid:
                    real=module.blobs.restore_blob
                    def crash(*args):
                        if after_write:real(*args)
                        os._exit(73)
                    module.blobs.restore_blob=crash;self.acquire(self.a);os._exit(0)
                self.assertEqual(os.waitstatus_to_exitcode(os.waitpid(pid,0)[1]),73)
                self.assertEqual(self.release(self.a)['status'],'restored')
                self.assertEqual(self.target.read_bytes(),self.original)
                self.assertEqual(self.release(self.b)['status'],'already-restored')
if __name__=='__main__':unittest.main()
