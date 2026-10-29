# Device catalog and external package intake — 2026-10-24

This is a fresh local Boiler Installer 0.1.0-preview build. It does not replace
an existing public attachment or establish physical-device acceptance.

## Resulting behavior

- The custom-build source page loads the signed GitHub device directory when
  entered. Generic configuration remains available while discovery runs.
- Transport failure automatically tries the independently reverified cache.
  Offline state and the inability to check newer revocations remain visible.
  Signature, rollback and manifest errors do not fall back to an older index.
- The explicit cache and current-PC detection buttons are removed. A retry
  action appears when discovery needs attention. Selecting a device still
  requires using its package, and revoked entries cannot be selected.
- Drop a declared Boiler Bash script, ZIP/tar package, or package folder to
  inspect it. File/folder pickers provide the same intake. The Shell drop
  receiver supports the elevated app's HWND using a narrow message filter.
  Import never executes model code or selects an unsigned package. Session
  trust and a separate use action precede its inclusion in the install plan.
- Bare arbitrary Bash scripts need the documented apply/verify/restore target
  root contract. The importer preserves source bytes, generates wrappers and
  inventories their actual hashes. The author remains responsible for OS
  compatibility, read-only verification and recovery.
- The device cache and Linux guest transport copy the full validated inventory,
  including explicitly inventoried .pyc files. Cache generations are verified
  before publication; incomplete generations can be isolated and recovered.
- [DEVICE_PACKAGES.md](DEVICE_PACKAGES.md) documents the contract and runnable
  example. `scripts/build-device-package.py` creates and revalidates an unsigned
  ZIP without executing, signing or publishing its contents.

## Verification

The final source Python suite passed 214 tests with 28 environment/platform
skips. The final Release and Debug builds passed without warnings or errors.
Independent security review found a filtered-copy/incomplete-cache defect; both
cache and guest transport were corrected and reviewed again.

The device WPF checks passed 26 assertions, including automatic discovery,
generic defaults, both languages, wide/compact layouts, session-trust removal,
failed-import selection reset, stale asynchronous responses, native WM_DROPFILES
parsing, and inventory-preserving cache recovery/guest transport. The broader
WPF suite passed 1,339 checks across 40 layout samples and 64 focus traversals,
with nine nonempty captures. Four dedicated device-page captures were inspected
at 1060x720 and 640x360 DIPs in Chinese and English. Compact layouts scroll.

The public GitHub signed directory was downloaded and verified in the normal
Windows user context: sequence 1, snapshot SHA-256
`475419f9f70b9018b8e9891b781cddb22c414405a0b8490be8eeb67481501a3d`.
This disproves an assumed broken public endpoint; the restricted execution
sandbox's socket denial is a separate environment constraint.

Native Shell message tests do not establish a physical Explorer drag gesture
across integrity levels. This update performs no physical installation, device
upgrade, reboot, or recovery acceptance. Published OXP3 experimental hardware
limits and the existing release gates remain applicable.

## Delivery checklist

| Item | State |
| --- | --- |
| Update list, installation entry and author guide | Complete for this local preview |
| Catalog/options/guest component IDs and defaults | Not applicable: no component selection or model runtime policy changes; existing IDs/defaults retained |
| Shared maintenance runtime inventory | Complete: protocol/local.py explicitly added to both matching runtime whitelists; internal candidate revision 0.1.0+maintenance.1 |
| Generated integration payload | Complete: freshly rebuilt from final sources and locked Decky/Intel/Realtek/MediaTek inputs; hashes recomputed by preparation tools |
| Source and WPF verification | Complete within the boundaries above; independent review/fix/review completed |
| Source export and installer package | Fresh explicit-whitelist checkout and timestamped complete ZIP; exact commit, files, size and SHA-256 are recorded in build-info.json and the ZIP sidecar |
| Maintenance candidate | Fresh unsigned 0.1.0+maintenance.1 package; no existing signed release or key replaced |
| Public signed maintenance/installer release and public redownload | Blocked: existing physical upgrade/reboot/recovery gates remain open; no candidate is claimed published |
| Physical installation/maintenance upgrade | Blocked: not performed by this local UI/intake update; source tests are not device acceptance |
| Legacy windows-installer and steamos-maintenance stack | Not applicable: screenshot and implementation belong to the independent Boiler stack; legacy product/runtime payloads are not changed |
| README versions | Not applicable: Boiler display version remains 0.1.0-preview; main integration remains 1.0.6; historical README prose retained |

Export and packaging exclude workspace history, local device records, credentials,
private signing material, unpublished power research and developer intake caches.
The full package carries the author guide and builder, not just an executable.
