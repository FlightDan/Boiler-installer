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
Intel/Realtek/MediaTek Wi-Fi and Bluetooth firmware uses a pinned linux-firmware commit and per-file
hashes in `components/locks/`. The prepared payload retains its vendor license
files; installed firmware licenses are placed under
`/usr/share/licenses/boiler-firmware/`. No new kernel module is built or included
by the generic wireless feature.
Bluetooth locks are under `components/locks/bluetooth/`. Intel Bluetooth retains
`LICENCE.ibt_firmware`; Realtek and MediaTek retain their respective upstream
notices. Firmware aliases declared by the fixed upstream tree and WHENCE are
materialized as ordinary files with the original source evidence retained.

SteamOS, Steam and their system packages are supplied by Valve and other
rights holders under their own terms. System images are obtained separately
from official sources. This project does not relicense SteamOS or imply vendor
affiliation. Product names and marks belong to their respective owners.

## OXP3 upstream repairs (20261029)

Speaker and connector adaptations retain the MIT notice of HHHHanasak1/onexplayer3-steamos-setup at commit e72e814da1944da3792076689995b6fe961f5768. Their complete notice and provenance are under adaptations/brands/onexplayer/oxp3/current/upstream-20261029 (relative to the Boiler root).

The xevmafix kernel module declares GPL, independently of the enclosing upstream repository MIT license. GPL-2.0 text, original C source, Makefile, build instructions and exact official build-input records are retained under adaptations/brands/onexplayer/oxp3/current/xevma-20261029 and in its device payload. No kernel package or toolchain binary is added to the public source export.

## Fixed keyboard host runtime

The fixed keyboard handler retains the public OneXPlayer3-SteamOS transaction
Store and its helpers under maintenance/keyboard_runtime/vendor, together with
the original AGPL-3.0 license. public-source-lock.json records the exact public
installer asset and source/member hashes. The original configuration merge is
retained for provenance; the host wrapper fixes its indented-section boundary
handling. No current experimental CPU policy is imported by this runtime.
