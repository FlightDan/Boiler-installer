import hashlib
import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from protocol import ProtocolError, validate_package
from protocol.local import import_local
from test_protocol import package


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_device_package", ROOT / "scripts/build-device-package.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class LocalDeviceImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / "a device.sh"
        self.raw = b'#!/bin/bash\n# boiler-script: 1\n# boiler-name: Example device\ncase "$1" in apply|verify|restore) : ;; *) exit 2;; esac\n'
        self.script.write_bytes(self.raw)

    def tearDown(self):
        self.temp.cleanup()

    def assert_unpublished(self, source, match=None):
        destination = self.root / "result"
        with self.assertRaisesRegex((ProtocolError, OSError, ValueError), match or ".*"):
            import_local(source, destination, developer=True)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob(".local-device-*")))

    def directory(self):
        directory = self.root / "source"
        directory.mkdir()
        package(directory)
        return directory

    def test_developer_flag_must_be_explicit_boolean(self):
        for flag in (False, None, 1, "true"):
            with self.subTest(flag=flag), self.assertRaises(ProtocolError):
                import_local(self.script, self.root / "out", developer=flag)

    def test_script_packaging_preserves_bytes_and_never_executes(self):
        with patch("subprocess.run", side_effect=AssertionError("must not execute")):
            result = import_local(self.script, self.root / "out", developer=True)
        root = Path(result["directory"])
        self.assertEqual(result["kind"], "script")
        self.assertTrue(root.is_absolute())
        self.assertEqual((root / "scripts/device.sh").read_bytes(), self.raw)
        manifest = validate_package(root, expected_manifest_sha256=result["manifest_sha256"])
        self.assertEqual(manifest["name"], "Example device")
        self.assertEqual(manifest["trust"], {"status": "unsigned-development", "official_release": False})
        self.assertEqual(manifest["compatibility"], {"architectures": ["x86_64"]})
        self.assertNotIn("maintenance_entry", manifest)
        self.assertEqual(manifest["status"], "experimental")
        hooks = manifest["components"][0]["hooks"]
        self.assertEqual(set(hooks), {"apply", "verify", "restore"})
        for phase, name in hooks.items():
            self.assertIn('"$script_dir/device.sh" ' + phase + ' "$1"', (root / name).read_text())
        self.assertEqual(result["manifest_sha256"], hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest())

    def test_env_bash_and_default_name(self):
        self.script.write_bytes(b"#!/usr/bin/env bash\n# boiler-script: 1\nexit 0\n")
        result = import_local(self.script, self.root / "out", developer=True)
        self.assertEqual(result["package"]["name"], "a device")

    def test_rejects_undeclared_or_incompatible_script(self):
        for raw in (b"#!/bin/bash\nexit 0\n", b"#!/bin/sh\n# boiler-script: 1\n",
                    self.raw.replace(b"\n", b"\r\n"), b"\xef\xbb\xbf" + self.raw,
                    self.raw + b"\x00", self.raw + b"\xff",
                    b"#!/bin/bash\nexit 0\n# boiler-script: 1\n"):
            with self.subTest(raw=raw):
                self.script.write_bytes(raw)
                self.assert_unpublished(self.script, "Boiler script contract")

    def test_rejects_duplicate_or_invalid_headers(self):
        for raw in (self.raw.replace(b"case", b"# boiler-script: 1\ncase"),
                    self.raw.replace(b"Example device", b"Example\tdevice")):
            with self.subTest(raw=raw):
                self.script.write_bytes(raw)
                self.assert_unpublished(self.script)

    def test_script_size_limit(self):
        with patch("protocol.local.MAX_SCRIPT", 8):
            self.assert_unpublished(self.script, "size limit")

    def test_directory_preserves_manifest(self):
        source = self.directory()
        before = (source / "manifest.json").read_bytes()
        result = import_local(source, self.root / "out", developer=True)
        self.assertEqual(result["kind"], "directory")
        self.assertEqual((Path(result["directory"]) / "manifest.json").read_bytes(), before)

    def test_rejects_directory_extra_or_modified_file(self):
        source = self.directory()
        (source / "extra").write_text("unexpected")
        self.assert_unpublished(source, "inventory")
        (source / "extra").unlink()
        (source / "payload/test").write_bytes(b"NO")
        self.assert_unpublished(source, "hash mismatch")

    def test_rejects_destination_collisions_and_descendants(self):
        target = self.root / "existing"
        target.mkdir()
        marker = target / "keep"
        marker.write_text("keep")
        with self.assertRaisesRegex(ProtocolError, "already exists"):
            import_local(self.script, target, developer=True)
        self.assertEqual(marker.read_text(), "keep")
        source = self.directory()
        with self.assertRaisesRegex(ProtocolError, "inside source"):
            import_local(source, source / "nested/out", developer=True)

    def test_zip_root_and_single_wrapper(self):
        source = self.directory()
        for wrapper in ("", "package/"):
            archive = self.root / ("wrapped.zip" if wrapper else "root.zip")
            with zipfile.ZipFile(archive, "w") as bundle:
                for file in source.rglob("*"):
                    if file.is_file():
                        bundle.write(file, wrapper + file.relative_to(source).as_posix())
            result = import_local(archive, self.root / ("wrapped" if wrapper else "plain"), developer=True)
            self.assertEqual(result["kind"], "archive")
            validate_package(result["directory"], expected_manifest_sha256=result["manifest_sha256"])

    def test_tar_gzip_xz(self):
        source = self.directory()
        for suffix, mode in ((".tar", "w"), (".tar.gz", "w:gz"), (".tar.xz", "w:xz")):
            archive = self.root / ("model" + suffix)
            with tarfile.open(archive, mode) as bundle:
                bundle.add(source, arcname="package")
            result = import_local(archive, self.root / ("out" + suffix), developer=True)
            self.assertEqual(result["kind"], "archive")

    def test_rejects_archive_traversal_links_and_extra_wrappers(self):
        for names in (("../escape",), ("one/manifest.json", "two/file")):
            archive = self.root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                for name in names:
                    bundle.writestr(name, b"{}");
            self.assert_unpublished(archive)
        archive = self.root / "link.tar"
        with tarfile.open(archive, "w") as bundle:
            entry = tarfile.TarInfo("link")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "../outside"
            bundle.addfile(entry)
        self.assert_unpublished(archive, "links")

    def test_rejects_symlink_source_and_member(self):
        link = self.root / "linked.sh"
        try:
            link.symlink_to(self.script)
        except (OSError, NotImplementedError):
            self.skipTest("Host does not permit symbolic links")
        self.assert_unpublished(link, "links")
        source = self.directory()
        (source / "payload/test").unlink()
        (source / "payload/test").symlink_to(self.script)
        self.assert_unpublished(source, "link")

    def test_copy_is_rechecked_before_publication(self):
        source = self.directory()
        from protocol import local
        original = local._copy_file
        def changed(file, target, maximum):
            original(file, target, maximum)
            target.write_bytes(b"NO")
        with patch("protocol.local._copy_file", side_effect=changed):
            self.assert_unpublished(source, "hash mismatch")

    def test_builder_roundtrip_and_no_overwrite(self):
        output = self.root / "model.zip"
        result = builder.build(self.script, output)
        self.assertEqual(result["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(result["size"], output.stat().st_size)
        imported = import_local(output, self.root / "roundtrip", developer=True)
        self.assertEqual(imported["manifest_sha256"], result["manifest_sha256"])
        before = output.read_bytes()
        with self.assertRaisesRegex(ProtocolError, "already exists"):
            builder.build(self.script, output)
        self.assertEqual(before, output.read_bytes())

    def test_builder_rejects_invalid_existing_inventory(self):
        source = self.directory()
        (source / "payload/test").write_bytes(b"NO")
        output = self.root / "bad.zip"
        with self.assertRaisesRegex(ProtocolError, "hash mismatch"):
            builder.build(source, output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
