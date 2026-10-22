import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from protocol import ProtocolError
from protocol.fetch import fetch_index,fetch_package,cached_index,fetch_github
from test_protocol import package


class FetchTests(unittest.TestCase):
    def setUp(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.key=Ed25519PrivateKey.generate(); self.pem=self.root/"public.pem"
        self.pem.write_bytes(self.key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))
        self.cache=self.root/"cache"; self.responses={}
    def tearDown(self): self.temp.cleanup()
    def download(self,url,path,**kwargs):
        Path(path).write_bytes(self.responses[url]); return {}
    def index(self,sequence,packages=None,revocations=None):
        value={"schema_version":1,"kind":"DeviceIndex","id":"index:devices","version":"1.0.0",
               "generated_at":"2026-10-22T00:00:00Z","sequence":sequence,"packages":packages or [],"revocations":revocations or []}
        raw=json.dumps(value).encode(); self.responses["https://registry/index"]=raw
        self.responses["https://registry/sig"]=self.key.sign(raw)
        return value
    def acquire_index(self):
        return fetch_index("https://registry/index","https://registry/sig",self.cache,public_key=self.pem)

    @patch("protocol.fetch.download")
    def test_monotonic_signature_and_same_sequence(self,download):
        download.side_effect=self.download
        self.index(2); self.acquire_index(); self.assertEqual(cached_index(self.cache,public_key=self.pem)["sequence"],2)
        self.index(1)
        with self.assertRaises(ProtocolError): self.acquire_index()
        self.index(2,revocations=["0"*64])
        with self.assertRaises(ProtocolError): self.acquire_index()
        self.index(3); self.responses["https://registry/sig"]=b"x"*64
        with self.assertRaises(ProtocolError): self.acquire_index()

    @patch("protocol.fetch.download")
    def test_fetch_pinned_package_and_revocation(self,download):
        download.side_effect=self.download
        source=self.root/"source"; source.mkdir(); package(source)
        digest=hashlib.sha256((source/"manifest.json").read_bytes()).hexdigest()
        archive=self.root/"package.zip"
        with zipfile.ZipFile(archive,"w") as stream:
            for file in source.rglob("*"):
                if file.is_file(): stream.write(file,"repo-commit/"+file.relative_to(source).as_posix())
        self.responses["https://codeload.github.com/owner/repo/zip/"+"1"*40]=archive.read_bytes()
        entry={"id":"device:test","version":"1.0.0","sha256":digest,"size":(source/"manifest.json").stat().st_size,
               "source":{"kind":"github","repository":"owner/repo","commit":"1"*40}}
        self.index(1,[entry]); self.acquire_index()
        result=fetch_package(self.cache,"device:test","1.0.0",self.root/"ready",public_key=self.pem)
        self.assertEqual(result["package"]["id"],"device:test")
        self.index(2,[entry],[digest]); self.acquire_index()
        with self.assertRaises(ProtocolError): fetch_package(self.cache,"device:test","1.0.0",self.root/"revoked",public_key=self.pem)
        self.assertFalse((self.root/"revoked").exists())

    def test_github_requires_explicit_developer_mode(self):
        with self.assertRaises(ProtocolError): fetch_github("https://github.com/owner/repo",self.root/"out")


if __name__=="__main__": unittest.main()
