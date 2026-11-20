# X2 Mini battery lifecycle compatibility — 2026-11-18

The legacy lifecycle starts and stops `oxp3-battery.service`. Candidate 2026.11.18.2 keeps that target and the original `charging/004.bin` logical path with an X2-only oneshot unit invoking `/usr/bin/true`. It has no battery provider, charge control or hardware access. The EFI provider lock and fan-driver dependency are unchanged. This supersedes the seven-file candidate described in the preceding historical record; the seven locked telemetry/sleep files remain unchanged, and the compatibility unit is separately pinned.

Source tests cover the eight delivered targets, three removed provider files, exact no-op unit and read-only telemetry. The actual package build, retained-maintenance upgrade and recovery, systemd execution, and physical-device acceptance remain separate pending gates. No release is claimed.
