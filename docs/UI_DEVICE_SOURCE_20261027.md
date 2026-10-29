# Device picker and upgrade-source refinement, 2026-10-27

Local 0.1.0-preview update for the supplied Windows installer screenshots.

- The device dropdown has an in-popup search field for brand/model keywords,
  case-insensitive multiword filtering, keyboard selection and an empty-result
  message. Searching does not load or trust a package. Brand/manufacturer metadata
  precedes the localized model name; the verified advisory DMI manufacturer is
  used when there is exactly one manufacturer. Missing brand metadata is not
  guessed. Existing brand prefixes are retained without duplication.
- Using a verified selected device package opens System source directly. The
  separate package import/review screen is retained for external packages that
  need explicit source trust. Returning from System source opens the initial
  device/generic choice. Existing package verification and execution are unchanged.
- The aggregate device checkbox is named **机型适配修复 / Device compatibility
  fixes**. New packages start with their components selected; a user's opt-out
  survives revisiting the same package. **Boiler generic maintenance** defaults
  on in custom and Vanilla flows, and can be disabled. The component catalog,
  selection catalog and runtime selection agree. Complete imported images and
  local image pairs continue to inject no components.
- System/desktop language defaults to the supported Windows user UI language,
  independent of the installer language toggle; manual overrides persist.
  Unsupported Windows languages preserve the image language. Workspace appears
  only on System source. These existing behaviors are rechecked in this build.
- The source card is named **升级包 / Upgrade bundle**. RAUC manifest metadata
  supplies the declared version; it is preview information, not authentication
  or expanded install support. Raw rootfs images do not establish an OS version
  from their size/header. The existing installation preparation checks the real
  root filesystem. The downloader continues to identify its actual pinned
  3.10/build source; other OS/build installation gates are unchanged.

## Verification and delivery

Final build, UI checks, independent review, regenerated-payload inventory and
standalone package identities are recorded in the local delivery sidecar under
`logs/ui-request-delivery-20261027.json` after verification completes.

The WPF build passes with zero warnings/errors. Seven targeted native suites
pass 752 assertions, including source/image validation, device routing/search,
upgrade metadata and freshness, default selections, language and graphics-search
regression checks. Fifty app-rendered captures cover Chinese/English and wide/
narrow layouts; representative picker, source and options captures were visually
inspected. The mechanical design scan returns no findings. Targeted Python
integration/catalog/preset suites run 34 tests with three platform skips.

Independent review fixes cover ignored pending bundles, currently associated
sources taking priority, role changes refreshing source labels, and changed files
clearing version previews and tooltips. The source inspection result does not
cache a second copy of the version label. Native focus tests activate their
test window and wait for dropdown close events before checking restored state.

| Checklist item | Status |
| --- | --- |
| Update record and affected entry points | Complete: this record and RELEASE_0.1.0.md. |
| New-install options/catalog/bilingual UI | Complete: core:maintenance defaults synchronized, existing IDs and execution contracts retained. |
| Legacy windows-installer / steamos-maintenance | Not applicable: separate legacy products; no legacy source or runtime changed. |
| Generic maintenance runtime/API/signing | Not applicable: maintenance runtime, ownership, update API and trust bytes unchanged; no new maintenance release. |
| Payload preparation | Complete: prepare-integration.py regenerated 18 files from current source and locked Decky; entire inventory and hashes equal the existing payload. |
| Version/README | Not applicable: still 0.1.0-preview; new local timestamp/build identity, no published version or asset replaced. |
| Source/UI/build checks and independent review | Complete locally within the scopes above; no installation or hardware acceptance implied. |
| Standalone Windows candidate and source whitelist | Complete local candidate: see timestamped package sidecar and final delivery record for exact source commit, size, hashes and inventory verification. |
| Public publication/download and physical upgrade/reboot/recovery | Blocked by existing Boiler production acceptance gates. This local UI build makes no physical-device acceptance claim. |

Source export and packaging use the explicit whitelist and an isolated checkout,
excluding workspace history, device records, credentials and unpublished research.
App-rendered WPF captures and synthetic key events are distinguished from live
desktop cursor input and physical installation testing.
