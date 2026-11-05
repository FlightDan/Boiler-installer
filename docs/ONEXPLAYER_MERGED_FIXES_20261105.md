# OneXPlayer merged fixes candidate — 2026-11-05

This local candidate builds on source commit `6471fbb58a38a5121e2b24ee79bc8ce096c89424`. Installer version is `0.2.0-preview.20261105.11`; generic maintenance version is `0.2.0+maintenance.17`. The canonical adaptation source lock and the existing shared journal and model retention behavior remain unchanged.

A missing `/etc/default/grub.d` is accepted during SSD repair preflight when its validated parent exists. The write stage creates the directory through its validated parent. Recovery restores the original EFI and drop-in bytes; a newly created empty grub.d directory may remain. The progress journal adds monotonic elapsed milliseconds while retaining existing fields and UTC timestamps.

Acceptance gates for this candidate: source regression, Linux root maintenance and recovery, Windows build and application checks, packaged archive and offline checks, upgrade and recovery, and hardware validation are pending. No public release or published-download verification is claimed.
