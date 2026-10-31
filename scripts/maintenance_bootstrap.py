#!/usr/bin/env python3
"""First install of the generic Boiler maintenance runtime.

This bootstrap accepts only the exact version in maintenance/release.json from
an explicitly selected, freshly fetched, signed generic maintenance index. It
does not consume the model/device index, fall back to a cache, or execute package
hooks. The downloaded package is fully verified before Engine.apply is called.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlparse


from maintenance import Engine
from maintenance.engine import _read
from maintenance.updates import target_environment
from protocol.fetch import fetch_index, fetch_package
from protocol.manifest import (ProtocolError, compatible, confined, load_json,
                               validate_package)


PACKAGE_ID = "bundle:maintenance"
COMPONENT_ID = "core:maintenance"
REGISTRY_ID = "registry:boiler-maintenance"
REPOSITORY = "FlightDan/Boiler-installer"
LIBRARY_PREFIX = "usr/local/lib/boiler/"
EXTRA_TARGETS = {
    "usr/local/bin/boiler-maintenance",
    "usr/local/bin/boiler-maintenance-ui",
    "usr/share/applications/Boiler-Maintenance.desktop",
}


class BootstrapError(RuntimeError):
    pass


def _is_empty_directory_tree(path: Path) -> bool:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return False
    if not path.is_dir():
        return False
    for child in path.iterdir():
        if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
            return False
        if child.is_dir():
            if not _is_empty_directory_tree(child):
                return False
        else:
            return False
    return True


def _index_urls(index_url: str, signature_url: str) -> None:
    for value in (index_url, signature_url):
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise BootstrapError("Signed maintenance index and signature must use credential-free HTTPS URLs")


def _release_version(release_path: str | Path | None = None) -> tuple[str, dict]:
    if release_path is None:
        raw = resources.files("maintenance").joinpath("release.json").read_bytes()
        release = json.loads(raw.decode("utf-8"))
    else:
        release = load_json(release_path)
    version = release.get("version") if isinstance(release, dict) else None
    if (release.get("schema_version") != 1 or release.get("product") != "Boiler Maintenance"
            or not isinstance(version, str) or not version):
        raise BootstrapError("Local Boiler maintenance release identity is invalid")
    return version, release


@contextmanager
def _trusted_key(public_key: str | Path | None):
    if public_key is not None:
        yield Path(public_key)
        return
    raw = resources.files("protocol").joinpath("trusted-public-key.pem").read_bytes()
    with tempfile.TemporaryDirectory(prefix="boiler-bootstrap-key-") as temporary:
        path = Path(temporary) / "trusted-public-key.pem"
        path.write_bytes(raw)
        if os.name != "nt":
            path.chmod(0o600)
        yield path


def _safe_first_install(root: Path, component: dict | None = None) -> None:
    """Refuse adoption/overlay before Engine construction can create state."""
    root = root.resolve()
    state = root / "var/lib/boiler/maintenance"
    if os.path.lexists(state):
        if state.is_symlink() or not state.is_dir():
            raise BootstrapError("Existing Boiler maintenance state path is unsafe; inspect it manually")
        engine = Engine(root, read_only=True)
        current = engine._state()
        if (set(current) != {"schema_version", "root", "owners", "components"}
                or current["schema_version"] != 1 or current["components"] or current["owners"]):
            raise BootstrapError("Existing Boiler maintenance installation found; use its update or recovery flow")
        for child in state.iterdir():
            if (child.name not in {"state.json", "lock", "transactions"} or child.is_symlink()
                    or (hasattr(child, "is_junction") and child.is_junction())):
                raise BootstrapError("Unknown Boiler maintenance state found; recover or inspect it before installation")
            if child.name == "lock":
                info = child.stat()
                if not child.is_file() or info.st_nlink != 1 or info.st_size > 1 or child.read_bytes() not in (b"", b"0"):
                    raise BootstrapError("Unknown Boiler maintenance lock state found; inspect it before installation")
            if child.name == "transactions":
                if not child.is_dir():
                    raise BootstrapError("Unsafe Boiler maintenance transaction state")
                for journal_path in child.iterdir():
                    if (journal_path.is_symlink() or not journal_path.is_file()
                            or not re.fullmatch(r"[0-9a-f]{32}\.json", journal_path.name)):
                        raise BootstrapError("Unknown Boiler maintenance transaction found; inspect it before installation")
                    journal = _read(journal_path)
                    if (journal.get("id") != journal_path.stem or journal.get("root") != engine.identity
                            or journal.get("status") not in ("restored", "rolled_back")):
                        raise BootstrapError("Unresolved Boiler maintenance transaction; run --recover first")

    # The generic runtime owns this dedicated directory. Retain empty parents
    # left by a verified rollback; never delete or overlay unknown contents.
    runtime = root / "usr/local/lib/boiler"
    if os.path.lexists(runtime) and not _is_empty_directory_tree(runtime):
        raise BootstrapError("Existing Boiler maintenance runtime path has content; refusing first-install overlay")

    for target in EXTRA_TARGETS:
        if os.path.lexists(confined(root, target)):
            raise BootstrapError("Existing Boiler maintenance launcher path found; refusing first-install overlay")
    if component is None:
        return

    targets = [item["target"] for item in component.get("files", [])]
    if not targets:
        raise BootstrapError("Signed maintenance component contains no files")
    for target in targets:
        if not (target.startswith(LIBRARY_PREFIX) or target in EXTRA_TARGETS):
            raise BootstrapError("Signed maintenance component declares an unexpected target: " + target)
        path = confined(root, target)
        if os.path.lexists(path):
            raise BootstrapError("First-install target already exists: " + target)


def install(root: str | Path, index_url: str, signature_url: str, *,
            release_path: str | Path | None = None,
            public_key: str | Path | None = None) -> dict:
    """Install the checkout's exact maintenance release into a clean target.

    ``public_key`` is an internal test seam; the command line always uses the
    pinned protocol/trusted-public-key.pem shipped with this source checkout.
    """
    _index_urls(index_url, signature_url)
    root = Path(root).resolve()
    if not root.is_dir():
        raise BootstrapError("Target root is not mounted")
    with _trusted_key(public_key) as key_path:
        return _install_with_key(root, index_url, signature_url, Path(release_path) if release_path else None,
                                 key_path)


def _install_with_key(root: Path, index_url: str, signature_url: str,
                      release_path: Path | None, public_key: Path) -> dict:
    version, release = _release_version(release_path)
    _safe_first_install(root)
    environment = target_environment(root)

    # A fresh empty cache makes first-install require a newly verified snapshot;
    # no stale index can hide a missing package or current revocation.
    with tempfile.TemporaryDirectory(prefix="boiler-maintenance-index-") as cache_name, \
            tempfile.TemporaryDirectory(prefix="boiler-maintenance-package-") as package_parent:
        cache = Path(cache_name)
        snapshot = fetch_index(index_url, signature_url, cache, public_key=public_key)
        index = snapshot["index"]
        if index.get("id") != REGISTRY_ID:
            raise BootstrapError("Signed index is not the generic Boiler maintenance registry")

        entry = next((item for item in index.get("packages", [])
                      if item.get("id") == PACKAGE_ID and item.get("version") == version), None)
        if entry is None:
            raise BootstrapError(
                f"Maintenance bundle version {version} is not published in this signed maintenance index"
            )
        if entry["sha256"] in index.get("revocations", []):
            raise BootstrapError("The signed maintenance bundle has been revoked")
        source = entry.get("source", {})
        parsed_source = urlparse(source.get("url", ""))
        if (source.get("kind") != "https" or parsed_source.scheme != "https"
                or parsed_source.hostname != "github.com" or parsed_source.username or parsed_source.password
                or parsed_source.query or parsed_source.fragment
                or not parsed_source.path.startswith("/FlightDan/Boiler-installer/releases/download/")):
            raise BootstrapError("Signed maintenance bundle source is not the official Boiler repository")

        package_dir = Path(package_parent) / "verified-package"
        acquired = fetch_package(cache, PACKAGE_ID, version, package_dir, public_key=public_key)
        package = validate_package(package_dir, expected_manifest_sha256=acquired["manifest_sha256"])
        if (package.get("id") != PACKAGE_ID or package.get("version") != version
                or package.get("purpose") != "maintenance"):
            raise BootstrapError("Verified package is not the requested Boiler maintenance release")
        if package.get("minimum_engine_version") != release.get("minimum_engine_version"):
            raise BootstrapError("Signed package engine requirement differs from release metadata")
        if package.get("source", {}).get("kind") != "github" or package["source"].get("repository") != REPOSITORY:
            raise BootstrapError("Verified package source is not the official Boiler repository")
        components = package.get("components", [])
        if len(components) != 1 or components[0].get("id") != COMPONENT_ID:
            raise BootstrapError("Maintenance bundle must contain exactly core:maintenance")
        component = components[0]
        if component.get("version") != version or component.get("hooks"):
            raise BootstrapError("Maintenance first-install package must be a matching static file component")

        # Keep the release metadata bound to the signed package inventory, too.
        packaged_release_path = confined(package_dir, "release.json")
        packaged_release = load_json(packaged_release_path)
        if (packaged_release.get("product") != "Boiler Maintenance"
                or packaged_release.get("version") != version
                or packaged_release.get("compatibility") != release.get("compatibility")):
            raise BootstrapError("Signed package release metadata differs from this source release")
        if component.get("compatibility", {}) != release.get("compatibility", {}):
            raise BootstrapError("Signed maintenance component compatibility differs from release metadata")
        if package.get("compatibility", {}) != release.get("compatibility", {}):
            raise BootstrapError("Signed maintenance package compatibility differs from release metadata")

        # These are the real target checks used by the maintainer. The environment
        # is discovered from the mounted target, never accepted from the caller.
        if not compatible(package, environment) or not compatible(component, environment):
            raise BootstrapError("Target SteamOS version or architecture is incompatible with this maintenance release")
        _safe_first_install(root, component)

        # The package is fully authenticated and statically validated at this
        # point. Engine.apply provides the normal write-ahead transaction and
        # restore journal. No downloaded hook or script is invoked.
        engine = Engine(root)
        empty_state = {"schema_version": 1, "root": engine.identity, "owners": {}, "components": {}}
        expected_state = hashlib.sha256(json.dumps(
            empty_state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        result = engine.apply(component, package_dir, environment=environment,
                              expected_state_sha256=expected_state)
        return {
            "status": "installed",
            "package": PACKAGE_ID,
            "version": version,
            "registry": index["id"],
            "index_sequence": index["sequence"],
            "transaction": result["transaction"],
            "files": result["files"],
            "recovery": "Engine transaction journal retained for restore or crash recovery",
        }


def recover(root: str | Path) -> dict:
    """Run the existing Engine recovery path without discarding state evidence."""
    root = Path(root).resolve()
    state = root / "var/lib/boiler/maintenance"
    if not os.path.lexists(state):
        raise BootstrapError("No existing Boiler maintenance state is available to recover")
    transactions = Engine(root).recover()
    return {"status": "recovered", "transactions": transactions,
            "recovery": "Unknown or incomplete state was preserved on error"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="install-boiler-maintenance.py",
        description=("Install the exact local Boiler maintenance release from an explicitly selected, "
                     "signed generic maintenance index. The model/device index is not accepted."),
    )
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--index-url", help="HTTPS URL of the generic maintenance DeviceIndex JSON")
    actions.add_argument("--recover", action="store_true", help="Explicitly recover preserved maintenance transactions")
    parser.add_argument("--signature-url", help="HTTPS URL of its detached Ed25519 signature")
    args = parser.parse_args(argv)
    try:
        if os.name != "posix" or os.geteuid() != 0:
            raise BootstrapError("Run this installer as root on the SteamOS target")
        if sys.flags.isolated != 1 or sys.flags.no_user_site != 1:
            raise BootstrapError("Launch the standalone installer with Python isolated mode: python3 -I -B <installer.pyz>")
        if args.recover:
            if args.signature_url:
                parser.error("--signature-url is only valid with --index-url")
            result = recover(Path("/"))
        else:
            if not args.signature_url:
                parser.error("--signature-url is required with --index-url")
            result = install(Path("/"), args.index_url, args.signature_url)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
