# Online model index integration candidate — 2026-11-04

This record describes a local, uncommitted candidate; it is not a release or physical-device acceptance.

The installer integrates compatible signed-package version selection, the custom component search and selection interface, fingerprint selection and provider protection, and protocol engine 0.1.5. Installer version is 0.2.0-preview.20261104.2; generic maintenance is 0.2.0+maintenance.6 with minimum engine 0.1.0.

The canonical adaptation snapshot and display-only guide snapshot were generated from FlightDan/Boiler-adaptations commit f8f86448f740334ef9fdc3b26fe3155c9b639a87 using the explicit canonical and model-catalog preparation scripts. Their source inventories and locks are generated from final bytes. The source whitelist includes the canonical snapshot and the integrated app files.

Validation for this local candidate: locked SDK Windows Release build succeeded; source export and canonical TOON/source-lock verification succeeded; boiler, model catalog, device and SSD UI checks passed without screenshots; the canonical snapshot synthetic suite and relevant source suites passed. The authority fingerprint overlay suite requires one committed-source verification to be rerun after the final installer commit: the local candidate is intentionally uncommitted. The actual model-package matrix and custom build checks remain blocked until matching authority-derived model bundles are available. Linux maintenance upgrade/recovery, packaged artifact, visual rendering, and physical hardware acceptance remain separate gates. No signing, publication, or download acceptance is claimed.
