import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest
import zipfile

from protocol.brand_bundle import stage_brand, import_brand
from protocol.local import import_local
from protocol.manifest import ProtocolError, validate_package
from protocol.resource_pool import decoder
from test_protocol import package


class BrandBundleImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.material = self.root / "material"
        self.material.mkdir()
        manifest = package(self.material)
        self.models = []
        for identity, model in (("device:oxp3", "oxp3"), ("device:x2-mini", "x2-mini")):
            guide = "brands/onexplayer/" + model + "/" + model + ".toon"
            name = "brands/onexplayer/" + model + "/runtime/manifest.json"
            self.models.append(dict(id=identity, guide=guide, manifest=name,
                                    files=[dict(path="payload/test", member="common/example/payload")]))
        self.brand = dict(schema_version=1, kind="BoilerBrandBundle", brand="onexplayer",
                          source_repository="FlightDan/Boiler-adaptations", source_commit="a"*40,
                          models=self.models, unresolved=[])
        self.manifests = {}
        for row in self.models:
            data = dict(manifest, id=row["id"], name={"en": row["id"], "zh": "机型"})
            self.manifests[row["manifest"]] = json.dumps(data).encode()
        self.archive = self.root / "bundle.zip"
        self.write_bundle()

    def write_bundle(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("brand.toon", decoder().dumps(self.brand))
            archive.writestr("common/example/payload", b"ok")
            for row in self.models:
                archive.writestr(row["guide"], "schema_version: 1\n")
                archive.writestr(row["manifest"], self.manifests[row["manifest"]])

    def stage(self):
        return stage_brand(self.archive, self.root / "staged", developer=True)

    def test_bundled_archive_lists_without_extracting_and_selects_only_one_model(self):
        from protocol.model_catalog import bundled_packages, bundled_model_package
        bundle = self.root / "cache/model-bundles"
        bundle.mkdir(parents=True)
        archive = bundle / "onexplayer.zip"
        shutil.copyfile(self.archive, archive)
        rows = []
        for model in self.models:
            raw = self.manifests[model["manifest"]]
            manifest = json.loads(raw)
            rows.append(dict(id=model["id"], version=manifest["version"],
                             manifest_sha256=hashlib.sha256(raw).hexdigest(), manifest_size=len(raw),
                             files=len(manifest["files"]), official_release=False))
        data = archive.read_bytes()
        descriptor = dict(schema_version=1, kind="LocalTestModelBrandBundle",
                          archive=dict(path="cache/model-bundles/onexplayer.zip", size=len(data),
                                       sha256=hashlib.sha256(data).hexdigest()), packages=rows)
        (bundle / "manifest.json").write_text(json.dumps(descriptor))
        listed = bundled_packages(self.root)
        self.assertEqual({x["id"] for x in listed}, {x["id"] for x in rows})
        self.assertFalse((self.root / "output").exists())
        with self.assertRaises(ProtocolError):
            bundled_model_package(rows[0]["id"], root=self.root, destination=self.root / "output")
        result = bundled_model_package(rows[0]["id"], root=self.root, trust_local_test=True,
                                       destination=self.root / "output")
        self.assertEqual(result["package"]["id"], rows[0]["id"])
        self.assertEqual({p.relative_to(self.root / "output").as_posix() for p in (self.root / "output").rglob("*") if p.is_file()},
                         {"manifest.json", "payload/test"})
        again = bundled_model_package(rows[0]["id"], root=self.root, trust_local_test=True,
                                      destination=self.root / "output")
        self.assertEqual(again["directory"], result["directory"])
        (self.root / "output/payload/test").write_bytes(b"no")
        with self.assertRaises(ProtocolError):
            bundled_model_package(rows[0]["id"], root=self.root, trust_local_test=True,
                                  destination=self.root / "output")
        self.assertEqual((self.root / "output/payload/test").read_bytes(), b"no")
        archive.write_bytes(data + b"tampered")
        with self.assertRaises(ProtocolError):
            bundled_model_package(rows[1]["id"], root=self.root, trust_local_test=True,
                                  destination=self.root / "tampered")
        self.assertFalse((self.root / "tampered").exists())

    def test_both_models_materialize_one_shared_file_into_valid_packages(self):
        result = self.stage()
        self.assertTrue(result["recognized"])
        self.assertEqual({row["id"] for row in result["models"]}, {"device:oxp3", "device:x2-mini"})
        self.assertEqual(result["models"][0]["name"]["zh"], "机型")
        for row in result["models"]:
            target = self.root / row["id"].split(":")[1]
            imported = import_brand(result["directory"], target, row["id"], row["manifest_sha256"], developer=True)
            self.assertEqual(imported["package"]["id"], row["id"])
            self.assertEqual((target / "payload/test").read_bytes(), b"ok")
            validate_package(target, expected_manifest_sha256=row["manifest_sha256"])

    def test_current_guides_are_model_bound_and_can_be_payload_members(self):
        for row in self.models:
            model = row["id"].split(":")[1]
            row["guide"] = f"devices/onexplayer/{model}.toon"
        row = self.models[0]
        row["files"][0]["member"] = row["guide"]
        manifest = json.loads(self.manifests[row["manifest"]])
        guide_data = b"schema_version: 1\n"
        digest = hashlib.sha256(guide_data).hexdigest()
        manifest["files"][0].update(size=len(guide_data), sha256=digest)
        manifest["components"][0]["files"][0].update(size=len(guide_data), sha256=digest)
        self.manifests[row["manifest"]] = json.dumps(manifest).encode()
        self.write_bundle()
        staged = self.stage()
        selected = staged["models"][0]
        result = import_brand(staged["directory"], self.root / "guide-output", selected["id"],
                              selected["manifest_sha256"], developer=True)
        self.assertEqual((Path(result["directory"]) / "payload/test").read_bytes(), guide_data)

    def test_unrelated_device_guides_and_payloads_are_rejected(self):
        for path in ("devices/onexplayer/x2-mini.toon", "devices/onexplayer/other.toon",
                     "devices/other/oxp3.toon"):
            with self.subTest(path=path):
                self.models[0]["guide"] = path
                self.write_bundle()
                with self.assertRaises(ProtocolError):
                    self.stage()
        self.models[0]["guide"] = "devices/onexplayer/oxp3.toon"
        self.models[0]["files"][0]["member"] = "devices/onexplayer/other.toon"
        self.write_bundle()
        with self.assertRaises(ProtocolError):
            self.stage()

    def test_review_binding_and_payload_integrity(self):
        result = self.stage()
        chosen = result["models"][0]
        with self.assertRaisesRegex(ProtocolError, "Unknown brand model"):
            import_brand(result["directory"], self.root / "unknown", "device:other", chosen["manifest_sha256"], developer=True)
        with self.assertRaisesRegex(ProtocolError, "manifest changed"):
            import_brand(result["directory"], self.root / "changed", chosen["id"], "0"*64, developer=True)
        (Path(result["directory"]) / "common/example/payload").write_bytes(b"no")
        with self.assertRaises(ProtocolError):
            import_brand(result["directory"], self.root / "tampered", chosen["id"], chosen["manifest_sha256"], developer=True)
        self.assertFalse((self.root / "tampered").exists())

    def test_pending_features_follow_selected_model_and_old_bundle_remains_compatible(self):
        old = self.stage()
        self.assertTrue(all(row["pending"] == [] for row in old["models"]))
        self.brand["pending"] = [
            {"model": "device:oxp3", "resource": "resource:intel-be201-firmware",
             "status": "disabled-resource-not-integrated"},
            {"model": "device:x2-mini", "component": "pl4-175w", "status": "implementation_missing"},
            {"model": "device:x2-mini", "component": "lsfg-vk", "status": "external_plugin"},
        ]
        self.write_bundle()
        staged = stage_brand(self.archive, self.root / "new-staged", developer=True)
        by_id = {row["id"]: row for row in staged["models"]}
        self.assertEqual([p.get("resource") for p in by_id["device:oxp3"]["pending"]],
                         ["resource:intel-be201-firmware"])
        self.assertEqual([p.get("component") for p in by_id["device:x2-mini"]["pending"]],
                         ["pl4-175w", "lsfg-vk"])
        selected = by_id["device:x2-mini"]
        imported = import_brand(staged["directory"], self.root / "new-output",
                                selected["id"], selected["manifest_sha256"], developer=True)
        self.assertEqual(imported["package"]["id"], "device:x2-mini")

    def test_unresolved_cannot_be_imported_and_developer_required(self):
        self.brand["unresolved"] = ["missing required runtime"]
        self.write_bundle()
        with self.assertRaises(ProtocolError):
            stage_brand(self.archive, self.root / "untrusted", developer=False)
        result = self.stage()
        self.assertEqual(result["unresolved"], ["missing required runtime"])
        row = result["models"][0]
        with self.assertRaisesRegex(ProtocolError, "unresolved"):
            import_brand(result["directory"], self.root / "out", row["id"], row["manifest_sha256"], developer=True)

    def test_standard_package_archive_is_still_importable(self):
        normal = self.root / "normal.zip"
        with zipfile.ZipFile(normal, "w") as archive:
            for path in self.material.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(self.material).as_posix())
        self.assertFalse(stage_brand(normal, self.root / "brand", developer=True)["recognized"])
        result = import_local(normal, self.root / "ordinary", developer=True)
        self.assertEqual(result["package"]["id"], "device:test")

    def test_isolated_installer_runtime_can_parse_and_import_brand(self):
        source = Path(__file__).resolve().parents[1]
        runtime = self.root / "installer-runtime"
        shutil.copytree(source / "protocol", runtime / "protocol",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (runtime / "adaptations").mkdir()
        shutil.copy2(source / "adaptations/toon-parser-lock.json", runtime / "adaptations/toon-parser-lock.json")
        shutil.copytree(source / "adaptations/vendor", runtime / "adaptations/vendor",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        python = source / "runtime/python/python.exe"
        if not python.exists():
            self.skipTest("Locked portable Windows Python has not been prepared")
        command = [str(python), "-B", "-c",
                   "import sys; sys.path.insert(0,sys.argv.pop(1)); from protocol.__main__ import main; sys.exit(main())",
                   str(runtime)]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        staged = self.root / "isolated-staged"
        result = subprocess.run(command + ["stage-brand", str(self.archive), str(staged), "--developer"],
                                cwd=runtime, env=env, capture_output=True, text=True, check=True)
        selected = json.loads(result.stdout)["result"]["models"][0]
        output = self.root / "isolated-output"
        result = subprocess.run(command + ["import-brand", str(staged), str(output), selected["id"],
                                           selected["manifest_sha256"], "--developer"],
                                cwd=runtime, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["result"]["package"]["id"], selected["id"])
        self.assertEqual((output / "payload/test").read_bytes(), b"ok")

    def test_escaping_and_duplicate_mapping_are_rejected(self):
        for files in (
            [dict(path="../escape", member="common/example/payload")],
            [dict(path="payload/test", member="../escape")],
            [dict(path="payload/test", member="common/example/payload"),
             dict(path="PAYLOAD/TEST", member="common/example/payload")],
            [dict(path="payload/other", member="common/example/payload")],
        ):
            with self.subTest(files=files):
                self.models[0]["files"] = files
                self.write_bundle()
                with self.assertRaises(ProtocolError):
                    self.stage()


if __name__ == "__main__":
    unittest.main()
