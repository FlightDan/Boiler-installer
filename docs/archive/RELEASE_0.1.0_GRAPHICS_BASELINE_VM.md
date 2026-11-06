# Boiler 0.1.0 preview — adjustable graphics candidates and VM validation

The graphics candidate list is an editable test baseline. It is not a permanent
model lock or an approval workflow. Change `components/graphics-baseline-spec.json`
and run `scripts/prepare-graphics-catalog.py --refresh --cache <directory>` to
regenerate its current byte inventory. Each installation binds that snapshot,
its selected profile and the actual target-image state for reproducibility.

## Implemented scope

- Eleven AMD/Intel integrated-graphics profiles use scoped provisional firmware
  families. Marketing-name-to-silicon mappings remain provisional. The baseline
  records its upstream revision, origin, Git objects, content digests and licenses.
- Installer choices now reach real read-only A/B planning, on-demand download,
  deployment, component reports and independent verification. Only missing files
  from the selected profile are downloaded; existing raw/compressed firmware and
  image-confined aliases are preserved. A firmware selection cannot add missing
  kernel driver support.
- Early-boot preparation retains each original initramfs as an exact byte prefix
  and appends a deterministic uncompressed CPIO fragment. It preserves existing
  boot modules and internal metadata without regenerating them from VM hardware.
  New firmware, boot files and notices share one maintenance file transaction.
- Current native firmware and module fingerprints, snapshot identity and both
  slot plans are rechecked before writing. Repeated installation retains verified
  prior ownership. Pending maintenance transactions require explicit recovery
  followed by a fresh plan; planning never recovers or mutates the source.
- The existing generic maintenance engine verifies and restores managed files.
  Its runtime API, signing key and release version remain unchanged. Archive byte
  prefixes and ordinary outer permissions/ownership are covered; no additional
  outer mtime, xattr or ACL preservation contract is introduced.
- Distribution remains metadata-only for firmware. No graphics binary, private
  device record or unpublished research algorithm is bundled in the installer.

## Evidence and limits

Final counts, source hashes, VM reports and archive identity are recorded in
`logs/graphics-baseline-delivery-20261025.json` and the timestamped package sidecar.
Source, host interaction, native Linux transactions, initramfs inspection,
SteamOS boot and physical GPU operation are separate evidence categories.

The real-stock read-only VM check covers both 3.8.14 slots and their default and
fallback initramfs images. All 556 original archive members, including 45 module
members, remain unchanged; the original compressed archives are retained exactly.
The isolated Linux kernel smoke reads appended firmware with the expected digest.
These results do not establish initialization, acceleration or display output of
an AMD/Intel GPU: the VM has an emulated graphics device, with no GPU passthrough.

Final results for this baseline:

- Source regression: 315 tests, 279 passed and 36 platform/environment skips.
  Independent review found and verified fixes for repeated ownership retention
  and pending-transaction invalidation of precomputed plans.
- Native Linux integration: all 13 tests passed, including GPU deployment,
  preservation, partial-operation recovery, repeat installation and restoration.
  All 14 initramfs helper tests also passed natively in the separate VM check.
- Windows graphics interaction checks: 58 passed; preset checks: 196 passed;
  current-route layout/focus checks: 1,386 passed. The session renderer returned
  empty captures, so new pixel-level acceptance is not claimed.
- All 33 read-only profile plans passed across prepared 3.8.14 A/B and official
  3.10 rootfs. AMD candidates preserved their existing files. Intel G3/G3E required
  only `i915/xe3lpd_3002_dmc.bin`; unrelated `xe3p` firmware was not injected.
- The real installer planning/download/apply/verify pipeline passed for 780M
  on a private 3.8.14 A/B copy and G3 Extreme on a newly prepared 3.10 A/B image.
  AMD preserved 13 root firmware files and supplemented both boot images in each
  slot. Intel preserved four root files and added the missing DMC file in each
  slot; its boot image received the selected five candidates. Separate read-only
  guest runs verified both slots. A live upstream fetch independently verified
  the DMC's 67,772 bytes, SHA-256 and Git object identity.
- The actual SteamOS 3.8.14 kernel and the final AMD initramfs reached
  `steamdeck login:` and Multi-User System in 139.922 seconds. This direct-kernel
  test used the stock hook's required `steamos.efi=PARTLABEL=efi-A` argument and
  a private NVMe QCOW2 overlay; the candidate backing image remained unchanged.

Full UEFI disk-start acceptance remains open. The additional fixture test that
omitted boot-menu staging timed out before a SteamOS serial banner; a supplemental
raw-to-raw test-copy preparation also failed at the final GPT extent on Windows.
Neither result is counted as a successful UEFI/installer-copy test. No source
image or physical disk was modified by those diagnostic attempts.

Physical installation, hardware reboot and public release retain their existing
acceptance gates. Default selection remains off; device packages keep their own
support policy and hide/clear this generic choice. Imported image flashing does
not silently inject a graphics selection.

## Delivery checklist

| Item | State |
| --- | --- |
| Editable baseline, metadata generator and source inventory | Complete; the per-run digests serve integrity and reproducibility |
| Installer UI, host preparation/download and guest integration | Connected; final verification evidence is recorded separately |
| Fresh metadata-only payload and existing maintenance runtime | Regenerated; exact inventory checked before packaging |
| Version/build identity | 0.1.0-preview with a new timestamp and isolated source commit |
| Targeted source/native/VM checks and independent review | See the final delivery record for results and separate scopes |
| Signed maintenance release | Not applicable: no maintenance runtime/API or trust change |
| Physical acceptance and public publication | Not performed by this VM validation task |
| Complete local Windows candidate | See the final delivery record and package sidecar |
| README | Not applicable: product version is unchanged |

This record supersedes the unavailable-graphics boundary in the preceding system
preset integration record. The experimental hardware boundary remains explicit.
