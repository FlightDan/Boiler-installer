# Boiler feedback integration candidate — 2026-10-30

Local candidate; no public release or physical-device acceptance is claimed.

## Changes

- Windows installer: optional manual feedback from the home screen and wizard. Actual installation, resume and download failures offer an individually confirmed error report. Cancellation is not reported. The automatic payload contains only fixed codes, version, platform and a random report ID; exception text and logs are not uploaded.
- SteamOS maintenance: failures offer individually confirmed reports; the model menu and the failure menu offer manual feedback, including if the catalog fails. Manual content is redacted before preview and sent only after confirmation. Neither client sends attachments or automatically retries. Redaction is best-effort and users are asked to inspect the preview.
- Uses the existing Cloudflare feedback API. Existing main installer and maintenance flows retain their results if reporting fails. Maintenance revision is `0.1.0+maintenance.11`; the public display version is unchanged.
- Maintainer runtime, signed-package input list and public-source whitelist include the new files. No experimental research or credentials are part of the feature.

## Verification and delivery gates

| Gate | Status and evidence |
| --- | --- |
| Independent review | Complete; pre-session Windows failure and Chinese sensitive-field redaction findings fixed and rechecked. |
| Windows source and build | Release WPF build passed, zero warnings/errors. Feedback fake-HTTP checks, model catalog checks, and installation log checks passed. Device-package operation errors and process crashes are not automatically reported. |
| Maintenance source | 51 targeted feedback, model UI and maintenance package tests passed. Tests use synthetic responses; no physical device was changed. |
| Installer maintenance payload | Fresh local candidate generated at a local staging directory; feedback modules and version metadata match manifest sizes and SHA-256. It is not the final packaged installer payload. |
| Standalone bootstrap | Local candidate `boiler-maintenance-feedback-devdan.pyz`; 370424 bytes, SHA-256 `2e78418854244d33a04f4b14bf6b6e179a846c3ec943cfe223a9f99d03a85c65`. It requires a signed index containing maintenance.11, which has not been published. It is not an authenticated public distribution. |
| Broader Windows checks | Blocked: `boiler-checks` requires 125 GiB free, approximately 104 GiB was available. `--installation-result-checks` failed at the visual ancestor check; origin is not yet established. Actual feedback dialogs have not been visually accepted. |
| Maintenance release / upgrades | Blocked: no final clean public source commit, signed maintenance.11 artifacts or public-download verification; Linux/KDE, upgrade, recovery, reboot and real-device validation remain outstanding. Existing signing identity status is ready. |
| Installer release | Blocked: complete maintenance release and upgrade gates first, then rebuild and verify the full Windows installer against the same final public source commit. No public attachment was changed. |
| README | Not applicable: displayed project version did not change. |

Worker deployment previously responded to health and a synthetic submit/read/delete test, but intermittent Cloudflare route errors were observed. The new clients have not been end-to-end validated on the target devices.

2026-10-30 domain update: the Worker is attached to https://feedback.devdan.cc, its workers.dev production URL is disabled, and both Boiler clients use the new domain. TLS, health and synthetic report submission, admin read and deletion passed. The Windows build, feedback checks and 43 maintenance tests passed; the maintenance payload and candidate bootstrap were regenerated. Final signed release, upgraded installations and hardware validation remain outstanding.

## 2026-10-31 automatic report confirmation

- Windows automatic error preview removes the review checkbox. The enabled “Agree to send anonymous report” button provides the per-report consent; closing or declining still sends nothing. Manual feedback retains its separate review checkbox because it can include user-entered text and an optional email address.
- Windows Release build passed with zero warnings/errors; `feedback-checks` passed using fake HTTP. Actual dialog visual verification and packaged-app verification are pending.
- The automatic report still contains only the fixed failure category and version. More useful fixed diagnostic stages and failure categories need a verified receiver contract and integration with the failure sites before they can be delivered; no exception messages or logs were added.
- Installation/maintenance options and payloads: not applicable to this Windows-only UI change; no component or maintenance runtime changed. Windows installer packaging and release: blocked pending final public source commit, maintenance release and upgrade acceptance, packaged UI verification and the remaining existing release gates. No public release or device acceptance is claimed. README: not applicable; display version unchanged.

## 2026-10-31 diagnostic report expansion (supersedes the prior machine-only description)

- Windows failure reports now capture the current operation's last stage in memory, retain a bounded excerpt of its output, apply best-effort redaction and show the exact request for explicit per-report consent. Installation and resume propagate a closed exception category; download and update preparation capture available exception details, with update preparation capturing its current-stage output. Image download has no full transfer log in this flow. No file-wide log search, background upload or retry was added. Local update import failures remain outside the pre-existing automatic offer.
- The service v1 contract accepts `events` (level, code, step, error_type, exit_code); manual error reports accept title, message and up to 4096 UTF-8 bytes of logs. Reports with an excerpt use `kind=error, mode=manual`; machine-only reports retain automatic mode. Raw logs can contain sensitive values that redaction misses, so the user must inspect the preview; the report cannot guarantee anonymity. No maintenance client or runtime was changed.
- Verification: Windows Release build passed with zero warnings/errors; feedback checks passed including stage boundaries, redaction examples, byte limit and fixed exception categories. Independent review found and prompted a fix for common install/resume exception classification. Local Worker normalization accepted a representative detailed error report; all 16 Worker unit tests passed. Public health returned 200 and service version 0.1.0. A production detailed submission, admin read/delete, controller-to-UI injected failure, actual dialog visual check and packaged-app check remain unverified.
- Delivery: installer/maintenance options and maintenance payload are not applicable to this Windows-only reporting change. Final installer package and public releases remain blocked by the existing maintenance release/upgrade acceptance, final public source commit and packaging gates; no published artifact or physical device was changed. README is not applicable because the display version is unchanged.
