# Boiler Installer 0.1.0 preview — implementation and release record

Status: development verification in progress; not a published production release.

## Implemented scope

- Independent WPF installer and explicit dependency locks; no sibling checkout
  is needed to prepare the common runtime. Three home routes, optional3.10,
  generic firmware groups, Decky and an optional generic maintenance runtime.
- Root-only IMG ZIP/Boiler package import, raw IMG and IMG.BZ2; strict5or8role
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
- Synthetic IMG/ZIP and WPF checks:25 assertions passed, including source
  immutability, both GPT copies, all8partition contents and protected-byte removal.
- Generic source tests: 91 run, 5 platform/environment skips in the latest full
  Windows suite. The signed updater then passed 13 focused regression cases.
- QEMU final-payload lifecycle: 8 tests passed, including private ownership,
  repeated install, restoration, shared-slot marker guards and native metadata
  and firmware symlinks confined to the mounted image.
- WPF visual capture is blocked in the current execution environment: the
  loaded visual tree produces an empty bitmap. Route/default state checks pass;
  screenshot appearance, scaling and touch layout remain unaccepted.
- Actual3.8.14 build20260707.10 source: fixture, A/B deployment with injected
  failure/recovery, sparse staging, Vanilla integration, read-only component
  verification and physical-readiness checks passed. This is image evidence.
- Actual 3.8.14 custom and 3.10 Vanilla/custom paths passed A/B component
  installation, read-only verification and physical-readiness checks. The 3.10
  custom run included all three firmware vendors. Coverage remains partial:
  Intel has 19 uncovered declarations, Realtek 1, MediaTek 4; exact declarations
  are retained in component results and both slot installation plans.
- Read-only hardware baseline inspection confirmed SteamOS 3.10 with the same
  formal maintainer public key. No upgrade, adoption, reboot or new hardware
  acceptance is implied by that inspection.

## Delivery checklist

| Item | State |
| --- | --- |
| Change list and repository contracts | Complete for the implementation described above |
| Common installer and maintenance source synchronization | In progress; final payload must match final source |
| Rebuilt component payload inventories | Prepared; repeat after final source changes |
| Final release/build identities | Pending final source commits |
| Source, image, new-install and maintenance tests | Partly complete; evidence categories kept separate |
| Signed old-maintainer compatibility bridge | Pending integration and formal release preparation |
| Generic maintainer publication and public download verification | Pending bridge and signature review |
| Old-to-new hardware upgrade, reboot and recovery acceptance | Pending; no hardware success claimed |
| Windows final package and release | Pending preceding maintenance gates |
| Existing README versions | Not applicable until a release version changes |

Known boundary: hook-based model adaptations cannot be universally rolled back
by reversing only their declared file inventory. Interrupted hooks retain their
journal and stop further deployment. Unverified radio hardware remains marked
unverified; firmware availability does not establish chipset support.
