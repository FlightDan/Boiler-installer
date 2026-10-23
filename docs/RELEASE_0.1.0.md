# Boiler Installer 0.1.0 preview — implementation and release record

Status: development verification in progress; not a published production release.

## Implemented scope

- Independent WPF installer and explicit dependency locks; no sibling checkout
  is needed to prepare the common runtime. Three home routes, optional 3.10,
  generic firmware groups, Decky and an optional generic maintenance runtime.
- Explicit current-PC manufacturer/model advice from read-only Windows BIOS registry
  values, using audited exact pairs. It never selects a target or a network adapter;
  external target devices remain a manual choice. Pinned source links open on click.
- Root-only IMG ZIP/Boiler package import, raw IMG and IMG.BZ2; strict 5- or 8-role
  layouts and both GPT copies. Imported content is copied before changes.
- Eight-role export reconstructs GPT metadata and zeros protected partitions and
  free extents, retaining both system slots, var/home bytes and partition IDs.
- Versioned manifest protocol, signed monotonic index cache, immutable sources,
  package inventory checks, revocations, explicit session developer trust.
- Durable file ownership transactions and interrupted-operation barriers;
  private legacy evidence adoption with the existing public key; cache-free
  Decky restore and shared A/B activation marker ownership.
- Independent OXP3 package source from previously public, pinned assets. It is
  experimental and does not claim new hardware validation.

## Evidence collected

- Windows WPF builds: succeeded with zero warnings.
- Standalone dependency preparation:5,799 files extracted into a clean output
  directory from locked archives; runtime verification checks702 public members.
- Synthetic IMG/ZIP and WPF checks: 32 assertions passed, including source
  immutability, both GPT copies, all 8 partition contents and protected-byte removal.
- Final independent Windows source suite: 191 run, 164 passed and 27
  platform/environment skips. Linux native checks are recorded separately below.
- QEMU final-payload lifecycle: 8 tests passed, including private ownership,
  repeated install, restoration, shared-slot marker guards and native metadata
  and firmware symlinks confined to the mounted image.
- WPF capture now produces nonempty home/custom/Vanilla/imported pages in the
  security follow-up. Route/default state checks pass; basic home/custom captures
  were inspected. Multiple scales, keyboard and touch interaction remain unaccepted.
- Actual 3.8.14 build20260707.10 source: fixture, A/B deployment with injected
  failure/recovery, sparse staging, Vanilla integration, read-only component
  verification and physical-readiness checks passed. This is image evidence.
- Actual 3.8.14 custom and 3.10 Vanilla/custom paths passed A/B component
  installation, read-only verification and physical-readiness checks. The 3.10
  custom run included all three firmware vendors. Coverage remains partial:
  Intel has 19 uncovered declarations, Realtek 1, MediaTek 4; exact declarations
  are retained in component results and both slot installation plans.
- Existing test-device SteamOS 3.10 maintenance runtime upgraded from
  `1.0.6+maintenance.9` to the publicly downloaded and verified `.10` bridge.
  Component revisions, formal key, old runtime and root protection were retained.
  Receipt export initially refused the real PARTUUID slot identity; the `.11`
  correction is published, its native Linux bridge tests passed 11/11, and the
  actual `.10` to `.11` tool-only upgrade passed. The next export safely rejected
  the 1.5 GB legacy index. A bounded read-only projection found 1,957 active files
  and a 138 MB maximum file; the old inline receipt limits cannot represent this
  installation. Streaming blob-backed migration passed independent source review;
  the legacy bridge and streaming exporter each passed 11 native Linux tests,
  including a 138 MB file, both receipt formats and late-publication failure.
  The public `.12` tool-only upgrade subsequently passed with component
  revisions/key/root protection retained. Its real private v2 export covered
  1,958 files and 431 MB of binary objects. Live generic adoption and reboot
  acceptance are still pending.
- OXP3 final7 package: complete disposable-image A/B installation passed, including
  common Decky and maintenance, two read-only model proofs (115 files and 12 journals
  each), and deployment-readiness checks. The original fixture hash was unchanged.
  The exact published `0.1.0-preview.1` archive passed the separate final-artifact
  run with both-slot proofs and unchanged source fixture. Public normal-mode
  download verified signed directory sequence 1, the fixed archive and its
  complete manifest; verified offline cache reuse also passed.
- Readonly multi-volume payload transport uses a dedicated disposable guest disk,
  preserving target drive identities; its generic Linux lifecycle passed 8/8.
  Bash is a pinned official offline APK, with existing locked readline dependencies.
- The final runtime passed 114/114 native Linux tests without skips: live-root
  protection 30, legacy v1 intake 5, binary adoption/upgrade/restore 34, engine
  transactions 11, signed updater 26 and installed CLI/component lifecycle 8.
  This includes a 138 MB
  adopted-file upgrade and complete restore chain, retained owned-file checks,
  native Bash execution, hostile environment rejection, ancestor replacement
  refusal, FIFO/link refusal and persistent manual-recovery barriers. These
  results apply to the recorded final runtime bytes. A separate disposable 3.10
  UEFI boot passed actual-root CLI apply/verify/restore, SIGKILL during file
  publication, pending-operation refusal and explicit transaction/protection
  recovery. Source backing disks remained read-only. These VM results do not
  replace physical-device adoption, reboot or hardware acceptance.
- Authenticated `engine-hooks-v1` maintenance updates use a private revalidated
  package snapshot, minimal process environment and persistent failure barriers.
  The first OXP3 package does not advertise live model-upgrade support until its
  legacy component ownership mapping and upgrade lifecycle are implemented.

## Delivery checklist

| Item | State |
| --- | --- |
| Change list and repository contracts | Complete for the implementation described above |
| Common installer and maintenance source synchronization | Complete for the frozen runtime; final public artifacts must bind these bytes |
| Rebuilt component payload inventories | Complete: 429 files regenerated and verified against final source and locked inputs |
| Final release/build identities | Published maintenance binds source 6a181e3; Windows security follow-up uses a new source commit and timestamped candidate |
| Source, image, new-install and maintenance tests | Partly complete; evidence categories kept separate |
| Signed old-maintainer compatibility bridge | `.10`, `.11` and `.12` published and publicly verified; actual `.12` upgrade and binary export passed; generic adoption pending |
| Generic maintainer publication and public download verification | maintenance-v0.1.0 published; original-key signatures, complete package inventories, fixed hashes and offline cache verified from public downloads |
| Old-to-new hardware upgrade, reboot and recovery acceptance | Pending; no hardware success claimed |
| Windows final package and release | Pending preceding maintenance gates |
| Existing README versions | Not applicable until a release version changes |

Known boundary: hook-based model adaptations cannot be universally rolled back
by reversing only their declared file inventory. Interrupted hooks retain their
journal and stop further deployment. Unverified radio hardware remains marked
unverified; firmware availability does not establish chipset support.

## Windows security follow-up (2026-10-23)

- IMG ZIP and BZip2 expansion now enforce a 1 TiB limit and keep at least 1 GiB
  of workspace free. Declared lengths are checked against actual streamed bytes.
  Failed or cancelled imports remove only the output created by that operation.
- ZIP central-directory metadata is bounded before framework enumeration. EOCD,
  ZIP64 offsets, actual record counts and exact directory spans are validated;
  conflicting later EOCD signatures are refused. Archives remain limited to a
  root IMG and, for Boiler packages, an optional image-manifest.json.
- BZip2 receives the held source through stdin instead of reopening its pathname.
  Cancellation terminates the owned child and observes both pipe tasks.
- Four helper launch paths now use the current application assembly, including
  Release builds. Independent review covered both imports and helper binding.
- Windows Python is updated to 3.13.17 and .NET runtime/SDK to 10.0.13/10.0.402,
  independently of the Linux maintainer. Official archive hashes, all 480 runtime
  members, the full SDK and isolated Python preparation are verified. No existing
  published attachment is replaced.
- The previous Windows candidate is withdrawn from publication. Final candidate
  evidence must name its own source commit, timestamp, size and SHA-256.

The pre-refresh Release build passed 79 synthetic image and WPF checks, including
the reproduced EOCD parser differential, valid ZIP64, byte/space limits, BZip2
stdin, cancellation, source preservation and both partition layouts. These checks
do not write physical disks or establish hardware acceptance.

## UAC request commitment follow-up (2026-10-23)

- All five elevated helper entry points require a purpose-specific SHA-256
  commitment supplied separately from the request pathname. The parent computes
  it from the original serialized bytes and retains a handle denying writes and
  deletion until the helper exits. Missing or mismatched commitments fail closed.
- Helpers verify one bounded byte snapshot before parsing or producing task
  output. Delete/format PowerShell subprocesses independently verify the same
  commitment before discovering disks or acting on the request.
- Partition recovery binds the reviewed journal snapshot and carries those
  verified bytes through both recovery reads. Normal atomic journal updates and
  existing source, target, ownership and protected-partition checks remain active.
- The standalone source now includes owned-VHD test scripts, the partition
  deletion protection policy, the Linux filesystem probe and USB inventory
  script. Missing sibling-checkout dependencies are explicitly whitelisted;
  a non-destructive script check covers references, syntax and policy refusal.
- New non-destructive CLI checks exercise helper tampering/cancellation and
  Boiler's three installation routes in Chinese and English at multiple layout
  sizes. Layout dimensions and programmatic focus traversal do not establish
  operating-system DPI, physical keyboard or touchscreen acceptance.
- The home page now hides the inactive wizard and keeps Tab traversal within
  its visible controls. The previous overlay allowed focus to reach covered
  wizard controls. All 24 language/size/route combinations pass 385 layout and
  programmatic focus assertions after this fix.
- Independent security review identified and closed pre-verification logging
  and a bounded-read length race. The 149 helper checks and 120 script dependency
  checks pass. Original-apphost administrator startup is separately confirmed;
  it is not inferred from launching a DLL without UAC.
- Administrator-owned 512 MiB VHD tests pass native partition writes, protected
  NTFS sentinel preservation, process-exit recovery, byte rechecks, cancellation,
  ownership refusal and the verified-snapshot recovery path. The journal can be
  replaced after snapshot verification without changing the consumed recovery
  state, and subsequent atomic journal updates still work. Pure physical-plan
  validation and ordinary-file recovery comparisons also pass. These are not
  physical-disk installations or actual power-loss tests.

Physical migration, reboot, recovery and hardware validation remain separately
recorded release gates. Windows-only changes do not replace the published Linux
maintenance artifacts or their signing identity.
