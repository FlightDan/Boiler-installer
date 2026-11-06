# Build a device package and installer

[Home](../README.md) · [中文](BUILD.md) · [TOON guide](TOON.en.md)

Start with the model TOON to find components and shared resources, then use the Boiler Installer tools to prepare the source snapshot and build inputs. This page uses adaptations commit `3edd6a340b56628785f05365d62349f7d03c63eb` and installer commit `04dce422d0c4831ced6d2b9efdbd852e7b02a724`; for other versions, check their respective input locks.

## Repository roles

| Location | Contents |
| --- | --- |
| [Boiler-adaptations](https://github.com/FlightDan/Boiler-adaptations) | Model TOONs, adaptation sources, shared resources, and the device index |
| [Boiler Installer](https://github.com/FlightDan/Boiler-installer) | Build tools for source snapshots, model packages, maintenance, and the installer |

Each repository has its own source inventory. When adaptations change, prepare a snapshot from the chosen commit for the build project; changing a link alone does not update the build inputs.

## 1. Check TOON and the source snapshot

Run these commands from the **Boiler Installer** repository root. This installer commit already includes a source snapshot bound to adaptations commit `3edd6a3`:

```powershell
python -B adaptations/scripts/toon_catalog.py check
```

To export a fresh snapshot of that commit, prepare an independent checkout whose origin is `FlightDan/Boiler-adaptations`, then replace both paths below. The output directory must be new:

```powershell
$Adaptations = "PATH_TO_BOILER_ADAPTATIONS_CHECKOUT"
$Snapshot = "PATH_TO_NEW_SNAPSHOT_DIRECTORY"
python -B scripts/prepare-canonical-model-snapshot.py --source-checkout "$Adaptations" --source-commit 3edd6a340b56628785f05365d62349f7d03c63eb --output "$Snapshot"
```

The command reads files listed in `model-source-files.json` from the specified Git commit, checks their sizes and hashes, and writes a snapshot. It does not automatically replace files in the installer checkout; updating the build project's snapshot also requires updating its source lock and inventory.

## 2. Prepare a resource pool from model requests

The resource-pool tool reads `resource_model.resources` from both models in the snapshot, checks each model's dependencies, and prepares the union of their requests. Replace the output path with a new directory:

```powershell
$Pool = "PATH_TO_NEW_RESOURCE_POOL"
python -B scripts/prepare-resource-pool.py --project . --check-inputs
python -B scripts/prepare-resource-pool.py --project . --output "$Pool"
```

This produces a resource pool and catalog, not a complete model package. It currently scans all models in the snapshot; both models currently request the same 14 resources. `common_references` is a separate field and is checked separately: its xe, BE201, and ALC245 resource IDs overlap with those 14, while BMI260 and the GM7 boot policy have separate references. Check the actual resource paths instead of counting repeated references as extra files.

The resource pool is a separate output; the current `canonical-build` does not consume that directory. The next section uses a base model package and a separately prepared common-resource stage.

## 3. Prepare shared resources and build one model

This example uses X2 Mini. First prepare a reviewed **complete X2 base package directory**, its reviewed manifest SHA-256, and the pinned public installer ZIP. The builder also needs the project's locked resource inputs; see the [common-resource preparation guide](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/adaptations/docs/CANONICAL_COMMON_PREPARATION_20261103.md).

Replace the paths and hash below in PowerShell; `$Stage` and `$Output` must be new directories:

```powershell
$Base = "PATH_TO_REVIEWED_X2_PACKAGE_DIRECTORY"
$BaseSha = "REVIEWED_BASE_MANIFEST_SHA256"
$PublicInstaller = "PATH_TO_PINNED_PUBLIC_INSTALLER.zip"
$Stage = "PATH_TO_NEW_COMMON_STAGE"
$Output = "PATH_TO_NEW_MODEL_OUTPUT"

python -B scripts/prepare-canonical-common.py --canonical-snapshot --gyro-public-archive "$PublicInstaller" --output "$Stage"
python -B adaptations/scripts/toon_catalog.py canonical-build --model guide:x2-mini --input "base=$Base" --input "stage=$Stage" --input "public-installer=$PublicInstaller" --base-manifest-sha256 "$BaseSha" --output "$Output"
```

The first command prepares shared device inputs. The second reads the X2 model data, checks the snapshot, base package and inputs, then builds an unsigned candidate for one model. For OXP3 use `guide:oxp3` and its corresponding base package. See the [model build guide](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/adaptations/docs/CANONICAL_MODEL_FINALIZATION_20261103.md) for the bases and their preparation.

## 4. Trim, reuse, and package

The intended two-model brand package uses both TOONs as a file list: check `components` and their `sources`, `resource_model.resources`, `common_references`, and `boot_policies`. Collect requested files and dependencies using their adaptation-repository paths, store a path referenced by both models once, and retain each model's own files. Check the inventory, licenses, integrity, and the inputs needed for installation, maintenance, and recovery.

The current `canonical-build` builds **one model** from an existing complete base package. Building one trimmed, deduplicated brand package from two TOONs requires a corresponding trimming builder and package-content checks; a complete single-model base build is not that brand package.

Code to start from:

- [TOON parsing, checks, and canonical-build](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/adaptations/scripts/toon_catalog.py)
- [Source snapshot preparation](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/scripts/prepare-canonical-model-snapshot.py) and [shared-resource preparation](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/scripts/prepare-canonical-common.py)
- [DevicePackage format](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/schemas/DevicePackage.schema.json) and [package validator](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/protocol/manifest.py)

To create an unsigned ZIP from an existing valid device-package directory:

```powershell
$Package = "PATH_TO_PACKAGE_DIRECTORY"
$Zip = "PATH_TO_NEW_PACKAGE.zip"
python -B scripts/build-device-package.py "$Package" --output "$Zip"
```

## 5. Build the Windows installer

The device package and installer are separate artifacts. In an independent Windows x64 checkout of Boiler Installer, prepare locked dependencies, build the application, prepare integration content, and run the packaging script. Start with the [Boiler Installer build instructions](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/README.md#build-from-an-independent-checkout) and [dependency guide](https://github.com/FlightDan/Boiler-installer/blob/04dce422d0c4831ced6d2b9efdbd852e7b02a724/docs/DEPENDENCY_BOOTSTRAP.md).

To add a built model package to the device registry, run `scripts/toon_catalog.py index-candidate` from the **Boiler Installer** source root as described in the [registry workflow](REGISTRY.en.md).

The examples above are pinned to the historical installer commit named on this page. Use `scripts/toon_catalog.py` as the single entrypoint in the current installer checkout, including for X2 Mini builds.
