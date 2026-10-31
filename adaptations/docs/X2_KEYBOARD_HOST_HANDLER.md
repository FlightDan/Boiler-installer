# X2 keyboard fixed host handler candidate

The model TOON selects the brand `keyboard` recipe and the fixed
`x2-keyboard-v1` host capability. It has no package files, hooks, executable
entry point or caller-controlled merge/state arguments. Engine capability
version 0.1.1 is required so an older installer cannot claim an empty static
component was installed. Installer/product and plugin versions remain separate.

The installer prepares a target-bound plan before selected writes, applies the
same lifecycle used by maintenance, carries `core:maintenance` automatically,
and verifies the installed setting through the fixed readonly handler.
Maintenance has an explicit keyboard menu and `models keyboard-options`,
`keyboard-plan`, `keyboard-execute` commands. Static shared repairs retain
their existing screen and Engine journals. Keyboard has its own explicit
Recover action; it never implicitly recovers an unrelated Engine, legacy or
root-protection transaction.

Only two `Wayland` keys in `home/deck/.config/kwinrc` are merged. New plan bytes
contain target/state fingerprints, not the user's configuration. The unchanged
public Store supplies durable originals, one shared-home lock and slot
generations. Its original merge is retained as provenance only; the new wrapper
correctly ends the group at indented following headers and rejects ambiguous
managed groups/keys. User edits after install preserve the entire current file
on upgrade and restore; the receipt explains keyboard settings may remain.
An unedited file returns to its exact original bytes/owner/mode after its last
managed partition releases it. Generic static Engine writes cannot establish a
second keyboard ledger. Existing generic/legacy owners require migration.

Slot authority comes from actual GPT PARTUUID and mounted-root topology, never
from a requested A/B name. Plans bind the root path/device/inode, authoritative
partition, separately mounted shared-home UUID/device/inode, account, target,
index and pending operation. Pending state identifies its original PARTUUID; only that original partition may recover it, and the other slot displays the reason without a Recover action. Execution repeats the binding under the normal
Engine state lock followed by the shared Store lock. Root protection remains a
separate platform transaction: normal Engine `_lock` still opens its existing
write window, while this home-only handler uses `_state_lock` and never opens
or recovers that window. Device names/DMI are advisory and cannot block it.

Dependencies follow the existing public behavior: SteamOS identity,
the target deck account/home, and the fixed nonempty bounded regular desktop
entry `usr/share/applications/org.kde.plasma.keyboard.desktop` without links.
No exact release/build or Exec/version gate is inferred. The other four components retain their independent system constraints. Presence does not prove executable
activation; guest programs and KDE are never launched during staging. Hardware
or actual virtual-keyboard behavior requires its own acceptance.

The public transaction sources and AGPL license are retained directly under
`maintenance/keyboard_runtime/vendor` with `public-source-lock.json`. They came
from the authenticated c442 public package and its published installer source.
The five public CPU sources are not consumed or changed. A fork needs no old
working directory or binary base to assemble these retained runtime sources:

```text
python -B adaptations/scripts/build-keyboard-runtime.py --project-root . --output <fresh-runtime-output>
python -B -m unittest discover -s tests -p test_x2_keyboard_dynamic.py -v
```

The builder independently pins its closed runtime inventory, copies only its
explicit files and rejects extras, changed locks, traversal and linked paths.
It supersedes the earlier unanchored `copytree(public)` prototype, which must
not be integrated. TOON preflight rehashes that same host inventory. Standard
installer, bootstrap and maintenance packaging retain its explicit runtime
closure; this runtime output alone is not an installable device package.

`test_x2_keyboard_mounts.py` is a separate opt-in native test. It requires the
specifically tagged 512 MiB disposable QEMU disk, creates actual GPT A/B root
partitions and mounts the same home partition beneath both roots. Portable
single-directory tests do not replace this mount/owner/lock verification.
All fixtures are synthetic; no user disk or device is used. The overall X2
model remains incomplete and unsigned, with other declared adaptations pending.
