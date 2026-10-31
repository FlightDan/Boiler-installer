# Complete-runtime verification memory — 2026-11-01

`SteamOS-logs-20261031-230213.zip` records model `.20261101.10` completing legacy
file staging and then its apply hook receiving SIGKILL during component
preparation. The session is not prepared and has no deployment record. The
guest reports 2096560 KiB total memory, consistent with the production 2048 MiB
integration configuration. SIGKILL alone does not identify its cause.

The previous single-library verification used an 8192 MiB audit VM. That proved
the repaired size limit but did not establish complete-runtime verification in
the actual 2048 MiB integration budget. The complete Opti archive expands to
444582913 bytes; the old directory decoder simultaneously retains installed
and current trees as base64, then verification makes additional decoded copies.

The bridge now verifies directory runtimes using 1 MiB streaming reads and
compact size/hash/metadata summaries. It retains exact journal/destination,
path-set, mode/owner, trusted-ancestor, descriptor/no-follow, regular-file,
single-link and unchanged-file checks. It preserves both original-backup
variants, missing/unexpected/disagreeing-backup and original-only-file checks,
256 MiB member, 1 GiB tree and 12000-entry bounds. Directory enumeration itself
streams. Proof fields/canonical hashes, report coverage, activation checks,
immutable payload decoder, legacy restore and inline adoption limits are
unchanged. No extra guest memory or production trust is granted.

The dedicated regression compares old/new proof bodies and hashes, then uses
the complete locked Opti and desktop HDR runtime bytes on a disk-backed owned
image in an offline 2048 MiB QEMU guest. It records old failure/kernel OOM
counters, new peak RSS/exit status, unchanged managed-file inventories,
changed-byte rejection and focused Linux regressions. An audit harness serial
was initially longer than the virtio field; that run stopped before formatting
its empty owned fixture. It is preserved separately from the corrected run.

## Delivery and acceptance boundaries

New immutable identities: OXP3 model `0.1.0-localtest.20261101.11`, installer
`0.2.0-local.20261101.ui.5`, generic maintenance `0.1.0+maintenance.17`.
The normal model and maintenance generators run before Windows packaging.
Whitelisted sources are frozen into an independent public-derived checkout
with exact unpublished commit; public origin/history and CPU baseline are
preserved. Windows/retained model copies must match. Old artifacts remain
immutable. The complete installer TAR is requested under `C:\`.
The intermediate unpublished `.ui.4`/maintenance `.16` source/build evidence is
retained; its Windows publication failures led to the separately documented
`WINDOWS_PUBLICATION_RETRY_20261101.md` fix included in this final candidate.

Actual gate results are recorded with the final build, not predeclared here:
source/locked dependencies, independent review, focused Windows/Linux tests,
2048 MiB full-runtime comparison, model and nested inventories, maintenance
archive, Windows build, final archive/re-extraction/offline/apphost checks.
The user also requests subsequent isolated-VM investigation of image checks,
synthetic deployment, boot preparation and maintenance/recovery after this TAR
is prepared; that investigation needs separate current-artifact evidence.

Full Python-suite completion, actual user-device installation/boot, physical
ESP/NVRAM/firmware and hardware acceptance remain unperformed unless separately
recorded. Source and synthetic VM checks do not establish those results.
Signing/publication/download verification are not applicable to this unsigned,
unpublished local candidate. Private logs, orchestration, signing inputs and
experimental research stay outside source exports. README prose is unchanged.
