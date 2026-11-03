# Canonical common-device preparation — 2026-11-03

This stage consumes the explicitly supplied Git archive for `FlightDan/Boiler-devices` commit `fb102eb212c08a714ab7bb8e5de0a09b6df067f6` (500931 bytes, SHA-256 `c6431ee949d5ba81c9522d1e3584d5c4a538c74465fa1f7c0f2cb9fb8218cc0d`). It preserves the archive's four shared source records, their source/license material, two model reference records, and the fileless GM7 provider declaration. Existing source locks remain unchanged.

The preparation checks each BE201 firmware input against both its canonical source record and the project input lock, and checks the xe module against its canonical module lock and the retained independent source-rebuild evidence. It stages the canonical guarded xe loader, the canonical ALC245 route script and the verified binary inputs. These are inputs to further integration: no runtime resource manifest or deployment authorization is generated. Model-specific board parameters and device matching are still required.

The retained public OXP3 release archive is independently pinned at 555684389 bytes and SHA-256 `d8bee8bad14f4cd136c814ebcaa3299fa59202b6cdc6d26ec9c52af0e055f4d4`, repository `FlightDan/OneXPlayer3-SteamOS`, commit `e181a9055e1c94e8d758e2b9575f60fb5fc5109c`, tag `v1.0.6+installer.20261022.220437`. The preparer authenticates an explicitly supplied archive using the existing bridge's held-handle reader and stages only four exact members: the BMI module (SHA-256 `94db735bde8e6d310b5056fb8faf6fac58d06fa54cf3a4b908273384f22b2e52`), modified InputPlumber executable (`1107d95c34863c7865cac64183673135098d77265a32763c22fc7efbc1893aa3`), InputPlumber 0.78 patch (`e1fd31a17aaacb4bb35b1158dae4471f0ea74f21861ef581ac9985bba9d1bc2d`), and license (`7fe14716ed31cc71fc3bf9af293c368d464a853a70ea86e9995388f6b9e929e5`). The source-only BMI record supplies the retained binding and restoration source. Presence and provenance of these bytes do not establish a working shared deployment or recovery implementation.

Reproduce with an explicit archive and a fresh output on a suitably sized project-owned RAID workspace:

```
python -B scripts/prepare-canonical-common.py --archive <verified-source-archive.zip> --gyro-public-archive <pinned-public-release.zip> --output <new-raid-directory>
python -B -m unittest discover -s tests -p test_canonical_common_preparation.py
```

The current preparation contains 77 inventoried files totaling 23208880 bytes, plus `preparation.json` (SHA-256 `0c87fcff3bca4ba6b86fdf4353b40637214908ce298d6d3e7e16abbb1e8818bc`). Every inventoried byte and the exact file set were checked. Earlier preparations from the previous canonical commit remain preserved as historical evidence; workspace locations are recorded in private project-local verification metadata. This is a local unsigned candidate, not a release artifact.

Gates: source archive and source-byte preparation complete; runtime package generation, installer and maintenance integration, recovery behavior, physical matching, Linux execution, build/package integration and released download verification remain downstream work. The user reports prior runtime verification on both OXP3 and X2 Mini; that report does not establish acceptance of a newly generated package. GM7 is a source provider declaration only and must be matched against the actual SSD. No hardware, disk, firmware, service or reboot action was performed during this preparation.
