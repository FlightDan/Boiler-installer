# Complete installer with directory-runtime size fix — 2026-11-01

This local candidate packages the verified OXP3 model
`0.1.0-localtest.20261101.10` into both Windows and retained-maintenance caches.
It corrects the 32% component-verification failure described in
`INSTALL_RUNTIME_SIZE_FIX_20261101.md`. X2 Mini keeps its existing
`.20261030.2` package bytes. The installer identity is
`0.2.0-local.20261101.ui.3`; generic maintenance is `0.1.0+maintenance.15`.

Sources are exported through the explicit source whitelist into a fresh
independent checkout retaining the existing public origin and history. Versions
are set before its exact unpublished source commit is frozen. The normal
maintenance packager runs first, followed by integration preparation, the
Windows Release build and the complete installer packager. No public release,
attachment replacement or signing identity change is authorized or performed.

The requested final directory is `C:\Boiler-32Fix-20261101-2`. It is inventoried
as empty before delivery. The build and verification evidence stays under
`dist/runtime-size-installer-20261101/`; its final `result.json` records actual
gate outcomes. Required gates include locked runtime members, both archive
inventories, exact source identity, new model identity in both caches, nested
legacy maintenance inventory, fresh extraction, offline model checks and
execution of the packaged apphost from the final directory.

Physical installation, first boot and device maintenance upgrade/recovery are
unperformed and remain separate acceptance gates. The earlier isolated Linux
test reproduces the exact old failure with the retained 138485536-byte library,
passes the corrected verification and rejects changed library bytes. This does
not establish a completed hardware installation. Private logs, orchestration,
signing inputs and experimental CPU material remain outside the source export.
README prose is unchanged.
