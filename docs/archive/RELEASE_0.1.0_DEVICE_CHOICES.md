# Boiler 0.1.0-preview — device support entry

## 2026-10-25: inline selection and hardware detection

- The left card now contains the model dropdown, automatic detection and a local
  package file picker. The verified directory loads on this page. Detection
  matches the current computer's manufacturer and model against the directory's
  exact DMI hints; it never grants package trust or executes package code.
  Manual selection remains available when no unique match exists, including when
  preparing an installation for a different computer.
- A local file is inspected before session trust and package selection. A late
  directory response cannot replace an imported, dropped or selected local
  package. Re-entering the route removes the old inline picker.
- Common components (Decky and the generic maintenance tool) appear before
  firmware. Graphics, audio and USB driver supply is explained as part of
  SteamOS and its kernel; additional model support belongs in a model package.
  This update adds no new driver payload or supported hardware claim.
- The firmware select-all control is removed. Firmware remains unselected by
  default. A separate button reads present Windows PnP devices in the background
  and matches Wi-Fi and Bluetooth brands independently, using PCI/USB hardware
  IDs and device fields. Ethernet, software devices, ambiguous brands and
  unrecognized radios do not produce guessed selections. Manual choices for an
  unrecognized transport are kept. Feedback identifies that this is the computer
  running the installer, which may differ from the installation target.
- Detection shows progress. Manual changes, a mode reset, leaving installation
  options, returning home and a newer request invalidate an earlier result.
  Entering review or starting installation cannot later acquire firmware from an
  outstanding read. A device package still hides and clears generic firmware.
- The source whitelist includes the new read-only hardware detector. Component
  IDs, defaults, firmware locks, download policy, guest deployment, model packages
  and Linux maintenance behavior are unchanged. Firmware stays metadata-only in
  the installer and is downloaded only for selected missing image files.

Validation on the final local source: DeviceSelectorQa built with zero warnings
and errors; 96 device-route, 64 wireless, 33 device-package, 25 input-association
and 93 protocol/general checks passed. Python discovery passed 249 tests with 29
environment-dependent skips. Read-only native enumeration inspected 205 devices
on this Windows host; there were no supported physical radios to validate. Both
languages and 1060/640 pixel layouts were checked. Independent reviews identified
late-directory and cross-page detection races; both were corrected and covered
by delayed-result regressions. Evidence is in `logs/device-selector-*-final.txt`
and `logs/device-choice-20261024T195257343/result.json`.

### Current delivery checklist

1. 完成: this version record describes the actual behavior and validation limits.
2. 完成: host interaction, selection validation and source whitelist are in sync.
   不适用: new component/maintenance options and model package changes; none are
   introduced by this host UI and hardware recommendation update.
3. 完成: product remains 0.1.0 / 0.1.0-preview; the local package uses a fresh
   timestamp, exact independent source snapshot commit and build inventory.
4. 完成: source tests, interaction checks, native read-only smoke and independent
   state/concurrency review. 阻塞: real Wi-Fi/Bluetooth chip acceptance requires
   a Windows computer with supported radios; synthetic checks do not establish it.
5. 不适用: no Linux maintenance payload or release changed.
6. 不适用: no public maintenance revision or device upgrade is part of this host
   interaction change. Existing new-stack physical release gates remain open.
7. 完成: runnable local preview and fresh full local preview packaging; its exact
   source, file hashes and integrity result are recorded under
   `dist/device-selection-20261025/`. 阻塞: public release remains behind the
   existing signed catalog/model and new-stack hardware acceptance gates; this
   local ZIP is not a public or hardware-accepted release.
8. 不适用: no version change, so README version references are unchanged.
9. 完成: local delivery checked across UI, source export, final executable and
   package inventory. Public release and physical acceptance are not claimed.

## Earlier device-entry update

Local UI development update, 2026-10-25. Versions remain 0.1.0 / 0.1.0-preview.
Public release, full installer packaging and device changes remain deferred by
the current interaction-development scope.

## Behavior

- The custom and Vanilla home entries first open a separate device-support
  level, before System source. It has no sidebar. The left card chooses a device;
  the right card chooses generic installation. Both reuse the existing home
  entry styling and stack at narrow widths.
- A white, outlined, bold Back to parent menu button occupies the upper left.
  It returns from the cards to the home menu, or from device selection to the
  cards. Choosing a route opens System source; the wizard sidebar begins there.
- Source verification leads to Installation options. Back from options returns
  to source; Back from source returns to the device-support level. Local IMG and
  paired-image entry routes continue directly to source.
- Dropping one device package shows its component preview immediately. Preview
  controls remain read-only until explicit session trust and complete validation.
  The selected package supplies the editable device component options after the
  source step. Generic Wi-Fi/Bluetooth options are hidden and cleared for device
  routes. Explicitly returning to generic installation starts with empty wireless
  selections. Common Decky/maintenance choices are preserved.
- An untrusted replacement preview cannot continue using an older selected
  package. Both the page action and final install action enforce this condition.
  Clearing a replacement preview preserves the prior valid selection.
- Protocol stdout/stderr now explicitly decode UTF-8, matching the Python
  protocol's declared encoding. Chinese package metadata no longer breaks JSON
  parsing on systems whose default console code page differs.
- Firmware remains on demand. This UI update adds no firmware binary libraries
  and does not change the pinned firmware catalog or image integration payload.

## Validation

- DeviceLayerQa build: zero warnings and errors. Native local preview launcher:
  `BoilerInstaller-device-layer-qa.exe`. `--device-choice-preview` opens the
  separate entry level in rehearsal mode with automatic disk reads disabled.
- Device-choice checks: 72 assertions passed, including both languages, 1060 and
  640 pixel window widths, hidden sidebar, parent navigation, source/options
  ordering, trust boundaries, component preservation, final entry refusal and
  unchanged local-image routes. Eight non-empty captures passed visual review.
  Evidence: `logs/device-choice-20261024T192122618/result.json`.
- Existing input-association checks: 25 passed; device checks: 31 passed;
  wireless options checks: 32 passed. Process I/O checks: 11 passed.
- Independent state/route review completed; identified issues were corrected and
  verified. Source/slot inspection in route tests uses a synthetic prevalidated
  fixture; this does not establish real image, device or cross-integrity Explorer
  gesture acceptance. No scripts from imported packages were executed.
- Earlier captures failed with empty pixels, including a solid rendering probe.
  They were recorded as failures. Final captures above are non-empty and were
  inspected; the earlier empty images were not reused as evidence.

## Task delivery checklist

1. Complete: behavior and validation recorded here.
2. Complete: Boiler UI, input association, protocol decoding and source export
   whitelist synchronized. Other installer/maintenance catalogs are not
   applicable: this change affects only the Boiler Windows UI route and has no
   new component ID, deployment action or maintenance option.
3. Not applicable: no public version or component version change.
4. Complete: targeted interaction checks, local compilation, visual review and
   independent state review. Physical/new-image acceptance remains out of scope.
5. Not applicable: maintenance publication is explicitly deferred.
6. Not applicable: public maintenance download and physical upgrades are deferred.
7. Complete: local preview executable refreshed. Full installer ZIP and public
   release are explicitly deferred; this is not a release-ready installer claim.
8. Not applicable: README version references remain unchanged.
9. Complete: local UI delivery checked against the authorized scope above.
