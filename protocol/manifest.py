"""Strict security boundary shared by the installer and generic maintenance.

Signatures cover the exact UTF-8 index bytes, not a reserialized JSON object.
Package manifests must be anchored by an index digest or an explicit developer
trust grant. A digest in an untrusted package is integrity, not authenticity.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile


class ProtocolError(ValueError):
    pass


_ID = re.compile(r"[a-z][a-z0-9_-]*:[a-z0-9][a-z0-9_.-]*\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.+-]+)?\Z")
MAX_MANIFEST = 8 * 1024 * 1024
ENGINE_VERSION = "0.1.0"


def _fail(message):
    raise ProtocolError(message)


def relative_path(value):
    if (not isinstance(value, str) or not value or len(value) > 4096 or
            "\\" in value or ":" in value or "\x00" in value or
            any(ord(c) < 32 for c in value) or value.startswith("/") or
            any(p in ("", ".", "..") or p.endswith((".", " ")) for p in value.split("/"))):
        _fail("Unsafe relative path")
    if any(p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1,10)], *[f"LPT{i}" for i in range(1,10)]} for p in value.split("/")):
        _fail("Reserved path")
    return value


def confined(root, relative):
    relative_path(relative)
    root = Path(root).resolve()
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            _fail("Symbolic links and junctions are not package paths")
    try:
        current.resolve().relative_to(root)
    except ValueError:
        _fail("Path escapes root")
    return current


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            _fail("Duplicate JSON key")
        out[key] = value
    return out


def load_json(path):
    path = Path(path)
    if path.stat().st_size > MAX_MANIFEST:
        _fail("Manifest exceeds size limit")
    try:
        return _decode_json(path.read_bytes())
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProtocolError("Invalid manifest JSON") from exc


def _decode_json(raw):
    try:
        return json.loads(raw.decode("utf-8"),object_pairs_hook=_pairs,
                          parse_constant=lambda x: _fail("Invalid JSON constant"))
    except (UnicodeError,json.JSONDecodeError,RecursionError) as exc:
        raise ProtocolError("Invalid manifest JSON") from exc


def _text(value, name):
    if not isinstance(value, str) or not value or len(value) > 4096:
        _fail("Invalid " + name)


def _identity(obj):
    if not isinstance(obj.get("id"), str) or not _ID.fullmatch(obj["id"]):
        _fail("Invalid namespaced id")
    if not isinstance(obj.get("version"), str) or not _VERSION.fullmatch(obj["version"]):
        _fail("Invalid version")


def _digest(obj):
    if not isinstance(obj.get("sha256"), str) or not _HASH.fullmatch(obj["sha256"]):
        _fail("Invalid SHA-256")
    if type(obj.get("size")) is not int or not 0 <= obj["size"] <= 128 * 1024**3:
        _fail("Invalid file size")


def _list(obj, key):
    value = obj.get(key, [])
    if not isinstance(value, list) or len(value) > 100000:
        _fail("Invalid list: " + key)
    return value


def _compat(value):
    if not isinstance(value, dict):
        _fail("Invalid compatibility")
    for key, entries in value.items():
        if key not in ("architectures", "os_ids", "device_ids", "os_versions", "kernels"):
            _fail("Unknown compatibility field")
        if not isinstance(entries, list) or not entries or any(not isinstance(s,str) or not s for s in entries):
            _fail("Invalid compatibility list")


def compatible(manifest, environment):
    minimum = manifest.get("minimum_engine_version")
    if minimum and tuple(map(int, minimum.split('.'))) > tuple(map(int, ENGINE_VERSION.split('.'))):
        return False
    for key, actual in (("architectures", "architecture"), ("os_ids", "os_id"),
                        ("device_ids", "device_id"), ("os_versions", "os_version")):
        allowed = manifest.get("compatibility", {}).get(key)
        if allowed and environment.get(actual) not in allowed:
            return False
    kernels = manifest.get("compatibility", {}).get("kernels")
    if kernels and (not environment.get("kernels") or any(kernel not in kernels for kernel in environment["kernels"])):
        return False
    return True


def _source(source):
    if not isinstance(source, dict) or source.get("kind") not in ("github", "local", "https"):
        _fail("Invalid immutable source")
    if source["kind"] == "github":
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source.get("repository", "")) or not _COMMIT.fullmatch(source.get("commit", "")):
            _fail("GitHub source must use a full pinned commit")
    elif source["kind"] == "https":
        if not source.get("url", "").startswith("https://"):
            _fail("HTTPS source required")
        _digest(source)
    else:
        _text(source.get("path", source.get("repository")), "local source")
        if source.get("base_commit") is not None and not _COMMIT.fullmatch(source["base_commit"]):
            _fail("Invalid local base commit")


def validate_manifest(obj, kind=None):
    if not isinstance(obj, dict) or type(obj.get("schema_version")) is not int or obj["schema_version"] != 1:
        _fail("Unsupported schema version")
    actual = obj.get("kind")
    if kind is not None and actual != kind:
        _fail("Unexpected manifest kind")
    if actual not in ("DeviceIndex", "DevicePackage", "ComponentManifest", "ImageManifest", "InstallPlan"):
        _fail("Unknown manifest kind")
    if actual != "InstallPlan":
        _identity(obj)
    _compat(obj.get("compatibility", {}))
    if "minimum_engine_version" in obj and not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", str(obj["minimum_engine_version"])):
        _fail("Minimum engine version must be a stable three-part version")
    if actual == "ComponentManifest":
        for field in ("requires", "conflicts"):
            ids = _list(obj, field)
            if any(not isinstance(i,str) or not _ID.fullmatch(i) for i in ids) or len(set(ids)) != len(ids):
                _fail("Invalid component dependency")
        seen = set()
        for entry in _list(obj, "files"):
            if not isinstance(entry, dict): _fail("Invalid file")
            relative_path(entry.get("source")); relative_path(entry.get("target")); _digest(entry)
            if entry["target"].casefold() in seen: _fail("Duplicate target")
            seen.add(entry["target"].casefold())
            if type(entry.get("mode", 420)) is not int or not 0 <= entry.get("mode",420) <= 0o777:
                _fail("Invalid file mode")
        hooks = obj.get("hooks", {})
        if not isinstance(hooks, dict): _fail("Invalid hooks")
        for phase, path in hooks.items():
            if phase not in ("apply", "verify", "restore"): _fail("Unknown hook")
            relative_path(path)
            if not path.startswith("scripts/") or not path.endswith(".sh"): _fail("Hooks must be guest Bash scripts")
    elif actual == "DevicePackage":
        _source(obj.get("source"))
        if "components" not in obj or "files" not in obj: _fail("Package inventory and components required")
        components = _list(obj, "components")
        ids = set()
        for component in components:
            validate_manifest(component, "ComponentManifest")
            if component["id"] in ids: _fail("Duplicate component")
            ids.add(component["id"])
        seen = set()
        for entry in _list(obj, "files"):
            if not isinstance(entry,dict): _fail("Invalid inventory file")
            relative_path(entry.get("path")); _digest(entry)
            if entry["path"].casefold() in seen: _fail("Duplicate inventory path")
            seen.add(entry["path"].casefold())
    elif actual == "DeviceIndex":
        _text(obj.get("generated_at"), "index timestamp")
        if type(obj.get("sequence")) is not int or obj["sequence"]<0: _fail("Signed index sequence required")
        if any(not isinstance(i,str) or not _HASH.fullmatch(i) for i in _list(obj,"revocations")): _fail("Invalid revocation digest")
        if "packages" not in obj: _fail("Index package list required")
        seen = set()
        for entry in _list(obj, "packages"):
            if not isinstance(entry,dict): _fail("Invalid index entry")
            _identity(entry); _digest(entry); _source(entry.get("source")); _compat(entry.get("compatibility",{}))
            key=(entry["id"],entry["version"])
            if key in seen: _fail("Duplicate index package")
            seen.add(key)
    elif actual == "ImageManifest":
        if obj.get("layout") not in ("steamos-ab","steamos-recovery"): _fail("Unsupported SteamOS image layout")
        if not isinstance(obj.get("image"),dict): _fail("Image descriptor required")
        _digest(obj["image"]); relative_path(obj["image"].get("path"))
        if obj.get("install_plan") is not None: validate_manifest(obj["install_plan"],"InstallPlan")
        if not isinstance(obj.get("architecture"),str): _fail("Image architecture required")
    elif actual == "InstallPlan":
        if obj.get("mode") not in ("normal", "vanilla", "imported", "developer"):
            _fail("Invalid install mode")
        for component in _list(obj,"components"): validate_manifest(component,"ComponentManifest")
    return obj


def verify_file(path, expected):
    _digest(expected)
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected["size"]:
        _fail("File missing or size mismatch")
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""): digest.update(block)
    if digest.hexdigest() != expected["sha256"]: _fail("File hash mismatch")
    return True


def validate_package(directory, *, allow_unsigned=False, expected_manifest_sha256=None):
    root=Path(directory).resolve()
    manifest_path=confined(root,"manifest.json")
    if expected_manifest_sha256 is not None:
        if not _HASH.fullmatch(expected_manifest_sha256): _fail("Invalid anchored manifest digest")
        verify_file(manifest_path,{"sha256":expected_manifest_sha256,"size":manifest_path.stat().st_size})
    elif not allow_unsigned:
        _fail("Package requires a signed-index digest or explicit session developer trust")
    # Read and authenticate the same bytes that are parsed below.
    raw=manifest_path.read_bytes()
    if len(raw)>MAX_MANIFEST: _fail("Manifest exceeds size limit")
    if expected_manifest_sha256 is not None and hashlib.sha256(raw).hexdigest()!=expected_manifest_sha256:
        _fail("Anchored manifest digest changed")
    obj=validate_manifest(_decode_json(raw),"DevicePackage")
    inventory={e["path"]:e for e in obj.get("files",[])}
    actual=set()
    for path in root.rglob("*"):
        if path.is_symlink() or (hasattr(path,"is_junction") and path.is_junction()): _fail("Package contains a link")
        if path.is_file(): actual.add(path.relative_to(root).as_posix())
    if actual != {"manifest.json",*inventory}: _fail("Package inventory does not match directory")
    for name, entry in inventory.items(): verify_file(confined(root,name),entry)
    for component in obj["components"]:
        for entry in component.get("files",[]):
            if entry["source"] not in inventory: _fail("Component source absent from package inventory")
            inv=inventory[entry["source"]]
            if any(inv[k]!=entry[k] for k in ("sha256","size")): _fail("Component source digest differs from inventory")
        if any(p not in inventory for p in component.get("hooks",{}).values()): _fail("Hook absent from inventory")
    return obj


def resolve_plan(components, selected, environment, *, mode="normal"):
    if mode not in ("normal","vanilla","imported","developer"): _fail("Invalid install mode")
    if mode == "imported":
        return {"schema_version":1,"kind":"InstallPlan","mode":mode,"components":[],"environment":environment}
    by_id={}
    for component in components:
        validate_manifest(component,"ComponentManifest")
        if component["id"] in by_id: _fail("Duplicate component id")
        by_id[component["id"]]=component
    ordered=[]; visiting=set(); done=set()
    def visit(identity):
        if identity in done: return
        if identity in visiting: _fail("Dependency cycle")
        if identity not in by_id: _fail("Missing dependency: "+identity)
        item=by_id[identity]
        if not compatible(item,environment): _fail("Incompatible component: "+identity)
        visiting.add(identity)
        for dependency in item.get("requires",[]): visit(dependency)
        visiting.remove(identity); done.add(identity); ordered.append(item)
    for identity in selected: visit(identity)
    owners={}
    for item in ordered:
        if any(conflict in done for conflict in item.get("conflicts",[])): _fail("Conflicting components")
        for entry in item.get("files",[]):
            target=entry["target"].casefold()
            if any(target==p or target.startswith(p+"/") or p.startswith(target+"/") for p in owners):
                _fail("Component target collision")
            owners[target]=item["id"]
    return {"schema_version":1,"kind":"InstallPlan","mode":mode,"components":ordered,"environment":environment}


def safe_extract_archive(archive, destination, *, max_bytes=8*1024**3, max_files=100000):
    """Reject links, traversal, aliases and bombs; publish only a complete tree."""
    destination=Path(destination)
    if destination.exists(): _fail("Extraction destination already exists")
    destination.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix=".boiler-extract-",dir=destination.parent))
    seen=set(); total=0
    try:
        is_zip=zipfile.is_zipfile(archive)
        opener=zipfile.ZipFile(archive) if is_zip else tarfile.open(archive,"r:*")
        with opener as bundle:
            members=bundle.infolist() if is_zip else bundle
            for count,member in enumerate(members,1):
                if count>max_files: _fail("Archive file count exceeds limit")
                name=(member.filename if is_zip else member.name).rstrip("/")
                relative_path(name)
                key=name.casefold()
                if key in seen: _fail("Duplicate archive path")
                seen.add(key)
                mode=member.external_attr>>16 if is_zip else member.mode
                directory=member.is_dir() if is_zip else member.isdir()
                if (is_zip and stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR)) or (not is_zip and not (member.isfile() or directory)):
                    _fail("Archive links or special files are forbidden")
                size=member.file_size if is_zip else member.size
                total+=size
                if total>max_bytes or size<0: _fail("Archive size exceeds limit")
                output=confined(staging,name)
                if directory: output.mkdir(parents=True,exist_ok=True); continue
                output.parent.mkdir(parents=True,exist_ok=True)
                stream=bundle.open(member) if is_zip else bundle.extractfile(member)
                with stream, output.open("xb") as target:
                    remaining=size
                    while remaining:
                        block=stream.read(min(1024*1024,remaining))
                        if not block: _fail("Truncated archive")
                        target.write(block); remaining-=len(block)
                    if stream.read(1): _fail("Archive entry exceeds declared size")
        os.replace(staging,destination)
    except BaseException:
        shutil.rmtree(staging,ignore_errors=True)
        raise
    return destination


def verify_signed_index(index_path, signature_path, public_key_path=None, *, openssl="openssl"):
    """Verify detached Ed25519 signature using the established project public key."""
    if public_key_path is None: public_key_path=Path(__file__).with_name("trusted-public-key.pem")
    raw=Path(index_path).read_bytes()
    signature=Path(signature_path).read_bytes()
    if len(raw)>MAX_MANIFEST: _fail("Index too large")
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key=load_pem_public_key(Path(public_key_path).read_bytes())
        if not isinstance(key,Ed25519PublicKey): _fail("Ed25519 public key required")
        key.verify(signature,raw)
    except ImportError:
        try:
            with tempfile.TemporaryDirectory(prefix="boiler-index-") as temp:
                snapshot=Path(temp)/"index.json"; detached=Path(temp)/"index.sig"
                snapshot.write_bytes(raw); detached.write_bytes(signature)
                result=subprocess.run([openssl,"pkeyutl","-verify","-pubin","-inkey",str(public_key_path),"-rawin","-in",str(snapshot),"-sigfile",str(detached)],capture_output=True,timeout=30)
        except (OSError,subprocess.TimeoutExpired) as exc: raise ProtocolError("Ed25519 verifier unavailable") from exc
        if result.returncode: _fail("Index signature rejected")
    except ProtocolError: raise
    except Exception as exc: raise ProtocolError("Index signature rejected") from exc
    return validate_manifest(_decode_json(raw),"DeviceIndex")


class DeveloperTrustSession:
    """In-memory trust only; never written into installed state or preferences."""
    def __init__(self): self._grants=set()
    def grant_local(self,directory):
        obj=validate_package(directory,allow_unsigned=True)
        digest=hashlib.sha256((Path(directory)/"manifest.json").read_bytes()).hexdigest()
        self._grants.add((str(Path(directory).resolve()),digest))
        return obj
    def validate_local(self,directory):
        digest=hashlib.sha256((Path(directory)/"manifest.json").read_bytes()).hexdigest()
        if (str(Path(directory).resolve()),digest) not in self._grants: _fail("Developer package has no session trust grant")
        return validate_package(directory,expected_manifest_sha256=digest)
    def github_source(self,repository,commit):
        source={"kind":"github","repository":repository,"commit":commit}; _source(source)
        self._grants.add((repository,commit))
        return source
