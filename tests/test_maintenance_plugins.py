import base64
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

from maintenance import MaintenanceError
from maintenance.__main__ import main
from maintenance.plugins import PluginMaintenance, STORE, CODE, UNIT, ENABLE, MARKER, SLOTS


@unittest.skipIf(os.name != "nt" and os.geteuid() != 0, "Private plugin transaction fixtures require root on Linux")
class PluginMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "mounted-root"; self.root.mkdir()
        self.adapter = PluginMaintenance(self.root)
        self.epoch = "a" * 32
        self.old_root = "/previous/integration-root"
        self.journal = self.root / STORE / self.epoch
        self.write("etc/passwd", b"deck:x:1000:1000::/home/deck:/bin/bash\n")
        self.write(UNIT, b"previous service\n")
        original_unit = self.saved(self.root / UNIT)
        self.write(UNIT, b"Boiler service\n")
        self.write(ENABLE, b"[Unit]\nWants=plugin_loader.service\n")
        self.write(MARKER, b"")
        if os.name != "nt": os.chown(self.root / MARKER, 1000, 1000)
        code = self.root / CODE / "services"; code.mkdir(parents=True)
        self.write(CODE + "/services/PluginLoader", b"new executable", mode=0o755)
        installed = self.journal / "code/installed"; installed.parent.mkdir(parents=True, mode=0o700)
        shutil.copytree(code, installed)
        reference = self.journal / "code/original-reference"; reference.mkdir()
        (reference / "PluginLoader").write_bytes(b"original executable")
        (reference / "PluginLoader").chmod(0o755)
        shutil.copytree(reference, self.journal / "code/original")
        self.json(STORE + "/" + self.epoch + "/code/state.json", {
            "phase": "installed", "destination": self.old_root + "/" + CODE + "/services",
            "original_exists": True, "original_metadata": self.inventory(reference),
            "installed_metadata": self.inventory(installed)})
        self.file_journal("unit", {UNIT: original_unit}, {UNIT: b"Boiler service\n"})
        self.file_journal("activation", {ENABLE: {"exists": False}, MARKER: {"exists": False}},
                          {ENABLE: b"[Unit]\nWants=plugin_loader.service\n", MARKER: b""})
        self.json(STORE + "/decky.json", {"id": "decky", "epoch": self.epoch,
                                         "phase": "files_installed", "slot": "A", "runtime": "not_validated"})
        self.slot("A", self.epoch, True)

    def write(self, relative, data, mode=0o644):
        path = self.root / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data); path.chmod(mode)
        return path

    def json(self, relative, value):
        path = self.write(relative, json.dumps(value).encode(), mode=0o600)
        # Real pointer/epoch and transaction directories are private.
        for ancestor in (path.parent, *path.parent.parents):
            if ancestor == self.root: break
            if ancestor.is_relative_to(self.root / STORE) or ancestor.is_relative_to(self.root / "home/.boiler"):
                ancestor.chmod(0o700)
        return path

    def slot(self, slot, epoch, owns_marker):
        return self.json(SLOTS + "/" + slot + ".json", {"schema": 1, "slot": slot, "epoch": epoch,
                         "owns_marker": owns_marker, "marker": MARKER})

    @staticmethod
    def saved(path):
        info = path.stat()
        return {"exists": True, "data": base64.b64encode(path.read_bytes()).decode(),
                "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}

    @staticmethod
    def inventory(path):
        result = {}
        for entry in [path, *path.rglob("*")]:
            info = entry.stat()
            result[entry.relative_to(path).as_posix()] = {"directory": entry.is_dir(),
                "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
        return result

    def file_journal(self, group, originals, applied):
        def absolute(name): return self.old_root + "/" + name
        self.json(STORE + "/" + self.epoch + "/" + group + "/state.json", {
            "phase": "installed", "allowed": [absolute(name) for name in originals],
            "original": {absolute(name): value for name, value in originals.items()},
            "applied": {absolute(name): base64.b64encode(value).decode() for name, value in applied.items()},
            "accepted": {}})

    def bytes(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file() and path.name != ".lock"}

    def test_list_verify_and_cli_do_not_write(self):
        before = self.bytes()
        with patch.object(Path, "mkdir", side_effect=AssertionError("read-only mkdir")), \
                patch.object(PluginMaintenance, "_lock", side_effect=AssertionError("read-only lock")), \
                patch.object(PluginMaintenance, "_write", side_effect=AssertionError("read-only write")):
            self.assertEqual(self.adapter.list()["components"][0]["id"], "core:decky")
            self.assertTrue(self.adapter.verify("decky")["ok"])
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--root", str(self.root), "plugins", "verify", "decky"]), 0)
            self.assertTrue(json.loads(output.getvalue())["result"]["ok"])
        self.assertEqual(self.bytes(), before)
        self.assertFalse((self.root / "var/lib/boiler/maintenance").exists())

    def test_restore_without_resources_preserves_original_and_is_idempotent(self):
        result = self.adapter.restore("decky")
        self.assertEqual(result["status"], "restored")
        self.assertEqual((self.root / UNIT).read_bytes(), b"previous service\n")
        self.assertFalse((self.root / ENABLE).exists()); self.assertFalse((self.root / MARKER).exists())
        self.assertEqual((self.root / CODE / "services/PluginLoader").read_bytes(), b"original executable")
        self.assertFalse((self.root / SLOTS / "A.json").exists())
        self.assertEqual(self.adapter.restore("decky")["status"], "restored")
        self.assertFalse(self.adapter.verify("decky")["ok"])

    def test_changed_code_refuses_before_configuration_changes(self):
        (self.root / CODE / "services/PluginLoader").write_bytes(b"user code")
        before = self.bytes()
        with self.assertRaisesRegex(MaintenanceError, "code changed"):
            self.adapter.restore("decky")
        self.assertEqual(self.bytes(), before)

    def test_changed_config_refuses_before_any_target_changes(self):
        (self.root / UNIT).write_bytes(b"user unit")
        before = self.bytes()
        with self.assertRaisesRegex(MaintenanceError, "configuration changed"):
            self.adapter.restore("decky")
        self.assertEqual(self.bytes(), before)

    def test_saved_arbitrary_target_cannot_escape_fixed_allowlist(self):
        path = self.journal / "unit/state.json"; value = json.loads(path.read_text())
        value["allowed"] = [self.old_root + "/etc/passwd"]
        path.write_text(json.dumps(value))
        before = self.bytes()
        with self.assertRaisesRegex(MaintenanceError, "allowlist"):
            self.adapter.restore("decky")
        self.assertEqual(self.bytes(), before)

    def test_shared_marker_owner_refuses_while_other_slot_active(self):
        self.slot("B", "b" * 32, False)
        before = self.bytes()
        with self.assertRaisesRegex(MaintenanceError, "Other SteamOS slot"):
            self.adapter.restore("decky")
        self.assertEqual(self.bytes(), before)

    def test_new_transaction_missing_slot_proof_fails_before_restore(self):
        (self.root / SLOTS / "A.json").unlink()
        before = self.bytes()
        with self.assertRaisesRegex(MaintenanceError, "ownership record"):
            self.adapter.restore("decky")
        self.assertEqual(self.bytes(), before)

    def test_nonowner_slot_restore_preserves_sibling_marker_and_reference(self):
        self.slot("A", "b" * 32, True); self.slot("B", self.epoch, False)
        pointer = self.root / STORE / "decky.json"; value = json.loads(pointer.read_text()); value["slot"] = "B"
        pointer.write_text(json.dumps(value))
        self.file_journal("activation", {ENABLE: {"exists": False}}, {ENABLE: b"[Unit]\nWants=plugin_loader.service\n"})
        self.assertEqual(self.adapter.restore("decky")["status"], "restored")
        self.assertTrue((self.root / MARKER).exists()); self.assertTrue((self.root / SLOTS / "A.json").exists())
        self.assertFalse((self.root / SLOTS / "B.json").exists())

    def test_legacy_missing_registry_retains_marker_and_reports_partial(self):
        shutil.rmtree(self.root / "home/.boiler")
        pointer = self.root / STORE / "decky.json"; value = json.loads(pointer.read_text()); value.pop("slot")
        pointer.write_text(json.dumps(value))
        result = self.adapter.restore("decky")
        self.assertEqual(result["status"], "partial_restore")
        self.assertEqual(result["retained_shared_state"], [MARKER])
        self.assertTrue((self.root / MARKER).exists())
        self.assertEqual((self.root / UNIT).read_bytes(), b"previous service\n")
        self.assertEqual(json.loads(pointer.read_text())["phase"], "restoring")

    def test_interrupted_code_rename_retries_without_resources_or_data_loss(self):
        rename = os.rename; interrupted = False
        def crash(source, destination):
            nonlocal interrupted
            rename(source, destination)
            if Path(source).name == "services" and Path(destination).name == "boiler-maintenance-removed" and not interrupted:
                interrupted = True; raise OSError("injected crash after code quarantine")
        with patch("maintenance.plugins.os.rename", side_effect=crash):
            with self.assertRaises(OSError): self.adapter.restore("decky")
        self.assertTrue(interrupted)
        self.assertEqual(self.adapter.restore("decky")["status"], "restored")
        self.assertEqual((self.root / CODE / "services/PluginLoader").read_bytes(), b"original executable")

    def test_unknown_and_missing_transactions_are_not_adopted(self):
        with self.assertRaisesRegex(MaintenanceError, "Unsupported plugin"):
            self.adapter.verify("arbitrary")
        self.json(STORE + "/unknown.json", {"phase": "files_installed"})
        with self.assertRaisesRegex(MaintenanceError, "dependent plugins"):
            self.adapter.restore("decky")


if __name__ == "__main__": unittest.main()
