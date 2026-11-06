# 0.1.0-preview — Opti2XMX 0.3.1 / Battery 0.2.2 / microphone local test

2026-10-30. This delivery is a complete local Boiler test bundle for the next hardware test. No Boiler source, tag, installer or maintenance release is published.

The OXP3 model advances to `0.1.0-localtest.20261030.3`. It includes the independently published Opti2XMX 0.3.1 plugin, the unchanged published XMX runtime, and all three original pinned OptiScaler archives. The archives remain at the plugin's canonical `downloads/<release-tag>/<exact-filename>` paths for offline preparation. FSR models and user game files require the user's own local import and are excluded from this bundle.

Opti2XMX public source commit: `c99638e3768ddd50326eade01cdd024031096c92`. The [0.3.1 release](https://github.com/FlightDan/Opti2XMX/releases/tag/v0.3.1) ZIP has SHA-256 `d2d6b3ccdfd73c8676450dcce5312330db861276e1ab2062e56efb0ac5043fea`. Anonymous public download matched the final ZIP and all 36 members; both public Build and test runs passed. The earlier locally generated Opti input is explicitly superseded and is excluded from this delivery.

The latest Battery 0.2.2 three-slider UI and event-driven native microphone mute synchronization are retained from the complete, manifest-anchored `localtest.20261030.2` model. OXP3 charging remains owned by its original installation/maintenance Store. The five ordinary CPU production source files remain byte-for-byte identical to the public Boiler-devices source lock at `72f864e0498c10366493c74608c5ffed62cc99ca`; no current private CPU source or dynamic-power research is imported. Model default selections and Decky ownership/dependency rules remain unchanged. Opti requires explicitly selecting the model Decky provider and Opti component.

The X2 Mini model remains the latest exact `0.1.0-localtest.20261030.2` candidate, with Battery standby and microphone fixes. OXP3 charge-control/provider/EC files are excluded from X2. Fingerprint support remains excluded from both models.

The generic retained maintenance runtime advances to `0.1.0+maintenance.8` and includes the same exact two model packages as the Windows installer. These are explicitly trusted local test packages, anchored by their full manifest digests; they are not newly signed production models and do not change any production trust key. The complete Windows ZIP is rebuilt from a fresh whitelist-only independent source commit and locked dependencies with the existing `package-installer.py --local-test` generator.

Verification separates source/software checks, archive inventory checks, and hardware acceptance. Exact artifact names, bytes, hashes, source commit and detailed final results are recorded in the adjacent generated delivery reports. This delivery itself performs no device writes, reboot, suspend or hibernation, and claims no new game, X2 hardware or real-sleep acceptance.

The final Opti2XMX 0.3.1 device deployment did not complete because the device SSH connection timed out before writes. The device remains on the earlier dev4 candidate; the final 0.3.1 interface has not been accepted on the physical device. Prior three-version actual offline preparation checks do not substitute for that final deployment or game acceptance.

| Delivery checklist | Status |
| --- | --- |
| Update scope, model/options and installation/maintenance entry points | Complete: updated OXP3 model resource generated through existing helpers; newest Battery/microphone and existing model defaults preserved. |
| Final payloads/manifests | Prepared through normal generators; exact final package/import/inventory results are in generated delivery reports. |
| Versions | OXP3 localtest.20261030.3; X2 exact localtest.20261030.2; maintenance.8; Boiler display remains 0.1.0-preview. |
| Targeted verification and independent review | Recorded in final local delivery report; no source/software result substitutes for hardware acceptance. |
| Maintenance signing/public release/download | Not applicable: local test only, production trust unchanged. |
| Device upgrade/reboot/restore/real sleep | Not applicable to packaging: next hardware test remains pending. |
| Full Windows local test ZIP | Required: generated from the same final independent source and exact retained model bytes, with a new timestamp and full SHA-256 inventory. |
| README | Not applicable: project display version unchanged. |
| Final reconciliation | Final generated report records the actual completion and remaining hardware boundaries. |
