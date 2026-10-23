# Standalone dependency preparation

The installer source does not depend on an adjacent OXP3 checkout or on tools
installed in a particular user's home. `scripts/runtime-dependencies.lock.json`
pins the public archive URLs, exact sizes and hashes, all 702 audited runtime and
rEFInd member hashes, 44 offline Alpine APK versions, and explicit SDK/runtime
versions. Inputs are byte-for-byte existing public distributions; no local
research tree, private record or signing key is read. The additional Bash 5.3.3-r1
APK is downloaded from Alpine over HTTPS and locked by exact size and SHA-256;
it is installed by the guest through Alpine signature verification. For offline
preparation, include it in the cache passed with `--cache`.

Use CPython 3.12 or later to prepare a clean checkout:

```powershell
python scripts/bootstrap-dependencies.py --profile all
```

The preparer downloads the locked existing public runtime source artifact and
the official .NET SDK archive into `cache/bootstrap`. SDK 10.0.402 includes
runtime 10.0.13; its SHA-512 was checked against Microsoft's official release
metadata, and the lock also contains its SHA-256. Setup executables are never
run. The published runtime supplies QEMU 11.1.0 (20260811 build), 7-Zip 26.03,
Alpine virt 3.23.6, desync 1.0.4, aria2 1.37.0, offline filesystem/Python/OpenSSL
APKs and console files. The .NET host, Core runtime and Windows Desktop runtime
come from 480 individually locked members of the official SDK archive, including
when only the runtime profile is requested. Unselected model payloads and the old
.NET runtime are not extracted from the public installer artifact.

For disconnected preparation, explicitly supply the already-downloaded inputs:

```powershell
python scripts/bootstrap-dependencies.py --profile all --public-installer inputs/oxp3-steamos-installer-1.0.6-20261022-220437.zip --dotnet-sdk inputs/dotnet-sdk-10.0.402-win-x64.zip --offline
python scripts/bootstrap-dependencies.py --profile all --public-installer inputs/oxp3-steamos-installer-1.0.6-20261022-220437.zip --dotnet-sdk inputs/dotnet-sdk-10.0.402-win-x64.zip --check-only
```

`--profile runtime` prepares the end-user runtime without a build SDK.
`--profile build` prepares only the pinned SDK. `--profile guest` prepares the
Alpine boot files, ISO, offline APKs and console. A PowerShell wrapper is available
as `scripts/prepare-runtime.ps1`; it accepts an explicit `-Python` path.
`scripts/build-app.ps1` then uses `runtime/dotnet/dotnet.exe`.

Every cached source archive is verified before opening. Every prepared file is
matched to its locked bytes; differing existing files cause a failure instead of
being silently overwritten. The full SDK is authenticated by the fixed archive
hashes and each extracted member is inventoried. Reports under
`runtime/dependencies/` record archive provenance and actual output digests.
Reports are verification evidence, not a replacement for the committed lock.

## rEFInd

```powershell
python scripts/prepare-refind.py --public-installer inputs/oxp3-steamos-installer-1.0.6-20261022-220437.zip --offline
```

This prepares `components/refind/manage.py`, unmodified rEFInd 0.14.2 from Arch
0.14.2-3, the publicly shipped Graphite Quiet theme, UsbXbox360Dxe v1.8.0,
configuration, source records and licenses. It performs no deployment or firmware
write. The existing recovery namespace remains byte-exact to retain the audited
transaction implementation. Deployment operates only against an explicitly
chosen mounted SteamOS ESP; existing Windows ESP contents remain outside its
managed file list. Tests run on disposable directory ESP fixtures:

```powershell
python -B -m unittest discover -s components/refind -p 'test_*.py' -v
```

Byte reproduction from pinned binaries is supported. Rebuilding QEMU, third-party
EFI binaries or the generated background from upstream source is a separate task;
the original image-generation process is not deterministic. Preserve the bundled
notices and audit the combined redistribution requirements before a new public
release. Local preparation, version probes and directory fixtures do not certify
physical disk installation, firmware controller input, suspend or WHPX behavior.
