# Boiler Installer 0.1.0 preview — implementation and release record

Status: development verification in progress; not a published production release.

## Implemented scope

- Independent WPF installer and explicit dependency locks; no sibling checkout
  is needed to prepare the common runtime. Three home routes, optional 3.10,
  generic firmware groups, Decky and an optional generic maintenance runtime.
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
- Synthetic IMG/ZIP and WPF checks: 29 assertions passed, including source
  immutability, both GPT copies, all 8 partition contents and protected-byte removal.
- Generic source tests: 119 run, 13 platform/environment skips in the latest full
  Windows suite. Native Linux maintenance updater verification is recorded separately.
- QEMU final-payload lifecycle: 8 tests passed, including private ownership,
  repeated install, restoration, shared-slot marker guards and native metadata
  and firmware symlinks confined to the mounted image.
- WPF visual capture is blocked in the current execution environment: the
  loaded visual tree produces an empty bitmap. Route/default state checks pass;
  screenshot appearance, scaling and touch layout remain unaccepted.
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
  Receipt export then safely refused the real PARTUUID slot identity; the `.11`
  correction is published and its native Linux bridge tests passed 11/11.
  No successful live adoption or reboot is claimed.
- Readonly multi-volume payload transport uses a dedicated disposable guest disk,
  preserving target drive identities; its generic Linux lifecycle passed 8/8.
  Bash is a pinned official offline APK, with existing locked readline dependencies.
- The final signed-update Linux suite passed 23/23, including native Bash execution,
  hostile environment rejection, ancestor replacement refusal, FIFO/link refusal
  and persistent manual-recovery barriers.
- Authenticated `engine-hooks-v1` maintenance updates use a private revalidated
  package snapshot, minimal process environment and persistent failure barriers.
  The first OXP3 package does not advertise live model-upgrade support until its
  legacy component ownership mapping and upgrade lifecycle are implemented.

## Delivery checklist

| Item | State |
| --- | --- |
| Change list and repository contracts | Complete for the implementation described above |
| Common installer and maintenance source synchronization | In progress; final payload must match final source |
| Rebuilt component payload inventories | Prepared; repeat after final source changes |
| Final release/build identities | Pending final source commits |
| Source, image, new-install and maintenance tests | Partly complete; evidence categories kept separate |
| Signed old-maintainer compatibility bridge | `.10` and `.11` published; signatures, full catalogs and public downloads verified; actual `.11` export acceptance pending |
| Generic maintainer publication and public download verification | Pending bridge and signature review |
| Old-to-new hardware upgrade, reboot and recovery acceptance | Pending; no hardware success claimed |
| Windows final package and release | Pending preceding maintenance gates |
| Existing README versions | Not applicable until a release version changes |

Known boundary: hook-based model adaptations cannot be universally rolled back
by reversing only their declared file inventory. Interrupted hooks retain their
journal and stop further deployment. Unverified radio hardware remains marked
unverified; firmware availability does not establish chipset support.
