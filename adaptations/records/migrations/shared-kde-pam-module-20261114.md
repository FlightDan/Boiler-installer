# Shared KDE PAM module — 2026-11-14

Both local fingerprint overlays consume one explicitly supplied PAM module,
locked to the SteamOS fprintd package and the extracted module bytes. The
existing component installs it at `/usr/lib/security/pam_fprintd.so`; no PAM
service files are changed. The retained package was authenticated against the
SteamOS repository's Valve CI signing key. Source, license, package and module
identity are recorded in `common/fingerprint/kde/pam-module-lock.json`.

Acceptance: source and input checks are separate from packaged installation,
upgrade/recovery, and actual lock-screen verification. All of those delivery
gates remain pending for these new payloads. Prior live enrollment and tests
predate the new packages and do not establish their deployment acceptance.
