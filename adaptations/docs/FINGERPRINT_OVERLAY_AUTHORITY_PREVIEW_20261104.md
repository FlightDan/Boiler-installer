# Fingerprint overlay authority preview

The same `scripts/build-fingerprint-overlay.py` is used from the canonical
adaptation checkout or its exact installer snapshot. Both require an explicitly
supplied installer root for protocol code. Release mode additionally requires
the exact authority checkout and commit A, and exact installer checkout and
commit I. It verifies their origins, HEADs and reviewed source inventories. The
installer protocol must support engine 0.1.5. Publishing and signature checks
remain separate steps.

The independently verified base packages derive from authority commit
`3edd6a340b56628785f05365d62349f7d03c63eb`. Their manifest SHA-256 values are
`3b4c7dce1c6850dd2e3601ef44542676f904a66eeae9d2ce14ad00e3aa9ddbb2`
for OXP3 and
`96ef91e8b7909ad9a345eef1feed1aac90156e6846e9c2f75fc265d006b273f8`
for X2 Mini. The native manifest is
`a51df24539a63ad82e74ac6b911f28835da19211ee50fd7c90457f15db20936f`.
Release candidates reject changed inputs, localtest versions, unavailable
Decky, or an enabled/default BE201 legacy repair. They preserve the original
base manifest and record base commit, builder A, installer I, and input hashes
as package evidence, then inventory, archive and reimport the result.

The required authority and installer commits must exist before building; the
base commit is separately fixed and does not depend on A, avoiding a commit
cycle. This is source/inventory validation only. Hardware acceptance, retained
maintenance upgrade and recovery, signed-index publication, download verification
and actual Windows installer integration remain separate gates.
