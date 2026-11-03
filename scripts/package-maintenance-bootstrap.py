#!/usr/bin/env python3
"""Build the deterministic self-contained first-install maintenance zipapp."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maintenance.engine import RUNTIME_FILES, runtime_files


PINNED_PUBLIC_KEY_SHA256 = "c3f443312c74bcdc3013d7a9deb306473dc6c3b21f6625319012c9dbdb9b1e86"
BOOTSTRAP_SOURCE = "scripts/maintenance_bootstrap.py"
ARCHIVE_BOOTSTRAP = "boiler_bootstrap.py"
BUILD_FILES = tuple(sorted({*RUNTIME_FILES, "maintenance/release.json", "LICENSE", BOOTSTRAP_SOURCE}))


def build_files(root):
    return tuple(sorted({*(path.relative_to(root).as_posix() for path in runtime_files(root=root)),
        'maintenance/release.json','LICENSE',BOOTSTRAP_SOURCE}))
MAIN = (
    "import sys\n"
    "if sys.flags.isolated != 1 or sys.flags.no_user_site != 1:\n"
    "    raise SystemExit('Use: python3 -I -B <installer.pyz> [--index-url URL --signature-url URL | --recover]')\n"
    "from boiler_bootstrap import main\n"
    "raise SystemExit(main())\n"
).encode("utf-8")


def _project_file(root: Path, name: str) -> bytes:
    path = root / name
    current = path
    while current != root:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("Bootstrap source contains a link: " + name)
        current = current.parent
    if not path.is_file():
        raise ValueError("Bootstrap source is missing: " + name)
    return path.read_bytes()


def package(root: str | Path, output: str | Path) -> dict:
    root = Path(root).resolve()
    if root != ROOT:
        raise ValueError("Bootstrap builder only packages the checkout containing this script")
    output = Path(output).resolve()
    if output.suffix.lower() != ".pyz":
        raise ValueError("Bootstrap output must use the .pyz suffix")
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("Bootstrap output must be outside the source checkout")

    whitelist_path = root / "scripts/source-files.json"
    whitelist = json.loads(whitelist_path.read_text(encoding="utf-8"))
    names=build_files(root)
    if (whitelist.get("kind") != "BoilerSourceWhitelist"
            or not set(names) <= set(whitelist.get("files", []))):
        raise ValueError("Standalone bootstrap sources are not all in the reviewed source whitelist")
    contents = {name: _project_file(root, name) for name in names}
    key = contents["protocol/trusted-public-key.pem"]
    if hashlib.sha256(key).hexdigest() != PINNED_PUBLIC_KEY_SHA256:
        raise ValueError("Bootstrap signing trust anchor differs from the pinned official key")
    try:
        release = json.loads(contents["maintenance/release.json"].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid standalone maintenance release metadata") from error
    if release.get("schema_version") != 1 or release.get("product") != "Boiler Maintenance" or not release.get("version"):
        raise ValueError("Invalid standalone maintenance release identity")

    if output.exists() or output.with_name(output.name + ".partial").exists():
        raise ValueError("Use a fresh bootstrap output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    archive_contents = {name: contents[name] for name in names}
    del archive_contents[BOOTSTRAP_SOURCE]
    archive_contents[ARCHIVE_BOOTSTRAP] = contents[BOOTSTRAP_SOURCE]
    archive_contents["__main__.py"] = MAIN
    # zipimport cannot discover an implicit namespace without directory entries.
    archive_contents["components/__init__.py"] = b""
    partial = output.with_name(output.name + ".partial")
    try:
        with partial.open("xb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, strict_timestamps=True) as archive:
                for name, data in sorted(archive_contents.items()):
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    archive.writestr(info, data, compress_type=zipfile.ZIP_STORED)
        os.link(partial, output)
        partial.unlink()
    except BaseException:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
        raise

    data = output.read_bytes()
    inventory = [
        {"path": name, "size": len(contents), "sha256": hashlib.sha256(contents).hexdigest()}
        for name, contents in sorted(archive_contents.items())
    ]
    return {
        "kind": "BoilerMaintenanceBootstrap",
        "version": release["version"],
        "output": str(output),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "trusted_public_key_sha256": PINNED_PUBLIC_KEY_SHA256,
        "inventory": inventory,
        "execution": "self-contained zipapp; invoke with python3 -I -B",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="package-maintenance-bootstrap.py")
    parser.add_argument("--output", required=True, help="Fresh .pyz path outside the checkout")
    args = parser.parse_args(argv)
    result = package(ROOT, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
