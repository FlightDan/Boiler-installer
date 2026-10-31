# X2 Mini partial shared adaptation builder

This source builder covers **four static components only**: the existing Battery
0.2.2 sleep controls (X2 charging remains disabled), microphone mute
synchronization, an explicitly selected game-mode environment isolation
drop-in, and the existing guarded KDE slot-switch tools. It does not complete the X2 Mini adaptation guide. No hardware,
suspend/reboot, display, firmware, or new physical installation acceptance is
claimed.

The model TOON and its referenced shared recipes determine the component
selection, target files, modes and compatibility. `source-lock.json` authenticates
the explicit payload source bytes. The builder reads project-local source files;
it does not scan an old model cache, import a legacy deployment hook, read a
private research tree, or use a prebuilt installer as a normal build input.

The intended adapter command, from the public project root, is:

```sh
python3 -B adaptations/brands/onexplayer/x2-mini/bridge/scripts/build.py \
  --project-root . --output dist/x2-shared-FRESH-BUILD
```

The adapter must be paired with the reviewed installer, maintenance and Windows
selection source changes. A new output directory is required. The output contains
the package, deterministic ZIP, exact ZIP re-import, and `verification.json`.
`build_verified` means the artifact build was checked; the separate
`full_x2_adaptation_complete:false` remains authoritative.

The isolation resource is the byte-exact 222-byte public installer resource,
SHA-256 `9616729dac8be729b56bd201b8cfbbb1fcd0eb2fb7163944638306856e85a13b`.
Its only target is
`home/deck/.config/systemd/user/gamescope-session.service.d/90-oxp3-desktop-env-isolation.conf`.
It clears `WAYLAND_DISPLAY` and `WAYLAND_SOCKET` when the next native game-mode
session starts. Installation and saving do not start a service, switch sessions,
reboot or sleep. It remains off until explicitly selected; the Windows
`selection_policy:explicit-opt-in` handling preserves existing component defaults.

The new component requires x86_64 SteamOS 3.10 build 20260924.1000 and only kernel
`7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9`, a target deck account, the native
`usr/lib/systemd/user/gamescope-session.service`, and `usr/bin/systemctl`.
No device name broadens these requirements. Existing symlink/junction and unsafe
ancestor checks remain in force. Newly needed fixed user directories are owned by
the target deck UID with mode 0755; existing directory ownership and modes are
preserved. A newly installed environment file is root-owned 0644; an existing file
keeps its UID/GID through the existing Engine transaction.

The slot-switch component installs the unchanged public controller, desktop UI,
restricted launcher, polkit rule, two desktop entries, and its MIT license. It
requires the same exact baseline plus all six image-owned executable tools:
`python3`, `pkexec`, `kdialog`, `steamos-bootconf`, `systemctl`, and `busctl`.
Dependencies resolve within the target image namespace; a host executable cannot
satisfy a missing guest tool. Privileged code and rule targets must remain
root-owned and have the supported modes; untrusted existing code is refused.
Existing supported files have exact original bytes and metadata backed up.
The component is off until explicitly selected. Installation never changes
`/esp`, a slot configuration, updater service state or a reboot request.

Only a later explicit invocation of the original UI/controller may inspect and
change boot selection. The retained controller validates both A/B slots, the
target's invalid flag, updater state and the fresh confirmation token. Cancellation,
an active RAUC install, unknown state, or a stale token refuses switching. Failure
handling preserves concurrent boot edits and restores owned changes where safe.
These action paths have mocked regression coverage; actual switching, reboot and
physical-device recovery have not been accepted here.

Installer and maintenance use the same Engine file ownership, authenticated
original backup, verification and conservative restoration. Restoration restores
the exact original bytes, metadata and absence; empty created user directories
remain. A modified managed target is preserved and causes verification/restore
refusal. After the OS changes, install is blocked while verification and restore
of the recorded transaction remain available.

The additive maintenance transition is restricted to the exact former X2 package
anchors `874ee0c4bf0f46ec5af94f7452ebe0d656b50bb2b5c673a42392c8ba9191db6c`
and `4765603630661f26e925be214d5da684bae81109240a23aa306333a2d99459e6`.
All old component definitions must remain identical; only the reviewed isolation
and slot-switch components may be added, and no hook is allowed. An unlisted retained
package is not automatically migrated. The old package and per-component
transactions remain retained; adding the new option does not rewrite the user's
old installed component selection. Missing, corrupt or semantically ineligible
current bundles fall back to the authenticated retained package; optional discovery
cannot block verification or restoration of the old package.

Run the exported synthetic tests as an ordinary Windows source check or as root
inside a controlled Linux test environment:

```sh
python3 -B tests/test_x2_shared.py --root . --package dist/x2-shared-FRESH-BUILD/package
python3 -B tests/test_x2_slot_controller.py --root .
```

All test targets are temporary synthetic directories. External process execution
is blocked in the tests. Windows skips real Linux installation result journals,
UID/mode and symlink checks; those skips are not Linux acceptance. The WPF source
selection check is not an actual UI acceptance test; the primary integration must
build and exercise the Windows application.

## Remaining work

| Guide item | Retained source and exact missing lifecycle work |
| --- | --- |
| input / multifunction | Brand common `scripts/input_profile.py`, templates and the exact-kernel published hid-oxp module/capability map exist. Need a separate X2 component, unique `1a86:fe00` interface 02 checks, actual button mapping, module indexing and activation, preservation of gyro profile/calibration, and symmetric recovery. Multifunction edits an existing capability-map block; it is not a fixed whole-file replacement. |
| gyro | Brand common `scripts/gyro/` and public .ko / InputPlumber bytes exist. Need exact ACPI/I2C/0x27, kernel and InputPlumber 0.78.0 gates, new-device calibration, recoverable `gyro_binding.py` start/stop, quiescence before file removal, depmod and service lifecycle. Historical direct insmod/rmmod wrappers are insufficient. |
| desktop-hdr | X2 four scripts and the authenticated full native runtime exist. Need standalone KWin/Qt ABI checks, SDC4301 / KDE Wayland / no-enabled-external-display gates, large runtime directory transaction, 475/700 trial, first HDR10 frame, independent ten-second recovery and user-owned launcher install/removal interlock. Static file copying does not reproduce that lifecycle. |
| game HDR | Existing OXP3 472/1100 is not an X2 475/700 implementation. An X2-specific implementation and display validation are missing. |
| GM7 / split-lock | Must generate changes from the actual SSD and accepted boot files; both can own the same GRUB file. Need one original and coordinated recovery. A fixed static file must not overwrite image-specific boot content. |
| keyboard | Must preserve and merge the target user's existing `kwinrc` Wayland group and verify the Plasma Keyboard desktop entry. A fixed payload would overwrite unrelated user configuration. |
| slot-switch | Static tool installation, maintenance and original-file restoration are implemented with the retained action guards. Actual A/B switching/reboot and physical-device acceptance remain separate. |
| partition-mount | Common `partition_mount.py`, `shared_storage.py`, `ntfs_steam.py` and helpers exist. Tool actions persist an owned block in `/etc/fstab`; removing only the static files would leave mounts configured and could remove the cleanup entry. A reviewed restore gate, reuse of the tool's fixed lock, and synthetic fstab tests are necessary before packaging it. No automatic fstab edits, mount actions or new concurrency contract are included in this release. This baseline does not include every later NTFS change. |
| windows-reboot | Common helper/plugin files exist. Need exact Windows EFI entry, prerequisite tools, user/privileged boundary, Decky dependency, file ownership and independent action/restore validation. |
| Wi-Fi | Select actual chip and preserve existing firmware/compressed variants; do not universally install BE201 firmware. Kernel/initramfs and boot health remain separate gates. |
| Manager / native TDP / overlay | Existing public OXP3 reference sources exist. Need actual X2 interfaces, units, RAPL limits and readbacks; OXP3 10–40W / PL2 limits do not establish X2 compatibility. |
| PL4 175W | User demand and feedback exist, but there is no separate retained public implementation. Need verified PL4 interface, original-value backup, application, persistence and recovery. PL1/PL2 cannot substitute for PL4. |
| other optional features | Flathub remote preservation, rEFInd/EFI, Opti2XMX/Decky, Waydroid r2 and LSFG licensing/dependencies remain their own adapters. Presence in a guide does not make them installed by this partial package. |
| excluded OXP3 features | DSB, EC/fan, charging, lights, OXP3 CPU policy and old volume/battery runtime remain excluded. No private dynamic-power research is included. |

The 2026-10-28 user report that a previously tested X2 machine worked normally is
retained as user feedback. It is useful prior experience, but does not independently
prove this new package, each component, brightness measurement, fresh installation,
maintenance transition or reboot/recovery acceptance.
