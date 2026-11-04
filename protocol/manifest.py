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
import time
import zipfile


class ProtocolError(ValueError):
    pass


_ID = re.compile(r"[a-z][a-z0-9_-]*:[a-z0-9][a-z0-9_.-]*\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.+-]+)?\Z")
MAX_MANIFEST = 8 * 1024 * 1024
ENGINE_VERSION = "0.1.5"


def _fail(message):
    raise ProtocolError(message)


def _filesystem_path(path):
    """Use Win32 extended paths for I/O without changing logical package names."""
    path = Path(path)
    if os.name != 'nt':
        return path
    value = os.path.abspath(path)
    if value.startswith('\\\\?\\'):
        return Path(value)
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)


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


def _bounded_bytes(path, maximum, name="Manifest"):
    """Enforce the limit on the opened input, before allocating its contents."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(_filesystem_path(path), flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            _fail(name + " must be a regular file")
        if info.st_size > maximum:
            _fail(name + " exceeds size limit")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        _fail(name + " exceeds size limit")
    return raw


def load_json(path):
    try:
        return _decode_json(_bounded_bytes(path, MAX_MANIFEST))
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
    if manifest.get('available', True) is not True:
        return False
    minimum = manifest.get("minimum_engine_version")
    if minimum and tuple(map(int, minimum.split('.'))) > tuple(map(int, ENGINE_VERSION.split('.'))):
        return False
    for key, actual in (("architectures", "architecture"), ("os_ids", "os_id"),
                        ("os_versions", "os_version")):
        allowed = manifest.get("compatibility", {}).get(key)
        if allowed and environment.get(actual) not in allowed:
            return False
    kernels = manifest.get("compatibility", {}).get("kernels")
    if kernels and (not environment.get("kernels") or any(kernel not in kernels for kernel in environment["kernels"])):
        return False
    return True


def device_advisory(manifest, environment):
    """Report a missing or different model identity without blocking execution."""
    allowed = manifest.get("compatibility", {}).get("device_ids")
    actual = environment.get("device_id")
    if allowed and actual not in allowed:
        return {"id": manifest.get("id"), "expected_device_ids": allowed,
                "actual_device_id": actual, "message": "Device model may be incompatible."}
    return None


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


def _hardware_advice(value):
    if (not isinstance(value, dict) or set(value) != {"mode", "pairs"}
            or value["mode"] != "advisory-exact-dmi-v1" or not isinstance(value["pairs"], list)
            or not 1 <= len(value["pairs"]) <= 32): _fail("Invalid advisory hardware matching")
    for pair in value["pairs"]:
        if not isinstance(pair, dict) or set(pair) != {"manufacturer", "model"}: _fail("Invalid advisory DMI pair")
        for field in pair.values():
            if (not isinstance(field, str) or not 1 <= len(field) <= 256 or not field.strip()
                    or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in field)):
                _fail("Invalid advisory DMI value")


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
        if 'ssd_boot_policy' in obj:
            policy = obj['ssd_boot_policy']
            if (policy != {'id': 'ssd:gm7-boot-repair', 'version': 1}
                    or type(policy.get('version')) is not int or obj.get('files') or obj.get('hooks')
                    or obj.get('requires') or obj.get('conflicts')
                    or 'dynamic_handler' in obj or obj.get('retain_owned_paths')
                    or obj.get('compatibility', {}) != {}
                    or obj.get('optional') is not True
                    or 'minimum_engine_version' not in obj
                    or tuple(map(int, obj['minimum_engine_version'].split('.'))) < (0, 1, 3)):
                _fail('Invalid fixed SSD boot provider contract')
        if 'cpu_mutable_config' in obj:
            from .cpu_contract import mutable_target
            try: mutable_target(obj)
            except ValueError as error: _fail(str(error))
        if "retain_owned_paths" in obj and type(obj["retain_owned_paths"]) is not bool:
            _fail("Retained ownership must be an explicit boolean")
        if obj.get("retain_owned_paths") and (obj.get("hooks") or not obj.get("files")):
            _fail("Retained ownership requires nonempty static file components")
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
        if 'oxp3_efi_contract' in obj:
            if (obj['oxp3_efi_contract'] != {'kind': 'oxp3-split-lock-efi-v1', 'component': 'oxp3:legacy-adaptation'}
                    or obj.get('id') != 'device:oxp3'
                    or obj.get('selection_contract') != {'kind': 'oxp3-legacy-options-v1', 'component': 'oxp3:legacy-adaptation'}
                    or 'minimum_engine_version' not in obj
                    or tuple(map(int, obj['minimum_engine_version'].split('.'))) < (0, 1, 3)):
                _fail('Invalid fixed OXP3 EFI bridge contract')
        if 'x2_efi_contract' in obj:
            if (obj['x2_efi_contract'] != {'kind': 'x2-split-lock-efi-v1', 'component': 'oxp3:legacy-adaptation'}
                    or obj.get('id') != 'device:x2-mini' or 'oxp3_efi_contract' in obj
                    or obj.get('selection_contract') != {'kind': 'oxp3-legacy-options-v1', 'component': 'oxp3:legacy-adaptation'}
                    or 'minimum_engine_version' not in obj
                    or tuple(map(int, obj['minimum_engine_version'].split('.'))) < (0, 1, 4)):
                _fail('Invalid fixed X2 EFI bridge contract')
        if 'oxp3_full_efi_contract' in obj:
            if (obj['oxp3_full_efi_contract'] != {'kind': 'oxp3-full-split-lock-efi-v1', 'component': 'oxp3:legacy-adaptation'}
                    or obj.get('id') != 'device:oxp3' or 'oxp3_efi_contract' in obj or 'x2_efi_contract' in obj
                    or obj.get('selection_contract') != {'kind': 'oxp3-legacy-options-v1', 'component': 'oxp3:legacy-adaptation'}
                    or 'minimum_engine_version' not in obj
                    or tuple(map(int, obj['minimum_engine_version'].split('.'))) < (0, 1, 4)):
                _fail('Invalid full OXP3 EFI bridge contract')
        if 'resources' in obj:
            resources = obj['resources']
            if not isinstance(resources, list): _fail('Invalid model resource references')
            ids, paths = set(), set()
            for row in resources:
                if not isinstance(row, dict) or (set(row) - {'id', 'path', 'default'} or not {'id', 'path'}.issubset(row) or ('default' in row and type(row['default']) is not bool)) or not isinstance(row['id'], str) or not _ID.fullmatch(row['id']) or not row['id'].startswith('resource:'):
                    _fail('Invalid model resource reference')
                relative_path(row['path'])
                if row['id'] in ids or row['path'] in paths: _fail('Duplicate model resource reference')
                ids.add(row['id']); paths.add(row['path'])
            if 'resource:intel-be201-firmware' in ids and any(c.get('id') == 'oxp3:wifi' and (c.get('available') or c.get('default')) for c in obj.get('legacy_capabilities', [])):
                _fail('Model resource conflicts with legacy Wi-Fi handler')
        if 'be201_firmware' in obj:
            if obj['be201_firmware'] != {'kind': 'intel-be201-firmware-v1',
                    'resource': 'common/network/intel/be201/resource.toon'}:
                _fail('Invalid fixed BE201 firmware descriptor')
            if any(c.get('id') == 'oxp3:wifi' and (c.get('available') or c.get('default'))
                   for c in obj.get('legacy_capabilities', [])):
                _fail('BE201 descriptor conflicts with legacy Wi-Fi handler')
        if "maintenance_entry" in obj:
            entry = obj["maintenance_entry"]
            if isinstance(entry, dict):
                if entry != {"kind": "engine-hooks-v1"}: _fail("Unsupported model maintenance contract")
            elif isinstance(entry, str):
                relative_path(entry)
                if not entry.startswith("scripts/") or not entry.endswith(".sh"): _fail("Invalid legacy maintenance entry")
            else: _fail("Invalid model maintenance contract")
        if "components" not in obj or "files" not in obj: _fail("Package inventory and components required")
        components = _list(obj, "components")
        ids = set()
        for component in components:
            validate_manifest(component, "ComponentManifest")
            if component["id"] in ids: _fail("Duplicate component")
            ids.add(component["id"])
        if ids.intersection(row['id'] for row in obj.get('resources', [])):
            _fail('Model resource ID conflicts with a component')
        if sum('ssd_boot_policy' in c for c in components) > 1:
            _fail('Only one SSD boot provider is supported per package')
        providers = {c['id'] for c in components if 'ssd_boot_policy' in c}
        if any(providers.intersection(c.get('requires', [])) for c in components):
            _fail('SSD boot provider cannot be an implicit model dependency')
        if any('ssd_boot_policy' in c for c in components) and (
                'minimum_engine_version' not in obj or tuple(map(int, obj['minimum_engine_version'].split('.'))) < (0, 1, 3)):
            _fail('SSD boot provider package requires engine 0.1.3 or newer')
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
            if "hardware_matching" in entry: _hardware_advice(entry["hardware_matching"])
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
    path=_filesystem_path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected["size"]:
        _fail("File missing or size mismatch")
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""): digest.update(block)
    if digest.hexdigest() != expected["sha256"]: _fail("File hash mismatch")
    return True


def validate_package(directory, *, allow_unsigned=False, expected_manifest_sha256=None):
    root=_filesystem_path(directory).resolve()
    manifest_path=confined(root,"manifest.json")
    if expected_manifest_sha256 is not None:
        if not _HASH.fullmatch(expected_manifest_sha256): _fail("Invalid anchored manifest digest")
    elif not allow_unsigned:
        _fail("Package requires a signed-index digest or explicit session developer trust")
    # Read and authenticate the same bytes that are parsed below.
    raw=_bounded_bytes(manifest_path,MAX_MANIFEST)
    if expected_manifest_sha256 is not None and hashlib.sha256(raw).hexdigest()!=expected_manifest_sha256:
        _fail("Anchored manifest digest changed")
    obj=validate_manifest(_decode_json(raw),"DevicePackage")
    inventory={e["path"]:e for e in obj.get("files",[])}
    if isinstance(obj.get("maintenance_entry"), str) and obj["maintenance_entry"] not in inventory:
        _fail("Legacy maintenance entry absent from package inventory")
    actual=set()
    for path in root.rglob("*"):
        if path.is_symlink() or (hasattr(path,"is_junction") and path.is_junction()): _fail("Package contains a link")
        if path.is_file(): actual.add(path.relative_to(root).as_posix())
    if actual != {"manifest.json",*inventory}: _fail("Package inventory does not match directory")
    for name, entry in inventory.items(): verify_file(confined(root,name),entry)
    for reference in obj.get('resources', []):
        if reference['path'] not in inventory: _fail('Model resource record absent from inventory')
    resource_references = [r for r in obj.get('resources', []) if r['id'] == 'resource:intel-be201-firmware']
    if resource_references and any(row['path'] != 'common/network/intel/be201/resource.toon' for row in resource_references):
        _fail('Invalid fixed firmware resource reference')
    if 'be201_firmware' in obj or resource_references:
        from protocol.resource_pool import decoder
        from protocol.firmware_contract import validate_contract
        resource_path = obj['be201_firmware']['resource'] if 'be201_firmware' in obj else resource_references[0]['path']
        if resource_path not in inventory:
            _fail('BE201 resource record absent from inventory')
        try:
            resource = decoder().loads(_bounded_bytes(confined(root, resource_path), MAX_MANIFEST).decode('utf-8'), strict=True)
            validate_contract(resource)
            if resource.get('kind') != 'BoilerResource' or resource.get('schema_version') != 1:
                _fail('Invalid BE201 resource record')
            payload = resource.get('payload', {}).get('files', [])
            paths = set()
            for row in payload:
                name = row['path']
                if name in paths or name not in inventory or not name.startswith('common/network/intel/be201/files/'):
                    _fail('Invalid BE201 payload inventory')
                if any(row[k] != inventory[name][k] for k in ('size', 'sha256')):
                    _fail('BE201 payload differs from package inventory')
                paths.add(name)
            if not paths or {row['source'] for row in resource['install']['files']} != paths:
                _fail('BE201 install sources differ from inventory')
            licenses = resource.get('license_files', [])
            if len(licenses) != 2 or len(set(licenses)) != 2 or any(
                    name not in inventory or not name.startswith('common/network/intel/be201/sources/') for name in licenses):
                _fail('BE201 licenses absent from package inventory')
        except (KeyError, TypeError, UnicodeError, ValueError) as exc:
            _fail('Invalid BE201 resource: ' + str(exc))
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
    warnings=[warning for item in ordered if (warning := device_advisory(item, environment))]
    return {"schema_version":1,"kind":"InstallPlan","mode":mode,"components":ordered,"environment":environment,
            "device_warnings":warnings}


def _windows_publication():
    return os.name == 'nt'


def _publish_directory(staging, destination, operation, *, guard=None):
    """Retry transient Windows access denial without replacing a destination."""
    first_error = None
    for attempt in range(6):
        if guard is not None:
            guard(destination)
        if os.path.lexists(destination):
            _fail('Publication destination already exists')
        try:
            operation(staging, destination)
            return
        except OSError as error:
            if not _windows_publication() or getattr(error, 'winerror', None) != 5:
                raise
            if first_error is None:
                first_error = error
            if attempt == 5:
                raise first_error
            time.sleep(0.1)


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
        _publish_directory(staging, destination, os.replace)
    except BaseException:
        shutil.rmtree(staging,ignore_errors=True)
        raise
    return destination


def verify_signed_index(index_path, signature_path, public_key_path=None, *, openssl="openssl"):
    """Verify detached Ed25519 signature using the established project public key."""
    if public_key_path is None: public_key_path=Path(__file__).with_name("trusted-public-key.pem")
    raw=_bounded_bytes(index_path,MAX_MANIFEST,"Index")
    signature=_bounded_bytes(signature_path,64,"Index signature")
    if len(signature)!=64: _fail("Index signature rejected")
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
        digest=hashlib.sha256(_bounded_bytes(Path(directory)/"manifest.json",MAX_MANIFEST)).hexdigest()
        self._grants.add((str(Path(directory).resolve()),digest))
        return obj
    def validate_local(self,directory):
        digest=hashlib.sha256(_bounded_bytes(Path(directory)/"manifest.json",MAX_MANIFEST)).hexdigest()
        if (str(Path(directory).resolve()),digest) not in self._grants: _fail("Developer package has no session trust grant")
        return validate_package(directory,expected_manifest_sha256=digest)
    def github_source(self,repository,commit):
        source={"kind":"github","repository":repository,"commit":commit}; _source(source)
        self._grants.add((repository,commit))
        return source
