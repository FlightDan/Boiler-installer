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

## 0.2.0-preview.20261105.8 — model resource component fix

Preview `.7` stopped at the component step with `Invalid device dependency graph.`
whenever a model resource such as the default `resource:intel-be201-firmware`
was selected. `ComponentOutcome.Expected` resolved selected ids only against the
package `components` list, while the guest plan installs developer-session
`resources` as ordinary components with their own ids. The host therefore
rejected its own selection before the guest integration ran.

The expected set now resolves both `components` and `resources` (resources stay
leaves), keeps dependency closure and duplicate/unknown-id rejection, and the
plugin matrix asserts that the shipped OXP3 and X2 manifests include their
resources in the expected set. `component-outcome-checks` covers the resource
leaf, dependency closure, and unknown-selection refusal.

The brand ZIP, model manifests, and maintenance runtime are unchanged from
`.6`. A physical run past this point still exercises the guest integration,
resource installation, SSD transaction, writes, and first boot on the target.

## 0.2.0-preview.20261105.9 — guest TOON parser payload and real default-selection QA

Preview `.8` reached the guest component step and failed resolving the package
resource manifests: the managed payload carried `protocol/` and `maintenance/`
but not `adaptations/toon-parser-lock.json` and `adaptations/vendor/`, so the
pinned TOON decoder could not authenticate `resource:intel-be201-firmware`.

The guest payload now includes the parser lock and vendored parser whenever the
protocol is staged.

The model image QA harness no longer selects only `oxp3:legacy-adaptation`. It
starts from the same component and resource defaults a freshly selected model
submits, including the package Decky provider for static Decky consumers, and it
applies the Windows split-lock EFI gate and the installation component gate. The
previous adaptation-only selection is why `.6`–`.8` host and guest gaps escaped
VM testing. This build still derived legacy repairs from the descriptive
`default` flags, which `.10` replaces.

VM evidence for `.9`: OXP3 and X2 Mini both passed the disposable-image model
integration, read-only verification (119 adaptation files and 16 journal entries
per slot for OXP3), and physical-readiness probe with
`oxp3-full-split-lock-efi-v1` / `x2-split-lock-efi-v1`. The GM7 SSD provider is
excluded from the VM set because it requires a reviewed physical target; that
transaction still needs hardware acceptance.

## 0.2.0-preview.20261105.10 — full wizard selection QA and legacy journal bounds

Preview `.9` still ran the disposable-image QA with legacy repairs taken from
the descriptive `default` flags. A wizard submit selects every installable
legacy capability, and that gap is exactly how surrounding bugs stayed invisible
to the VM suite.

The model QA now derives legacy selections from the availability and dependency
closure (`MainWindow.DefaultLegacyCapabilities`) like a fresh model selection,
so the VM run covers `split-lock`, `opti2xmx`, Decky, the fan and CPU controls,
Waydroid, and the default components and BE201 resource together.

Running the full selection found a real install stop: shared-home component
journals embed the installed payload bytes for restore, and `opti2xmx` needs
several hundred megabytes, exceeding the installer's 128 MiB metadata bound in
the fresh-slot check and the adapter-proof evidence cap. Shared journals and
their proof evidence now use a 512 MiB bound while slot journals keep 128 MiB,
and the integration guest runs with 4 GiB instead of 1 GiB because that check
legitimately loads a large journal.

VM evidence for `.10` (fresh disposable SteamOS 3.10 image, both slots):
OXP3 and X2 Mini passed model integration, read-only verification
(`oxp3:legacy-adaptation` 119 files / 16 journals per slot), and the physical
readiness probe with `oxp3-full-split-lock-efi-v1` / `x2-split-lock-efi-v1`.
Physical GM7 SSD handoff and first boot remain hardware gates.




