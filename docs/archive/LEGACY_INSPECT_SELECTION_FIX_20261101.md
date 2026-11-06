# Legacy maintenance inspection selection

Local candidate identities: installer `0.2.0-local.20261101.ui.13`, generic maintenance
`0.1.0+maintenance.25`, and OXP3 model `0.1.0-localtest.20261101.13`.

## Confirmed defect

Inspection displayed the authenticated legacy installation selection but prepared
capabilities using the package default selection. Completed nondefault slot-switch,
partition-mount and Decky installations were consequently reported as unmanaged
conflicts, blocking dependent Opti maintenance. The owned Linux lifecycle fixture
reproduced this after a successful maintenance 21 to 24 upgrade; its original A/B
installation proofs matched before the attempted model adoption.

Inspection now prepares capabilities from the saved Boiler selection, otherwise
the authenticated legacy selection, otherwise the package default. Reviewed Apply
continues to adopt validated originals through the existing path. Inspection stays
read-only, and altered files, unmanaged conflicts, other-owner conflicts, pending
transactions and trust checks retain their existing barriers.

The normal upstream-repairs builder copies the reviewed maintenance bridge as well
as the runtime adapter and inventories the resulting bytes before archive validation.
The immutable public adaptation source, CPU baseline, input locks, runtime adapter
and original-journal decoder remain unchanged. Existing adopted package pointers
remain content addressed; this change does not silently retarget model12 generations.

## Acceptance boundaries

- Focused bridge and builder regressions, including a causal old-behavior failure,
  passed before candidate preparation. Exact commands and logs belong to the local
  verification record; synthetic tests do not establish installed-device acceptance.
- Normal model regeneration, maintenance-before-Windows packaging, both retained
  model inventories and actual packaged-executable checks are delivery gates.
- A fresh owned Linux lifecycle must validate the legacy capabilities, adoption,
  apply, conflict refusal, original restoration, reapply and inactive-slot protection.
  Preserve the earlier failed fixture and all original source proofs.
- Source, package, installed-lifecycle and two-slot read-only verification results
  are separate. A prior candidate's successful VM check is not a new lifecycle pass.
- Physical hardware, Steam frontend/game behavior, signing, publication and public
  downloads have no acceptance claim from this local candidate. Desktop visual
  inspection remains deferred at the user's request.
