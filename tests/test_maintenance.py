import json
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
        def crash(path,snapshot):
            original(path,snapshot); calls.append(path)
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


if __name__=="__main__": unittest.main()
