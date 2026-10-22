# Boiler Installer

Windows WPF installer and generic offline maintenance engine for SteamOS.
This is the 0.1.0 preview source tree. Production release gates are recorded in
[the release record](docs/RELEASE_0.1.0.md).

The application offers custom builds, prebuilt IMG/Boiler package import, and
Vanilla installation. The official 3.10 upgrade is optional. Generic additions
are Intel, Realtek and MediaTek firmware for existing kernel modules, Decky,
and the maintenance runtime. Device-specific policy is supplied by a pinned
package from [Boiler-devices](https://github.com/FlightDan/Boiler-devices).
The first model package remains in OneXPlayer3-SteamOS.

## Build from an independent checkout

Use Windows x64 and Python 3.11 or newer for preparation. Follow
[DEPENDENCY_BOOTSTRAP.md](docs/DEPENDENCY_BOOTSTRAP.md) to prepare the exact
.NET SDK, QEMU, Linux guest, archive tools and rEFInd inputs. The scripts accept
explicit offline archive paths and never read a sibling working directory.

```powershell
python scripts/bootstrap-dependencies.py --profile all
python scripts/prepare-python.py --output runtime/python
powershell -NoProfile -File scripts/build-app.ps1
```

Prepare optional component payloads from the committed locks with
`scripts/prepare-decky.py` and `scripts/prepare-wireless.py`, then assemble a
fresh integration directory with `scripts/prepare-integration.py`. Each command
provides `--help`. Missing selected payloads block installation.

```powershell
python -m unittest discover -s tests -v
runtime/dotnet/dotnet.exe app/bin/Debug/net10.0-windows/OxpInstaller.dll --cli boiler-checks
```

Run the built `BoilerInstaller.exe` from the complete prepared tree. SteamOS
images are obtained separately from official sources. No SteamOS image,
private signing key, local device credential or private research code belongs
in a source or installer release.

## Package and maintenance contracts

The versioned schemas live in `schemas/`; the strict executable validator lives
in `protocol/`. Normal mode verifies the signed snapshot, manifest anchor and
entire package inventory. Developer trust is explicit and limited to that run.
Metadata/notes do not execute code. Model hooks run only inside the managed
Linux guest after the package and component selection are checked.

`boiler-maintenance --root / ...` supports file transactions, verification,
restoration, interrupted-operation recovery and explicit legacy receipt
adoption. `plugins list|verify|restore` handles known Decky transactions without
a download cache. Arbitrary hook side effects require model-specific recovery;
a pending hook prevents an automatic disk deployment or silent rollback.

Windows installer, generic maintenance and model packages have independent
versions. Existing OXP3 states, keys and release assets are preserved. The
compatibility bridge and hardware acceptance remain separate release gates;
source tests are not hardware acceptance.
