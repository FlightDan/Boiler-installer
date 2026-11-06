# Graphics detection and selection UI, 2026-10-25

The generic installation options now include an integrated-graphics firmware
section with local hardware detection and a manual single-profile choice.
Radeon 780M/880M, the seven documented Ryzen Z1/Z2 variants and Intel Arc
G3/G3 Extreme are read from the existing candidate catalogue. The default is to
keep the image's graphics firmware. The section and review summary explicitly
label these choices as preview only. SteamOS continues to supply the actual
graphics driver; no GPU installation or download has been enabled.

Detection is an explicit button action. It reads Windows PCI display devices
and, for Z variants, the processor product name. It automatically selects a
single confirmed candidate. Ambiguous or unknown results and native read
failures preserve the current manual choice. Shared AMD PCI IDs do not identify
780M/880M by themselves. RX/PRO/discrete devices cannot stand in for a Z
processor's integrated GPU. This computer may differ from the installation
target; the UI asks the user to check the result.

Native enumeration runs in the background. Request, context and selection
generations prevent a late result from overwriting edits, reviving a selection
after a mode/package change, or modifying the reviewed plan after navigation.
Language refresh preserves choices and does not start discovery. A device
package hides, disables and clears the generic graphics selection. Imported
and local-image modes cannot submit it.

`SelectedOptions.GraphicsProfile` carries the preview choice to the review
screen and is omitted when null. Host validation rejects unknown profiles and
package/mode conflicts. The installation controller refuses a graphics preview
before opening an installation session; Python integration independently
refuses non-null `graphics_profile`. GPU identities remain outside selectable
firmware payload components. Nothing is silently downloaded or written to an
image. The installer continues to carry zero firmware binary files.

## Verification

- Final GraphicsQa build: zero warnings and errors.
- Graphics UI/matching: 58 checks, including exact model suggestions, all Z
  variants, duplicate devices, wrong classes/vendors, conflicting names,
  external discrete GPUs, manual edits and late results, package/mode reset,
  review text, dropdown access and deployment refusal.
- Existing wireless checks: 64 passed. System-preset checks: 176 passed.
  Device/generic/package routing checks: 96 passed.
- Two bounded visual passes inspected Chinese and English at 1060x720 and
  640x360. The first pass found overly long model labels and misleading initial
  status after manual selection; the final labels fit and status agrees with
  the selected model. Four final graphics captures are retained.
- Independent review found and rechecked the discrete-GPU/Z ambiguity and stale
  post-detection selection message. Final review found no remaining material
  issue. Impeccable's mechanical detector reported no findings in the changed
  graphics interface and subtitle sources.
- Final Python regression: 255 tests, 226 passed and 29 platform skips.
- Fresh integration preparation matches the active 18-file inventory exactly,
  including the current maintainer runtime; firmware binary count remains zero.
- Both `BoilerInstaller.exe` and `BoilerInstaller-interaction.exe` match the tested
  GraphicsQa launcher. The existing UIUX Preview shortcut retains its original
  target and arguments and now reaches the updated interaction launcher. Prior
  launcher files are preserved in `work/graphics-ui-entry-backup-20261025`.
- The explicit source export contains 351 files and builds independently with
  zero warnings/errors. Existing source-card partials referenced by the current
  application were also retained in the whitelist for dependency completeness;
  this task does not claim authorship of those separate source-card changes.

Evidence: `logs/graphics-ui-build-20261025.txt`,
`logs/graphics-ui-graphics-options-checks-20261025.json`,
`logs/graphics-ui-wireless-options-checks-20261025.json`,
`logs/graphics-ui-system-preset-checks-20261025.json`,
`logs/graphics-ui-device-choice-checks-20261025.json`, and
`logs/graphics-ui-delivery-20261025.json`.

## Delivery checklist

| Item | Status |
| --- | --- |
| Update record and source inventory | Complete: recorded here and added to the explicit export whitelist. |
| Installer options, model selection and review | Complete for local UI preview; graphics deployment remains unavailable. |
| Fresh payload and maintainer synchronization | Complete: regenerated integration matches the active metadata-only inventory and current runtime; no GPU component or maintainer operation enabled. |
| Local preview entry and source export | Complete: both launchers updated with backups; existing shortcut works through its unchanged target; clean export builds. |
| Build and interaction verification | Complete locally as above; no claim of physical GPU acceptance. |
| Actual GPU download/install support | Blocked by reviewed per-profile firmware locks, target-kernel evidence and early-boot integration/acceptance. |
| Public maintenance/Windows archives and release | Deferred under the existing local-preview scope; no release or physical-device change. |
| Version/README | Not applicable: 0.1.0 preview remains unchanged. |

## Searchable model dropdown, 2026-10-26

The model dropdown now opens a search field above the candidate list. Filtering
matches the GPU/processor label and profile identity, ignores case, and supports
multiple whitespace-separated terms (for example `7840U` or `Z2 Extreme`). An
empty-result message explains how to recover. Typing and dismissing the list
preserve the selected installation model; reopening clears the filter. Enter
confirms a search result, an empty-query Enter retains the current model, Escape
cancels, and arrow keys enter native list navigation. The nested editor retains
space and editing shortcuts. Labels wrap within both the closed field and list.

The Detect this computer's graphics button is now on the right of the model
field in the same row. Detection matching, deployment policy and firmware
delivery are unchanged by this UI refinement. This is a Windows-only local
interaction update to the existing 0.1.0 preview.

Verification and delivery:

| Item | Status |
| --- | --- |
| Update record and source export inventory | Complete: this follow-up and the 0.1.0 release record describe the change. All five modified source/document paths already belong to the explicit whitelist. |
| Installer/component/maintenance synchronization | Complete: model identities, selection serialization, detection and deployment paths retained. No component catalog, option IDs, guest scripts or maintenance runtime changes are needed for this Windows control. |
| Payload/manifest regeneration | Not applicable: changed files are the WPF control/template, its checks and documentation; no integration or maintenance payload input changed. |
| Version/README | Not applicable: still 0.1.0-preview, no public version change. |
| Build | Complete: final GraphicsSearchQa compilation has zero warnings/errors. |
| Independent review | Complete: fixed empty-query Enter resetting the selection; final review found no remaining scoped issue. Arrow-key focus transfer intentionally uses native list navigation. |
| Interaction/layout checks | Complete: 100 WPF assertions, eight nonempty captures, no capture errors; Chinese/English at 1060x720 and 640x360. Includes search focus, processor and multiword queries, no results, preserved selection, empty Enter, result Enter, Escape, spaces and button position. |
| Direct desktop input | Blocked: Computer Use reports `GetCursorPos failed: access denied (0x80070005)`. No actual injected desktop typing/click acceptance is claimed. Programmatic WPF routed-key tests and rendered captures passed. |
| Local preview entries | Complete: BoilerInstaller.exe and BoilerInstaller-interaction.exe match the tested GraphicsSearchQa launcher. Existing UIUX Preview shortcut retains its target and arguments. Prior entries are retained in work/graphics-search-entry-backup-20261026. The old already-open source-card window was preserved. |
| New-install/device acceptance | Not applicable to this UI-only refinement; no image or physical-device changes performed. |
| Signed maintenance/installer archives, public download and upgrade verification | Not applicable under the existing local UI-preview scope; packaging/publication remains deferred. |

Evidence: `logs/graphics-search-build-20261026.txt`,
`logs/graphics-search-checks-20261026.json` and
`logs/graphics-search-delivery-20261026.json`.
