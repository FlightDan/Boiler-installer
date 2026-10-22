# Boiler Maintenance 0.1.0 preview release record

The generic runtime has its own version in `maintenance/release.json`, separate
from the Windows installer and model packages. This artifact covers the
already-installed `core:maintenance` file component. Package identity is
`bundle:maintenance`. Legacy OXP3 state adoption remains an explicit operation.

## Build from the final independent public checkout

Use Python 3.11+ and Git. The checkout HEAD must be the exact public source commit,
with a clean working tree and a committed file set equal to
`scripts/source-files.json`. Source bytes must match that commit, including line
endings. Create artifacts outside the checkout, with a fresh output path:

```powershell
python -B scripts/package-maintenance.py --root . `
  --source-commit <40-character-public-HEAD> `
  --output ../artifacts/boiler-maintenance-0.1.0.zip `
  --asset-url https://github.com/FlightDan/Boiler-installer/releases/download/maintenance-v0.1.0/boiler-maintenance-0.1.0.zip
```

The command performs no network access and never reads signing keys. It emits a
deterministic ZIP plus `boiler-maintenance-0.1.0.zip.json` containing archive size,
SHA-256, exact file inventory and an unsigned `index_entry`. Ignored local files
are not selected. The archive uses a fixed ZIP timestamp, stable paths and stored
entries to avoid dependence on compression-library versions. Existing outputs
are refused.

The package has `manifest.json` (DevicePackage), `component.json`
(ComponentManifest), `release.json`, `source-identity.json`, the project license,
an explicit list of maintenance/protocol Python files, the byte-identical public
key and the established `boiler-maintenance` launcher. Source identity records
the immutable GitHub commit and exact input sizes/hashes. No directory tree is
copied. Runtime dependencies remain SteamOS Python 3 and OpenSSL for Ed25519
verification when the optional Python cryptography module is unavailable.

## Existing trust and update protocol

Publisher metadata is unsigned integrity evidence, not a trusted registry. Merge
its complete `index_entry` into a monotonically sequenced DeviceIndex covering
all supported packages and sign the exact UTF-8 index bytes with the existing
official Ed25519 key. Use the protocol's detached raw signature format; do not
sign reserialized JSON or introduce an alternate key. The index entry anchors
both the manifest size/hash and immutable HTTPS ZIP size/hash. Upload all referenced
assets before exposing that signed registry. Do not overwrite released assets.

Normal discovery, download, reviewed planning and execution use the generic
maintainer's `discover`, `plan-update` and `execute-update` commands. A package is
accepted only through the signed-index digest. Existing owned files and signing
anchors are rechecked; edited/unowned targets and key changes block an ordinary
update. The launcher retains `/usr/local/lib/boiler` and
`/usr/local/bin/boiler-maintenance`, matching new installation payloads. The
installer's preparation reads the same independent maintenance version.
The launcher starts Python in isolated mode (`-I -B`) and explicitly adds only
the installed Boiler path, preventing `PYTHONPATH`, current-directory and user
site customization from selecting maintenance code. CLI arguments pass through
unchanged.

## Scope, verification and remaining gates

- Complete: explicit source inventory, independent commit binding, reproducible
  packaging, exact package inventory, unchanged public key and generic updater
  file-component representation.
- Complete: focused source tests cover reproducibility, inventory, compatibility,
  dirty/wrong/parent checkouts, unlisted committed files, unsafe URLs, key changes,
  existing outputs, and planning/executing an owned runtime update in a temporary
  filesystem. Signature verification has separate protocol tests; the packaging
  integration test substitutes signed discovery and never uses an official key.
  An environment-injection test also checks the isolated launcher against hostile
  `PYTHONPATH`, current-directory and user-site Python files.
- Supported software scope: SteamOS `3.8.14` and `3.10`, `x86_64`, engine at least
  `0.1.0`. This pure Python runtime does not install kernel modules and has no
  kernel ABI restriction. If a future build needs one, its exact kernel strings
  must be listed in `maintenance/release.json`; the builder preserves them in
  component, package and signed-index input. This does not claim device or
  wireless hardware acceptance.
- Pending: official signing/publication, public redownload and signature/hash
  checks, required existing-version upgrade/restart/recovery device acceptance,
  and the matching Windows release. Artifact generation and source tests alone
  do not satisfy those gates.
- Not applicable to this generic artifact: old OXP3 release rebuilds or replacing
  the legacy signing anchor; model-specific payloads are separate packages.

Detailed publication and hardware results belong in the final version release
record and must be updated only when actually completed.
