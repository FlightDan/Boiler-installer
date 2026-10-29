# Boiler 0.1.0 preview — functional integration

This local candidate connects system presets to the existing Windows installer
and managed Linux image deployment. Display version remains 0.1.0-preview.

## Changes

- System language, the initial deck password and SSH now reach the actual A/B
  image stage, including a request containing only presets. Both slot reports
  must contain `core:system-presets` before the controller permits disk writing.
- Language changes use `/etc/locale.conf` and KDE global defaults. The installer
  checks compiled libc locale data, KDE and the requested translation. It does
  not execute programs from the supplied image or download language packs.
- Shared home configuration remains outside per-slot transactions. Existing
  compatible user preferences are retained; conflicting or ambiguous user
  language overrides require changing the language in KDE instead.
- Passwords use the existing private serial input channel, are hashed inside
  the managed guest and never enter selection JSON, review text or logs. The
  private shadow transaction resets password age and rejects expired accounts
  or incompatible ageing policy. Original shadow bytes remain private.
- SSH adds an offline target dependency on the image's existing sshd service.
  Unchecked SSH preserves image settings. Service files and the deck login shell
  are checked; network connectivity and actual login need first-boot acceptance.
- Presets use the existing generic maintenance engine for file ownership,
  read-only verification, interrupted-operation recovery and explicit restore.
  No maintenance runtime API, trusted key or model package is changed.
- The main routes already invoke real image import/download, device packages,
  selected wireless downloads, boot configuration and disk deployment. Existing
  route regression checks now follow device support before system source and
  distinguish the single IMG picker from the batch association picker.

## Remaining boundaries

Graphics profiles remain unavailable for deployment. Their current catalogue
contains hardware identity candidates only, with no reviewed per-profile file
locks, target-kernel proof or transactional early-boot update path. The installer
continues to refuse a graphics selection before creating an installation session.
Wireless locks do not authorize GPU downloads.

Shared-storage injection remains unavailable in the generic installer. This
update does not change that separate feature's compatibility policy.

No physical disk was written, no test device was changed and no reboot or
hardware login/language acceptance is claimed. Generated language data and
deployed configuration do not prove a running Plasma session or SSH connection.

## Verification and delivery

Final results and candidate artifact identity are recorded in
`logs/functional-integration-delivery-20261025.json`. Source, Windows request
checks, native Linux transaction checks, image checks, payload equality and
archive verification are separate evidence categories.

- Final Windows source regression: 269 tests, 238 passed and 31 platform or
  environment skips. Independent source review found no remaining material issue.
- Windows preset request/secret/layout checks: 196 passed. Current-route WPF
  layout and focus regression: 1,386 passed. This session's rendering produced
  empty captures, so fresh pixel-level visual acceptance is not claimed.
- Native managed Linux integration: all 12 tests passed, including preset-only
  installation, private recovery bytes, restoration, modified-shadow refusal,
  maintenance coexistence and legitimate read-only locale hardlinks.
- Read-only resource checks accepted all nine selectable languages in both
  prepared 3.8.14 slots and the official 3.10 rootfs. Source image sizes and
  modification times were unchanged. These checks do not boot those desktops.
- A private sparse copy of the actual prepared 3.8.14 image passed Chinese
  language, password and SSH deployment to both slots through the real guest
  entry and secret channel. A subsequent separate read-only guest verified both
  slots. The source image and physical disks were not modified.
- Final source and isolated standalone Release builds have zero warnings and
  errors. Fresh integration preparation reproduces the active 18-file payload
  exactly, including the unchanged maintenance runtime and zero firmware binaries.
- The complete candidate uses the exact 355-file isolated source whitelist.
  Full member hashes, extracted UAC apphost self-check, packaged preset checks,
  Python 3.13.17 and cryptography 46.0.3 were verified. Independent package review
  found no private device records, credentials, private keys or unpublished
  research. Final ZIP identity remains in its timestamped sidecar.

| Required item | State |
| --- | --- |
| Change list and installation entries | Complete in this record and the preset controls |
| Options/catalog/defaults and English/Chinese copy | Complete for Boiler; the separate OXP3 installer is unaffected |
| Source whitelist and final payload | Prepared from final sources; see delivery evidence |
| Version/build identity | 0.1.0-preview with a fresh timestamp and isolated source commit; existing public artifacts are not replaced |
| Source, native integration and image verification | See final delivery evidence; categories are not interchangeable |
| Maintenance runtime/signing | Not applicable: runtime bytes and published maintenance behavior are unchanged; presets use its existing file transaction contract |
| Published maintenance and physical upgrade/reboot | Existing release acceptance gates remain open; this candidate does not satisfy them |
| Complete Windows candidate | See delivery evidence for location, size, SHA-256 and source commit |
| Public Windows release | Blocked by existing physical acceptance gates; no public release is claimed |
| README | Not applicable: version unchanged; no prose edits |

All source export and packaging use an explicit whitelist in a separate checkout.
Private device records, root history, credentials and unpublished research are
excluded. The locally compiled executable is separate from a complete archive.
