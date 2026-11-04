"""Import an explicitly selected model from a local TOON brand bundle.

The brand archive is a transport for ordinary DevicePackages. It grants no
trust: import requires the same developer-session authorization as a local
package and the selected package still passes the existing manifest validator.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
import tempfile
import zipfile

from .local import MAX_ARCHIVE, _copy_file, _regular_file, _safe_path
from .manifest import (MAX_MANIFEST, ProtocolError, _bounded_bytes, _decode_json,
                       _publish_directory, confined, relative_path,
                       safe_extract_archive, validate_manifest, validate_package)
from .resource_pool import decoder


def _brand(root):
    root = _safe_path(root)
    def read_member(name, limit):
        return _bounded_bytes(confined(root, name), limit)
    return _read_brand(read_member, lambda name: confined(root, name).is_file())


def _read_brand(read_member, has_member):
    raw = read_member("brand.toon", MAX_MANIFEST)
    try:
        value = decoder().loads(raw.decode("utf-8"), strict=True)
    except (UnicodeError, ValueError) as error:
        raise ProtocolError("Invalid brand index") from error
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("kind") != "BoilerBrandBundle"
            or value.get("brand") != "onexplayer"
            or value.get("source_repository") != "FlightDan/Boiler-adaptations"
            or not isinstance(value.get("source_commit"), str)
            or not value["source_commit"]):
        raise ProtocolError("Unsupported brand bundle")
    unresolved = value.get("unresolved")
    if not isinstance(unresolved, list):
        raise ProtocolError("Brand bundle missing unresolved status")
    pending = value.get("pending", [])
    if not isinstance(pending, list) or any(not isinstance(item, dict) for item in pending):
        raise ProtocolError("Invalid pending feature list")
    entries = value.get("models")
    if not isinstance(entries, list) or not entries:
        raise ProtocolError("Brand bundle has no models")
    models, ids = [], set()
    for row in entries:
        if not isinstance(row, dict):
            raise ProtocolError("Invalid brand model")
        identity, guide, manifest_name = row.get("id"), row.get("guide"), row.get("manifest")
        if not isinstance(identity, str) or not identity.startswith("device:") or identity in ids:
            raise ProtocolError("Duplicate or invalid brand model")
        ids.add(identity)
        for path in (guide, manifest_name):
            relative_path(path)
            if not path.startswith("brands/onexplayer/"):
                raise ProtocolError("Brand metadata path outside brand")
        if not has_member(guide):
            raise ProtocolError("Brand model guide is missing")
        manifest_raw = read_member(manifest_name, MAX_MANIFEST)
        manifest = validate_manifest(_decode_json(manifest_raw), "DevicePackage")
        if manifest["id"] != identity:
            raise ProtocolError("Brand model identity differs from package")
        mappings = row.get("files")
        if not isinstance(mappings, list):
            raise ProtocolError("Brand model files missing")
        paths, members = {}, {}
        for item in mappings:
            if not isinstance(item, dict):
                raise ProtocolError("Invalid brand file mapping")
            logical = relative_path(item.get("path"))
            member = relative_path(item.get("member"))
            if not member.startswith(("common/", "brands/onexplayer/")):
                raise ProtocolError("Brand payload outside permitted directories")
            if logical.casefold() in paths:
                raise ProtocolError("Duplicate brand package path")
            paths[logical.casefold()] = (logical, member)
            if not has_member(member):
                raise ProtocolError("Brand payload member missing")
            members[logical] = member
        if set(members) != {item["path"] for item in manifest.get("files", [])}:
            raise ProtocolError("Brand mapping differs from package inventory")
        models.append({"id": identity, "name": manifest.get("name", identity),
                       "manifest": manifest_name, "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                       "pending": [item for item in pending if item.get("model") == identity],
                       "files": members})
    return value, models


def stage_brand(source, destination, *, developer=False):
    """Recognize a brand ZIP and stage a stable copy for explicit model choice."""
    if developer is not True:
        raise ProtocolError("Local import requires explicit developer session trust")
    source, destination = _safe_path(source), _safe_path(destination)
    if not source.is_file() or source.suffix.lower() != ".zip":
        return {"recognized": False}
    with _regular_file(source) as stream:
        if not zipfile.is_zipfile(stream):
            return {"recognized": False}
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            if "brand.toon" not in archive.namelist():
                return {"recognized": False}
    if os.path.lexists(destination):
        raise ProtocolError("Brand staging destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".local-brand-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        snapshot = temporary / "archive.zip"
        _copy_file(source, snapshot, MAX_ARCHIVE)
        tree = safe_extract_archive(snapshot, temporary / "extracted")
        value, models = _brand(tree)
        _publish_directory(tree, destination, os.rename, guard=_safe_path)
    return {"recognized": True, "directory": str(destination),
            "models": [{key: item[key] for key in ("id", "name", "manifest_sha256", "pending")} for item in models],
            "unresolved": value["unresolved"]}


def import_brand(source, destination, identity, expected_manifest_sha256, *, developer=False):
    """Materialize one reviewed model and validate it as a normal DevicePackage."""
    if developer is not True:
        raise ProtocolError("Local import requires explicit developer session trust")
    source, destination = _safe_path(source), _safe_path(destination)
    if os.path.lexists(destination):
        raise ProtocolError("Package destination already exists")
    value, models = _brand(source)
    if value["unresolved"]:
        raise ProtocolError("Brand bundle contains unresolved required resources")
    selected = [model for model in models if model["id"] == identity]
    if len(selected) != 1:
        raise ProtocolError("Unknown brand model")
    model = selected[0]
    if not expected_manifest_sha256 or model["manifest_sha256"] != expected_manifest_sha256:
        raise ProtocolError("Reviewed package manifest changed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".local-brand-model-", dir=destination.parent) as temporary:
        ready = Path(temporary) / "verified"
        ready.mkdir()
        manifest_raw = _bounded_bytes(confined(source, model["manifest"]), MAX_MANIFEST)
        (ready / "manifest.json").write_bytes(manifest_raw)
        manifest = validate_manifest(_decode_json(manifest_raw), "DevicePackage")
        for entry in manifest["files"]:
            logical = entry["path"]
            target = confined(ready, logical)
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(confined(source, model["files"][logical]), target, entry["size"])
        package = validate_package(ready, expected_manifest_sha256=expected_manifest_sha256)
        if package["id"] != identity:
            raise ProtocolError("Brand selection changed")
        _publish_directory(ready, destination, os.rename, guard=_safe_path)
    return {"package": package, "directory": str(destination),
            "manifest_sha256": expected_manifest_sha256, "kind": "brand"}


def import_bundled_brand(anchor, destination, identity, expected_manifest_sha256, manifest_size, files, version):
    """Verify the bundled archive and materialize only the selected model."""
    source, destination = _safe_path(anchor["path"]), _safe_path(destination)
    if os.path.lexists(destination):
        raise ProtocolError("Package destination already exists")
    with _regular_file(source) as stream:
        digest = hashlib.sha256()
        size = 0
        while block := stream.read(1024 * 1024):
            size += len(block)
            if size > anchor["size"] or size > MAX_ARCHIVE:
                raise ProtocolError("Bundled archive size mismatch")
            digest.update(block)
        if size != anchor["size"] or digest.hexdigest() != anchor["sha256"]:
            raise ProtocolError("Bundled archive verification failed")
        stream.seek(0)
        try:
            archive = zipfile.ZipFile(stream)
        except (zipfile.BadZipFile, OSError) as error:
            raise ProtocolError("Invalid bundled archive") from error
        with archive:
            members, folded, total = {}, set(), 0
            for count, member in enumerate(archive.infolist(), 1):
                if count > 100000:
                    raise ProtocolError("Archive file count exceeds limit")
                name = relative_path(member.filename.rstrip("/"))
                if name.casefold() in folded:
                    raise ProtocolError("Duplicate archive path")
                folded.add(name.casefold())
                if stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ProtocolError("Archive links or special files are forbidden")
                total += member.file_size
                if member.file_size < 0 or total > 8 * 1024**3:
                    raise ProtocolError("Archive size exceeds limit")
                if not member.is_dir():
                    members[name] = member

            def read_member(name, limit):
                member = members.get(name)
                if member is None or member.file_size > limit:
                    raise ProtocolError("Missing or oversized brand member")
                with archive.open(member) as entry:
                    data = entry.read(limit + 1)
                if len(data) != member.file_size:
                    raise ProtocolError("Invalid brand member length")
                return data
            brand, models = _read_brand(read_member, members.__contains__)
            if brand["unresolved"]:
                raise ProtocolError("Brand bundle contains unresolved required resources")
            selected = next((model for model in models if model["id"] == identity), None)
            if selected is None:
                raise ProtocolError("Unknown brand model")
            if selected["manifest_sha256"] != expected_manifest_sha256:
                raise ProtocolError("Reviewed package manifest changed")
            raw = read_member(selected["manifest"], MAX_MANIFEST)
            manifest = validate_manifest(_decode_json(raw), "DevicePackage")
            if (len(raw) != manifest_size or manifest["version"] != version
                    or len(manifest["files"]) != files or manifest.get("purpose") == "maintenance"):
                raise ProtocolError("Bundled model metadata mismatch")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".bundled-model-", dir=destination.parent) as temporary:
                ready = Path(temporary) / "verified"
                ready.mkdir()
                (ready / "manifest.json").write_bytes(raw)
                for item in manifest["files"]:
                    target = confined(ready, item["path"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    member = members[selected["files"][item["path"]]]
                    if member.file_size != item["size"]:
                        raise ProtocolError("Brand member size differs from inventory")
                    with archive.open(member) as entry, target.open("xb") as output:
                        remaining = member.file_size
                        while remaining:
                            block = entry.read(min(1024 * 1024, remaining))
                            if not block:
                                raise ProtocolError("Truncated archive")
                            output.write(block)
                            remaining -= len(block)
                        if entry.read(1):
                            raise ProtocolError("Archive entry exceeds declared size")
                package = validate_package(ready, expected_manifest_sha256=expected_manifest_sha256)
                _publish_directory(ready, destination, os.rename, guard=_safe_path)
    return {"package": package, "directory": str(destination)}
