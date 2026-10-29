# SteamOS partition selection for reinstallation — 0.1.0-preview

This is a local Boiler update on 2026-10-25. It does not replace a published
installer or change the integration version. The current UI/interaction scope
continues to defer full packages, publication and physical-device changes.

## Behavior

The Destination page identifies complete standard SteamOS A/B partition groups
on the currently selected disk. A read-only native GPT query binds names and
attributes to the same disk GUID, partition GUIDs, types and byte ranges as the
Windows inventory. Detection requires the eight contiguous roles `esp`, `efi-A`,
`efi-B`, `rootfs-A`, `rootfs-B`, `var-A`, `var-B`, `home`, with their expected GPT
types. A volume label, Steam game library or arbitrary Linux filesystem alone
does not qualify. This identifies a partition layout, not a running OS/version.

The new Reinstall SteamOS section shows detected, deletable and selected counts
and the selected capacity. Select all chooses only recognized partitions that
pass the existing deletion policy on this disk. Users can deselect individual
partitions or clear the selection. Refreshing or changing disks clears it;
changing interface language preserves the selection on the same snapshot.

The existing EFI, Windows, recovery, boot-file, read-only and installer-volume
protections remain in force. Protected SteamOS boot partitions are listed as
kept and cannot be selected; depending on boot-file contents this includes the
`efi-A`/`efi-B` slots as well as the ESP. The action does not remove every boot
partition or erase a whole disk. Missing, renamed, fragmented or partial role
sets, unsupported disk geometry and unavailable native metadata do not enable
automatic selection. Native identification requires administrator access.

The elevated helper owns the final confirmation and revalidates the digest-bound
request. Its confirmation lists the selected disk and every selected partition,
capacity and byte range, warns about system/game/personal-file loss, and defaults
to Cancel. Explicit consent is required. Deletion happens immediately after
confirmation, separately from installation.

Every target is checked before any deletion. Each removal is selected by a
freshly verified GUID/range, with the disk and entire remaining layout compared
before and after the removal. Changes to any retained partition stop the batch.
Windows partition renumbering is supported because GUIDs remain authoritative.
Deletion is sequential and cannot be rolled back. A failure may leave some
partitions deleted; there is no automatic retry. Success, failure and cancellation
invalidate the previous UI snapshot. A successful refresh selects the largest
eligible released region, including when the earliest selected partition was
too small. At least 64 GiB of contiguous space is still required.

## Local preview and validation

`BoilerInstaller.exe --steamos-reinstall-preview` opens an explicitly labeled
synthetic Windows + SteamOS layout in rehearsal mode, without automatic disk
enumeration. Its deletion flow simulates snapshot changes. The local
`Boiler SteamOS Reinstall Preview.lnk` opens this view. Developer mode also has a
SteamOS reinstall example button. Rehearsal mode cannot delete real partitions.

- Final Debug and Release builds: zero warnings/errors.
- New WPF and snapshot checks: 65 assertions passed in Chinese and English at
  1060x720 and 640x360 DIPs; four nonempty app-rendered captures. This covers
  exact-layout recognition/refusals, protected and unrelated partition exclusion,
  selection/reset/language behavior, confirmation consent, simulation and
  released-space reselection. Rendered captures are separate from physical
  keyboard/touch and OS-scaling acceptance.
- Existing partition picker/format/single-deletion checks: 112 assertions passed.
  The navigation assertion was updated to the current Destination → Boot options
  route; the old assertion still expected the superseded Destination → Source
  route. The test now explicitly opens Destination before exercising it.
- Existing helper request binding checks: 149 assertions passed. These use safe
  validation failures, without physical disk writes.
- PowerShell pure/native-byte and mocked batch checks: 28 checks passed in both
  PowerShell 7 and the actual backend's Windows PowerShell 5.1. They cover layout
  recognition, native byte bounds, swapped identities, retained partition
  mutations, renumbering, zero-write preflight, sequential rechecks and stopping
  after partial failure/concurrent mutation. All real disk queries/removals are
  blocked in these checks.
- Fresh-context review found a Windows PowerShell 5.1 JSON-array enumeration
  issue; it was corrected and the reviewer independently confirmed all 28 checks.
- Bundled script syntax/dependencies and the source whitelist were checked using
  Windows PowerShell 5.1. New application sources, GPT role policy and scripts are
  explicitly included for future source export/installer packaging.

Evidence is recorded locally under `logs/steamos-reinstall-*`,
`work/steamos-reinstall-final-qa`, and `work/steamos-partition-*`. No physical
partition deletion, full reinstallation, reboot or hardware acceptance was
performed. No public source push or installer/maintainer release was made.

## Delivery checklist

| Item | State |
| --- | --- |
| Update list and acceptance limits | 完成：recorded here for 0.1.0-preview |
| Windows installation entry/scripts and source whitelist | 完成：Destination, native identification, guarded batch deletion, confirmation and local preview synchronized |
| Component IDs/options/catalog, guest integration and component payload manifest | 不适用：host-side partition management adds no guest component or payload; existing component IDs/defaults/dependencies remain applicable |
| Version/build identity | 完成：0.1.0-preview retained; fresh local Debug/Release binaries, no published attachment overwritten |
| Targeted source/interaction/security checks and independent review | 完成：within the synthetic and mock boundaries above |
| Linux maintenance release and published maintenance upgrade verification | 不适用：the Linux maintainer does not manage host Windows partitions; its runtime/signing/payloads are unaffected |
| Windows full package/public release/public download verification | 阻塞：explicit current Boiler scope defers this work; when resumed, export the final allowlisted source and build a fresh timestamped package, then perform release/hardware gates; existing ZIPs are not updated |
| Physical reinstallation/reboot acceptance | 阻塞：no device changes are authorized by the current local-preview scope; requires separately scoped device acceptance |
| README version-only synchronization | 不适用：no version change; READMEs untouched |
| Final local delivery audit | 完成：local launchers, source/script inventory and evidence synchronized; deferred stages are not reported as released/accepted |
