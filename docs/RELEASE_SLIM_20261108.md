# Boiler Installer 0.2.1 slim Preview

## Scope and identity

The requested installer version, display version, tag (`v0.2.1`) and slim ZIP name use **0.2.1**. The package remains in the existing preview channel and GitHub prerelease policy; it is not hardware acceptance or promotion of the accepted update streams.

The installer contains the SteamOS owned-boot-partition eligibility repair and the complete partition-navigation regression repair documented in [STEAMOS_PARTITION_DELETE_20261107](STEAMOS_PARTITION_DELETE_20261107.md). The Chinese action is “选择 SteamOS 分区全部删除”. Verified full eight-role and exact residual three-role layouts may be selected; live identity, ownership, host/Windows protections and explicit deletion consent still apply. Partition numbers alone never authorize deletion.

Navigation checks now establish an actual inspected synthetic source, use the explicit imported route, and await the WPF dispatcher. Production source, physical target and helper protections remain unchanged.

Generic maintenance remains independently versioned at `0.2.0+maintenance.17`. No adaptation/TOON, model package, private CPU policy or signing identity is changed. Generic maintenance candidate preparation precedes the Windows package; this release does not publish a separate maintenance update or new signed discovery index.

## Public source and packaging boundary

Publish only `scripts/source-files.json` through `scripts/export-source.py`, onto an independent checkout of the existing public history. Do not publish internal development history, raw device captures, credentials, `.local/`, signing inputs or private research. The previous public baseline `c75bf9f25036924679f8fd9e856ba7c9bf1530f8` has the same 1077 file blobs as local baseline `02c0be4b7ef6734f10364ffdf2687bf5ee9b8756`; their histories are deliberately separate.

The release ZIP must be built from the clean, exact new public commit using `scripts/package-release-installer.py`, and named `boiler-installer-0.2.1-win-x64-slim.zip`. No executable model/device packages may occur at the top level or in retained maintenance. The pinned display catalog and generic maintenance runtime remain present. Model acquisition and signed generic maintenance distribution remain separate.

## Delivery gates at source freeze

| Gate | Status / boundary |
| --- | --- |
| Repair regressions | Earlier candidate: Release build 0 warnings/errors; partition navigation 126, source digest 35, SteamOS 86, generic checks 131 passed. Earlier Python run: 993 tests, 98 Windows skips. These are not final package results. |
| Public source | Whitelist export, outgoing diff/privacy check and exact public commit required before packaging. |
| Generic maintenance | Fresh candidate, archive/manifest/inventory and applicable software tests required first. Unsigned candidate integrity is not authenticated update publication. |
| Windows build and actual slim ZIP | Required: clean public commit, locked dependencies, Release build, all-member inventory and hashes, version ordering, model-payload exclusions. |
| Extracted executable / offline catalog | Required: extracted bytes match build-info before execution, packaged CLI regressions and actual rendered UI review. |
| Public download | Required: new immutable prerelease asset, public redownload, size/SHA-256/source/full inventory verification. Final results belong in the release notes and separate local release ledger. |
| Windows Authenticode | Not applicable to the existing unsigned preview policy; do not claim the ZIP or executable is signed. |
| Signed index / maintenance publication | Not applicable: no new index, bootstrap or independent maintenance distribution is promoted. Existing trust anchors are preserved. |
| Linux / maintenance upgrade / recovery | Platform-skipped privileged tests and real installed-system upgrade/recovery remain unverified. Candidate software checks must be reported separately. |
| Production Windows deletion | Blocked/unexecuted: supported-bus controlled VM deletion of eight/three partitions is still required. The owned VHD native-read result is not production deletion acceptance. |
| Physical device / firmware / user disk | Not executed. No user disk deletion, install, firmware write, reboot or sleep/wake action is implied. |

Any failed required packaging or public-download gate stops release completion. Do not replace an existing attachment or hand-edit generated inventories. Historical repair evidence remains immutable; final artifact fingerprints, public source commit and published URLs are recorded separately after actual verification.

On this machine, preparation and work files must use the RAID volume; C: free space does not meet the complete preparation flow requirements. Machine-specific absolute workspace settings must not enter the portable package.
