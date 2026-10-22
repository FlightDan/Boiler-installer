"""No-network regressions for locked dependency preparation boundaries."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


python_preparer = module("boiler_python_preparation_tests", "prepare-python.py")
bootstrap = module("boiler_bootstrap_readonly_tests", "bootstrap-dependencies.py")


class PythonPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name); self.cache = self.base / "cache"; self.cache.mkdir()
        self.output = self.base / "python"

    @staticmethod
    def archive(contents):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, data in contents.items(): archive.writestr(name, data)
        return stream.getvalue()

    @staticmethod
    def artifact(name, raw, kind="python"):
        return {"name": name, "url": "https://example.invalid/" + name,
                "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "kind": kind}

    def run_prepare(self, artifacts, response=b""):
        lock_path = Path(python_preparer.__file__).with_name("python-runtime-lock.json")
        read = Path.read_text
        def read_lock(path, *args, **kwargs):
            return json.dumps({"schema_version": 1, "artifacts": artifacts}) if path == lock_path else read(path, *args, **kwargs)
        with patch.object(Path, "read_text", read_lock), \
                patch.object(sys, "argv", ["prepare-python.py", "--output", str(self.output), "--cache", str(self.cache)]), \
                patch.object(python_preparer.urllib.request, "urlopen", return_value=io.BytesIO(response)) as network, \
                contextlib.redirect_stdout(io.StringIO()):
            self.network = network
            python_preparer.main()

    def test_existing_partial_is_never_truncated_or_downloaded_over(self):
        raw = self.archive({"python.exe": b"locked runtime"})
        partial = self.cache / "python.zip.partial"; partial.write_bytes(b"existing unfinished bytes")
        with self.assertRaises(FileExistsError):
            self.run_prepare([self.artifact("python.zip", raw)], raw)
        self.assertEqual(partial.read_bytes(), b"existing unfinished bytes")
        self.network.assert_not_called()

    def test_cache_hash_conflict_does_not_overwrite_existing_input(self):
        raw = self.archive({"python.exe": b"locked runtime"})
        cached = self.cache / "python.zip"; cached.write_bytes(b"administrator data")
        with self.assertRaises(ValueError): self.run_prepare([self.artifact("python.zip", raw)], raw)
        self.assertEqual(cached.read_bytes(), b"administrator data")
        self.network.assert_not_called()

    def test_download_size_limit_is_enforced_before_extraction(self):
        raw = self.archive({"python.exe": b"locked runtime"})
        item = self.artifact("python.zip", raw); item["size"] -= 1
        with self.assertRaisesRegex(ValueError, "exceeded locked size"):
            self.run_prepare([item], raw)
        self.assertFalse((self.cache / "python.zip").exists())
        self.assertFalse((self.output / "python.exe").exists())

    def test_archive_collision_preserves_previously_extracted_bytes(self):
        first = self.archive({"python.exe": b"first locked runtime"})
        second = self.archive({"python.exe": b"conflicting wheel member"})
        (self.cache / "python.zip").write_bytes(first); (self.cache / "wheel.zip").write_bytes(second)
        with self.assertRaisesRegex(ValueError, "path collision"):
            self.run_prepare([self.artifact("python.zip", first), self.artifact("wheel.zip", second, "wheel")])
        self.assertEqual((self.output / "python.exe").read_bytes(), b"first locked runtime")
        self.network.assert_not_called()

    def test_bootstrap_check_only_missing_root_never_creates_directory(self):
        missing = self.base / "missing-root"
        with patch.object(bootstrap.urllib.request, "urlopen", side_effect=AssertionError("unexpected network")):
            with self.assertRaises(ValueError): bootstrap.prepare(missing, "all", check_only=True)
        self.assertFalse(missing.exists())


if __name__ == "__main__": unittest.main()
