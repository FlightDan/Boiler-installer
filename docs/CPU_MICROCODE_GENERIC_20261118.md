# Generic CPU microcode and visible GPU options — 2026-11-18

Generic custom and vanilla installation provide a CPU tab with mutually exclusive AMD and Intel microcode choices. The existing explicit current-computer detection button matches only the CPUID vendor string. Manual choices and opt-outs are retained for the reviewed destination; discovery does not run on page entry or refresh. Model packages, imported images and local images do not receive generic CPU injection.

The GPU tab exposes the existing authoritative graphics selector. Radeon 780M and 880M use their existing pinned firmware inventories and deployment paths; no duplicate graphics profiles or device policy were added.

CPU sources are declared in `components/microcode-sources.json`. `scripts/prepare-microcode-catalog.py` generates `components/microcode-firmware.json` from exact official Git objects, including sizes, SHA-256, source blobs and redistribution notices. Intel uses release `microcode-20261110` at `d71fe4e2f0f70a49ebbac7f2c7e915f181361974`; AMD uses linux-firmware at `2ab4ce216003ee2c2396c69e19455e4379b0e1f0`. The runtime consumes and verifies the source-spec digest. Windows downloads only the selected vendor. AMD uses the established TUNA/GitLab/Gitiles path; Intel uses immutable official GitHub raw/blob endpoints.

The complete selected vendor inventory is needed to construct an early CPU archive, including when matching firmware paths already exist in the image. Existing native firmware files remain preserved. CPU microcode is placed in an uncompressed early CPIO before the original initramfs; existing standalone vendor microcode images are included in the coordinated boot transaction. Original patch bytes and original initramfs bytes are retained. The kernel performs processor/revision matching; firmware presence is not proof that a physical CPU accepted an update or that BIOS prerequisites are met.

When CPU and GPU boot changes coexist, internal `core:firmware-boot` owns the composed boot artifacts and depends on the selected public CPU/GPU components. It is recorded in the install plan and verified explicitly; public result IDs remain the selected CPU/GPU IDs. Existing managed boot ownership requires explicit restoration before a fresh CPU preparation. Maintenance verifies and restores the retained Engine transactions; the derived boot transaction is verified separately and restored before its dependencies. This change does not add an automatic microcode refresh service after an OS image replacement: changed ownership requires restoration and fresh installer planning. No BIOS flash, live microcode reload, CPU performance policy or experimental device policy is introduced.

Delivery gates:

- Source metadata and targeted regression checks: complete. Full source suite: 1084 tests, 98 platform skips, passed; final microcode-focused suite: 15 tests passed. Source-spec drift, locked-source/download mismatch, native early-archive layouts and invalid Intel signatures have regressions.
- Windows Release build and UI behavior: complete, zero warnings/errors and eight application suites passed. Includes CPU vendor recognition, cancellation/overrides, visible CPU/GPU tabs and 780M/880M ID round trips.
- Genuine Linux guest integration and maintenance transaction checks: complete, 16 tests passed. Synthetic root fixtures cover combined CPU/GPU ownership, newer native CPU payload retention, standalone microcode images, dependency refusal, original-byte restoration and malformed-boot refusal before writes.
- Prepared runtime inventories and local artifact verification: complete. Both CPU vendors and 780M/880M selected payloads were materialized and byte-verified. The 157 Intel files and all six AMD containers were parsed; AMD merging preserves all 55 original patch byte strings. The metadata-only production packaging validator accepts the rebuilt current integration inventory.
- Visual acceptance: blocked; WPF rendering returned zero pixels even for a solid probe. Geometry/control behavior passes are not visual acceptance.
- Physical CPU acceptance: not performed.
- Publication: not requested; no release or public attachment is produced.

The retained adaptation workspace indexes the local application build, locked byte cache, selected download payloads and verification evidence under `artifacts/cpu-microcode-20261118` in `MANIFEST.json`. An initial full-suite run exposed stale test expectations and fixtures. The corrected final logs accompany the original diagnostics.
