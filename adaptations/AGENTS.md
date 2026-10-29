# Adaptation collection rules

- Preserve the hierarchy: `common/` for shared inputs, `brands/<vendor>/common/` for vendor inputs, and `brands/<vendor>/<model>/` for model records. The OXP3 bridge lives in `brands/onexplayer/oxp3/bridge/`.
- The checked source collection in `source-plan.json` and `source-lock.json` is a byte-exact snapshot of public `FlightDan/OneXPlayer3-SteamOS` commit `72f864e0498c10366493c74608c5ffed62cc99ca`. Its CPU sources are an immutable public baseline. Do not regenerate from a private/current working tree.
- `scripts/adaptations.py check` verifies this snapshot, licenses, model guides, and source-only defaults. These records do not authorize automatic installation or claim live hardware acceptance.
- Bridge builders consume only explicitly supplied authenticated public archives or explicit hash-locked inputs in `inputs/`. Keep the existing lock files, upstream repository names, license notices, and migration provenance. No fallback to the old workspace or sibling projects.
- Additional source or build inputs require an explicit reviewed inventory, size/SHA-256, provenance, and license record before source export. Do not promote `.local/` material or private helper scripts by a recursive scan.
- Run the collection and bridge tests after layout or source changes. Rebuild affected packages and verify imports before advancing a release. Preserve the original model-specific compatibility and recovery gates.
