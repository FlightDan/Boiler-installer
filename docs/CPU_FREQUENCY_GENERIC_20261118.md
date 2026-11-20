# Generic CPU frequency driver activation — 2026-11-18

The generic CPU page provides `cpufreq:intel` and `cpufreq:amd`, independently of CPU microcode. Each group permits one vendor. Explicit current-computer detection matches the CPU vendor only, fills both groups independently and preserves manual opt-outs for the reviewed target. Model packages and imported/local images cannot receive generic driver activation.

These components enable the existing kernel frequency driver at startup through its documented `status` interface. They do not download a driver or add a CPU performance policy. A self-contained script, fixed-vendor oneshot systemd unit and multi-user target Wants drop-in are ordinary Engine-managed files. Installation into an offline image does not run the service. GRUB, EFI, SSD repair and microcode/initramfs ownership are unchanged.

The runtime checks the actual CPU vendor before accessing the vendor interface. Active drivers require no write. Intel off/passive and AMD disable/passive/guided states request active and read back the actual status. Missing interfaces and unsupported states are reported as unavailable; write/readback failures are reported as activation failures rather than successful activation. The component does not explicitly set governor, EPP, frequency limits, TDP, fan or EC parameters. A driver mode switch may cause the kernel to reset its own settings; the unit is ordered before existing CPU policy services.

Restoration removes or restores the three persistent files through the existing durable Engine. A reboot then returns to the operating system's original startup choice. Uninstallation does not force a live driver off or invent a previous CPU policy. `core:maintenance` is a required dependency.

References: [Intel status interface](https://docs.kernel.org/admin-guide/pm/intel_pstate.html#global-attributes), [AMD status interface](https://docs.kernel.org/admin-guide/pm/amd-pstate.html#global-attributes).

Delivery gates:

- Source/runtime regression checks: complete. Full source suite: 1091 tests passed with 98 platform skips; final CPU frequency suite: seven tests passed. Covers supported status transitions, fixed paths, vendor mismatch, missing interfaces, write/readback failure, exact static assets, private download payload and tampering refusal.
- Windows Release build and ordinary UI behavior: complete, zero warnings/errors; eight application suites passed. Includes four CPU rows, independent groups, brand-only detection, manual overrides and restored maintenance dependency after target switching.
- Genuine Linux VM offline installation, repeated installation and restoration: complete, 19 tests passed. Both vendors use the real downloader private payload, then installation and guarded restoration. Offline sysfs/EFI/initramfs fixtures remain unchanged; modified installed files are protected.
- Prepared runtime inventory and production packaging validation: complete. All 117 members are checked against generated sizes/hashes; 24 components and 107 retained-maintenance files match final sources. No complete portable installer package was requested or produced.
- Visual acceptance: blocked; the current WPF environment returned no visible pixels even for a solid probe. Control/layout behavior checks do not establish visual acceptance.
- Physical driver mode switching: not performed; no real sysfs interface is written during source tests.
- Publication: not requested.

The retained adaptation workspace indexes the local application build, public-whitelist source snapshot, generated integration runtime and validation evidence under `artifacts/cpu-frequency-20261118` in `MANIFEST.json`.
