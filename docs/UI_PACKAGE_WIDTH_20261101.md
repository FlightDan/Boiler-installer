# Device import layout follow-up

This follows `UI_IMPORT_NAVIGATION_20261101.md` and records the current local UI candidate, not a public release.

## Change

The device-package selector now occupies one full content row instead of sharing a two-column grid with a tall generic-installation tile. Its border, 12 DIP corner radius, 20 DIP padding and 16 DIP separation match the official recovery source card. Generic installation is a compact secondary row. Routine directory/provenance information is collapsed; a directory failure exposes its retry action automatically. The duplicated inline package-use button is removed, leaving the fixed wizard footer as the continuation action.

The shared page scroller reserves its vertical scrollbar lane so showing or hiding a scrollbar cannot change card width between wizard pages. Sidebar and footer remain in place. Explicit local imports still select the package automatically and default its adaptation items to checked.

## Acceptance

- Windows Release build: complete, zero warnings/errors using the locked build script.
- UI regressions: complete, 387 checks across `device-choice-checks`, `model-catalog-checks`, `boiler-device-checks`, `input-association-checks` and `source-card-checks`. Evidence: `logs/ui-width-*-20261101.txt`.
- Width acceptance: complete. Chinese and English, at 1060×720 and 640×480, compare the actual card X coordinate, width and padding against `RecoverySourceCard`; all four comparisons pass within one DIP.
- Visual acceptance: complete for actual synthetic WPF windows. Final PNGs are in `work/ui-package-width-20261101-final/`; desktop Chinese and narrow English captures were inspected. No physical device write or installer execution is established by these screenshots.
- Independent review: complete. Narrow English wrapping and async footer enablement reviewed; retry visibility corrected and single-click package-to-source navigation verified.
- Maintenance/runtime/format migration: not applicable. No payload, recovery, request, receipt or persisted-format changes.
- Package inventory/signing/download/public release: not applicable. Local application rebuild only; no release attachment changed.

## Previous source-suite result

The earlier complete Python source run has now finished: 693 tests, one failure, five errors and 74 skips (`logs/ui-flow-python-tests-20261101.txt`). The failure was a stale source-text assertion requiring the old unchecked UI policy. It is replaced by the existing real empty-selection staging check plus the WPF default-selection and opt-out regressions. The affected `test_x2_shared.py` module was rerun: 36 tests, OK, nine skips (`logs/ui-width-x2-selection-20261101.txt`).

The five other errors remain outside this UI patch: one whole-project fixture copy error involving retained local history, two missing vendored parser fixture files, and two permission failures creating keyboard test fixtures. Historical inputs and ACL-denied entries remain preserved. The complete Python suite has not been rerun, and is not represented as passing. The earlier comprehensive `boiler-checks` disk-space blocker (125 GiB required, approximately 19 GiB available) also remains.
