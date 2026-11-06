# First-install model retention: preview 18

The normal installer retains an authenticated selected model package for generic
maintenance after both core maintenance and a supported device component install
successfully. It checks for an interrupted model action and an existing different
retained package before component writes. Retention uses the existing authenticated
snapshot and pointer format; it does not apply model maintenance or establish trust
for later sessions. A later local-test maintenance session still requires explicit
trust. A package without the supported maintenance contract, an unselected device,
or an installation without core maintenance does not create a retained pointer.

The generic maintenance runtime remains maintenance.27, with the original producer
identity and immutable archive bytes. Its association with this installer must be
verified separately. The preview source commit, Windows build, focused source tests,
actual extracted installer checks, imported-model retention and maintenance lookup,
signatures, public source availability and download, upgrade/recovery, rendered UI,
and physical hardware acceptance are separate delivery gates. They are pending
until evidenced individually. Existing earlier full-installer and lifecycle results
are not evidence that this new first-install handoff has run on hardware.
