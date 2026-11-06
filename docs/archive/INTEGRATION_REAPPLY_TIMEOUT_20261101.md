# Prepared-image integration deadline — 2026-11-01

The UI8 local candidate used a 40-minute outer deadline for the complete A/B integration task. A real public `Prepared=true`, `Physical=false` controller run reused the completed model 12 image and the original full custom selection, with 2 GiB of guest memory and temporary outputs in a dedicated RAID workspace.

The unchanged model apply hook verifies an already-applied installation before returning. Integration then performs its normal post-apply verification. In the observed run, both slot A verification passes completed, all A component transactions committed, and slot B completed its first 2,095-file/44-journal verification. The outer 40-minute deadline cancelled the VM during the remaining B work. The controller reported `TimeoutException` at 53.5%; no native target was created. This is not a completed installation or a successful B result.

Evidence is retained under `work/full-vm-ui8/` and the closed, incomplete test image under its dedicated VM session. That image must not be adopted or automatically repaired. A retry starts from the separately verified clean input, not the interrupted image.

The complete `integration` task now has a bounded 60-minute outer deadline. Individual package hooks keep their existing 900-second limits; `integration-verify` remains 2 GiB/20 minutes, model-maintenance checks remain 90 minutes, and other task limits are unchanged. No model data, trust, source binding, recovery barrier, or proof format changes.

Acceptance requires a new immutable candidate, the same public prepared-image path completing against a clean input without timeout overrides, both A/B results, actual owned virtual-disk write/readback and UEFI boot, plus the separately recorded maintenance and final read-only verification gates. Source tests and partial results do not establish that acceptance.
