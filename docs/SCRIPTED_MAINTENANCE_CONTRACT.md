# Authenticated model maintenance hooks

A DevicePackage can opt into the engine-managed maintenance lifecycle with this
exact declaration:

```json
{"maintenance_entry": {"kind": "engine-hooks-v1"}}
```

The object accepts only `kind`; it names an engine contract, not an executable.
Component hooks still use the existing `hooks.apply`, `hooks.verify`, and
`hooks.restore` inventory paths. Every referenced script must be present in the
authenticated package inventory. A legacy string such as
`"maintenance_entry": "scripts/maintenance.sh"` remains readable metadata for
compatibility, but cannot enable generic scripted execution. Missing or unknown
contracts fail closed for scripted upgrades. File-only packages do not need this
declaration.

The declaration is not a trust grant. Normal discovery requires a verified
DeviceIndex, a pinned package digest and source identity, compatible actual
target OS/architecture/kernels, and existing component ownership. Planning must
refuse implicit adoption, downgrades, edited managed files and ownership
collisions. Execution requires the exact reviewed plan hash, revalidates its
signed discovery/package/environment/state anchors, and creates a protected
private snapshot before any hook runs. It must execute the authenticated
snapshot, never a mutable download cache or user-provided script path.

Only selected component upgrades and their required upgrades execute, in
dependency order. An unselected or unchanged component must not run apply or
verify hooks as an update side effect. The engine applies each upgraded
component's declared files and calls its authenticated apply/verify hooks inside
that component's normal transaction journal. There is no arbitrary dispatcher
outside the engine journal and no general CLI `allow_hooks` switch. The engine
records the committed component generation and the updater retains the reviewed
plan, source provenance and per-component execution receipts.

Hook processes receive a fixed system executable path, root home, locale, and
the engine's target/transaction identity. Ambient shell startup, loader and
Python environment variables are not inherited. Trusted integration can pass
explicit bounded `BOILER_*` context without replacing target or transaction
identity; authenticated maintenance updates pass no extra ambient context.
This environment hygiene does not sandbox an authenticated root script.

Hook execution can modify model-specific state beyond declared files. A failed
hook therefore preserves its engine journal and failed updater receipt behind a
`manual_recovery` barrier. Later discovery may remain read-only, but another
write attempt must not silently recover, clear, overwrite, or replay that failed
scripted transaction. Previously committed components in the same batch remain
recorded. This contract does not promise atomic batch rollback or infer complete
rollback from a declared `restore` script. An authenticated model recovery
lifecycle and explicit operator review are required for those external effects.

The initial OXP3 compatibility package does not declare this contract. Its
legacy-adaptation hook is bound to the managed installer guest and can stage the
old installer once; it is not a live upgrade implementation. Existing legacy
adoption also produces granular `oxp3:*` ownership rather than ownership of the
single installation adapter. A future OXP3 maintenance package must implement
that real lifecycle, component mapping and recovery behavior before opting in.
Do not add this declaration merely to bypass the current refusal. Scripted
fixture tests establish software behavior, not device upgrade, reboot or
hardware acceptance.

## Static updates that retain existing ownership

A signed static `ComponentManifest` may declare `retain_owned_paths: true` to
update listed files while preserving omitted paths already owned by that exact
component. The default is false. This is an upgrade-only contract and cannot be
combined with hooks or used to adopt unowned files. It does not authorize a
pattern, directory walk or machine-specific path supplied by a script.

Planning resolves retained paths from the installed component's committed
journal and exact ownership records. Each retained entry binds its target,
prior transaction, existence, size, SHA-256, mode, uid and gid in the reviewed
plan. Execution rebuilds the plan, then the engine rechecks live ownership and
bytes under its transaction lock. Case aliases, overlapping paths, stale owner
records and edited retained files are rejected. Absent owned paths remain absent.

Retained files are not rewritten during application. The new journal preserves
their current generation for rollback, while earlier journals retain original
pre-install backups. Historical legacy revision receipts remain historical;
retaining one does not assert that the old maintenance engine installed the new
payload. Both engines continue to reject unexpected file changes through their
own integrity checks. Equal-version, identical static content is reported as
unchanged, never as a fabricated functional upgrade.
