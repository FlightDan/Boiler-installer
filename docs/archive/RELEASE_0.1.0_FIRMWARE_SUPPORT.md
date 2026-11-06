# Boiler 0.1.0-preview — wireless firmware support

Local development update, 2026-10-25. This is not a public release or hardware
acceptance record. The existing project/display versions remain 0.1.0 and
0.1.0-preview; README version references therefore need no change.

## Behavior

- Generic configuration in the custom and Vanilla routes offers six independent,
  default-off firmware selections: Intel, Realtek and MediaTek Wi-Fi and Bluetooth.
- Selecting an authenticated device package hides, disables and clears this group.
  Returning to generic configuration enables the empty group; previous choices
  are not restored. Merely inspecting an imported package or granting session
  trust does not select it. Local IMG and paired-image routes do not offer the
  group. Core Decky/maintenance choices are preserved when device selection changes.
- Serialization filters hidden values. Both the Windows selection validator and
  image integration validator reject device packages combined with hand-selected
  generic wireless firmware, including the legacy `wireless` input channel.
- Firmware selection counts now update after each checkbox change. English and
  Chinese labels distinguish Wi-Fi from Bluetooth and ask for the wireless chip
  vendor rather than the CPU vendor.

## Firmware and maintenance

All six firmware catalogues use fixed linux-firmware revision
`2ab4ce216003ee2c2396c69e19455e4379b0e1f0`. Bluetooth includes 126 Intel files
(38 aliases), 45 Realtek files (11 aliases), and 8 MediaTek files in its metadata.
These counts describe available candidates, not installed or bundled binaries.
The locks and integration manifest retain their original source, aliases and
license records. When needed, aliases are materialized as ordinary files and
their source bytes are checked against Git object identities, sizes and SHA-256.

Only the target image's existing vendor driver helpers justify a Bluetooth
selection. No replacement kernel or `.ko` payload is supplied. Existing regular,
compressed and valid image-confined linked firmware is preserved. Unavailable
paths and runtime-selected filenames are recorded explicitly. Bluetooth remains
`runtime_dependent` / `not_validated` until checked with actual controller
hardware; installing candidate files does not prove the device works. Board
specific Realtek configuration families remain outside the generic allowlist.
The fixed source does not establish MT7927 Bluetooth coverage.

The active `cache/integration` contains metadata for the six firmware components,
the existing core resources and the current 14-file generic maintenance runtime.
It has 18 physical inventory files and **zero firmware binary files**. Full
firmware preparation is restricted to the explicit development-cache flag; that
cache is kept outside the active directory and is rejected by the packaging
verifier. No development firmware library is part of the installer delivery.
The manifest-driven
maintenance engine records, verifies and restores the new component identities
without a separate device-specific adapter. The installer packaging verifier
requires all eight component identities, checks each firmware metadata inventory
and source/alias evidence against its reviewed lock, and rejects any extra
firmware bytes in the production inventory.

Before applying selected wireless components, the host runs a separate read-only
VM pass against the final A/B image. It keeps existing firmware and generates a
per-slot missing-file plan. The host validates that plan against the current
selection and catalogue, downloads only the union of those missing records and
the necessary redistribution notices, and verifies every file against its fixed
origin, SHA-256 and Git blob. Equal contents and aliases share a SHA-addressed
cache. Core resources are copied only when selected. The private payload retains
the full metadata but contains only its planned bytes.

Immediately before any target write, integration recomputes the slot plan and
rejects changed options, metadata or native firmware state. Unselected components,
existing files and the graphics/SOF diagnostic list cause no firmware download.
Bluetooth remains a vendor candidate-family supplement selected by the user;
offline inspection does not identify the exact controller or prove its operation.
There is no implicit first-boot download service.

No AMD/Intel graphics or SOF payload has been added merely because its vendor
exists. The image audit below determines the current kernel/firmware boundary.

## Verification

- Independent review found missing WHENCE/Git symlink aliases in the initial
  Bluetooth preparation. The fix resolves bounded aliases inside the reviewed
  filename families, rejecting cycles, path escapes, cross-family targets,
  missing targets and conflicting destinations. The corrected preparation was
  used to generate the final locks and source/alias evidence.
- Full Windows Python source suite after the on-demand change: 248 tests,
  219 passed and 29 platform skips. After the final exact-path optimization,
  the 18-test wireless suite also passed with elevated temporary-directory link
  support, including all three native-link tests and the directory-leaf test.
- WPF selection validation: 32 checks passed. Actual synthetic package import,
  package selection and trust-revocation interactions passed. These do not execute
  the package against a physical device.
- WPF layout/focus suite: 1,363 checks, 40 layouts, 64 focus traversals, 9 rendered
  captures, zero failures. Covers Chinese/English and four window sizes. The
  wireless group is checked against the route. The first visual inspection of
  Chinese and compact English captures found no changed-label overlap.
- FirmwareQa build: zero warnings and zero errors. Impeccable detector: no
  findings in the changed production WPF sources.
- Final active integration: metadata-only production inventory, 18 physical files
  and zero firmware binaries. The production packager verifies the exact core
  inventory, all firmware locks and their alias evidence. Manifest SHA-256:
  `2a42c95eec0d1eaf34c9897b026fb3609b956039bb6a1085cc2e51ce795b8873`.
- On-demand download tests cover one missing file, native compressed firmware,
  an empty selection without a VM pass, selected core resources, cross-run SHA
  reuse, aliases, changed selections/records/targets/origins, hash errors and
  unpublished-output cleanup. A separate review verified the union of different
  A/B missing files without copying another candidate from the same catalogue.
- Independent review found an empty-selection slot-plan mismatch and verified
  its fix. Host-side cleanup now removes the owned private staging directory
  after forced cancellation. A forced kill may leave an unreferenced temporary
  file in the shared SHA cache; temporary names are never accepted as cache hits
  or installer payloads.

Local evidence is in `logs/firmware-on-demand-catalog-20261025.json`,
`logs/firmware-on-demand-python-suite-20261025.txt`,
`logs/wireless-on-demand-options-checks-20261025.txt`,
`logs/wireless-device-checks-20261025.txt`, and
`logs/wireless-on-demand-layout-checks-20261025.txt`. Earlier full-cache reports are
development evidence and do not describe the final delivery inventory.

## Image audit

The existing 3.8.14 prepared A/B image uses build `20260707.10`, kernel
`6.16.12-valve24.4-1-neptune-616-gfe145653a794`. Both the 3.10 recovery image and
the direct `rootfs.img` use build `20260924.1000`, kernel
`7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9`; their coverage results agree.

| Static module/firmware check | 3.8.14 prepared image | 3.10 recovery and rootfs |
| --- | --- | --- |
| AMD `amdgpu` | Driver present; 583 declared paths, 37 missing | Driver present; 637 declared paths, 91 missing |
| Intel `i915` | Driver present; 45 declared paths, none missing | Driver present; 47 declared paths, 2 missing |
| Intel `xe` | Driver present; 30 declared paths, none missing | Driver present; 35 declared paths, 2 missing |
| SOF | 25 modules present, 416 topology files | 26 modules present, 483 topology files |

The two Intel missing paths in 3.10 are `i915/xe3lpd_3002_dmc.bin` and
`i915/xe3p_lpd_dmc.bin`. They are shared DMC requests, so they are counted
once in the image-wide total: 37 unique missing paths in 3.8.14 and 93 in 3.10.
These are declarations across all supported chips, not a diagnosis of the
computer running the Windows installer. No hardware identity was inferred.

The audit handles compressed firmware and resolves native links inside the
guest image namespace. The SOF files and modules are present, but those modules
have no static `firmware=` properties; a zero static missing count does not prove
that every hardware-selected DSP image, codec configuration or topology exists.
Actual audio routing and controller operation require a hardware boot check.

All SteamOS source devices were opened read-only and mounted without filesystem
journal replay; their source size and modification time remained unchanged.
Full lists and the audit method are retained under
`work/firmware-audit-20261025/summary.json` and `summary.md`. The initial 33 Linux
synthetic transaction checks and subsequent 51 on-demand tests passed. The latter
records exact copied-source hashes and predates the final exact-path lookup
optimization and guest progress messages; those changes have separate native
link regression tests and real-image verification. These checks do not boot a
SteamOS desktop or modify any physical device.

The Windows-to-QEMU read-only planning route passed on the existing 3.8.14
prepared image. Selecting only Intel Bluetooth preserved 99 native files per
slot and planned 27 missing files per slot, with no downloads or image writes.
The first run exposed repeated directory enumeration for exact names; direct
image-confined path lookup removed that cost. Independent review also found
and corrected a directory-leaf mismatch between exact and wildcard lookup.
Evidence: `logs/wireless-real-image-plan-20261025.json` and
`work/firmware-audit-20261025/linux-on-demand-tests.json`.

## Delivery scope and checklist

| Required item | Result for this update |
| --- | --- |
| Update list and acceptance boundary | Complete: recorded here, not in README. |
| Installation options, execution and component inventory | Complete for the independent Boiler project; the separate OXP3 installer is unaffected. |
| Fresh payloads and maintenance synchronization | Complete: metadata-only firmware catalogue and current generic runtime; production inventory rejects firmware binaries. |
| Version/build identity | Complete: local preview only, no public version/tag or existing release asset overwritten. |
| Source, integration and UI verification | Complete locally: source tests, Linux transactions/download tests, UI checks, read-only image audits and real-image planning. Hardware boot acceptance is separate. |
| Signed maintainer Release and public download verification | Blocked by the explicitly deferred release stage; would require a new signed release and public verification after that stage resumes. |
| Physical upgrade/reboot/recovery acceptance | Blocked by the deferred device stage; no device changes are part of this UI/local-development task. |
| Full Windows package/Release | Blocked by the deferred release stage; no full ZIP or public release was created. |
| README version references | Not applicable: project/display versions did not change. |

The new local executable is `BoilerInstaller-wireless-qa.exe`, using the
`FirmwareQa` build directory. A previously running Debug installer was left open;
its loaded executable is not counted as validation of this build. No commit,
public source export, signing, upload, system installation or research-code
distribution was performed.
