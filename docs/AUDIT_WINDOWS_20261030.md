# Windows installer audit — 2026-10-30

This records the Windows/app portion of the split-project audit. It is not physical-device acceptance or a claim that every possible defect has been excluded. All executions used owned ordinary files, in-memory firmware, synthetic WPF events, loopback HTTP, and harmless child processes. No physical disk write, NVRAM change, restart, release, or remote image download was performed.

## Confirmed findings and fixes

### W1 — P2: rejected/cancelled source snapshots retained untracked image data

`BoilerImage.SnapshotAsync` created a new image before observing a cancelled token and had no failure cleanup. The installation controller creates its session journal only after this copy, so a cancelled copy could consume substantial workspace without an available recovery session. A completed copy rejected by the reviewed SHA-256 check had the same cleanup omission.

Reproduction: call `SnapshotAsync` on an owned synthetic image with an already cancelled token; the original implementation threw while retaining a source image. The before-fix assertion is retained in `logs/windows-audit/before-snapshot-cleanup.txt`. The controller's source-digest rejection is also covered by an ordinary-file regression.

Fix: check cancellation before creating the snapshot; on copy failure remove only an output whose `CreateNew` succeeded. Remove the owned completed snapshot when its reviewed digest is rejected. Successful snapshot bytes and SHA-256 binding are unchanged.

### W2 — P2: failed image export prevented retry to the same destination

`BoilerImage.ExportAsync` left its `.partial` ZIP when image writing, metadata serialization, cancellation, or publication failed. Its entry guard then rejected every retry to that destination.

Reproduction: use a valid synthetic recovery image and inject an exception on metadata's second serialization (after ZIP/image output creation). The old implementation retained the partial file; evidence is in `logs/windows-audit/before-export-cleanup.txt`.

Fix: remove only a partial file created by this invocation after failure; preserve pre-existing files and use the existing no-overwrite publication. Regression tests verify cleanup, a successful same-destination retry, and preservation of an unowned pre-existing partial.

### W3 — P2: firmware registration could never retry after a clean rollback

Physical recovery calls `FirmwareBoot.Register` in the original deployment directory. `RegisterCore` always created `firmware-before.json` with `CreateNew`. A transient BootOrder write failure rolled back the firmware but retained this receipt, causing all later recovery attempts to fail before registration.

Reproduction: the in-memory firmware store accepts a new entry, rejects the first BootOrder write, and observes normal rollback. A second registration in the same directory then fails with an existing-receipt exception. Evidence is in `logs/windows-audit/before-firmware-retry.txt`.

Fix: retain the receipt format. A retry can archive the prior receipt only after validating its size (at most 256 KiB), ordinary workspace path, Boot6000–Boot6FFF name, exact current ESP load option, original order and attributes, requested default choice, recorded resulting order, absent chosen entry, and absent success result. The unchanged registration path then creates a fresh receipt before firmware writes. Existing production session and physical-disk leases cover this operation. Prior receipt bytes are preserved in a unique `firmware-before.retry-<id>.json` file.

Recovery remains deliberately blocked for changed order/attributes, a leftover entry, a success result, a changed ESP/choice, or an invalid receipt. These states need explicit inspection rather than an automatic boot-order overwrite. This audit did not add handling for them.

## Coverage and limitations

| Area | Files inspected | Evidence / boundary |
| --- | --- | --- |
| Helper request binding and target authorization | `app/HelperRequestBinding.cs`, `MediaHelper.cs`, helper dispatch in `App.xaml.cs`, helper portions of `InstallationController.cs`, `PartitionDeletion.cs`, `RemainingSpaceFormat.cs`, `ReviewedInstallationCli.cs` | Tampered/oversized/truncated/cross-purpose requests, retained Windows file-share leases, safe helper/PowerShell rejection tests. No successful destructive helper execution. |
| Physical selection and native target binding | `app/DiskPlanning.cs`, `PartitionSelection.cs`, `PhysicalInstallation.cs`, `PhysicalRecovery.cs`, `NativeMediaDevice.cs`, `NativeVolumes.cs`, relevant GPT/planning check code | Pure planning/GPT metadata and ordinary-file recovery comparisons. Hardware handles, volume locking, actual append/write and UEFI behavior remain unaccepted by this audit. |
| Snapshot, import, export and archive bounds | `app/BoilerImage.cs`, `BoilerExportStream.cs`, import/stream code in `Backend.cs`, `RecoveryImage.cs`, `InputDiscovery.cs`, `QcowReadOnlyGraph.cs`, `RawImageStaging.cs` | Synthetic IMG/ZIP/ZIP64/BZip2, parser differential, byte-budget/space/cancellation, source immutability, protected-content export tests. QEMU staging/backing code was inspected; a complete VM installation was not executed. |
| Source inspection, assignment and final request | `app/BoilerSourceInspection.cs`, `SourceFileDigest.cs`, `InputAssociations.cs`, `BoilerWorkflow.cs`, final-request and navigation sections of `MainWindow.xaml.cs`, request/controller validation | Full-file digest and source invalidation, input-assignment conflict/cancellation/binding, final request validation and larger-image target tests. These are functional WPF state tests; screenshot output in this environment was empty. |
| Downloads and component source handoff | `app/AriaDownload.cs`, `AdvancedDownloads.cs`, `FirmwareDownloads.cs`, official-source binding/staging/download sections of `OfficialSystem.cs`, package cache handoff in `BoilerCatalog.cs` and `Backend.cs` | Loopback aria2 pause/resume, changed remote identity, non-range refusal, publication recovery and UI source-selection tests. No claim of Valve signature authenticity beyond the existing trust model; no production server or complete remote image transfer test. Python/guest source verification is covered separately. |
| Persistence, logs, password transport and restart | `app/InstallerWorkspace.cs`, `InstallationRecovery.cs`, `InstallationLog.cs`, `DiagnosticLogExport.cs`, secure serial-input and process cleanup portions of `Backend.cs`, `InstallerRestart.cs`, `FirmwareBoot.cs` | Pure receipt retry/rollback guards; source logging, request-secret nonserialization, ANSI handling, process cancellation and failed log-sink cleanup tests. Password guest handshake and real restart were not executed. |
| Wider UI/component surface | Other app controls/options/graphics/wireless/home/adaptation files | Existing regression suites cover some of these areas; this Windows audit did not perform exhaustive line-by-line review of all WPF/component files. Linux components, protocol, build/release scripts, packages and project relocation have separate owners/reports. |

## Verification

Release build using the locked bundled SDK: passed, zero warnings/errors. Reproduce with `scripts/build-app.ps1 -Configuration Release`; run the built DLL with `runtime/dotnet/dotnet.exe app/bin/Release/net10.0-windows/OxpInstaller.dll --cli <suite>`.

| Suite | Result |
| --- | --- |
| `boiler-checks` | 101 checks passed; visual acceptance remains `blocked-empty-render` (home/custom/vanilla/imported screenshots). Includes five new owned-output regressions. |
| `physical-recovery-checks` | 30 events passed; in-memory firmware retry and all nine refusal cases, previous-receipt preservation, and full ordinary-file completed-prefix comparison. |
| `source-digest-checks` | 19 checks passed. |
| `input-discovery-checks` | 46 checks passed. |
| `input-association-checks` | 25 checks passed. |
| `input-binding-checks` | 5 checks passed. |
| `helper-binding-checks` | 149 events passed; detailed checks are in the path recorded by `logs/windows-audit/helper-binding-checks.txt`. |
| `native-volume-checks` | 18 pure parsing/validation checks passed. |
| `process-checks` | 11 checks passed, owned harmless child processes only. |
| `physical-planning-checks` | 63 pure planning/GPT checks passed. |
| `installation-log-checks` | 14 checks passed. |
| `aria2-checks` | 33 events passed; three loopback download tasks, full ordinary-byte comparison, cancellation/resume/publication and source-selection behavior. |

Per-suite outputs are retained in `logs/windows-audit/`; these local runtime evidence files are not release payloads. Installation-script/component-list synchronization, final payload rebuild, independent integration review, project relocation, signing, packaging and publication are handled by the primary audit/release task and are not asserted complete here. No component catalog change or receipt-schema migration is required by these app-only fixes.
