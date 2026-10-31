# Feedback log selection — 2026-11-01

Local source/build candidate. No public release or physical-device acceptance.

## Reproduction and change

The manual Windows feedback editor required a title and description and had no
file picker. A user stalled at 32% could not submit their existing log directly.
SteamOS maintenance had the same text-only limitation.

- Windows now offers an explicit text-log picker and removal action. With a log
  selected, title and description are optional; fixed defaults satisfy the v1
  service contract. Text-only feedback and optional email remain available.
- SteamOS maintenance offers log selection or text feedback. Log submission does
  not ask the user to describe the problem and uses the existing preview and
  per-report confirmation flow.
- Only the selected file is read. Reads are bounded to a tail window around
  64 KiB; UTF-8 and BOM-marked UTF-16 are supported. The report includes at most
  4096 UTF-8 bytes of redacted log text, within the existing 12288-byte request
  limit. The UI explicitly explains that this is an excerpt, not a full-file
  upload. The file path/name are not added as attachment metadata.
- Preview and send use one immutable prepared report. File edits after selection
  cannot change the bytes being sent. No real service submission is part of the
  checks. Reporting does not resume, cancel or otherwise change installation.
- Independent Windows review found ANSI-colored logs were rejected as binary and
  large single-line logs were treated as empty. Both reproductions were fixed and
  covered by targeted regressions.

## Delivery and acceptance

| Gate | Status / boundary |
| --- | --- |
| Windows build | Complete: locked Release build, zero warnings/errors; root local launcher rebuilt. |
| Feedback source checks | Complete: fake HTTP, redaction, UTF limits, UTF-16/ANSI/large single-line input, no-description preview, consent, edit retention and removal. Evidence: `logs/feedback-log-checks-20261101.txt`. |
| Windows visual check | Complete for the rendered Chinese editor at its default size; explicit scroll surface background fixed a transparent capture. Native file-picker interaction, English/small-window visual acceptance and physical touch remain unverified. |
| Model catalog regression | Complete: `logs/feedback-model-catalog-checks-20261101.txt`; synthetic UI/fixtures only. |
| Broad Windows suite | Blocked: `boiler-checks` requires 125 GiB workspace; about 9.6 GiB was available. Evidence: `logs/feedback-boiler-checks-20261101.txt`. |
| Maintenance source | Complete on Windows: 20 feedback, 29 model UI and 8 maintenance-package tests passed. FIFO branch and native KDE interaction require Linux verification; no Linux acceptance claimed. |
| Maintenance runtime inventory | Complete for fresh generic staging at `work/feedback-log-payload-20261101`: all 75 members match generated sizes and SHA-256; three changed feedback modules also match final source bytes. Evidence: `logs/feedback-log-payload-checks-20261101.json`. No signed package or installed/runtime cache replacement is claimed. |
| Options / recovery formats | Not applicable: no install options, privileged operations, package format or recovery state transition changed. Existing results remain independent of feedback. |
| Production service acceptance | Blocked: existing v1 manual-error/log shape retained, but the new log-feedback codes and production delivery have not been end-to-end verified. |
| Signed maintenance / installer release | Blocked: no final public source commit, immutable release identity, signed packages, public downloads, upgrade/recovery or hardware acceptance. No published attachment changed. |
| 32% installation stall | Not investigated in this change; awaiting the user's log. |
| README | Not applicable: no displayed release version change. |
