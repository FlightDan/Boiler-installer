# xevma kmod loader hotfix preview

The loader previously executed `kmod modprobe xe`, which the target kmod
multicall dispatcher rejected. Generated OXP3, X2 and shared-resource loaders
now execute the fixed `/usr/bin/kmod` binary with `modprobe` or `insmod` as
argv[0]. Stock xe is loaded before the locked workaround module. Failure
propagation, OS/build/kernel, module digest and existing-module guards remain.
Historical source snapshots and the module bytes remain immutable.

Installer: 0.2.0-preview.20261103.1. Generic maintenance: 0.2.0+maintenance.2.
OXP3 candidate: 0.1.0-localtest.20261102.14. X2 candidate:
0.1.0-localtest.20261102.x2-full.1. Shared xevma resource:
2026.10.29+kernel7.2.4.model-advisory.3.

The installer asset is slim: it contains no executable model/device package.
This release does not automatically repair an already installed model or
transform an externally imported old package. Rebuild external packages with
the corrected source and import through the existing reviewed path. Generic
maintenance remains a separate distribution; no signed update index is changed.

Delivery gates are tracked separately:

- Source regression: pending final public-checkout run.
- Generated resource inventory/import: pending final-byte rebuild.
- Generic maintenance package: pending build and extraction verification.
- Windows Release build and packaged CLI: pending.
- Public downloads and hashes: pending publication.
- Windows Authenticode: not applicable to this unsigned preview policy.
- Device activation, upgrade/recovery, reboot and sleep/wake: blocked, not
  deployed or tested on the handheld. No hardware acceptance is claimed.
- Latest discovery and signed device/maintenance index promotion: not applicable;
  this is a prerelease and does not replace the accepted distribution streams.

The observed loader failure is confirmed. Its connection to the suspend/resume
hang is unproven. For this machine, preparation/work files must be on the RAID
volume; its C: free space does not meet the complete preparation flow needs.
Do not embed this machine's absolute workspace path in portable packages.
