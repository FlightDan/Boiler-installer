# Third-party notices

Boiler's original source is AGPL-3.0-only unless a file states otherwise.
Third-party code and binaries retain their own licenses.

The common installer engine derives from the publicly released
[OneXPlayer3-SteamOS installer](https://github.com/FlightDan/OneXPlayer3-SteamOS).
Device-specific code and its notices belong in the separate model package.

Runtime preparation uses exact archived files, versions and hashes listed in
`scripts/runtime-dependencies.lock.json`. This includes QEMU, Alpine Linux and
its APK packages, .NET, aria2, desync, 7-Zip and optional rEFInd/gamepad firmware.
The lock preserves corresponding licenses/source references carried by the
public distribution. 7-Zip's notice is also in `licenses/7-Zip-License.txt`.
Rebuilds must retain all license members selected by the dependency lock.

The minimal Python runtime and cryptography/cffi/pycparser wheels are fixed in
`scripts/python-runtime-lock.json`; their license files remain in the prepared
runtime. Their licenses do not change to AGPL through inclusion here.

Decky Loader's pinned official binary, corresponding source archive and license
are selected by `components/locks/decky.json` and the preparation script.
Intel/Realtek/MediaTek firmware uses a pinned linux-firmware commit and per-file
hashes in `components/locks/`. The prepared payload retains its vendor license
files; installed firmware licenses are placed under
`/usr/share/licenses/boiler-firmware/`. No new kernel module is built or included
by the generic wireless feature.

SteamOS, Steam and their system packages are supplied by Valve and other
rights holders under their own terms. System images are obtained separately
from official sources. This project does not relicense SteamOS or imply vendor
affiliation. Product names and marks belong to their respective owners.
