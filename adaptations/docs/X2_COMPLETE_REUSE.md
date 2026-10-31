# Complete X2 reuse of the public OXP3 implementation

The complete X2 DevicePackage uses external identity `device:x2-mini` and keeps
the OXP3 implementation component IDs, capabilities, script entry points and
shared-home journals. This is intentional implementation reuse: changing the
internal IDs would lose the original package's dependency and recovery rules.
The same fixed `oxp3-legacy-options-v1` selection and model maintenance contracts
are accepted only for the two explicit external OXP3/X2 identities.

## Rebuild the bridge overlay

From a public Boiler checkout, provide an explicit complete OXP3 `.8` package
whose manifest SHA-256 is
`cb39c94bbe7eb351221c380ebdb7a875a097b52c17e90bfb0f1edc22b12ab684`:

Produce that package with the existing `toon_catalog.py build --model
guide:oxp3` pipeline and its nine explicit input mappings documented in
[TOON_MODEL_CATALOG.md](TOON_MODEL_CATALOG.md). The resulting
`<oxp3-build-directory>/final/package` is the bridge command's `--base`.
The pipeline starts from its explicitly pinned public `.2` base and rebuilds
`.3`, `.4` and `.8`; this does not discover or trust an old model cache.

```text
python -B adaptations/scripts/build-complete-bridge.py --base <complete-oxp3-package-directory> --output <fresh-overlay-directory>
```

The tool validates that package, accepts only four fixed source members, verifies
their original bytes and produces only the four reviewed outputs plus a lock.
The canonical LF lock SHA-256 is
`a88f7d51fbe3b54d3aa1aab9ddfd8b5acc094c589dd9cfe1ee1415b2903c553d`.
No old workspace, sibling checkout, private directory, cache search or package
program is imported or run. The complete package builder consumes this explicit
overlay together with the separately reviewed X2 HDR overlay.

The source generator is AGPL-3.0-or-later. Its original bridge work retains the
Boiler/OXP3 source license; the speaker derivative retains the MIT upstream
notice in its source, and the complete package preserves all original source
and kernel-driver GPL notices and licenses. The transform does not remove or
replace any original license member.

## Installation and maintenance

The full X2 package requires Boiler engine capability 0.1.2. Older hosts must
refuse it before executing a component. New installation passes explicit chosen
OXP3 adaptation IDs to the same authenticated hooks. On-device maintenance uses
the same package contract, retains the exact package inventory, binds the plan
to the target and state, and verifies/restores from the retained package even
if the downloadable cache has disappeared.

X2 uses the same generic package retention pointer and transaction lifecycle as
OXP3. The current installation retains its authenticated package and journals;
its own verify/restore operations continue when the current cache is missing
or corrupt. New installation starts with the selected complete package.

## Model advice and real dependencies

Model-name mismatch is advice. The speaker and xe allocation loader continue
past a different or unavailable DMI name. They retain their actual codec/card,
jack, exact kernel, operating-system and authenticated module checks. Legacy
maintenance compatibility already uses SteamOS facts rather than DMI names.
HDR has its own reviewed advisory mapping overlay.

Kernel drivers retain board-policy selection: oxpec chooses an EC register
layout from its existing DMI table (including X2Mini PRO), while hid-oxp selects
button/MCU quirks. These are the original hardware implementation, not host
package selection gates; no replacement hardware profile or rebuilt driver is
invented by this bridge.

## Validation boundary

The reusable source tests cover X2 external identity with the shared selection,
its retained package maintenance after cache corruption/loss, trust and fixed
maintenance-contract requirements, model advice and unchanged dependency checks.
Facade process calls are mocked;
these tests do not establish device hardware, reboot, KDE or firmware acceptance.
Run real legacy bridge, Linux slot/recovery and final package checks separately.
