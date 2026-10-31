"""Build an unsigned, locally verifiable Opti model-package candidate.

All inputs are supplied explicitly. The public bridge's locked readers are used
to authenticate plugin and offline archive bytes; plugin code is never imported
or executed by this builder.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import types
import zipfile


BASE_MANIFEST_SHA256 = "c80efd9cce07d35c5f8828d17b5fab8c15fbc177a10476754cfac04db3f27485"
CPU_SOURCE_LOCK_SHA256 = "b57fe2a5c69d0d5e41ba5a9710c0310eedc4a8b2d8b5048bbfb3a429e9aea9fa"
PUBLIC_CPU_SOURCE_COMMIT = "72f864e0498c10366493c74608c5ffed62cc99ca"
BRIDGE_BUILD_SHA256 = "4bf4fac5f58ac6ba4ea4e6789c675e5c2b22c5226b849ddcbc8209c99b3c4e32"
OFFLINE_LOCK_SHA256 = "a09a8b18ddaf4c7c93d0be9fa33165a5a59b9dc66529703cb483354d8b1cabf3"
PROTOCOL_MANIFEST_SHA256 = "6e1c168d04508fd588ad994511ffc5f3e9d26a87982cce820b1cbb2417f0ded0"
PROTOCOL_LOCAL_SHA256 = "1c9f73f2621ac0553d7a109401130a7801e24ef0f9ac29b6d8298526c32f2429"
PLUGIN_SOURCE_ORIGIN = "https://github.com/FlightDan/Opti2XMX/tree/"

EXPECTED_CPU_FILES = {
    "cpu-plugin-index.js": "a1cd67a64fc69b4f279b17ffe8fc5948b37fb7f4a233a10745fa01698492fa44",
    "cpu-plugin-main.py": "36e6ceacda689a4856486c5fe2fd541094b4f603b8282b79886ad7519f30736b",
    "cpu_control.py": "7dabf1846c473fbd3a17f4453e835fd5116d74abe6d0e3aee528bc74dcaa4872",
    "deploy-cpu.py": "3120cfd39c794d5475ee400acb90cb000842269fef71388c31783221913e9352",
    "power_control.py": "d5c5efd2f847d443bbff04dd30a3af847576d867eebcfb9b25c94d8e2b31654f",
}

EXPECTED_PLUGIN_FILES = frozenset(
    """
    plugin.json package.json main.py manager.py opti.py download_progress.py
    runtime_setup.py runtime-source.json per_game.py dist/index.js README.md
    README.zh-CN.md LICENSE THIRD_PARTY_NOTICES.md docs/RUNTIME.md
    docs/FRAME_GENERATION.md fsr.py fsr_cm.py fsr-source.json
    opti-archive-files.json docs/FSR_XMX.md fsr_compat/manifest.json
    fsr_compat/bin/fsrxdec.dll fsr_compat/bin/libwim-15.dll
    fsr_compat/licenses/fsrxdec-LICENSE.txt
    fsr_compat/licenses/wimlib/COPYING.GPLv3.txt
    fsr_compat/licenses/wimlib/COPYING.LGPL.txt
    fsr_compat/licenses/wimlib/COPYING.libdivsufsort-lite.txt
    fsr_compat/licenses/wimlib/COPYING.txt
    fsr_compat/sources/fsr_compat/build.ps1
    fsr_compat/sources/fsr_compat/dependency-source.json
    fsr_compat/sources/fsr_compat/fsrxdec.c
    fsr_compat/sources/fsr_compat/fsrxdec.def
    fsr_compat/sources/fsr_compat/LICENSE
    fsr_compat/sources/fsr_compat/README.md
    fsr_compat/sources/fsr_compat/test_fsrxdec.py
    fsr_compat/sources/wimlib-1.14.5.tar.gz
    """.split()
)

MUTABLE_PREFIXES = (
    "payload/legacy/components/opti2xmx/",
    "payload/legacy/integration/sources/opti2xmx/",
    "payload/legacy/integration/opti2xmx/",
)
MUTABLE_EXACT = frozenset(
    {
        "manifest.json",
        "payload/legacy/integration/manifest.json",
        "payload/legacy/components/opti2xmx-release.json",
        "payload/legacy/components/optiscaler-release.json",
    }
)
FORBIDDEN_PATH = re.compile(
    r"(?:^|[/_.-])(g3e|research|experimental[-_]cpu|dynamic[-_]power|cpu[-_](?:policy|scheduler))(?:$|[/_.-])",
    re.IGNORECASE,
)
FORBIDDEN_CONTENT = (
    re.compile(rb"g3e", re.IGNORECASE),
    re.compile(rb"dynamic[\s_-]{0,3}power[\s_-]{0,3}(?:schedul|policy)", re.IGNORECASE),
    re.compile(rb"(?:power|cpu)[\s_-]{0,3}research", re.IGNORECASE),
)
TEXT_SUFFIXES = frozenset(
    {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".c", ".h", ".md", ".txt", ".json", ".ps1",
     ".sh", ".toml", ".yaml", ".yml", ".ini", ".xml", ".html", ".conf", ".cfg", ".csv", ".bat", ".cmd"}
)
VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?\Z"
)
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
MAX_LOCK_BYTES = 1024 * 1024
MAX_PLUGIN_MEMBER_BYTES = 2 * 1024 * 1024


class CandidateError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_limited(path: Path, maximum: int = MAX_LOCK_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise CandidateError(f"Expected a regular input file: {path.name}")
    if path.stat().st_size > maximum:
        raise CandidateError(f"Input exceeds the configured size limit: {path.name}")
    return path.read_bytes()


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise CandidateError("Unsafe package-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise CandidateError("Unsafe package-relative path")
    if ":" in path.parts[0] or value.startswith("/"):
        raise CandidateError("Unsafe package-relative path")
    return path.as_posix()


def scan_tree(root: Path, *, with_hash: bool = True) -> dict[str, tuple[int, str]]:
    root = root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise CandidateError("Package root must be a regular directory")
    result: dict[str, tuple[int, str]] = {}
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(current)
        for name in list(dirs):
            path = base / name
            if path.is_symlink() or not path.is_dir():
                raise CandidateError("Package contains a link or non-directory entry")
        for name in files:
            path = base / name
            if path.is_symlink() or not path.is_file():
                raise CandidateError("Package contains a link or non-regular file")
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode):
                raise CandidateError("Package contains a non-regular file")
            relative = safe_relative(path.relative_to(root).as_posix())
            if relative.casefold() in {key.casefold() for key in result}:
                raise CandidateError("Package has case-colliding paths")
            result[relative] = (path.stat().st_size, sha256_file(path) if with_hash else "")
    return result


def package_inventory(root: Path) -> dict[str, tuple[int, str]]:
    inventory = scan_tree(root)
    inventory.pop("manifest.json", None)
    return inventory


def verify_inventory_manifest(root: Path, manifest: dict, protocol_manifest) -> dict[str, tuple[int, str]]:
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "DevicePackage":
        raise CandidateError("Input is not a supported DevicePackage")
    if manifest.get("id") != "device:oxp3":
        raise CandidateError("Input package identity differs from the locked base")
    listed: dict[str, tuple[int, str]] = {}
    for row in manifest.get("files", []):
        relative = safe_relative(row.get("path"))
        size, digest = row.get("size"), row.get("sha256")
        if not isinstance(size, int) or size < 0 or not isinstance(digest, str) or not HASH_PATTERN.fullmatch(digest):
            raise CandidateError("Malformed package inventory row")
        if relative in listed:
            raise CandidateError("Duplicate package inventory path")
        listed[relative] = (size, digest)
    actual = scan_tree(root, with_hash=False)
    listed_sizes = {path: size for path, (size, _) in listed.items()}
    actual_sizes = {path: size for path, (size, _) in actual.items() if path != "manifest.json"}
    if actual_sizes != listed_sizes:
        raise CandidateError("Package inventory does not match its files")
    try:
        checked = protocol_manifest.validate_package(
            root, allow_unsigned=True, expected_manifest_sha256=sha256_file(root / "manifest.json")
        )
    except Exception as exc:
        raise CandidateError("Protocol package validation failed") from exc
    if checked.get("id") != manifest.get("id"):
        raise CandidateError("Protocol validator returned a different package identity")
    return listed


def verify_cpu_source_lock(path: Path) -> dict[str, str]:
    raw = read_limited(path, 8 * 1024 * 1024)
    if sha256_bytes(raw) != CPU_SOURCE_LOCK_SHA256:
        raise CandidateError("CPU source lock is not the fixed public baseline")
    try:
        lock = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateError("CPU source lock is not valid JSON") from exc
    source = lock.get("source", {})
    if source.get("commit") != PUBLIC_CPU_SOURCE_COMMIT:
        raise CandidateError("CPU source lock commit differs from the fixed public baseline")
    rows = [
        row
        for row in lock.get("files", [])
        if row.get("path", "").startswith("brands/onexplayer/oxp3/scripts/controls/cpu/")
    ]
    files: dict[str, str] = {}
    for row in rows:
        relative = safe_relative(row.get("path"))
        name = PurePosixPath(relative).name
        digest = row.get("sha256")
        if name in files or not isinstance(digest, str) or not HASH_PATTERN.fullmatch(digest):
            raise CandidateError("Malformed CPU source lock")
        files[name] = digest
    if files != EXPECTED_CPU_FILES:
        raise CandidateError("The locked CPU source set differs from the five-file public baseline")
    return files


def verify_cpu_payload(base: Path, output: Path, manifest: dict, cpu_files: dict[str, str]) -> None:
    prefix = "payload/legacy/components/controls/cpu/"
    inventory = {row["path"]: row for row in manifest["files"]}
    for name, digest in cpu_files.items():
        relative = prefix + name
        path = output / PurePosixPath(relative)
        row = inventory.get(relative)
        if row is None or row.get("sha256") != digest or sha256_file(path) != digest:
            raise CandidateError("A CPU payload file differs from the fixed public baseline")
        if sha256_file(base / PurePosixPath(relative)) != digest:
            raise CandidateError("The locked base CPU file differs from the fixed public baseline")


def scan_forbidden_paths(paths) -> int:
    matches = [path for path in paths if FORBIDDEN_PATH.search(path)]
    if matches:
        raise CandidateError("A path whitelist contains a prohibited research or policy marker")
    return len(matches)


def verify_no_build_inputs_in_package(paths) -> int:
    leaked = [
        path
        for path in paths
        if Path(path).name.casefold().endswith(".input-lock.json")
        or Path(path).name.casefold() == "build-current-test.py"
    ]
    if leaked:
        raise CandidateError("A builder-only input or private helper entered the package")
    return len(leaked)


def scan_text_for_private_research(root: Path, approved_cpu_files: dict[str, str]) -> dict[str, int]:
    scanned = 0
    approved_cpu = 0
    for current, _, files in os.walk(root, topdown=True, followlinks=False):
        for name in files:
            path = Path(current) / name
            if path.suffix.casefold() not in TEXT_SUFFIXES:
                continue
            if path.is_symlink() or not path.is_file():
                raise CandidateError("Text path whitelist contains a link or non-regular file")
            data = path.read_bytes()
            relative = path.relative_to(root).as_posix()
            cpu_name = relative.removeprefix("payload/legacy/components/controls/cpu/")
            if relative.startswith("payload/legacy/components/controls/cpu/") and cpu_name in approved_cpu_files:
                if sha256_bytes(data) != approved_cpu_files[cpu_name]:
                    raise CandidateError("Approved CPU baseline bytes changed before research scan")
                approved_cpu += 1
                scanned += 1
                continue
            if any(pattern.search(data) for pattern in FORBIDDEN_CONTENT):
                raise CandidateError("Text payload contains a prohibited research marker")
            scanned += 1
    return {"text_file_count": scanned, "fixed_public_cpu_files_checked": approved_cpu}


def verify_plugin_lock(
    lock_path: Path,
    plugin_path: Path,
    *,
    expected_lock_sha256: str,
    expected_archive_sha256: str,
    expected_source_commit: str,
) -> tuple[dict, dict, str]:
    # These expectations come from the reviewed caller/TOON recipe. The input
    # lock must never nominate its own trust anchor.
    if (
        not isinstance(expected_lock_sha256, str)
        or not HASH_PATTERN.fullmatch(expected_lock_sha256)
        or not isinstance(expected_archive_sha256, str)
        or not HASH_PATTERN.fullmatch(expected_archive_sha256)
        or not isinstance(expected_source_commit, str)
        or not COMMIT_PATTERN.fullmatch(expected_source_commit)
    ):
        raise CandidateError("Independent reviewed plugin expectations are required")
    raw = read_limited(lock_path)
    lock_digest = sha256_bytes(raw)
    if lock_digest != expected_lock_sha256:
        raise CandidateError("Plugin input lock differs from the independently reviewed SHA-256")
    try:
        lock = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateError("Plugin lock is not valid JSON") from exc
    if (
        lock.get("schema_version") != 1
        or lock.get("kind") != "LocalOptiPluginInput"
        or not isinstance(lock.get("version"), str)
        or not VERSION_PATTERN.fullmatch(lock["version"])
        or not isinstance(lock.get("source_commit"), str)
        or not COMMIT_PATTERN.fullmatch(lock["source_commit"])
    ):
        raise CandidateError("Plugin lock schema or source identity is invalid")
    archive = lock.get("archive")
    files = lock.get("files")
    if not isinstance(archive, dict) or not isinstance(files, dict):
        raise CandidateError("Plugin lock is missing archive or member pins")
    if (
        lock["source_commit"] != expected_source_commit
        or archive.get("sha256") != expected_archive_sha256
    ):
        raise CandidateError("Plugin identity differs from the independently reviewed expectations")
    size, digest = archive.get("size"), archive.get("sha256")
    if not isinstance(size, int) or size <= 0 or not isinstance(digest, str) or not HASH_PATTERN.fullmatch(digest):
        raise CandidateError("Plugin archive pin is malformed")
    if not plugin_path.is_file() or plugin_path.is_symlink():
        raise CandidateError("Plugin input must be an explicit regular file")
    if plugin_path.stat().st_size != size or sha256_file(plugin_path) != digest:
        raise CandidateError("Plugin archive differs from its explicit lock")
    names: set[str] = set()
    for name, member_digest in files.items():
        safe_relative(name)
        if name not in EXPECTED_PLUGIN_FILES or name.casefold() in names:
            raise CandidateError("Plugin lock contains a path outside the reviewed whitelist")
        if not isinstance(member_digest, str) or not HASH_PATTERN.fullmatch(member_digest):
            raise CandidateError("Plugin member hash is malformed")
        names.add(name.casefold())
    if set(files) != EXPECTED_PLUGIN_FILES:
        raise CandidateError("Plugin lock member set differs from the reviewed whitelist")
    calculated = sha256_bytes(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())
    if lock.get("files_sha256") != calculated:
        raise CandidateError("Plugin member-list digest differs from the lock")
    if any("cpu" in name.casefold() for name in files):
        raise CandidateError("Plugin input attempts to add CPU policy files")
    if scan_forbidden_paths(files.keys()) != 0:
        raise CandidateError("Plugin path scan failed")

    expected_members = {"opti2xmx/" + name for name in files}
    try:
        with zipfile.ZipFile(plugin_path) as archive_zip:
            members = archive_zip.infolist()
            names_on_disk: set[str] = set()
            runtime_bytes = None
            for item in members:
                member = safe_relative(item.filename)
                mode = item.external_attr >> 16
                if (
                    member not in expected_members
                    or member.casefold() in names_on_disk
                    or item.is_dir()
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or item.file_size > MAX_PLUGIN_MEMBER_BYTES
                ):
                    raise CandidateError("Plugin archive contains an unapproved member")
                relative = member.removeprefix("opti2xmx/")
                data = archive_zip.read(item)
                if sha256_bytes(data) != files[relative]:
                    raise CandidateError("Plugin archive member differs from its lock")
                names_on_disk.add(member.casefold())
                if relative == "runtime-source.json":
                    runtime_bytes = data
            if names_on_disk != {name.casefold() for name in expected_members} or runtime_bytes is None:
                raise CandidateError("Plugin archive is incomplete")
    except (OSError, zipfile.BadZipFile) as exc:
        raise CandidateError("Plugin archive is not a readable ZIP") from exc
    try:
        runtime = json.loads(runtime_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateError("Plugin runtime descriptor is malformed") from exc
    return lock, runtime, lock_digest


def load_bridge(build_path: Path):
    if build_path.is_symlink() or not build_path.is_file() or sha256_file(build_path) != BRIDGE_BUILD_SHA256:
        raise CandidateError("Public bridge build.py does not match the reviewed version")
    spec = importlib.util.spec_from_file_location("opti_candidate_public_bridge", build_path)
    if spec is None or spec.loader is None:
        raise CandidateError("Unable to load the explicit public bridge builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_protocol(protocol_root: Path):
    manifest_path = protocol_root / "manifest.py"
    local_path = protocol_root / "local.py"
    try:
        protocol_hashes_match = (
            sha256_file(manifest_path) == PROTOCOL_MANIFEST_SHA256
            and sha256_file(local_path) == PROTOCOL_LOCAL_SHA256
        )
    except OSError:
        protocol_hashes_match = False
    if not protocol_root.is_dir() or protocol_root.is_symlink() or not protocol_hashes_match:
        raise CandidateError("Explicit package protocol files do not match the reviewed versions")
    package_name = "_opti_candidate_protocol"
    package = types.ModuleType(package_name)
    package.__path__ = [str(protocol_root.resolve())]
    sys.modules[package_name] = package
    loaded = {}
    for name, path in (("manifest", manifest_path), ("local", local_path)):
        spec = importlib.util.spec_from_file_location(package_name + "." + name, path)
        if spec is None or spec.loader is None:
            raise CandidateError("Unable to load the explicit package protocol")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["manifest"], loaded["local"]


def extract_offline_archives(base: Path, resources: dict, pins: dict, destination: Path) -> list[Path]:
    opti = resources.get("opti2xmx")
    if not isinstance(opti, dict) or not isinstance(opti.get("files"), list):
        raise CandidateError("Locked base lacks the original Opti integration inventory")
    entries = opti["files"]
    archives = pins["optiscaler_release"]["archives"]
    result: list[Path] = []
    integration_root = base / "payload" / "legacy" / "integration"
    for item in archives:
        filename = item["filename"]
        matches = [
            row
            for row in entries
            if isinstance(row.get("target"), str) and PurePosixPath(row["target"]).name == filename
        ]
        if len(matches) != 1:
            raise CandidateError("Base integration has no unique source for a locked offline archive")
        row = matches[0]
        target = row["target"]
        if not target.startswith("home/deck/") or not target.endswith("/" + filename):
            raise CandidateError("Offline archive target is outside the reviewed download target pattern")
        source = safe_relative(row.get("source"))
        if not source.startswith("opti2xmx/"):
            raise CandidateError("Offline archive source is outside the locked Opti component")
        source_path = integration_root / PurePosixPath(source)
        if source_path.is_symlink() or not source_path.is_file():
            raise CandidateError("Offline archive source is not a regular package member")
        if source_path.stat().st_size != item["size_bytes"] or sha256_file(source_path) != item["sha256"]:
            raise CandidateError("Base offline archive differs from the public lock")
        destination_path = destination / filename
        shutil.copyfile(source_path, destination_path)
        result.append(destination_path)
    if len(result) != len(archives):
        raise CandidateError("Incomplete offline archive set")
    return result


def mutable_path(path: str) -> bool:
    return path in MUTABLE_EXACT or path.startswith(MUTABLE_PREFIXES)


def verify_preserved_bytes(base: Path, output: Path) -> int:
    before = scan_tree(base)
    after = scan_tree(output)
    return verify_preserved_maps(before, after)


def verify_preserved_maps(before: dict[str, tuple[int, str]], after: dict[str, tuple[int, str]]) -> int:
    changed = [
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path) and not mutable_path(path)
    ]
    if changed:
        raise CandidateError("A non-Opti package member changed")
    preserved = sum(1 for path in set(before) | set(after) if not mutable_path(path) and before.get(path) == after.get(path))
    if preserved == 0:
        raise CandidateError("No original package members were verified")
    return preserved


def verify_resources_preserved(before: dict, after: dict) -> None:
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise CandidateError("Malformed integration manifest")
    if {key: value for key, value in before.items() if key != "opti2xmx"} != {
        key: value for key, value in after.items() if key != "opti2xmx"
    }:
        raise CandidateError("An unrelated component integration record changed")


def inventory_rows(package: Path) -> list[dict]:
    inventory = package_inventory(package)
    ordered_paths = sorted(package / PurePosixPath(path) for path in inventory)
    return [
        {
            "path": path.relative_to(package).as_posix(),
            "size": inventory[path.relative_to(package).as_posix()][0],
            "sha256": inventory[path.relative_to(package).as_posix()][1],
        }
        for path in ordered_paths
    ]


def write_json(path: Path, value) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def ensure_candidate_output(path: Path, *, protected_input_roots=()) -> Path:
    # Check the lexical path before resolve could hide a symlink or junction.
    candidate = Path(os.path.abspath(path))
    for part in (candidate, *candidate.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise CandidateError("Output path cannot contain a link or junction")
    if candidate.exists() or candidate.is_symlink():
        raise CandidateError("Output path already exists")
    for root in protected_input_roots:
        root = Path(root).resolve(strict=True)
        if candidate.resolve(strict=False).is_relative_to(root):
            raise CandidateError("Output cannot be inside an input package")
    return candidate


def build_candidate(args) -> dict:
    if not VERSION_PATTERN.fullmatch(args.version):
        raise CandidateError("A canonical model-package candidate version is required")
    if args.project_root.is_symlink():
        raise CandidateError("Project root must not be a link")
    project_root = args.project_root.resolve(strict=True)
    if not project_root.is_dir():
        raise CandidateError("Project root must be an explicit regular directory")
    if args.base.is_symlink():
        raise CandidateError("Base package must not be a link")
    base = args.base.resolve(strict=True)
    if not base.is_dir():
        raise CandidateError("Base package must be an explicit regular directory")
    output_root = ensure_candidate_output(args.output, protected_input_roots=(base,))

    bridge_path = project_root / "adaptations/brands/onexplayer/oxp3/bridge/scripts/build.py"
    offline_lock_path = project_root / "adaptations/brands/onexplayer/oxp3/bridge/opti-offline-lock.json"
    source_lock_path = project_root / "adaptations/source-lock.json"
    protocol_root = project_root / "protocol"
    bridge = load_bridge(bridge_path)
    protocol_manifest, protocol_local = load_protocol(protocol_root)

    offline_raw = read_limited(offline_lock_path)
    if sha256_bytes(offline_raw) != OFFLINE_LOCK_SHA256:
        raise CandidateError("Public Opti offline lock does not match the reviewed version")
    pins = bridge.opti_lock(offline_raw)
    plugin_lock, _runtime_info, plugin_lock_digest = verify_plugin_lock(
        args.plugin_lock,
        args.plugin_zip,
        expected_lock_sha256=args.expected_plugin_lock_sha256,
        expected_archive_sha256=args.expected_plugin_sha256,
        expected_source_commit=args.expected_plugin_source_commit,
    )
    cpu_files = verify_cpu_source_lock(source_lock_path)

    manifest_path = base / "manifest.json"
    base_manifest_raw = read_limited(manifest_path, 16 * 1024 * 1024)
    base_manifest_digest = sha256_bytes(base_manifest_raw)
    if base_manifest_digest != BASE_MANIFEST_SHA256:
        raise CandidateError("Base package manifest differs from its fixed hash")
    try:
        base_manifest = json.loads(base_manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateError("Base package manifest is malformed") from exc
    base_files = verify_inventory_manifest(base, base_manifest, protocol_manifest)
    verify_cpu_payload(base, base, base_manifest, cpu_files)
    base_path_scan = scan_forbidden_paths(base_files.keys())

    plugin_source_url = PLUGIN_SOURCE_ORIGIN + plugin_lock["source_commit"]
    pins["kind"] = "LocalTestOptiOfflineInputs"
    plugin_archive = {
        "version": plugin_lock["version"],
        "filename": args.plugin_zip.name,
        "size_bytes": plugin_lock["archive"]["size"],
        "sha256": plugin_lock["archive"]["sha256"],
        "url": plugin_source_url,
    }
    plugin_release = copy.deepcopy(pins["plugin_release"])
    plugin_release.update(
        plugin_version=plugin_lock["version"],
        plugin_sha256=plugin_lock["archive"]["sha256"],
        source_commit=plugin_lock["source_commit"],
        source_url=plugin_source_url,
        release_url=None,
        files=plugin_lock["files"],
        files_sha256=plugin_lock["files_sha256"],
        published=False,
        official_release=False,
    )
    pins["plugin_archive"] = plugin_archive
    pins["plugin_release"] = plugin_release
    plugin_path_scan = scan_forbidden_paths(plugin_lock["files"].keys())

    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".opti-candidate-", dir=output_root.parent) as temp_name:
        work = Path(temp_name)
        package = work / "package"
        shutil.copytree(base, package, symlinks=True)
        resources_path = package / "payload/legacy/integration/manifest.json"
        resources_raw = read_limited(resources_path, 8 * 1024 * 1024)
        resources_before = json.loads(resources_raw)
        offline_dir = work / "offline"
        offline_dir.mkdir()
        offline_paths = extract_offline_archives(package, resources_before, pins, offline_dir)
        records, offline = bridge.read_opti_inputs(args.plugin_zip, offline_paths, pins)
        if set(records) != EXPECTED_PLUGIN_FILES:
            raise CandidateError("Public bridge returned a member set outside the explicit plugin whitelist")
        update = bridge.apply_opti_update(package, resources_before, records, offline, pins)
        if update.get("contains_game_state") is not False:
            raise CandidateError("Opti update metadata must exclude game state")
        update["kind"] = "local-test-opti-offline-v1"

        resources_after = json.loads((package / "payload/legacy/integration/manifest.json").read_text(encoding="utf-8"))
        verify_resources_preserved(resources_before, resources_after)
        runtime_relative = "payload/legacy/integration/opti2xmx/runtime.tar.gz"
        if sha256_file(base / PurePosixPath(runtime_relative)) != sha256_file(package / PurePosixPath(runtime_relative)):
            raise CandidateError("Published Opti runtime bytes changed")

        candidate_manifest = copy.deepcopy(base_manifest)
        candidate_manifest["version"] = args.version
        components = [
            item for item in candidate_manifest.get("components", []) if item.get("id") == "oxp3:legacy-adaptation"
        ]
        if len(components) != 1:
            raise CandidateError("Base package does not have a unique adaptation component")
        components[0]["version"] = args.version
        capability = [
            item for item in candidate_manifest.get("legacy_capabilities", []) if item.get("id") == "oxp3:opti2xmx"
        ]
        if len(capability) != 1:
            raise CandidateError("Base package does not have a unique Opti capability record")
        capability[0].update(
            payload_version=plugin_lock["version"],
            targets=[row["target"] for row in resources_after["opti2xmx"]["files"]],
            offline_optiscaler_versions=resources_after["opti2xmx"].get("offline_optiscaler_versions", []),
        )
        candidate_manifest["payload_updates"] = [update]
        candidate_manifest["files"] = inventory_rows(package)
        write_json(package / "manifest.json", candidate_manifest)

        package_manifest_digest = sha256_file(package / "manifest.json")
        output_files = verify_inventory_manifest(package, candidate_manifest, protocol_manifest)
        verify_cpu_payload(base, package, candidate_manifest, cpu_files)
        candidate_files = {row["path"]: (row["size"], row["sha256"]) for row in candidate_manifest["files"]}
        preserved_count = verify_preserved_maps(base_files, candidate_files)
        final_path_scan = scan_forbidden_paths(candidate_files.keys())
        build_input_paths = verify_no_build_inputs_in_package(candidate_files.keys())
        text_scan = scan_text_for_private_research(package, cpu_files)
        if candidate_manifest.get("trust", {}).get("official_release") is not False:
            raise CandidateError("Candidate must remain explicitly non-official")
        if candidate_manifest.get("trust", {}).get("status") != "unsigned-development":
            raise CandidateError("Candidate must remain unsigned development material")

        archive_path = work / "package.zip"
        bridge.deterministic_zip(package, archive_path)
        imported_path = work / "imported"
        imported = protocol_local.import_local(archive_path, imported_path, developer=True)
        if imported.get("manifest_sha256") != package_manifest_digest:
            raise CandidateError("Protocol import did not preserve the package manifest hash")
        archive_members = set()
        with zipfile.ZipFile(archive_path) as built_zip:
            for info in built_zip.infolist():
                member = safe_relative(info.filename)
                if member.casefold() in {name.casefold() for name in archive_members}:
                    raise CandidateError("Built archive contains duplicate paths")
                archive_members.add(member)
        if archive_members != set(candidate_files) | {"manifest.json"}:
            raise CandidateError("Built archive membership differs from the checked package tree")

        report = {
            "schema_version": 1,
            "kind": "UnsignedOptiModelPackageCandidate",
            "version": args.version,
            "trust_status": "unsigned-development",
            "official_release": False,
            "physical_device_acceptance": False,
            "base_manifest_sha256": base_manifest_digest,
            "public_cpu_source_commit": PUBLIC_CPU_SOURCE_COMMIT,
            "cpu_source_lock_sha256": CPU_SOURCE_LOCK_SHA256,
            "cpu_files": [
                {"name": name, "sha256": digest} for name, digest in sorted(cpu_files.items())
            ],
            "bridge_build_sha256": BRIDGE_BUILD_SHA256,
            "offline_lock_sha256": OFFLINE_LOCK_SHA256,
            "plugin_version": plugin_lock["version"],
            "plugin_source_commit": plugin_lock["source_commit"],
            "plugin_archive_sha256": plugin_lock["archive"]["sha256"],
            "plugin_lock_sha256": plugin_lock_digest,
            "plugin_member_count": len(records),
            "offline_archive_count": len(offline),
            "retained_runtime_sha256": sha256_file(package / PurePosixPath(runtime_relative)),
            "package_manifest_sha256": package_manifest_digest,
            "package_zip_sha256": sha256_file(archive_path),
            "package_zip_size": archive_path.stat().st_size,
            "package_file_count": len(output_files),
            "unrelated_package_files_byte_identical_count": preserved_count,
            "all_unrelated_package_files_byte_identical": True,
            "unrelated_component_records_unchanged": True,
            "path_whitelist_scan": {
                "base_file_count": len(base_files),
                "plugin_member_count": len(records),
                "plugin_member_whitelist_exact": True,
                "forbidden_base_path_matches": base_path_scan,
                "forbidden_plugin_path_matches": plugin_path_scan,
                "forbidden_output_path_matches": final_path_scan,
                "build_only_input_or_helper_paths": build_input_paths,
                "plugin_input_lock_packaged": False,
                "text_file_count_scanned": text_scan["text_file_count"],
                "fixed_public_cpu_text_files_hash_exempted": text_scan["fixed_public_cpu_files_checked"],
                "forbidden_research_content_matches": 0,
                "non_opti_package_changes": 0,
            },
            "blockers": [],
        }
        output_root.mkdir(parents=True)
        shutil.copytree(package, output_root / "package")
        shutil.copyfile(archive_path, output_root / "package.zip")
        write_json(output_root / "report.json", report)
        return report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--plugin-zip", type=Path, required=True)
    parser.add_argument("--plugin-lock", type=Path, required=True)
    parser.add_argument("--expected-plugin-lock-sha256", required=True,
                        help="Independent reviewed lock SHA-256, retained in the caller's TOON recipe")
    parser.add_argument("--expected-plugin-sha256", required=True,
                        help="Independent reviewed plugin ZIP SHA-256")
    parser.add_argument("--expected-plugin-source-commit", required=True,
                        help="Independent reviewed exact plugin source commit")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    try:
        report = build_candidate(parse_args(argv))
    except CandidateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "report": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
