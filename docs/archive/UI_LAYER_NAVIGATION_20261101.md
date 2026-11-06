# First-layer entry and second-layer installer navigation

This supersedes the first-layer layout and numbering described in `UI_PACKAGE_WIDTH_20261101.md`. Historical screenshots and records are retained.

## Accepted hierarchy

- First layer, first page: Choose device support. Restore the original symmetric wide layout: device selection/local package import on the left, generic installation on the right. At narrow sizes the cards stack. This entry screen does not consume an installation step number or show the installer sidebar.
- Second layer: 01 System source, 02 Installation options, 03 Destination, 04 Boot settings, 05 Review, 06 Progress, 07 Result. The sidebar starts with System source; device support is not an additional numbered entry.
- The device-card action prepares a selected directory package or continues with an already selected package. A successful explicit local package import still selects the package automatically and enters System source, with package components initially checked. No source-trust or extra Use confirmation is reintroduced.
- Back from System source returns to the first-layer cards and preserves the package and its edited options. Continuing an already selected generic branch preserves all option choices. Back from the first layer returns to installation modes.
- First-layer navigation is contained in the cards and top Back button. The second layer keeps its fixed footer and shared content width. Its first Back button explicitly says Back to device support.

## Verification and delivery boundary

- Windows Release build: complete, locked build script, zero warnings/errors.
- UI regressions: complete, 397 checks across `device-choice-checks`, `model-catalog-checks`, `boiler-device-checks`, `input-association-checks`, `source-card-checks`; current evidence is in `logs/ui-layers-*-20261101.txt`.
- Geometry: wide cards must have equal actual widths/heights and share a row. Narrow layout must stack without horizontal overflow. Chinese and English are checked at 1060×720 and 640×480.
- Navigation checks: first-layer/sidebar separation; second-layer 01/02/07 labels; source-to-entry Back; retained local package continuation; retained generic options on re-entry; local import defaults and removed redundant confirmations.
- Screenshots: complete for synthetic native WPF windows; wide first/second layers and narrow English entry inspected. No physical device, firmware, partition mutation or actual installation is part of this verification.
- Independent review: complete. Its stale uncommitted model-candidate finding was fixed in generic continuation and covered by the option-preservation/candidate-reset regression.
- Maintenance, runtime payloads, plan/helper/receipt formats and persisted recovery state: unchanged; no migration or runtime inventory rebuild applies.
- Public packaging, signing, downloads and hardware acceptance: not applicable to this local UI rebuild. No public attachment is replaced. Earlier comprehensive Python fixture/permission errors and the full Boiler disk-space blocker recorded in `UI_PACKAGE_WIDTH_20261101.md` are not represented as resolved by this patch.
