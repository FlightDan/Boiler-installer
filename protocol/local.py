"""Explicit session-only local package intake. Import never executes package code."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from .manifest import ProtocolError, relative_path, safe_extract_archive, validate_package, _filesystem_path


MAX_SCRIPT = 8 * 1024**2
MAX_ARCHIVE = 8 * 1024**3
SCRIPT_HELP = ("Use the Boiler script contract: UTF-8/LF Bash, '# boiler-script: 1', "
               "and arguments PHASE (apply/verify/restore), TARGET_ROOT. "
               "See docs/DEVICE_PACKAGES.md.")


def _link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _safe_path(path):
    """Inspect the lexical path before resolve can hide a link or junction."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if _link(_filesystem_path(part)):
            raise ProtocolError("Local package paths cannot contain links or junctions")
    return path


@contextlib.contextmanager
def _regular_file(path):
    path = _safe_path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ProtocolError("Local package input must be a regular file")
        yield stream


def _copy_file(source, destination, maximum):
    with _regular_file(source) as stream, destination.open("xb") as target:
        size = os.fstat(stream.fileno()).st_size
        if size > maximum:
            raise ProtocolError("Local package input exceeds size limit")
        copied = 0
        while block := stream.read(min(1024**2, maximum + 1 - copied)):
            copied += len(block)
            if copied > maximum:
                raise ProtocolError("Local package input exceeds size limit")
            target.write(block)
        if copied != size:
            raise ProtocolError("Local package input changed while copying")
        target.flush()
        os.fsync(target.fileno())


def _script_package(source, destination):
    with _regular_file(source) as stream:
        raw = stream.read(MAX_SCRIPT + 1)
    if len(raw) > MAX_SCRIPT:
        raise ProtocolError("Script exceeds size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ProtocolError(SCRIPT_HELP) from exc
    lines = text.splitlines()
    if (not lines or lines[0] not in ("#!/bin/bash", "#!/usr/bin/env bash")
            or b"\r" in raw or b"\x00" in raw):
        raise ProtocolError(SCRIPT_HELP)
    headers = {}
    for line in lines[1:64]:
        if not line.strip():
            continue
        if not line.startswith("#"):
            break
        for key in ("boiler-script", "boiler-name"):
            prefix = "# " + key + ":"
            if line.startswith(prefix):
                if key in headers:
                    raise ProtocolError("Duplicate script declaration. " + SCRIPT_HELP)
                headers[key] = line[len(prefix):].strip()
    if headers.get("boiler-script") != "1":
        raise ProtocolError(SCRIPT_HELP)
    name = headers.get("boiler-name", source.stem)
    if not name or len(name) > 256 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name):
        raise ProtocolError("Invalid boiler-name declaration")
    digest = hashlib.sha256(raw).hexdigest()
    scripts = destination / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "device.sh").write_bytes(raw)
    hooks = {}
    for phase in ("apply", "verify", "restore"):
        relative = "scripts/" + phase + ".sh"
        hooks[phase] = relative
        wrapper = ("#!/bin/bash\nset -euo pipefail\n"
                   '[[ "$#" -eq 1 && "$1" = /* && -d "$1" ]] || { echo "Expected an absolute target root" >&2; exit 2; }\n'
                   'script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"\n'
                   'exec /bin/bash "$script_dir/device.sh" ' + phase + ' "$1"\n')
        (destination / relative).write_bytes(wrapper.encode("utf-8"))
    inventory = []
    for file in sorted(scripts.iterdir()):
        data = file.read_bytes()
        inventory.append({"path": file.relative_to(destination).as_posix(),
                          "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    compatibility = {"architectures": ["x86_64"]}
    manifest = {"schema_version": 1, "kind": "DevicePackage", "id": "device:local-" + digest[:24],
                "version": "0.1.0", "name": name, "status": "experimental",
                "trust": {"status": "unsigned-development", "official_release": False},
                "source": {"kind": "local", "path": source.name}, "compatibility": compatibility,
                "notes": ["Local script imported with explicit session trust. The author is responsible for OS compatibility, read-only verify, and complete restore. No hardware acceptance or automatic maintenance upgrade is declared."],
                "files": inventory,
                "components": [{"schema_version": 1, "kind": "ComponentManifest",
                                "id": "local-script:" + digest[:24], "version": "0.1.0", "name": name,
                                "default": True, "compatibility": compatibility, "files": [], "hooks": hooks}]}
    (destination / "manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _copy_package(source, destination):
    total = count = 0
    for path in source.rglob("*"):
        if _link(path):
            raise ProtocolError("Package contains a link or junction")
        info = path.stat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ProtocolError("Package contains a special file")
        count += 1
        total += info.st_size
        if count > 100000 or total > MAX_ARCHIVE:
            raise ProtocolError("Package inventory exceeds size limit")
    manifest = validate_package(source, allow_unsigned=True)
    # Authenticate copied bytes against the exact manifest observed at validation.
    with _regular_file(source / "manifest.json") as stream:
        raw = stream.read(MAX_SCRIPT + 1)
    if len(raw) > MAX_SCRIPT:
        raise ProtocolError("Manifest exceeds size limit")
    destination.mkdir()
    for entry in manifest["files"]:
        name = relative_path(entry["path"])
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy_file(source / name, target, entry["size"])
    (destination / "manifest.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def import_local(source, destination, *, developer=False):
    """Copy into a verified generation; developer=True authorizes intake, never execution.

    The caller must separately obtain session trust before selecting an unsigned
    package for privileged guest installation. This function persists no trust.
    """
    if developer is not True:
        raise ProtocolError("Local import requires explicit developer session trust")
    source, destination = _safe_path(source), _safe_path(destination)
    display_destination = destination
    source, destination = _filesystem_path(source), _filesystem_path(destination)
    if os.path.lexists(destination):
        raise ProtocolError("Package destination already exists")
    if not source.exists():
        raise ProtocolError("Local package source does not exist")
    if source.is_dir() and (destination == source or source in destination.parents):
        raise ProtocolError("Package destination cannot be inside source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".local-device-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        ready = temporary / "verified"
        if source.is_dir():
            kind = "directory"
            expected = _copy_package(source, ready)
        elif source.suffix.lower() == ".sh":
            kind = "script"
            _script_package(source, ready)
            expected = hashlib.sha256((ready / "manifest.json").read_bytes()).hexdigest()
        else:
            if not source.name.lower().endswith((".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2")):
                raise ProtocolError("Use a .sh script, package folder, ZIP, or tar archive")
            kind = "archive"
            snapshot = temporary / "archive"
            _copy_file(source, snapshot, MAX_ARCHIVE)
            tree = safe_extract_archive(snapshot, temporary / "extracted")
            children = list(tree.iterdir())
            root = tree
            if not (tree / "manifest.json").is_file() and len(children) == 1 and children[0].is_dir():
                root = children[0]
            expected = _copy_package(root, ready)
        manifest = validate_package(ready, expected_manifest_sha256=expected)
        if os.path.lexists(destination):
            raise ProtocolError("Package destination already exists")
        _safe_path(destination)
        os.rename(ready, destination)
    return {"package": manifest, "directory": str(display_destination),
            "manifest_sha256": expected, "kind": kind}
