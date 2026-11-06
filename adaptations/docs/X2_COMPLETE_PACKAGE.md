# X2 Mini complete OXP3 reuse build

The X2 complete package inherits every top-level component and every legacy
capability from the exact reviewed OXP3 `.20261030.8` package. Internal
`oxp3:*` component names, service paths and durable journals remain the shared
implementation. The external package identity is `device:x2-mini`. Model-name
mismatch is advisory; the original component interfaces, kernel and runtime
dependencies keep their applicable checks.

The authoritative X2 TOON selects `x2-complete-v1`, declares the inherited four
top-level components and 28 capabilities, and independently pins the two overlay
locks. The builder accepts three explicit inputs and never scans a model cache
or a sibling project:

```text
python -B scripts/toon_catalog.py build --model device:onexplayer-x2-mini --input base=<complete-oxp3-package> --input bridge_overlay=<reviewed-bridge-overlay> --input hdr_overlay=<reviewed-hdr-overlay> --output <fresh-directory>
```

Normal callers should use `toon_catalog.py build --model guide:x2-mini` with
explicit `--input base=...`, `--input bridge_overlay=...` and
`--input hdr_overlay=...` mappings, so the same TOON input pins also pass normal
catalog preflight. `--adaptations-root` is available only for an explicitly
selected candidate catalog tree.

The base manifest SHA-256 is
`cb39c94bbe7eb351221c380ebdb7a875a097b52c17e90bfb0f1edc22b12ab684`.
Each overlay directory contains only `overlay-lock.json` and its explicitly
enumerated regular files. The lock binds the complete base, the overlay role,
each original SHA-256 and each replacement's relative source, size and SHA-256.
Only fixed bridge or HDR paths can change; public CPU source bytes cannot be
overlaid. Extra files, links, traversal, dirty inputs and reused output paths
are rejected before package assembly.

The builder copies the complete authenticated base, applies the two overlays,
regenerates the embedded maintenance application inventory and top-level
component source digests from the final bytes, and checks every untouched base
file. It then produces a deterministic complete ZIP, validates it through the
canonical protocol and imports the ZIP again. The result reports all inherited
components/capabilities and every allowed file difference. HDR uses X2's
475/700 mapping; the complete native runtime and non-mapping assets are retained.

`x2-full-reuse.json` records the complete base provenance, inherited feature set
and X2 HDR mapping. X2 is delivered as a new complete package. The original
shared implementation retains its own installation, verification and recovery
behavior.

`full_x2_adaptation_complete` describes complete inherited package coverage. It
does not claim physical-device acceptance, panel measurements, driver loading,
actual suspend/reboot or a signed public release. Source and manifest tests,
isolated installation/maintenance tests, and hardware acceptance are recorded
separately. The full X2 package requires host engine capability `0.1.2` so older
hosts cannot misinterpret its identity or ownership rules.
