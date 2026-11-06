# Graphics repository intake, 2026-10-25

This local preview update records the requested AMD and Intel graphics families
in a separate repository catalogue. It does **not** enable GPU installation or
claim that the listed hardware works with a SteamOS image. No public version,
release asset or README version has changed.

Follow-up: [UI_GRAPHICS_20261025.md](UI_GRAPHICS_20261025.md) records the later
local detection/manual-selection preview. The initial catalogue-only stage
below remains the history of this intake; GPU deployment is still unavailable.

## Recorded candidates

The user clarified the requested names as Radeon **780M / 880M**, not Ryzen
processor-series names. `components/graphics-support.json` keeps these GPUs
separate. The documented processor examples are Ryzen 7 7840U and 8840U for
780M, and Ryzen AI 9 365 for 880M. These are examples, not an exhaustive CPU
list or evidence that every processor in a numbered series shares firmware.

The AMD Z entries retain Z1, Z1 Extreme, Z2, Z2 Extreme, AI Z2 Extreme, Z2 Go and
Z2 A separately. AMD's official table documents different RDNA generations and
compute-unit counts. It does not establish a firmware/IP mapping or GPU PCI ID
for each model, so the catalogue assigns neither a Radeon model number nor a
shared firmware list to these processors.

Intel Arc G3 Extreme is documented as Panther Lake / Arc B390, with GPU device
IDs B080 and B082. The related Arc G3 is separately documented as Arc B370, with
B081 and B083. These are hardware identity records, not proof that a target
kernel supports those IDs. Each record links its primary manufacturer sources.

## Delivery boundary

The intended delivery remains on demand: no GPU binaries in the installer,
preserve native files, generic installation without a device package only, and
eventually use a fresh profile- and image-bound missing-file plan. The pinned
linux-firmware origin is **origin metadata only**, not a reviewed per-profile
file lock. There are no firmware paths, download plans or GPU binary bytes in
this candidate catalogue.

Every profile has `install_available:false`. All four enablement gates remain
open: reviewed file locks and notices, target-kernel evidence, early-boot
firmware coverage, and hardware runtime validation. Broad `amdgpu`, `i915` or
`xe` static missing-file reports must not be used as installation lists. Those
reports describe all chips handled by a driver, not the selected GPU. The
current integration has no GPU initramfs inspection/rebuild/verification route;
copying root-filesystem firmware would not establish early-boot availability.

The installation options and the existing Wi-Fi/Bluetooth selection flow are
unchanged. The candidate identities are absent from selectable integration
components. Generic integration rejects forged candidate selections before any
target writes. Device packages retain their existing route and ownership rules.
The generic manifest-driven maintainer has no new installed GPU component to
update or restore at this stage. The independent OXP3 installer and maintainer
are unaffected. No private G3E power research or algorithms were read or added.

`prepare-graphics-catalog.py` validates and generates a metadata-only index
without network access. `prepare-integration.py` validates the same catalogue
and embeds it separately as `graphics_candidates`. The production verifier
compares it exactly against current source and continues to reject extra
firmware binaries, development caches and unknown selectable components.

## Verification and delivery checklist

| Item | Result |
| --- | --- |
| Requested family inventory and identity evidence | Complete: 11 documented candidates; exact 780M/880M names confirmed by the user. |
| Installer catalogue and preparation | Complete: separate candidate metadata, no enabled GPU option or binary payload. |
| Maintainer synchronization | Not applicable: no GPU installation, ownership or restore policy has been enabled. Existing maintainer runtime is regenerated with the integration payload. |
| Independent review and tests | Complete: 254 Python tests, 225 passed and 29 platform skips; independent clean-export review and all 5 graphics tests passed. |
| Local payload generation and inventory | Complete: fresh candidate index and integration payload; 11 candidate profiles, 8 unchanged selectable components, 18 physical inventory files and zero firmware binaries. |
| Windows app build/UI checks | Not applicable: no application source, controls or selectable options changed. |
| GPU download/install support | Blocked by exact profile-to-file locks, target-kernel evidence and an early-boot verification/update route. No GPU prefetch is synthesized from audit JSON. |
| Hardware acceptance | Blocked by the above enablement work; no hardware changes or boot tests performed. |
| Signed maintenance and Windows public packaging/releases | Deferred by the existing local-preview scope in WORKSPACE_COORDINATION.md; no publication or full package created. |
| Version and README | Not applicable: unchanged 0.1.0 local preview; no existing asset overwritten. |

Source tests and generated inventory evidence are kept in the local `logs/`
directory. They establish repository preparation and delivery boundaries, not
working graphics on a physical machine.

The independent review found an initial source-whitelist omission. All five new
sources were added explicitly. A fresh 344-file source export at
`work/graphics-review-ws1bmwtb/clean-source` then passed both preparation scripts
and all five graphics tests. The corrected final review reported no remaining
actionable findings. No cache files, firmware binaries or private research were
added to the whitelist.

The final catalogue metadata digest is
`83b047cc1610e174c7b5985dd7a4d7fc21bdb2e9b3b6b35f3767c431e867cdb0`.
The generated integration manifest SHA-256 is
`f795bab0870182d1bf9aa919ec528d36538cd42f7a3805e7dd665e93de8753c6`.
The catalogue index is `cache/graphics-candidates-20261025/manifest.json`.
The freshly prepared integration is activated at `cache/integration`, with the
previous active directory retained at
`cache/integration-before-graphics-candidates-20261025`.

Evidence: `logs/graphics-catalog-python-suite-20261025.txt` and
`logs/graphics-catalog-inventory-20261025.json`. Windows platform skips include
Linux-only privileged transaction checks and unavailable native symlink tests;
none of those skips are counted as hardware acceptance.
