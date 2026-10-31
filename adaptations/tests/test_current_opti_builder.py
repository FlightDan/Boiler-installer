from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec=importlib.util.spec_from_file_location('current_opti_builder_tests',ROOT/'scripts/build-current-opti.py')
candidate=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=candidate
spec.loader.exec_module(candidate)


class CandidateBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='boiler-opti-unit-')
        self.temp = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_fixed_public_cpu_baseline_has_five_members(self):
        self.assertEqual(candidate.PUBLIC_CPU_SOURCE_COMMIT, "72f864e0498c10366493c74608c5ffed62cc99ca")
        self.assertEqual(len(candidate.EXPECTED_CPU_FILES), 5)
        self.assertEqual(len(candidate.EXPECTED_PLUGIN_FILES), 37)

    def test_base_anchor_and_plugin_origin_are_fixed(self):
        self.assertEqual(
            candidate.BASE_MANIFEST_SHA256,
            "c80efd9cce07d35c5f8828d17b5fab8c15fbc177a10476754cfac04db3f27485",
        )
        source_commit = "a" * 40
        self.assertEqual(
            candidate.PLUGIN_SOURCE_ORIGIN + source_commit,
            "https://github.com/FlightDan/Opti2XMX/tree/" + source_commit,
        )

    def test_manifest_inventory_uses_platform_path_order_and_lf_json(self):
        root = self.temp / "ordered"
        root.mkdir()
        for name in ("LICENSE", "components/a.json", "components/B.json"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name, encoding="utf-8")
        rows = candidate.inventory_rows(root)
        expected_paths = [
            path.relative_to(root).as_posix()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path != root / "manifest.json"
        ]
        self.assertEqual([row["path"] for row in rows], expected_paths)
        output = self.temp / "line-endings.json"
        candidate.write_json(output, {"ok": True})
        self.assertEqual(output.read_bytes(), b'{\n  "ok": true\n}\n')

    def test_plugin_lock_and_archive_require_exact_path_whitelist_and_hashes(self):
        files = {}
        data_by_name = {}
        for name in sorted(candidate.EXPECTED_PLUGIN_FILES):
            data = b'{"schema":1}' if name == "runtime-source.json" else ("member:" + name).encode()
            data_by_name[name] = data
            files[name] = hashlib.sha256(data).hexdigest()
        archive_path = self.temp / "plugin.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in data_by_name.items():
                archive.writestr("opti2xmx/" + name, data)
        files_digest = hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        lock = {
            "schema_version": 1,
            "kind": "LocalOptiPluginInput",
            "version": "0.3.2-dev.1",
            "source_commit": "a" * 40,
            "archive": {
                "size": archive_path.stat().st_size,
                "sha256": candidate.sha256_file(archive_path),
            },
            "files": files,
            "files_sha256": files_digest,
        }
        lock_path = self.temp / "plugin-lock.json"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        expected = dict(
            expected_lock_sha256=candidate.sha256_file(lock_path),
            expected_archive_sha256=candidate.sha256_file(archive_path),
            expected_source_commit="a" * 40,
        )
        parsed, runtime, lock_digest = candidate.verify_plugin_lock(lock_path, archive_path, **expected)
        self.assertEqual(parsed["version"], "0.3.2-dev.1")
        self.assertEqual(runtime, {"schema": 1})
        self.assertEqual(lock_digest, candidate.sha256_file(lock_path))

        # Rewriting the source identity and rehashing the untrusted lock cannot
        # substitute for the caller's independently retained pins.
        lock["source_commit"] = "b" * 40
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        with self.assertRaisesRegex(candidate.CandidateError, "reviewed SHA-256"):
            candidate.verify_plugin_lock(lock_path, archive_path, **expected)
        changed_lock_expected = dict(expected, expected_lock_sha256=candidate.sha256_file(lock_path))
        with self.assertRaisesRegex(candidate.CandidateError, "reviewed expectations"):
            candidate.verify_plugin_lock(lock_path, archive_path, **changed_lock_expected)
        lock["source_commit"] = "a" * 40
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        with self.assertRaisesRegex(candidate.CandidateError, "reviewed expectations"):
            candidate.verify_plugin_lock(lock_path, archive_path, **dict(expected, expected_archive_sha256="0" * 64))
        with self.assertRaisesRegex(candidate.CandidateError, "expectations are required"):
            candidate.verify_plugin_lock(lock_path, archive_path, **dict(expected, expected_source_commit=""))

        tampered = self.temp / "tampered.zip"
        with zipfile.ZipFile(tampered, "w") as archive:
            for name, data in data_by_name.items():
                archive.writestr("opti2xmx/" + name, data)
            archive.writestr("opti2xmx/cpu-policy.py", b"not allowed")
        lock["archive"] = {"size": tampered.stat().st_size, "sha256": candidate.sha256_file(tampered)}
        tampered_lock = self.temp / "tampered-lock.json"
        tampered_lock.write_text(json.dumps(lock), encoding="utf-8")
        with self.assertRaisesRegex(candidate.CandidateError, "unapproved member"):
            candidate.verify_plugin_lock(tampered_lock, tampered,
                expected_lock_sha256=candidate.sha256_file(tampered_lock),
                expected_archive_sha256=candidate.sha256_file(tampered),
                expected_source_commit="a" * 40)

    def test_cli_requires_all_independent_plugin_expectations(self):
        arguments = ["--project-root", ".", "--base", ".", "--plugin-zip", "plugin.zip",
                     "--plugin-lock", "lock.json", "--version", "0.1.0", "--output", "output"]
        with self.assertRaises(SystemExit):
            candidate.parse_args(arguments)
        parsed = candidate.parse_args(arguments + [
            "--expected-plugin-lock-sha256", "1" * 64,
            "--expected-plugin-sha256", "2" * 64,
            "--expected-plugin-source-commit", "3" * 40,
        ])
        self.assertEqual(parsed.expected_plugin_lock_sha256, "1" * 64)
        self.assertEqual(parsed.expected_plugin_sha256, "2" * 64)
        self.assertEqual(parsed.expected_plugin_source_commit, "3" * 40)

    def test_non_opti_package_bytes_must_remain_identical(self):
        base = self.temp / "base"
        output = self.temp / "output"
        (base / "payload/legacy/components/opti2xmx").mkdir(parents=True)
        (base / "payload/legacy/integration/opti2xmx").mkdir(parents=True)
        (base / "payload/legacy/integration").mkdir(parents=True, exist_ok=True)
        (base / "payload/legacy/integration/opti2xmx/runtime.tar.gz").write_bytes(b"runtime")
        (base / "payload/legacy/components/opti2xmx/plugin.py").write_bytes(b"old plugin")
        (base / "payload/legacy/integration/manifest.json").write_text("{}", encoding="utf-8")
        (base / "payload/legacy/integration/other.bin").write_bytes(b"unchanged")
        (base / "manifest.json").write_text("{}", encoding="utf-8")
        shutil.copytree(base, output)
        (output / "payload/legacy/components/opti2xmx/plugin.py").write_bytes(b"new locked plugin")
        (output / "payload/legacy/integration/manifest.json").write_text('{"opti2xmx":{}}', encoding="utf-8")
        (output / "manifest.json").write_text('{"version":"candidate"}', encoding="utf-8")
        self.assertGreater(candidate.verify_preserved_bytes(base, output), 0)
        (output / "payload/legacy/integration/other.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(candidate.CandidateError, "non-Opti"):
            candidate.verify_preserved_bytes(base, output)

    def test_forbidden_research_path_markers_are_rejected(self):
        self.assertEqual(candidate.scan_forbidden_paths(["components/options.json"]), 0)
        with self.assertRaisesRegex(candidate.CandidateError, "research or policy"):
            candidate.scan_forbidden_paths(["payload/g3e/scheduler.py"])

    def test_text_scan_rejects_unlocked_research_markers(self):
        root = self.temp / "scan"
        root.mkdir()
        (root / "plugin.py").write_text("dynamic power scheduler", encoding="utf-8")
        with self.assertRaisesRegex(candidate.CandidateError, "research marker"):
            candidate.scan_text_for_private_research(root, candidate.EXPECTED_CPU_FILES)

        (root / "plugin.py").write_text("ordinary plugin code", encoding="utf-8")
        result = candidate.scan_text_for_private_research(root, candidate.EXPECTED_CPU_FILES)
        self.assertEqual(result["text_file_count"], 1)
        self.assertEqual(result["fixed_public_cpu_files_checked"], 0)

    def test_output_allows_explicit_fresh_directory_and_protects_package(self):
        outside = self.temp/'outside'
        self.assertEqual(candidate.ensure_candidate_output(outside), outside.absolute())
        with self.assertRaisesRegex(candidate.CandidateError, "already exists"):
            candidate.ensure_candidate_output(self.temp)
        with self.assertRaisesRegex(candidate.CandidateError, "inside an input package"):
            candidate.ensure_candidate_output(self.temp / "nested" / "output", protected_input_roots=(self.temp,))

    def test_output_rejects_linked_ancestry_before_resolve(self):
        output = self.temp / "ancestor" / "new-output"
        linked = output.parent
        original = Path.is_symlink
        with mock.patch.object(Path, "is_symlink", lambda path: path == linked or original(path)):
            with self.assertRaisesRegex(candidate.CandidateError, "link or junction"):
                candidate.ensure_candidate_output(output)
        with mock.patch.object(Path, "is_junction", lambda path: path == linked, create=True):
            with self.assertRaisesRegex(candidate.CandidateError, "link or junction"):
                candidate.ensure_candidate_output(output)

    def test_builder_lock_and_private_helper_paths_never_enter_package(self):
        self.assertEqual(candidate.verify_no_build_inputs_in_package(["manifest.json", "payload/file.bin"]), 0)
        with self.assertRaisesRegex(candidate.CandidateError, "builder-only input"):
            candidate.verify_no_build_inputs_in_package(["payload/opti2xmx.input-lock.json"])
        with self.assertRaisesRegex(candidate.CandidateError, "builder-only input"):
            candidate.verify_no_build_inputs_in_package(["tools/build-current-test.py"])


if __name__ == "__main__":
    unittest.main()
