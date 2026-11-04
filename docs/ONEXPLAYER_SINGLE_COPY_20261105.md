# ONEXPLAYER single-copy installer

Local candidate 0.2.0-preview.20261105.6 embeds the ONEXPLAYER brand ZIP built from
FlightDan/Boiler-adaptations at `8d5053462aac703866866ca18b8f8635c5d0c007`.
The model TOON guides select the files included in the brand ZIP. Both guides use
the same bundled BE201 resource.

The installer lists both models from bundled metadata. Selecting a model extracts
its verified installation inventory into the normal package cache. Installation
retains the selected package through the same transaction used by imported model
packages, so maintenance and recovery can use it offline. The installer ZIP
contains a single brand ZIP and generic maintenance files.

To test on hardware, choose OXP3 or X2 Mini under **Bundled with installer**.
Available TOON components are selected unless explicitly opted out. Check Wi-Fi
and Bluetooth in SteamOS and exercise maintenance and recovery on the test target.
Physical-device acceptance remains pending. Keep the preparation directory on the
RAID volume on the test machine. This build is for local testing.

## 0.2.0-preview.20261105.7 — SSD handoff contract fix

Preview `.6` refused to start a physical installation as soon as a Predator GM7
boot repair and the default `oxp3:split-lock` repair were selected. The Windows
gate only recognised the legacy `device:oxp3` + `oxp3_efi_contract` manifest
shape, while the packaged OXP3 model declares `oxp3_full_efi_contract` and the
X2 Mini model declares `x2_efi_contract`. The handler-side gate
(`components/integration.py`) already accepted all three, so only the Windows
precheck rejected the shipped packages.

`SsdBootRepair.SupportsFixedModelEfi` now accepts exactly one model-owned
contract (`oxp3_efi_contract`, `oxp3_full_efi_contract` for OXP3, or
`x2_efi_contract` for X2 Mini), still requires `oxp3:legacy-adaptation` to be
selected and `oxp3:gm7-resume` to stay unavailable, and rejects missing,
ambiguous, or foreign contracts. `ssd-ui-checks` covers each accepted shape and
the rejection cases.

The physical-mode result page no longer falls back to rehearsal wording when the
request fails before a session exists: the start/resume failure titles and the
recent-task summary follow the physical or rehearsal mode.

The brand ZIP, model manifests, and maintenance runtime are unchanged from
`.6`. Windows UI and offline package checks, Linux install/maintenance/recovery
rehearsals, and physical ONEXPLAYER acceptance are separate gates; physical
acceptance of this build remains pending.

