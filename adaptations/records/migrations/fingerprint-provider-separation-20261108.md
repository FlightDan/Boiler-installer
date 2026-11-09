# Fingerprint provider separation: source-stage record

This change separates the generic unlock bridge from source references for
BF63112 and FTE3600. It does **not** replace the existing runtime or make either
new source record an installable package.

## Current boundary

- The Decky bridge calls configured fprintd tools; it neither selects a model nor
  handles PINs or template bytes. Its existing locked public source is unchanged.
- The historical `oxp3:fingerprint-unlock` package still combines a BF-specific
  native runtime, a model rule and the generic bridge. Its old paths, ownership
  checks, journal and rollback behavior are untouched by this source-stage change.
- The OXP3 model references the BF63112 source; the X2 Mini model references BF63112
  and FTE3600. This declares possible source inputs, not a guarantee that either
  sensor is present on every machine. Existing model-specific BF reset/rules are
  retained as legacy board configuration, not copied into a common driver.
- The pure matching function consumes explicit target observations and authenticated
  provider records. ACPI HID identifies a candidate, not a chip variant, working
  driver, GPIO wiring, compatible kernel, authorization to install, or other
  hardware acceptance. Multiple candidate devices are ambiguous, unknown devices
  prevent a candidate decision, and every result is explicitly non-installable.
  It is not called by the installer or maintenance engine yet.
- FTE3600 ACPI was observed on one X2 Mini test device and one nonconstant frame
  was captured; enrollment, verification and unlock were not established. No
  image, template, credentials or private capture is included here.
- Provider references identify upstream commits, not ready-to-ship artifacts. The
  BF native validator still requires an independently anchored runtime inventory,
  dependency closure and licenses. The FTE provider has no imported immutable
  source archive, verified runtime inventory or maintained installation payload.
  Actual source/license closure must be locked before shipping either new provider.

## Delivery gates

| Gate | Status |
| --- | --- |
| Source records and synthetic matching | Implemented in this change; execution results must be reported separately. |
| Generic bridge source | Existing locked bridge unchanged; no new release or lock. |
| Model TOON and generated views | Source references only; not an executable package. |
| Driver/runtime construction and licensing | Partially cleared: both backends build, link and test together in one libfprint — recipe, provenance and the exact unproven items are in `fingerprint-dual-libfprint-20261109.md`. Still blocked beyond that: no provider payload, kernel module, package or signature exists, and three FTE wrap tests need a glibc environment. |
| Installer selection and authenticated target matching | Blocked: source matcher not integrated, target binding and explicit opt-out persistence required. |
| Maintenance, upgrade, provider switching and interrupted recovery | Blocked: transactional integration, retained ownership, pre-existing installations and conflict tests required. Legacy paths must not be overwritten. |
| Enrollment, verification, Steam unlock and PIN/password fallback | Blocked: physical-device tests and acceptance required; a captured frame is insufficient. |
| Package/build, signature, publication and download verification | Not performed; no release is claimed. |

A final design must ensure that sensor providers do not compete for the single
`net.reactivated.Fprint` D-Bus service, do not overwrite foreign or modified
files or templates, and support rollback before any real installation.
