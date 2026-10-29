# Third-party notices

Boiler-devices contains original registry metadata, source collection tooling,
model installation guides, and tests. Original repository content is licensed under
AGPL-3.0-only; the full, unmodified license text is in [LICENSE](LICENSE).

The device evidence links to the existing
[OneXPlayer3-SteamOS project](https://github.com/FlightDan/OneXPlayer3-SteamOS)
and its published releases. Those links identify evidence and package sources;
the brand/model source collection now also carries explicitly listed UTF-8
component sources from public commit `72f864e0498c10366493c74608c5ffed62cc99ca`.
`source-plan.json` lists their original paths; generated `source-lock.json`
records exact sizes and SHA-256 values. Original author notices and embedded
license headers remain unchanged. Component-specific license files, including
the HDR/split-lock upstream MIT licenses and light component notices/licenses,
are retained beside the corresponding sources. Their license terms take
precedence over the repository default. Generated model guides are not binary
device packages and do not redistribute the native driver/runtime archives.

The BMI260 kernel binding source retains Alex Lanzano's author attribution and
the upstream `GPL-2.0-only OR BSD-2-Clause` SPDX identifiers. A complete GPL v2
license is provided in `licenses/GPL-2.0.txt`, copied without modification from
the Git for Windows distribution's license document; its original preface is
also retained. `licenses/license-inventory.json` records that supplementary
document's origin, size and SHA-256. This does not alter the upstream dual
license choice. TabMaster's separate third-party source patch is outside this
collection's export list.

Python is required to run the builder and tests, but no Python interpreter or
third-party Python package is bundled in this source repository. This registry
does not bundle QEMU, Alpine Linux, .NET, rEFInd, Decky, or model payloads.
