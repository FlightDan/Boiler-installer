# X2 Mini battery profile — 2026-11-18

The normal X2 complete-package CLI produces version 2026.11.18.2. It uses the seven locked telemetry and sleep files and an X2-only compatibility unit at the original service path. The fixed first-boot and restore runners still start and stop that service name. The X2 unit runs only /usr/bin/true and contains no charging action. The OXP3 charge provider, D-Bus policy, and SteamOS Manager remote are absent from the X2 payload.

The component ID and sparse logical file paths are retained. The reviewed EFI provider lock still covers the legacy options file and its existing fan-driver dependency. Historical builder calls can reproduce the older profile.

The locked inputs, X2 telemetry without charge controls, rejected charge writes, TOON views and canonical source inventory were verified. The new local ZIP was rebuilt and reimported. Its extracted eight-file component was installed and restored in a temporary Linux root, and systemd verified its unit. The shared S4 suite passed 20 Linux fixture tests.

The existing X2 Mini B slot was repaired through its original journal, with original recovery snapshots retained. The plug-in returned battery telemetry on three reads and saved the current S4 policy without changing it. Decky reloaded successfully. The full ZIP was not deployed on a device. Migration from an existing eleven-file installation to the new eight-file profile and inactive-slot acceptance are pending. OXP3 hardware verification and publication are pending.
