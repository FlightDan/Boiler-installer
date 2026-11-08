# TOON: how models refer to adaptations

[Home](../README.md) · [中文](TOON.md) · [Build guide](BUILD.en.md)

TOON is structured text. Here, each model's TOON is a checklist describing components, dependencies, source paths, hardware parameters, and build inputs. Tools read it to check those records and generate catalogs or invoke a builder.

## Start with the model files

- [ONEXPLAYER 3: oxp3.toon](../devices/onexplayer/oxp3.toon)
- [X2 Mini: x2-mini.toon](../devices/onexplayer/x2-mini.toon)

These are the maintained model files. `models.toon` and `common-device-sources.toon` are generated catalogs or views. The installer source snapshot generates each model's `manifest.json` from TOON; the adaptation repository's existing checker also reads that manifest to check the model guide. Change the model TOON first, then use the relevant tools to synchronize and check these views.

`complete` in the generated catalog describes the model's build-data status. Consult the separate records for installation, maintenance, recovery, and hardware tests.

## What a TOON contains

| Field | Purpose |
| --- | --- |
| `id`, `name` | Model identity and name |
| `components` | Components, dependencies, source paths, and notes |
| `build` | The build adapter and its required inputs and source locks |
| `resource_model.resources` | Resource IDs requested by the model and model parameters |
| `resource_model.common_references` | References to shared device resources and boot policies |

For example, OXP3's `resource_model.common_references` contains the following row; X2 Mini refers to the same path:

```text
"resource:bmi260-gyro",common/sensors/bosch/bmi260/gyro/source.toon
```

The [BMI260 source.toon](../common/sensors/bosch/bmi260/gyro/source.toon) lists resource files, sizes, SHA-256 hashes, origins, licenses, hardware matching, and recovery information. Paths in the model TOON are relative to the adaptation repository root; file paths inside `source.toon` are relative to that resource directory.

Both models also use `boot_policies` to reference the [GM7 components.toon](../common/ssd/acer/gm7/components.toon). This boot policy is listed separately from source entries in `resources`; its use is matched to the SSD identity.

In TOON, `resources[4]{id,source}` means four rows, each containing a resource ID followed by a source path. Update the bracketed count when adding or removing rows. The parser checks the fields, counts, and references.

## Change or reuse an adaptation

1. Find the components and resources in the model TOON, then follow `sources` or `source` to the code and licenses.
2. Keep reusable hardware implementations in `common/` and reference them from the models; keep brand-shared and model-specific files under their respective directories.
3. Record the actual hardware, system, and tests; maintain source inventories and locks, then run the repository checks. Generate derived files with the tools rather than entering hashes by hand.
4. Use Boiler Installer's snapshot and build tools for the required inputs; the [build guide](BUILD.en.md) lists the current commands.

From the installer root, run `python -B scripts/toon_catalog.py generate --catalog-root <adaptation-checkout>` to update the model catalog and its shared-reference views. Use `check` through the same entrypoint to verify them.

TOON describes what is needed; builders and components provide execution, maintenance, and recovery. When using two model TOONs as a shared shopping list, include a shared path once and retain each model's own files.

`models.toon` schema 2 lists only branded `canonical_id` values and repository-relative `source` paths. Each model TOON declares `legacy_aliases` for existing guide and package identities and keeps the remaining device metadata in `device_record`; name and hardware matching come from `runtime`. The `devices/*.json` files are legacy protocol views and do not rewrite historical signatures. From the installer root, run `python -B scripts/toon_catalog.py generate --catalog-root <adaptation-checkout>` to update views and `check --catalog-root <adaptation-checkout>` to verify them. The resource view derives its name and hardware matching from `runtime` to avoid duplicate maintenance.

The shared-reference views for the two existing models are generated from their TOON records and validated against the common source locks. `common-device-source-index.json` remains the two-model compatibility view.
