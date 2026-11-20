# Current-computer hardware selection — 2026-11-18

In generic custom and vanilla configuration, clicking current-computer detection after destination acknowledgement uses the existing read-only Windows wireless and graphics detection to propose existing options; the existing hardware identification results remain informational. It does not scan on entry or refresh. Other computers require manual choices. The user can adjust choices, and explicit wireless and graphics choices are retained per reviewed destination for this session. Model packages and SSD target matching retain their existing behavior.

- Source and targeted checks: complete. Wireless and graphics checks cover explicit button activation, no scans on entry/refresh, manual opt-outs, provider changes, target restoration and stale results. The UI preference key uses the reviewed disk GUID and range and permits localized model names or absent serials; SSD repair validation is unchanged.
- Windows build: Release succeeded with zero warnings/errors. Wireless, graphics, device, custom-build, SSD, baseline Boiler and model-catalog behavior checks passed; the catalogue behavior run used its existing no-capture mode. Source-export relocation regression and whitespace checks passed.
- Installer UI visual review: blocked. WPF returned zero visible pixels even for its solid-color rendering probe. Captures are not accepted as visual evidence; diagnostics are retained separately from passing behavioral checks.
- Installation, maintenance and recovery: no transaction, request, payload or recovery changes; existing selected options continue through their established paths.
- Published artifacts and download verification: not applicable to this source change; no release prepared.
- Physical hardware acceptance: not performed; read-only detection does not establish Linux driver or hardware compatibility.

The retained adaptation workspace indexes the local build and validation evidence under `artifacts/hardware-selection-20261118` in `MANIFEST.json`. This record covers the unpublished working-tree candidate.
