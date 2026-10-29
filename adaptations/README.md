# Boiler device registry

This repository contains the Boiler device registry and a brand/model adaptation source collection.
Start at [ADAPTATIONS.md](ADAPTATIONS.md) for OXP3 and X2 Mini installation guides.
Executable device packages continue to use the existing authenticated package workflow.

`device-index.json` follows DeviceIndex schema version 1. Sequence numbers are
monotonic, revocations refer to manifest SHA-256 values, and each production
package must carry a pinned source and verified manifest digest. The current
index is unsigned and intentionally advertises no production package. A key or
release URL is never invented to make a draft look published.

`devices/oxp3.json` distinguishes existing legacy release support, experimental
Boiler migration and blocked combinations. Evidence, notes and known limitations
travel with the device record. Claims for an old release do not certify a newly
packaged bridge.

Build a local development index from a real package directory:

```sh
python scripts/build-index.py --package ../Boiler-oxp3/dist/device-oxp3 --output dist/device-index.dev.json
```

The command calculates the manifest digest and every inventory digest before
adding a local-only source. It never signs, uploads or changes the production
index. An authenticated production index and accepted model package are release
gates. Boiler's generic installer consumes an authenticated index or a visible,
explicit local development trust grant.

The same standalone script can prepare an **unsigned release candidate** from
the exact model package directory and its ZIP. Run this in the independent
public registry checkout after committing the reviewed device metadata:

```sh
python scripts/build-index.py --package /path/to/model-package --archive /path/to/model.zip --asset-url https://github.com/FlightDan/OneXPlayer3-SteamOS/releases/download/REVIEWED_TAG/model.zip --sequence 1 --output dist/index.candidate.json
```

The tag and filename are explicit operator inputs, not a published-asset claim.
The command validates every ZIP file against the package manifest, rejects
additional files and links, and calculates both the ZIP and manifest size and
SHA-256. Its standalone v1 preflight checks component identities, compatibility,
file references and digests, and inventoried hooks and maintenance entries.
The model manifest must name its actual full GitHub source commit.
Metadata comes from exact committed `devices/oxp3.json` bytes and remains
experimental. `verified_at` records local artifact inventory verification only;
the compatibility records retain their independent evidence and limitations.

Sequence numbers must increase beyond the committed baseline. For later
releases, supply the last independently signature-verified index using
`--previous-index /path/to/verified/index.json`; the candidate must exceed that
sequence and preserve all previous revocations in the committed registry.
This script does not authenticate a previous-index file itself. Keep the
candidate bytes immutable for the release operator's existing signing flow,
publish and recheck the exact ZIP first, and only then expose the signed index.
The script does not sign, access a key, upload, or change `device-index.json`.
