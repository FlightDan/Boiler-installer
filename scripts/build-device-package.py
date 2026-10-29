"""Build an unsigned local device ZIP without executing or signing its scripts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol.local import import_local, _safe_path
from protocol.manifest import ProtocolError, validate_package


def build(source, output):
    output = _safe_path(output)
    if output.suffix.lower() != ".zip":
        raise ProtocolError("Output must use the .zip extension")
    if os.path.lexists(output):
        raise ProtocolError("Package ZIP destination already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".build-device-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        result = import_local(source, temporary / "package", developer=True)
        root = Path(result["directory"])
        manifest = validate_package(root, expected_manifest_sha256=result["manifest_sha256"])
        archive = temporary / "package.zip"
        with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as bundle:
            for name in sorted(["manifest.json", *[item["path"] for item in manifest["files"]]]):
                bundle.write(root / name, name)
        # Re-import the exact archive, so ZIP bytes and extracted inventory are checked.
        import_local(archive, temporary / "recheck", developer=True)
        if os.path.lexists(output):
            raise ProtocolError("Package ZIP destination already exists")
        _safe_path(output)
        # Hard linking publishes without replacing another creator's output.
        os.link(archive, output)
    checksum = hashlib.sha256()
    with output.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            checksum.update(block)
    return {"path": str(output), "size": output.stat().st_size, "sha256": checksum.hexdigest(),
            "manifest_sha256": result["manifest_sha256"], "device_package": manifest["id"],
            "trust": "unsigned-development", "official_release": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), ensure_ascii=False, indent=2))
