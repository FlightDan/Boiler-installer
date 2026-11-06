# Boiler Maintenance 0.1.0+maintenance.1 — local candidate

The internal revision advances because the shared protocol runtime now contains
bounded local device-package intake and automatic signed-index transport
fallback. The display version remains 0.1.0-preview. Both explicit runtime
whitelists include protocol/local.py; generated inventories are rebuilt from
these exact sources. Component identities, installation defaults, existing model
hooks and the maintainer's component upgrade policy are unchanged.

The new import-device command only validates and copies bytes with an explicit
developer intake flag. It neither executes hooks nor grants durable trust.
Normal authenticated package updates still require their signed index and
manifest digest. Automatic fallback re-verifies the cached signature and only
handles network failure, preserving authentication and rollback rejection.

The final source suite passed 214 tests (28 platform/environment skips).
Independent review and remediation are recorded in
[the device update record](DEVICE_CATALOG_UPDATE_20261024.md).

This is an unsigned package candidate. It has not been signed, published,
downloaded as a new public release, or accepted through device upgrade, reboot
and recovery. Existing maintenance-v0.1.0 assets remain intact. A future release
must satisfy the existing hardware gates, use the established signing identity,
publish all referenced assets before its signed index, and then complete public
download/signature/hash and actual upgrade verification. A local ZIP or a source
test result does not satisfy those gates.
