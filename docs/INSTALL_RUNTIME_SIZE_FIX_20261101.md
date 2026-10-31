# Directory runtime verification failure — 2026-11-01

The diagnostic export `SteamOS-logs-20261031-221159.zip` records a failure at
component preparation (02.01, 32%), before deployment: `Prepared` is false and
`Deployment` is null. Model `.20261101.9` reaches `verify_staged`, whose
`_normal_snapshot(entry['applied'])` rejects a directory-runtime member above
the generic inline backup limit of 128 MiB. The retained Opti archive contains
`runtime/igc/igc/local/lib/libopencl-clang2.so.15`, 138485536 bytes. The pinned
directory decoder and protected file reader already support 256 MiB members.

Read-only directory verification now uses that existing 256 MiB boundary at
both normalization sites. Byte, metadata, journal, ownership and activation-link
verification remain required. Ordinary file journals and schema-v1 adoption
retain the generic 128 MiB limit. Large-file adoption remains a separate blocked
migration until this model exports the existing binary receipt format; this fix
does not claim that migration is implemented. Legacy directory backups and
restore code are unchanged.

The normal repair builder overlays the reviewed bridge source onto its exact
retained base and generates final inventories under new model identity
`0.1.0-localtest.20261101.10`. Immutable input snapshots, CPU/HDR/runtime bytes,
licenses and previous candidates remain preserved. No README prose changes.

## Delivery gates

- Windows targeted regressions: complete, 26 tests, 4 Linux-only skips, including
  the retained input-profile and nested maintenance inventory regressions.
- Linux regressions: complete, 10 tests, zero skips in an offline disposable
  Alpine/QEMU guest, with root ownership and no-follow checks active.
- Actual retained runtime decoder: complete; the exact 138485536-byte Intel
  library reproduces the old adapter's reported exception. The new adapter
  verifies its actual bytes and metadata through the pinned directory decoder
  and protected file reader. Changing the library is rejected. The guest
  exits successfully; no host physical disk or network is attached. Evidence:
  `work/runtime-size-linux-evidence/result.json` and `console.log` (private,
  excluded from public source export).
- Independent review: complete; both normalization paths, unchanged v1 limits,
  builder inventory and source whitelist reviewed, no blocking findings.
- New model inventory and fresh import: complete; all 529 archive members and
  all 40 nested maintenance members verified, with 496 unrelated base files
  unchanged. The initial build's import hit Windows `WinError 5` at temporary
  directory rename. A fresh normal protocol import into another workspace
  directory succeeded; its extracted bytes match every archive member. The
  original failed staging evidence remains preserved.
- Installation/maintenance options and metadata: unchanged; no selection or
  public protocol change. Both apply and subsequent verify use the same fix.
- Windows application build: not applicable to this Python-only model change;
  no application source changed. Whole installer packaging and active/retained
  cache switching: unperformed; a standalone model candidate is not a
  replacement installer. Explicit local-package import selects this candidate.
- Physical installation, first boot and recovery: unperformed. This diagnostic
  records no physical deployment; no user disk, ESP or firmware is touched.
- Public source commit, signing and public-download verification: not applicable
  to the unsigned unpublished diagnostic model candidate; no public release.

Private logs and test orchestration remain outside the source whitelist.

## Candidate identity

The standalone model ZIP is 433231807 bytes, SHA-256
`a1dc5080198662e410bf2524732c40fabbd7a30a34989ac8b4c0930293d0e317`.
Its manifest SHA-256 is
`65ad8997f0fd5cd3a6a1bbd56969580df1aba459958bec476d98fe6adff45312`.
The generated archive is retained under `dist/runtime-size-fix-20261101/` with
`verification.json`. These are local diagnostic model identities, not an exact
published installer source commit or a claim of completed hardware acceptance.
