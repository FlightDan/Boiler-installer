# TOON model sources and generated catalog

Maintain each model in `adaptations/devices/<vendor>/<model>.toon`. The
official TOON parser reads these files directly. The model's component IDs,
recommendations, dependencies, source paths, provenance and build input records
have one maintained source. `models.toon` is the automatically generated total
catalog; do not edit it or the JSON compatibility views by hand. Use `python -B scripts/toon_catalog.py legacy-json --output <fresh-directory>`
for the legacy registry JSON when building an index.

The decoder is the official `toon-format` Python release **1.2.0**, implementing
TOON specification **4.4**, with `strict=True`. It is MIT licensed, requires
Python 3.10+, and has no required third-party runtime dependencies. Its source,
release metadata and MIT license are retained under `adaptations/vendor/`.
`toon-parser-lock.json` records the exact upstream wheel, SHA-256, size, URL and
every extracted file. The loader executes only authenticated source snapshots,
including package submodules; a Python bytecode cache cannot replace the pin. It
does not implement a custom TOON subset or accept duplicate keys/count drift.

Upstream: https://github.com/toon-format/toon-python and
https://github.com/toon-format/spec/blob/main/SPEC.md. The wheel is
`toon_format-1.2.0-py3-none-any.whl`, 22863 bytes, SHA-256
`d94db4b088a281a7bbc85e75d7fbd24e6a0ec7bc110edb129285259eca6522c0`.
To reproduce the vendored bytes, download the exact archive URL in the lock,
verify its size and hash before extraction, then extract its listed files into
`adaptations/vendor/`. No global pip installation or online lookup is needed
for ordinary catalog/build use. Retain the MIT license and release metadata.

## Commands

Run with the project's existing Python runtime from any current directory:

```text
python -B scripts/toon_catalog.py generate
python -B scripts/toon_catalog.py check
python -B scripts/toon_catalog.py preflight --model guide:oxp3 --input base=<reviewed-base-2-directory> --input plugin_zip=<reviewed-plugin.zip> --input plugin_lock=<reviewed-plugin-lock.json> --input decky=<prepared-decky-3.3.1> --input xevma=<exact-xevma-directory> --input historical_plugin_zip=<opti2xmx-0.3.1.zip> --input optiscaler_094=<original-0.9.4.7z> --input optiscaler_079=<original-0.7.9.7z> --input optiscaler_nightly=<original-nightly.7z>
python -B scripts/toon_catalog.py build --model guide:oxp3 --input base=<reviewed-base-2-directory> --input plugin_zip=<reviewed-plugin.zip> --input plugin_lock=<reviewed-plugin-lock.json> --input decky=<prepared-decky-3.3.1> --input xevma=<exact-xevma-directory> --input historical_plugin_zip=<opti2xmx-0.3.1.zip> --input optiscaler_094=<original-0.9.4.7z> --input optiscaler_079=<original-0.7.9.7z> --input optiscaler_nightly=<original-nightly.7z> --output <new-directory>
python -B scripts/toon_catalog.py preflight --model guide:x2-mini --input base=<complete-oxp3-package> --input bridge_overlay=<reviewed-bridge-overlay> --input hdr_overlay=<reviewed-hdr-overlay>
python -B scripts/toon_catalog.py build --model guide:x2-mini --input base=<complete-oxp3-package> --input bridge_overlay=<reviewed-bridge-overlay> --input hdr_overlay=<reviewed-hdr-overlay> --output <new-directory>
python -B -m unittest discover -s adaptations/tests -p test_toon_catalog.py -v
```

`--root` explicitly selects the independent project source/input root.
`--catalog-root` optionally selects a separate candidate `adaptations` tree for
read-only validation against that source root. Defaults derive from the script
location, never a machine-specific absolute path or sibling project. `generate`
writes only the explicitly selected catalog tree. `check` is read-only.

The generator discovers model files and rejects a wrong hierarchy, duplicate
identity, malformed component data, missing/excluded/cyclic dependencies,
installation defaults, traversal, reserved names and linked paths. It generates:

- `adaptations/models.toon`: total TOON model catalog with brand-qualified IDs and source paths;
- `adaptations/adaptations.json`: the existing non-installable source catalog;
- each model's `manifest.json`: the existing `ModelInstallGuide` JSON view;
- each model's `model.md`: generated marked sections, preserving existing prose
  and creating a minimal guide for a newly added machine file.

Adding a correctly placed model automatically adds it to the total catalog.
New executable build behavior requires a reviewed adapter in `ADAPTERS`; a TOON
file cannot provide a program, shell command, import, network action or hook.
Package/index/runtime protocols remain unchanged JSON.

## Explicit inputs and completion limits

`build.materials` records the common, brand and model source locks with their
exact SHA-256. Component `sources` must exist in these reviewed inventories.
The checks rehash original source bytes, rather than treating the existence of a
lock file as proof. The immutable public CPU inventory remains under the
original `source-lock.json`; current Battery/microphone and upstream repairs
retain separate locks. Private sources/helpers are never discovered or exported.

`build.inputs` records each adapter argument's name, kind, project-relative path,
lock path, expected lock digest and a concrete acquisition/preparation note.
Package inputs bind their own manifest SHA-256 and pass the canonical package
validator. Locked directory/file inputs are checked against their lock here and
against the adapter's existing detailed byte/ABI/license checks when built.
Missing or changed inputs fail before creating the output directory.

The OXP3 `oxp3-current-v1` adapter requires all nine explicit CLI mappings shown
above. TOON contains only portable project-relative aliases and reviewed pins;
the session's actual artifact paths are never written back into it. Explicit
artifact paths may be outside the source root, but linked paths and changed
anchors fail. There is no implicit cache or private-directory lookup.

The base must be the complete `.20261030.2` package, manifest SHA-256
`510342dec0bfe6ce39389f5e13cae0d504755aa4072d90b73641b4dd2d5aead9`.
The fixed public `adaptations/scripts/build-shared-base.py` receives that base,
the public Opti2XMX 0.3.1 ZIP and all three original OptiScaler archives.
`records/oxp3-shared-base/opti031-inputs.json` independently pins their exact
sizes, SHA-256, upstream URLs, plugin member inventory, runtime and licenses;
TOON records the required artifact roles and that lock's SHA-256. Missing or
changed artifacts fail preflight before the pipeline creates output. The `.3`
package is rebuilt under `shared/`, then validated against manifest SHA-256
`c80efd9cce07d35c5f8828d17b5fab8c15fbc177a10476754cfac04db3f27485`.
The `.3` builder also requires its exact historical ZIP hash and canonical
protocol reimport. See `OXP3_SHARED_BASE_REBUILD.md` for acquisition identities.

The next fixed entry `adaptations/scripts/build-current-opti.py` receives only
the newly built `.3`, current plugin ZIP/lock, version `.20261030.4`, and three
independent expectations from `build.plugin_pins`: lock SHA-256, plugin ZIP
SHA-256 and exact source commit. It authenticates the plugin's complete member
inventory and preserves the immutable public CPU baseline and unrelated bytes.
The adapter validates the new `.4` under `opti/` against manifest SHA-256
`a56006b425522d1c753254d3e0b9d11f7e79aea21c221a4a481830508c410393` before
calling the fixed upstream repair builder with that newly built package plus
the explicit prepared Decky and xevma inputs. Final `.8` inventory and archive
checks remain unchanged. Prebuilt `.3` or `.4` cannot replace the `.2` input,
and either failed intermediate stops before the final stage.

The `.3` and `.4` ZIPs are deterministic and compared to historical hashes.
The existing final upstream builder retains filesystem timestamps in its ZIP;
`.8` reproducibility means the exact manifest and each payload file. Its ZIP
hash is recorded for each run and is not asserted identical across builds.

The `.2` package remains an explicitly supplied authenticated artifact. This
pipeline does not yet reconstruct every earlier intermediate from retained
source alone. Existing public tools can build `.2` from an explicitly supplied
anchored earlier package and source-locked Battery/microphone inputs, but the
earlier public-installer bridge still needs reviewed historical provenance-label
support before the complete chain can start from that installer. A clean fork
needs the declared `.2` and locked third-party artifacts; this is not a complete
zero-intermediate or third-party-source-only bootstrap. The historical label
`Boiler-oxp3` is provenance data, never a directory to create or discover.

The X2 complete adapter uses a pinned full OXP3 package and explicit, separately
locked identity and HDR overlays. It inherits every OXP3 component and legacy
capability. The X2 HDR overlays select 475/700 mapping; the remaining assets
are retained from the OXP3 base. Build the two overlays from the same explicit
base with `build-complete-bridge.py` and `build-x2-hdr-overlay.py` before calling
the X2 TOON adapter. See `X2_COMPLETE_REUSE.md` and `X2_COMPLETE_PACKAGE.md`.
Package completeness describes inherited scope; it does not establish physical
hardware acceptance, public signing, release or deployment. PL4 is a separate
X2-specific pending request, not an inherited OXP3 implementation.

## Runtime display metadata and old snapshots

Each machine TOON contains `runtime` with its device ID, bilingual name and exact
advisory DMI pairs. The generator derives `adaptations.json.runtime_models` from
these fields. Display metadata accepts no package hooks, trust grants or install
authorization. Guide JSON remains `installable: false`; executable packages keep
their existing signature/digest and explicit local-test session requirements.

Physical model-name/DMI mismatch is advisory. The interface warns and lets the
user decide whether to continue. Catalog source-ID/path checks prevent metadata
substitution and do not inspect the physical device. OS, kernel/ABI, required
tools, protected-storage and recovery requirements remain separate checks.

The snapshot preparer reads the generated model list and metadata from an exact
committed public subtree. Lock and runtime parsing support a bounded dynamic
model count (up to 128) with a one-to-one association between canonical manifest
paths, guide identities and runtime identities. A new machine TOON reaches the
total catalog, committed snapshot, offline view and hash-checked online view
without adding a Python display registry. Missing names, invalid/duplicate DMI,
duplicate IDs, unknown references and unlisted snapshot members are rejected.

Immutable old three-file snapshots without `runtime_models` retain an explicit
two-model compatibility branch. The public `MODELS` compatibility symbol remains
only for the pre-existing legacy executable maintenance facades; it does not
constrain dynamic display records or authorize new facades. Canonical local-test
bundle preparation uses identities from the same pinned catalog and still
requires exact paths, package hashes and separate session trust.

Existing production snapshot bytes, signature verification, registry sequencing
and trusted public keys are unchanged. Refreshing a production display snapshot
requires a new exact public source commit. Tests use isolated synthetic Git
commits to prove the third-model flow; that is not a published-source refresh.

Installer preparation, standalone bootstrap and generic maintenance-update
packaging enumerate display snapshots from the checked lock. They retain an
explicit executable runtime source whitelist; dynamic guide data never adds
code. Missing locked manifests or snapshot members omitted from the source
whitelist stop preparation before an incomplete artifact is produced.

## Source and release checks

Generate and check the seven TOON compatibility views, export only the explicit
source whitelist, and rebuild final package and maintenance inventories from the
same source. Validate the resulting archive and retained maintenance copy.
Local source checks, isolated Linux/Windows tests, public signing and physical
hardware acceptance are separate results.