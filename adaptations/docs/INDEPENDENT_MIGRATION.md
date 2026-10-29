# Independent adaptation migration

Boiler Installer owns the source/build/verification hierarchy under `adaptations/`.
Shared source is in `common/`; vendor-wide source is in `brands/onexplayer/common/`;
model records are in `brands/onexplayer/{oxp3,x2-mini}/`. The OXP3 bridge and its
builders/tests are in `brands/onexplayer/oxp3/bridge/`. No sibling checkout is read
implicitly. Existing public repository history remains in its original repositories;
`migration-provenance.json` retains the source identities and original inventories.

The 104-file public adaptation snapshot remains byte-exact at upstream commit
`72f864e0498c10366493c74608c5ffed62cc99ca`; this includes the public CPU baseline.
Its source plan, hashes, source-only guides, license inventory, and compatibility
limits remain authoritative. Current Battery 0.2.2 source and the native microphone
source have a separate `current-source-lock.json`. They do not replace the frozen
CPU snapshot. The current Battery source is checked against all eleven shipped
OXP3 targets and the read-only X2 variant; the microphone is checked against the
three shipped targets for each model. No suspend or physical hardware acceptance
is implied by these byte comparisons.

Run from the independent root:

```text
python -B adaptations/scripts/adaptations.py check
python -B adaptations/scripts/prepare-current-inputs.py --check-shipped
python -B -m unittest discover -s adaptations/tests -v
python -B -m unittest discover -s adaptations/brands/onexplayer/oxp3/bridge/tests -v
python -B -m unittest discover -s adaptations/brands/onexplayer/common/battery -p test_sleep_control.py -v
node adaptations/brands/onexplayer/common/battery/check-battery-sleep-ui.mjs
```

`prepare-current-inputs.py --output <new-directory>` regenerates the 24 static
Battery/microphone input records from the maintained sources and templates. It
never evaluates deployment generators or executes emitted target shell commands.
The reviewed original input lock is retained under `records/shared-20261030/`.
A future functional change requires a new reviewed source/input lock and version.

`brands/onexplayer/oxp3/bridge/scripts/build-shared-adaptations.py` consumes the
explicit current input lock and the authenticated original OXP3 base manifest
`c442eef2039779be8e3f432a65bb5cc46c432f8cd7b51734ea76168ab07136e2`.
The migration retains that base under ignored `inputs/oxp3-base/`. The original
public installer archive (555684389 bytes, SHA-256
`d8bee8bad14f4cd136c814ebcaa3299fa59202b6cdc6d26ec9c52af0e055f4d4`) is under
ignored `inputs/public-installer/`; its immutable upstream lock can reauthenticate
it. These build inputs are local retained artifacts, excluded from public source
and ordinary installer packaging. They must be included in a private migration
backup before the old workspace is removed. Public URLs remain explicit external
upstream inputs for a clean bootstrap.

The original shared base already contains the published Opti 0.3.0 additive
payload. Current Opti 0.3.1 and later changes use their own maintained project
source and pinned inputs; the old shared base is an authenticated build input,
not the current final release. Rebuilding shared inputs does not promote an old
Opti baseline over a newer released plugin. Final model rebuild/integration must
apply the reviewed current Opti update and refresh model/maintenance descriptors.

Bridge release sources now bind the exact `FlightDan/Boiler-installer` commit and
`adaptations/brands/onexplayer/oxp3/bridge` subtree. Upstream component provenance
and the existing legacy public lock are retained. Registry release records bind
the integrated `adaptations/` subtree in the same independent public checkout.
No new signing identity is generated during this migration.

Active model-guide snapshots are regenerated from an exact public
`FlightDan/Boiler-installer` commit with the explicit `adaptations` source subtree.
The snapshot preparer reads committed Git bytes, records that subtree in its
generated lock, and preserves each original guide/catalog byte. Raw refresh URLs
and guide links use the recorded commit and subtree. Frozen upstream revisions,
component source inventories and licenses remain historical provenance.

The Windows model registry now reads the distinct `boiler-device-index.json` and
`boiler-device-index.sig` assets from the installer repository's Latest release.
Every future installer Latest release must retain both assets and all referenced
model packages. The registry identity, sequence history, signature format and
public key are unchanged. Generic maintenance update streams keep their existing
identities, sequence histories, artifacts and explicit cache/update entrypoints;
sharing the repository does not combine the registries. Maintenance releases
still do not take Latest.

The catalog migration passed 13 focused source tests, including an isolated real
Git source fixture, exact committed subtree reproduction, unsafe-subtree and
remote/offline hash rejection. Final generated production locks, refreshed
maintenance/installer payloads, native UI checks and final public download/device
acceptance remain separate integration gates.

Additional authored assets/records are preserved under ignored `.local/` with an
inventory and require explicit review before source export. The sanitized root
`AGENTS.md` is public. Device credentials, signing identities, unpublished research,
old workspace Git history, local tooling and retained binary build inputs are
excluded by the source export policy. README prose was not changed.

Migration preparation is complete for source consolidation, source locks and local
static verification. Formal release, refreshed final packages, published download
verification, required upgrade/recovery tests and physical-device acceptance are
separate gates. The old workspace must remain until those gates are accepted.
