import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maintenance import Engine
from maintenance.engine import _json_write, _read
from protocol import fetch


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


bootstrap = load_module("boiler_maintenance_bootstrap_tests", ROOT / "scripts/maintenance_bootstrap.py")
builder = load_module("boiler_maintenance_bootstrap_builder", ROOT / "scripts/package-maintenance-bootstrap.py")


class Signer:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.public_key = self.directory / "test-public.pem"
        self.openssl = shutil.which("openssl")
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            if not self.openssl:
                raise unittest.SkipTest("Ed25519 tests require OpenSSL or the optional cryptography module")
            self.private_key = self.directory / "test-private.pem"
            subprocess.run([self.openssl, "genpkey", "-algorithm", "ED25519", "-out", str(self.private_key)],
                           check=True, capture_output=True, timeout=30)
            subprocess.run([self.openssl, "pkey", "-in", str(self.private_key), "-pubout", "-out", str(self.public_key)],
                           check=True, capture_output=True, timeout=30)
            self._serialization = None
            self._key = None
        else:
            self._key = Ed25519PrivateKey.generate()
            self._serialization = serialization
            self.public_key.write_bytes(self._key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))

    def sign(self, data):
        if self._key is not None:
            return self._key.sign(data)
        message = self.directory / "message.bin"
        signature = self.directory / "signature.bin"
        message.write_bytes(data)
        subprocess.run([self.openssl, "pkeyutl", "-sign", "-inkey", str(self.private_key), "-rawin",
                        "-in", str(message), "-out", str(signature)], check=True, capture_output=True, timeout=30)
        return signature.read_bytes()


class MaintenanceBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="boiler-bootstrap-tests-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.release = json.loads((ROOT / "maintenance/release.json").read_text(encoding="utf-8"))
        self.release_path = self.work / "release.json"
        self.release_path.write_text(json.dumps(self.release), encoding="utf-8")
        self.target = self.make_target()
        self.signer = Signer(self.work)
        self.index_url = "https://registry.example.invalid/maintenance/index.json"
        self.signature_url = "https://registry.example.invalid/maintenance/index.sig"
        self.asset_url = ("https://github.com/FlightDan/Boiler-installer/releases/download/"
                          "maintenance-v0.1.0/boiler-maintenance.zip")

    def make_target(self, version="3.10"):
        root = self.work / ("target-" + str(len(list(self.work.glob("target-*")))))
        (root / "etc").mkdir(parents=True)
        (root / "etc/os-release").write_text(f'ID=steamos\nVERSION_ID="{version}"\n', encoding="utf-8")
        binary = bytearray(20)
        binary[:6] = b"\x7fELF\x02\x01"
        binary[18:20] = (62).to_bytes(2, "little")
        (root / "usr/bin").mkdir(parents=True)
        (root / "usr/bin/env").write_bytes(binary)
        (root / "usr/lib/modules").mkdir(parents=True)
        return root

    def artifact(self, *, hooks=False, hook_marker=None, release=None):
        release = release or self.release
        version = release["version"]
        payload = {"payload/maintenance.txt": b"verified Boiler maintenance runtime\n",
                   "release.json": json.dumps(release, sort_keys=True).encode("utf-8")}
        component = {
            "schema_version": 1, "kind": "ComponentManifest", "id": bootstrap.COMPONENT_ID,
            "version": version, "compatibility": release["compatibility"],
            "files": [{"source": "payload/maintenance.txt", "target": "usr/local/lib/boiler/maintenance.txt",
                       "size": len(payload["payload/maintenance.txt"]),
                       "sha256": hashlib.sha256(payload["payload/maintenance.txt"]).hexdigest(), "mode": 0o644}],
        }
        if hooks:
            marker = str(hook_marker) if hook_marker else "/tmp/boiler-bootstrap-hook-ran"
            payload["scripts/apply.sh"] = ("#!/bin/sh\ntouch " + marker + "\n").encode()
            component["hooks"] = {"apply": "scripts/apply.sh"}
        inventory = [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                     for name, data in sorted(payload.items())]
        manifest = {
            "schema_version": 1, "kind": "DevicePackage", "id": bootstrap.PACKAGE_ID,
            "purpose": "maintenance", "version": version,
            "source": {"kind": "github", "repository": bootstrap.REPOSITORY, "commit": "a" * 40},
            "minimum_engine_version": release["minimum_engine_version"],
            "compatibility": release["compatibility"], "components": [component], "files": inventory,
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, data in sorted({**payload, "manifest.json": manifest_bytes}.items()):
                archive.writestr(name, data)
        archive_data = archive_bytes.getvalue()
        archive_path = self.work / ("maintenance-" + str(len(list(self.work.glob("maintenance-*.zip")))) + ".zip")
        archive_path.write_bytes(archive_data)
        entry = {
            "id": bootstrap.PACKAGE_ID, "version": version, "size": len(manifest_bytes),
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(), "compatibility": release["compatibility"],
            "minimum_engine_version": release["minimum_engine_version"],
            "source": {"kind": "https", "url": self.asset_url, "size": len(archive_data),
                       "sha256": hashlib.sha256(archive_data).hexdigest()},
        }
        return archive_path, manifest, entry

    def index(self, *, packages=(), registry=None, sequence=1):
        value = {
            "schema_version": 1, "kind": "DeviceIndex", "id": registry or bootstrap.REGISTRY_ID,
            "version": "0.1.0", "sequence": sequence, "generated_at": "2026-10-30T00:00:00Z",
            "revocations": [], "packages": list(packages),
        }
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return raw, self.signer.sign(raw)

    def downloader(self, raw, signature, archive_path=None):
        by_url = {self.index_url: raw, self.signature_url: signature}
        if archive_path is not None:
            by_url[self.asset_url] = archive_path.read_bytes()

        def download(url, path, *, maximum, expected=None, timeout=60):
            data = by_url[url]
            if len(data) > maximum:
                raise AssertionError("Fixture exceeds download limit")
            digest = hashlib.sha256(data).hexdigest()
            if expected and (expected["size"] != len(data) or expected["sha256"] != digest):
                raise AssertionError("Fixture differs from signed download identity")
            Path(path).write_bytes(data)
            return {"sha256": digest, "size": len(data)}
        return download

    def install(self, target=None, *, packages=None, registry=None, signature=None, archive_path=None,
                release=None):
        target = target or self.target
        raw, signed = self.index(packages=packages or [], registry=registry)
        signed = signature if signature is not None else signed
        with mock.patch.object(fetch, "download", side_effect=self.downloader(raw, signed, archive_path)):
            return bootstrap.install(target, self.index_url, self.signature_url,
                                     release_path=self.release_path, public_key=self.signer.public_key)

    def test_signed_package_installs_via_engine_and_can_be_restored(self):
        archive, _manifest, entry = self.artifact()
        result = self.install(packages=[entry], archive_path=archive)
        self.assertEqual(result["status"], "installed")
        installed = self.target / "usr/local/lib/boiler/maintenance.txt"
        self.assertEqual(installed.read_bytes(), b"verified Boiler maintenance runtime\n")
        engine = Engine(self.target)
        self.assertTrue(engine.verify(bootstrap.COMPONENT_ID)["ok"])
        self.assertEqual(engine.restore(result["transaction"])["status"], "restored")
        self.assertFalse(installed.exists())
        self.assertEqual(engine._state()["components"], {})

    def test_missing_candidate_version_and_model_registry_are_rejected_before_state_writes(self):
        archive, _manifest, entry = self.artifact()
        for registry, packages, expected in (
                (bootstrap.REGISTRY_ID, [], "not published"),
                ("registry:boiler-devices", [entry], "not the generic")):
            with self.subTest(registry=registry, packages=bool(packages)):
                target = self.make_target()
                raw, signature = self.index(registry=registry, packages=packages)
                with mock.patch.object(fetch, "download", side_effect=self.downloader(raw, signature, archive)):
                    with self.assertRaisesRegex(bootstrap.BootstrapError, expected):
                        bootstrap.install(target, self.index_url, self.signature_url,
                                          release_path=self.release_path, public_key=self.signer.public_key)
                self.assertFalse((target / "var/lib/boiler/maintenance").exists())
                self.assertFalse((target / "usr/local/lib/boiler").exists())

    def test_invalid_signature_incompatible_target_and_revocation_fail_before_apply(self):
        archive, _manifest, entry = self.artifact()
        raw, signature = self.index(packages=[entry])
        for name, target, signed, expected in (
                ("bad-signature", self.make_target(), b"x" * 64, "signature rejected"),
                ("incompatible", self.make_target("3.9"), signature, "incompatible")):
            with self.subTest(name=name):
                with mock.patch.object(fetch, "download", side_effect=self.downloader(raw, signed, archive)):
                    with self.assertRaisesRegex((bootstrap.BootstrapError, ValueError), expected):
                        bootstrap.install(target, self.index_url, self.signature_url,
                                          release_path=self.release_path, public_key=self.signer.public_key)
                self.assertFalse((target / "var/lib/boiler/maintenance").exists())

        revoked = dict(entry)
        revoked_index = {
            "schema_version": 1, "kind": "DeviceIndex", "id": bootstrap.REGISTRY_ID,
            "version": "0.1.0", "sequence": 2, "generated_at": "2026-10-30T00:00:00Z",
            "revocations": [entry["sha256"]], "packages": [revoked],
        }
        raw = json.dumps(revoked_index, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with mock.patch.object(fetch, "download", side_effect=self.downloader(raw, self.signer.sign(raw), archive)):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "revoked"):
                bootstrap.install(self.make_target(), self.index_url, self.signature_url,
                                  release_path=self.release_path, public_key=self.signer.public_key)

    def test_existing_runtime_state_and_downloaded_hooks_are_refused_without_execution(self):
        archive, _manifest, entry = self.artifact()
        target = self.make_target()
        collision = target / "usr/local/lib/boiler/foreign.txt"
        collision.parent.mkdir(parents=True)
        collision.write_text("preserve", encoding="utf-8")
        raw, signature = self.index(packages=[entry])
        with mock.patch.object(fetch, "download", side_effect=AssertionError("must stop before network")):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "runtime path has content"):
                bootstrap.install(target, self.index_url, self.signature_url,
                                  release_path=self.release_path, public_key=self.signer.public_key)
        self.assertEqual(collision.read_text(encoding="utf-8"), "preserve")

        target = self.make_target()
        unknown_state = target / "var/lib/boiler/maintenance/unknown.json"
        unknown_state.parent.mkdir(parents=True)
        unknown_state.write_text("keep", encoding="utf-8")
        with mock.patch.object(fetch, "download", side_effect=AssertionError("must stop before network")):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "Unknown Boiler maintenance state"):
                bootstrap.install(target, self.index_url, self.signature_url,
                                  release_path=self.release_path, public_key=self.signer.public_key)
        self.assertEqual(unknown_state.read_text(encoding="utf-8"), "keep")

        marker = self.work / "hook-executed"
        archive, _manifest, entry = self.artifact(hooks=True, hook_marker=marker)
        raw, signature = self.index(packages=[entry])
        target = self.make_target()
        with mock.patch.object(fetch, "download", side_effect=self.downloader(raw, signature, archive)):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "static file component"):
                bootstrap.install(target, self.index_url, self.signature_url,
                                  release_path=self.release_path, public_key=self.signer.public_key)
        self.assertFalse(marker.exists())
        self.assertFalse((target / "var/lib/boiler/maintenance").exists())

    def test_engine_recovery_preserves_journal_and_allows_retry_in_empty_paths(self):
        archive, manifest, _entry = self.artifact()
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(self.work / "package")
        package_root = self.work / "package"
        engine = Engine(self.target)
        component = manifest["components"][0]
        result = engine.apply(component, package_root,
                              environment={"os_id": "steamos", "os_version": "3.10",
                                           "architecture": "x86_64", "kernels": []})
        journal_path = engine._journal(result["transaction"])
        journal = _read(journal_path)
        journal["status"] = "applying"
        _json_write(journal_path, journal)
        _json_write(engine.state_dir / "state.json", journal["before_state"])
        self.assertTrue((self.target / "usr/local/lib/boiler/maintenance.txt").exists())

        recovered = bootstrap.recover(self.target)
        self.assertEqual(recovered["transactions"], [result["transaction"]])
        self.assertEqual(_read(journal_path)["status"], "rolled_back")
        self.assertFalse((self.target / "usr/local/lib/boiler/maintenance.txt").exists())
        bootstrap._safe_first_install(self.target)
        self.assertTrue((self.target / "usr/local/lib/boiler").is_dir())

    def test_zipapp_is_reproducible_and_runs_isolated_from_cwd_and_pythonpath(self):
        first, second = self.work / "first.pyz", self.work / "second.pyz"
        report = builder.package(ROOT, first)
        builder.package(ROOT, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with self.assertRaisesRegex(ValueError, "only packages the checkout"):
            builder.package(self.work, self.work / "wrong-root.pyz")
        self.assertEqual(report["trusted_public_key_sha256"], builder.PINNED_PUBLIC_KEY_SHA256)
        with zipfile.ZipFile(first) as archive:
            names = set(archive.namelist())
            self.assertIn("__main__.py", names)
            self.assertIn("boiler_bootstrap.py", names)
            self.assertIn("protocol/trusted-public-key.pem", names)
            self.assertIn("maintenance/release.json", names)
            self.assertNotIn("scripts/maintenance_bootstrap.py", names)

        hostile = self.work / "hostile"
        hostile.mkdir()
        marker = self.work / "site-customize-ran"
        poison = "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('bad')\n"
        (hostile / "sitecustomize.py").write_text(poison, encoding="utf-8")
        (hostile / "maintenance.py").write_text(poison + "raise RuntimeError('hostile module loaded')\n", encoding="utf-8")
        environment = dict(os.environ, PYTHONPATH=str(hostile))
        help_result = subprocess.run([sys.executable, "-I", "-B", str(first), "--help"], cwd=hostile,
                                     env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--index-url", help_result.stdout)
        self.assertIn("--recover", help_result.stdout)
        self.assertFalse(marker.exists())

        raw, signature = self.index()
        index_path, signature_path = self.work / "signed-index.json", self.work / "signed-index.sig"
        index_path.write_bytes(raw)
        signature_path.write_bytes(signature)
        verify_code = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from protocol.manifest import verify_signed_index; "
            "print(verify_signed_index(sys.argv[2], sys.argv[3], sys.argv[4])['id'])"
        )
        verified = subprocess.run([sys.executable, "-I", "-B", "-c", verify_code,
                                   str(first), str(index_path), str(signature_path), str(self.signer.public_key)],
                                  cwd=hostile, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(verified.stdout.strip(), bootstrap.REGISTRY_ID)
        self.assertFalse(marker.exists())

        archive, _manifest, entry = self.artifact()
        raw, signature = self.index(packages=[entry])
        index_path.write_bytes(raw)
        signature_path.write_bytes(signature)
        target = self.make_target()
        install_code = '''
import hashlib, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from protocol import fetch
payloads = {sys.argv[7]: pathlib.Path(sys.argv[3]).read_bytes(),
            sys.argv[8]: pathlib.Path(sys.argv[4]).read_bytes(),
            sys.argv[9]: pathlib.Path(sys.argv[5]).read_bytes()}
def download(url, path, *, maximum, expected=None, timeout=60):
    data = payloads[url]
    digest = hashlib.sha256(data).hexdigest()
    if len(data) > maximum or (expected and (expected["size"] != len(data) or expected["sha256"] != digest)):
        raise ValueError("fixture digest mismatch")
    pathlib.Path(path).write_bytes(data)
    return {"sha256": digest, "size": len(data)}
fetch.download = download
import boiler_bootstrap as bootstrap
print(json.dumps(bootstrap.install(pathlib.Path(sys.argv[2]), sys.argv[7], sys.argv[8], public_key=sys.argv[6])))
'''.strip()
        installed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", install_code, str(first), str(target), str(index_path),
             str(signature_path), str(archive), str(self.signer.public_key), self.index_url,
             self.signature_url, self.asset_url],
            cwd=hostile, env=environment, capture_output=True, text=True, timeout=60)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        result = json.loads(installed.stdout)
        self.assertEqual(result["status"], "installed")
        self.assertEqual((target / "usr/local/lib/boiler/maintenance.txt").read_bytes(),
                         b"verified Boiler maintenance runtime\n")
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
