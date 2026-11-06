# Transient Windows directory publication failure — 2026-11-01

Actual Python-runtime preparation, local model import and maintenance-package
tests repeatedly reported Windows error 5 when atomically publishing a fully
validated temporary directory. The private reproduction ran the existing
runtime preparer with unchanged locked inputs: one `.unpack-wheel` publication
failed, and the exact same `os.replace` succeeded 100 ms later. This establishes
a transient failure; it does not identify which Windows subsystem held access.

Extraction and local import now share a small bounded publication helper.
Extraction retains `os.replace`; local import retains `os.rename`. An immediate
attempt is followed by at most five 100 ms retries, solely on Windows error 5.
Every attempt refuses an existing destination, and local import repeats its
existing safe-path guard before every attempt. Validation, staging, atomicity,
cleanup, archive restrictions and session-only developer trust are preserved.
There is no copy/delete fallback or trust grant. Persistent and other failures
still fail; non-Windows behavior has no retry delay.

Seven focused regressions cover both callers, bounded/persistent failures,
nonmatching errors, a newly appearing destination, repeated guards, a newly
unsafe path and staging cleanup. The normal runtime preparer and protocol/local
intake checks are rerun without the diagnostic wrapper. Actual outcomes of the
frozen-source build, targeted Windows/Linux tests, maintenance and installer
inventories, fresh extraction, offline models and packaged executable checks
are retained in the final build's machine-readable evidence.

This change is included in installer `0.2.0-local.20261101.ui.5` and generic
maintenance `0.1.0+maintenance.17`; OXP3 `.20261101.11` payload bytes remain
unchanged. Formats/options, Linux archive behavior and model payload/legacy
restore are unchanged. Maintenance is prepared before Windows packaging.
Earlier candidates and failed invocation evidence remain immutable.

Signing, publication and public-download verification are not applicable to
this unsigned unpublished candidate. Physical installation, first boot,
firmware and device maintenance/recovery acceptance remain unperformed.
Private probe logs/helpers stay outside the source whitelist. README prose is
unchanged.
