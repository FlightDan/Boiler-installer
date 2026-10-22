import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from protocol import (ProtocolError, validate_manifest, validate_package, resolve_plan,
                      safe_extract_archive, DeveloperTrustSession, load_json,
                      verify_signed_index)


def component(identity="core:decky",data=b"ok",target="etc/test",requires=None):
    return {"schema_version":1,"kind":"ComponentManifest","id":identity,"version":"1.0.0",
            "requires":requires or [],"files":[{"source":"payload/test","target":target,
            "sha256":hashlib.sha256(data).hexdigest(),"size":len(data),"mode":420}]}


def package(root):
    (root/"payload").mkdir(); (root/"payload/test").write_bytes(b"ok")
    comp=component()
    manifest={"schema_version":1,"kind":"DevicePackage","id":"device:test","version":"1.0.0",
              "source":{"kind":"local","path":"fixture"},"components":[comp],
              "files":[{"path":"payload/test","sha256":comp["files"][0]["sha256"],"size":2}]}
    (root/"manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
    return manifest


class ProtocolTests(unittest.TestCase):
    def test_traversal_and_duplicate_targets(self):
        for target in ("../etc/test","/etc/test","etc/../test","C:/test","etc\\test","etc/CON","etc/test."):
            with self.subTest(target=target),self.assertRaises(ProtocolError):
                validate_manifest(component(target=target))
        c=component(); c["files"].append(dict(c["files"][0],target="ETC/Test"))
        with self.assertRaises(ProtocolError): validate_manifest(c)

    def test_dependency_order_cycle_conflict_collision(self):
        a=component("core:a",target="etc/a",requires=["core:b"])
        b=component("core:b",target="etc/b")
        self.assertEqual([c["id"] for c in resolve_plan([a,b],["core:a"],{})["components"]],["core:b","core:a"])
        b["requires"]=["core:a"]
        with self.assertRaises(ProtocolError): resolve_plan([a,b],["core:a"],{})
        b["requires"]=[]; b["conflicts"]=["core:a"]
        with self.assertRaises(ProtocolError): resolve_plan([a,b],["core:a"],{})
        b.pop("conflicts"); b["files"][0]["target"]="etc/a/child"
        with self.assertRaises(ProtocolError): resolve_plan([a,b],["core:a"],{})

    def test_compatibility_and_modes(self):
        c=component(); c["compatibility"]={"device_ids":["known"]}
        with self.assertRaises(ProtocolError): resolve_plan([c],[c["id"]],{})
        self.assertEqual(resolve_plan([c],[c["id"]],{},mode="imported")["components"],[])
        self.assertEqual(len(resolve_plan([component()],["core:decky"],{},mode="vanilla")["components"]),1)

    def test_engine_and_kernel_compatibility(self):
        c=component(); c['minimum_engine_version']='99.0.0'
        with self.assertRaises(ProtocolError): resolve_plan([c],[c['id']],{})
        c['minimum_engine_version']='0.1.0'; c['compatibility']={'kernels':['6.1-known']}
        for env in ({}, {'kernels':['6.1-other']}, {'kernels':['6.1-known','6.1-other']}):
            with self.assertRaises(ProtocolError): resolve_plan([c],[c['id']],env)
        self.assertEqual(len(resolve_plan([c],[c['id']],{'kernels':['6.1-known']})['components']),1)

    def test_exact_inventory_digest_and_session_trust(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); package(root)
            with self.assertRaises(ProtocolError): validate_package(root)
            trust=DeveloperTrustSession(); trust.grant_local(root); trust.validate_local(root)
            with self.assertRaises(ProtocolError): DeveloperTrustSession().validate_local(root)
            (root/"payload/test").write_bytes(b"no")
            with self.assertRaises(ProtocolError): trust.validate_local(root)
            (root/"payload/test").write_bytes(b"ok"); (root/"extra").write_bytes(b"unlisted")
            with self.assertRaises(ProtocolError): trust.validate_local(root)

    def test_duplicate_json_and_mutable_source_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"x"; p.write_text('{"a":1,"a":2}')
            with self.assertRaises(ProtocolError): load_json(p)
        with self.assertRaises(ProtocolError): DeveloperTrustSession().github_source("owner/repo","main")

    def test_archive_atomic_and_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); archive=root/"x.zip"; output=root/"out"
            for entries in (("../escape",),("A","a"),("test",)):
                with zipfile.ZipFile(archive,"w") as stream:
                    for name in entries: stream.writestr(name,b"12345")
                with self.assertRaises(ProtocolError): safe_extract_archive(archive,output,max_bytes=4)
                self.assertFalse(output.exists())
            with zipfile.ZipFile(archive,"w") as stream: stream.writestr("payload/test",b"ok")
            safe_extract_archive(archive,output)
            self.assertEqual((output/"payload/test").read_bytes(),b"ok")

    def test_signature_exact_bytes(self):
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        except ImportError: self.skipTest("cryptography unavailable")
        key=Ed25519PrivateKey.generate()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); index=root/"index.json"; signature=root/"index.sig"; pem=root/"key.pem"
            index.write_bytes(json.dumps({"schema_version":1,"kind":"DeviceIndex","id":"index:devices","version":"1.0.0","generated_at":"2026-10-22T00:00:00Z","sequence":1,"packages":[]}).encode())
            pem.write_bytes(key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))
            signature.write_bytes(key.sign(index.read_bytes()))
            verify_signed_index(index,signature,pem)
            index.write_bytes(index.read_bytes()+b"\n")
            with self.assertRaises(ProtocolError): verify_signed_index(index,signature,pem)


if __name__=="__main__": unittest.main()
