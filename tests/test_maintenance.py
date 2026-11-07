import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maintenance import Engine,MaintenanceError
from protocol import ProtocolError
from test_protocol import component


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name)
        self.root=self.base/"root"; self.root.mkdir(); self.payload=self.base/"package"
        (self.payload/"payload").mkdir(parents=True); (self.payload/"payload/test").write_bytes(b"ok")
        (self.root/"etc").mkdir(); (self.root/"etc/test").write_bytes(b"original")
        self.engine=Engine(self.root)
    def tearDown(self): self.temp.cleanup()

    def test_apply_verify_restore_original(self):
        result=self.engine.apply(component(),self.payload)
        self.assertTrue(self.engine.verify("core:decky")["ok"])
        self.assertEqual((self.root/"etc/test").read_bytes(),b"ok")
        self.engine.restore(result["transaction"])
        self.assertEqual((self.root/"etc/test").read_bytes(),b"original")
        with self.assertRaises(MaintenanceError): self.engine.verify("core:decky")

    def test_device_mismatch_warns_and_applies(self):
        item = component(); item['compatibility'] = {'device_ids':['known']}
        result = self.engine.apply(item, self.payload, environment={'device_id':'other'})
        self.assertEqual(result['device_warning']['actual_device_id'], 'other')
        self.assertTrue(self.engine.verify(item['id'])['ok'])
        self.engine.restore(result['transaction'])

    def test_binary_new_install_needs_no_legacy_store_and_creates_public_system_parents(self):
        result=self.engine.apply(component(target='etc/new-system/runtime/file'),self.payload)
        target=self.root/'etc/new-system/runtime/file'
        self.assertEqual(target.read_bytes(),b'ok')
        self.assertFalse((self.root/'home/.oxp3-maintenance').exists())
        journal=json.loads(self.engine._journal(result['transaction']).read_bytes())
        self.assertFalse(journal['legacy_lock_required'])
        self.assertEqual(journal['operations'][0]['after']['storage'],'blob-v1')
        self.assertNotIn('data',journal['operations'][0]['after'])
        if os.name != 'nt':
            self.assertEqual(target.parent.stat().st_mode & 0o777,0o755)
        self.engine.restore(result['transaction'])
        self.assertFalse(target.exists())

    def test_ownership_and_modified_restore(self):
        tx=self.engine.apply(component(),self.payload)
        with self.assertRaises(MaintenanceError): self.engine.apply(component("wifi:intel"),self.payload)
        (self.root/"etc/test").write_bytes(b"user change")
        self.assertFalse(self.engine.verify("core:decky")["ok"])
        with self.assertRaises(MaintenanceError): self.engine.restore(tx["transaction"])
        self.assertEqual((self.root/"etc/test").read_bytes(),b"user change")

    def test_restore_preserves_later_other_component(self):
        a=self.engine.apply(component(),self.payload)
        b=self.engine.apply(component("wifi:intel",target="etc/wifi"),self.payload)
        self.engine.restore(a["transaction"])
        self.assertTrue(self.engine.verify("wifi:intel")["ok"])
        self.engine.restore(b["transaction"])
        self.assertFalse((self.root/"etc/wifi").exists())

    def test_crash_pending_rolls_back(self):
        original=self.engine._put_snapshot
        calls=[]
        def crash(path,snapshot,**kwargs):
            original(path,snapshot,**kwargs); calls.append(path)
            if len(calls)==1: raise RuntimeError("simulated crash")
        # Suppress automatic recovery to simulate process death after durable write.
        with patch.object(self.engine,"_put_snapshot",side_effect=crash),patch.object(self.engine,"_recover",return_value=[]):
            with self.assertRaises(RuntimeError): self.engine.apply(component(),self.payload)
        self.assertEqual((self.root/"etc/test").read_bytes(),b"ok")
        self.assertEqual(len(Engine(self.root).recover()),1)
        self.assertEqual((self.root/"etc/test").read_bytes(),b"original")

    def test_bad_hash_rejects_before_journal(self):
        c=component(); c["files"][0]["sha256"]="0"*64
        with self.assertRaises(ProtocolError): self.engine.apply(c,self.payload)
        self.assertEqual(list(self.engine.journals.iterdir()),[])
        self.assertEqual((self.root/"etc/test").read_bytes(),b"original")

    def test_legacy_evidence_preserves_original(self):
        state=self.base/"legacy.json"; key=self.base/"legacy.pem"
        state.write_bytes(b'{"legacy":true}'); key.write_bytes(b"existing public key")
        report=self.engine.inspect_legacy(state,key)
        self.assertFalse(report["migration_complete"])
        self.assertEqual(state.read_bytes(),b'{"legacy":true}')
        self.assertEqual(key.read_bytes(),b"existing public key")

    def test_upgrade_does_not_drop_owned_paths(self):
        self.engine.apply(component(),self.payload)
        c=component(); c["files"]=[]
        with self.assertRaises(MaintenanceError): self.engine.apply(c,self.payload)

    def test_upgrade_refuses_modified_owned_file(self):
        self.engine.apply(component(),self.payload)
        (self.root/"etc/test").write_bytes(b"user settings")
        with self.assertRaises(MaintenanceError): self.engine.apply(component(),self.payload)
        self.assertEqual((self.root/"etc/test").read_bytes(),b"user settings")

    def test_dependency_restore_is_blocked_while_in_use(self):
        first=self.engine.apply(component(),self.payload)
        second=component("wifi:intel",target="etc/wifi",requires=["core:decky"])
        self.engine.apply(second,self.payload)
        with self.assertRaises(MaintenanceError): self.engine.restore(first["transaction"])

    def test_unmet_dependencies_and_compatibility_fail_before_write(self):
        c=component(requires=["core:missing"])
        with self.assertRaises(MaintenanceError): self.engine.apply(c,self.payload)
        c=component(); c["compatibility"]={"architectures":["x86_64"]}
        with self.assertRaises(MaintenanceError): self.engine.apply(c,self.payload)

    def _multi_component(self):
        entries=[]
        for name in ('a','b','c'):
            data=('value-'+name).encode()
            source=self.payload/"multi"/(name)
            source.parent.mkdir(parents=True,exist_ok=True); source.write_bytes(data)
            entries.append(dict(source='multi/'+name,target='etc/multi/'+name,
                                sha256=hashlib.sha256(data).hexdigest(),size=len(data),mode=0o644))
        return dict(schema_version=1,kind='ComponentManifest',id='fixture:multi',version='1.0.0',
                    requires=[],files=entries)

    @unittest.skipUnless(os.name=='posix','POSIX directory durability is required')
    def test_apply_syncs_each_changed_directory_once_and_restore_keeps_per_file_barriers(self):
        target_dir=self.root/"etc/multi"
        def counted(action):
            total=0; real=os.fsync
            def counting(descriptor):
                nonlocal total
                info=os.fstat(descriptor)
                try: directory=os.stat(target_dir)
                except FileNotFoundError: return real(descriptor)
                if (info.st_dev,info.st_ino)==(directory.st_dev,directory.st_ino): total+=1
                return real(descriptor)
            with patch('os.fsync',counting): action()
            return total
        result=None
        def apply():
            nonlocal result
            result=self.engine.apply(self._multi_component(),self.payload)
        self.assertEqual(counted(apply),1)
        self.assertEqual(counted(lambda: self.engine.restore(result['transaction'])),3)
        self.assertFalse(target_dir.joinpath('a').exists())
        # A second apply recreates the files; its three replaces share one sync.
        replaced=None
        def reapply():
            nonlocal replaced
            replaced=self.engine.apply(self._multi_component(),self.payload)
        self.assertEqual(counted(reapply),1)
        self.assertEqual(counted(lambda: self.engine.restore(replaced['transaction'])),3)

    @unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'Private CAS verification requires a root-owned store')
    def test_repeated_private_blob_verification_reuses_the_verified_signature(self):
        from maintenance import adoption_blobs as implementation
        data=b'private blob bytes'; source=self.payload/"blob-source"; source.write_bytes(data)
        source.chmod(0o600)
        entry=dict(sha256=hashlib.sha256(data).hexdigest(),size=len(data))
        implementation._cache_blob(self.engine,source,entry)
        target=implementation.blob_cache(self.engine)/entry['sha256']
        implementation._VERIFIED_PRIVATE.clear()
        real=hashlib.sha256; calls=[]
        def counting(*args,**kwargs):
            calls.append(1); return real(*args,**kwargs)
        with patch.object(implementation.hashlib,'sha256',counting):
            implementation._verified_private(target,entry)
            self.assertEqual(len(calls),1)
            implementation._verified_private(target,entry)
            self.assertEqual(len(calls),1)
        # Any content or metadata change invalidates the reused digest.
        os.chmod(target,0o600)
        os.utime(target,None)
        with patch.object(implementation.hashlib,'sha256',counting):
            implementation._verified_private(target,entry)
        self.assertEqual(len(calls),2)


if __name__=="__main__": unittest.main()
