"""Mounted-image verification and privileged-hook crash boundaries."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from maintenance import Engine, MaintenanceError
from maintenance import engine as implementation


class MaintenanceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "mount-a"
        (self.root / "etc").mkdir(parents=True)
        (self.root / "etc/test").write_bytes(b"original")
        self.payload = self.base / "package"
        (self.payload / "payload").mkdir(parents=True)
        (self.payload / "payload/test").write_bytes(b"installed")
        self.component = {
            "schema_version": 1, "kind": "ComponentManifest",
            "id": "test:boundary", "version": "1.0.0",
            "files": [{"source": "payload/test", "target": "etc/test",
                       "sha256": hashlib.sha256(b"installed").hexdigest(),
                       "size": len(b"installed"), "mode": 0o644}],
        }
        self.engine = Engine(self.root)

    @staticmethod
    def file_bytes(root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}

    def test_remount_verifies_without_writes_and_can_restore(self):
        transaction = self.engine.apply(self.component, self.payload)["transaction"]
        remounted = self.base / "mount-b"
        shutil.copytree(self.root, remounted)
        before = self.file_bytes(remounted)
        with patch.object(Engine, "_lock", side_effect=AssertionError("write lock")), \
                patch.object(Engine, "_recover", side_effect=AssertionError("recovery")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("mkdir")):
            result = Engine(remounted, read_only=True).verify_readonly(self.component["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(self.file_bytes(remounted), before)
        Engine(remounted).restore(transaction)
        self.assertEqual((remounted / "etc/test").read_bytes(), b"original")
        self.assertEqual((self.root / "etc/test").read_bytes(), b"installed")

    def test_readonly_pending_transaction_refuses_without_recovery(self):
        transaction = self.engine.apply(self.component, self.payload)["transaction"]
        journal_path = self.engine._journal(transaction)
        journal = json.loads(journal_path.read_text())
        journal["status"] = "applying"
        journal_path.write_text(json.dumps(journal))
        before = self.file_bytes(self.root)
        with patch.object(Engine, "_recover", side_effect=AssertionError("recovery")):
            with self.assertRaisesRegex(MaintenanceError, "Pending transaction"):
                Engine(self.root, read_only=True).verify_readonly(self.component["id"])
        self.assertEqual(self.file_bytes(self.root), before)

    def test_readonly_engine_rejects_mutating_entry_points(self):
        reader = Engine(self.root, read_only=True)
        before = self.file_bytes(self.root)
        for action in (lambda: reader.apply(self.component, self.payload),
                       reader.recover, lambda: reader.restore("0" * 32)):
            with self.subTest(action=action), self.assertRaisesRegex(MaintenanceError, "Read-only"):
                action()
        self.assertEqual(self.file_bytes(self.root), before)

    def test_hook_recovery_barrier_never_rolls_back_script_side_effects(self):
        transaction = self.engine.apply(self.component, self.payload)["transaction"]
        journal_path = self.engine._journal(transaction)
        journal = json.loads(journal_path.read_text())
        journal["component"]["hooks"] = {"apply": "scripts/apply.sh"}
        (self.root / "etc/hook-side-effect").write_bytes(b"script changed this")
        for status in ("hook_running", "hook_completed", "manual_recovery_required"):
            with self.subTest(status=status):
                journal["status"] = status
                journal_path.write_text(json.dumps(journal))
                before = self.file_bytes(self.root)
                with self.assertRaisesRegex(MaintenanceError, "manual recovery"):
                    Engine(self.root).recover()
                self.assertEqual(self.file_bytes(self.root), before)

    @unittest.skipIf(os.name == "nt", "Privileged hook execution is intentionally Linux-only")
    def test_successful_hook_then_failed_state_commit_requires_manual_recovery(self):
        script = b'printf hook > "$BOILER_TARGET_ROOT/etc/hook-side-effect"\n'
        (self.payload / "scripts").mkdir()
        (self.payload / "scripts/apply.sh").write_bytes(script)
        self.component["hooks"] = {"apply": "scripts/apply.sh"}
        manifest = {
            "schema_version": 1, "kind": "DevicePackage", "id": "device:boundary",
            "version": "1.0.0", "source": {"kind": "local", "path": "test"},
            "components": [self.component],
            "files": [{"path": "payload/test", "sha256": hashlib.sha256(b"installed").hexdigest(),
                       "size": len(b"installed")},
                      {"path": "scripts/apply.sh", "sha256": hashlib.sha256(script).hexdigest(),
                       "size": len(script)}],
        }
        (self.payload / "manifest.json").write_text(json.dumps(manifest))
        write = implementation._json_write

        def fail_state_commit(path, value):
            if Path(path) == self.engine.state_dir / "state.json":
                raise OSError("injected state commit failure after successful hook")
            return write(path, value)

        with patch.object(implementation, "_json_write", side_effect=fail_state_commit):
            with self.assertRaises(OSError):
                self.engine.apply(self.component, self.payload, allow_hooks=True)
        journal = json.loads(next(self.engine.journals.glob("*.json")).read_text())
        self.assertEqual(journal["status"], "manual_recovery_required")
        self.assertEqual((self.root / "etc/hook-side-effect").read_bytes(), b"hook")
        self.assertEqual((self.root / "etc/test").read_bytes(), b"installed")
        with self.assertRaisesRegex(MaintenanceError, "manual recovery"):
            self.engine.recover()


if __name__ == "__main__":
    unittest.main()
