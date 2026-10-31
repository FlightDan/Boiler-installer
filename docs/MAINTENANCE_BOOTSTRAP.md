# Standalone maintenance bootstrap

`scripts/package-maintenance-bootstrap.py` creates a deterministic, self-contained
zipapp from the reviewed maintenance and protocol runtime, the current
`maintenance/release.json`, the pinned public key, and
`scripts/maintenance_bootstrap.py`. The generated `.pyz` does not import code
from the working directory or `PYTHONPATH`; launch it with Python isolated mode:

```text
python3 -I -B boiler-maintenance-bootstrap.pyz --index-url <generic-maintenance-index.json> --signature-url <generic-maintenance-index.sig>
```

The generic maintenance index and detached signature URLs are explicit inputs.
The model/device index is a separate stream and is rejected by registry identity.
The installer refreshes a new temporary cache, verifies the exact index bytes
against the embedded official Ed25519 key, and looks for the exact package
version in the zipapp's `maintenance/release.json`. It never falls back to a
cached index. If that version is absent or revoked, installation stops before
creating maintenance state or writing target files. Current source candidates
that have not been published therefore cannot be installed from a historical
index.

The package archive and its manifest are checked against the signed index and
the full package inventory before use. First install accepts only the static
`bundle:maintenance` package containing `core:maintenance`; it does not invoke
downloaded hooks. The real target SteamOS release, architecture, and kernel
environment are discovered before `Engine.apply` performs its write-ahead
transaction. Existing maintenance ownership, nonempty runtime directories,
conflicting target files, and unknown state are preserved and refused.

An interrupted first install can be recovered explicitly:

```text
python3 -I -B boiler-maintenance-bootstrap.pyz --recover
```

This delegates to `Engine.recover()` and the existing live-root protection
window. It keeps journals and unknown state intact on error. After recovery,
only an empty ownership state with terminal `restored` or `rolled_back`
journals can be used for another first-install attempt. Empty parent directories
left by rollback are retained and may be reused.

Build an artifact outside the checkout at a fresh output path:

```text
python -B scripts/package-maintenance-bootstrap.py --output ../artifacts/boiler-maintenance-bootstrap.pyz
```

The output is a local bootstrap candidate, not a published or independently
signed release. Distribute it only through the project's trusted source or
release process. The installer authenticates the maintenance index and package;
it does not make an untrusted copy of the bootstrap zipapp trustworthy.
