# 32% preparation failure — local package candidate, 2026-11-01

This local build packages the current reviewed source whitelist, the short-line
guest command fix, and OXP3 model `0.1.0-localtest.20261101.9` with its regenerated
inner maintenance inventory. X2 Mini retains its existing `.20261030.2` bytes.
The installer identity is `0.2.0-local.20261101.ui.2`; generic maintenance is
`0.1.0+maintenance.14`. Identities are set in the isolated source checkout
before its exact source commit is frozen. Nothing is published or signed.

The package workflow preserves the existing public repository origin and
history, overlays only whitelisted sources, prepares maintenance first, builds
Windows from the frozen checkout, and uses fresh artifact names. Final machine
readable evidence is retained under `dist/install32-test-20261101-1/`.

Required software gates are the clean source commit, dependency members,
maintenance ZIP inventory, Windows build, complete installer ZIP inventory,
active/retained model equality, inner OXP3 maintenance hashes, fresh extraction,
offline model validation and execution of the packaged application. Each gate's
actual result is recorded in the final `result.json`; this record does not
predeclare those results.

Physical installation, boot, device maintenance upgrades/recovery and hardware
acceptance remain separate and unperformed. Comprehensive `boiler-checks` was
previously blocked by the 125 GiB workspace gate; the broad Windows Python suite
did not finish. Neither is represented as passing. Targeted source regressions,
the isolated QEMU password check and the terminal-truncation reproduction are
recorded in `INSTALL_32_PERCENT_FIX_20261101.md`. Re-extraction checks cannot
substitute for device acceptance.
