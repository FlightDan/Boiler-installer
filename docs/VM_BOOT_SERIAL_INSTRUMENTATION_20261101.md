# Disposable VM boot console instrumentation

## Confirmed failure

Normal SteamOS virtual boot checks require a real `SteamOS ... (ttyS0)` login
banner. The tested generated SteamOS GRUB configuration selected `console=tty1`
and did not enable a serial console. The supported model's serial-getty policy
also requires the explicit `oxp3.serial-console=1` kernel parameter. Waiting for
the serial banner without providing those parameters can reject a bootable
system.

A controlled local test kept the firmware, rEFInd, steamcl, GRUB, installed
kernel, partition layout, memory and disk contents. It appended only
`console=ttyS0,115200n8 oxp3.serial-console=1` to the generated GRUB boot commands
in a fresh copy-on-write overlay. The guest reached the real SteamOS serial
login. This is diagnostic evidence for the instrumented overlay; it is not an
unmodified physical-device boot or a Steam frontend acceptance result.
Both a standard-VGA control and the existing production virtio graphics
configuration reached that login with 2 GiB of guest memory. The final full
hash comparison confirmed that the original prepared image and native VHD
retained their pre-diagnostic bytes. No graphics configuration change is
required for this fix.

## Delivery behavior

The normal SteamOS VM boot path prepares its newly created disposable overlay
with one fixed offline guest task before starting the EFI observation timer.
Preparation preserves the existing EFI chain and kernel paths, changes only
the permitted generated EFI-slot GRUB commands, and records before/after
hashes and cleanup. It handles the supported generated forms idempotently and
refuses conflicting or unsupported configurations.
Root selection supports the observed 3.8 `--fs-uuid` form and 3.10
`--part-uuid` form. Each identifier is checked against the corresponding
mounted slot filesystem or GPT partition; the two identifiers are not
interchangeable. The original 3.8 recovery has only slot A, so its read-only
syntax check does not establish a complete generated A/B installation result.

The backing image and deployed media retain their original bytes and physical
serial policy. Preview, Windows EFI probe and interactive UI protocols retain
their own existing acceptance signals. The normal VM check continues to
require a real SteamOS serial login; a running QEMU process or a screenshot is
not a substitute.

No model package, CPU policy, public index, installation-plan format or recovery
journal format changes are part of this fix. The installer and retained
maintenance candidate use new immutable build identities and regenerated
inventories. Existing failed-boot virtual sessions use the public recovery
path, which rechecks completed partition contents before another boot attempt.

## Acceptance boundaries

- Controlled diagnostic: actual EFI-to-SteamOS serial login observed using a
  fresh overlay; original source integrity is checked separately.
- Source regressions, genuine Linux preparation, final packaged executable,
  public recovery and final artifact inventories require their own recorded
  candidate results. Diagnostic success alone does not complete these gates.
- Installed maintenance apply/adopt/verify/restore remains a separate VM gate.
- Windows visual acceptance is deferred at the user's request because the
  available desktop did not render usable pixels.
- Physical hardware, firmware, Steam frontend, games, signing, publication and
  public-download acceptance are not established by these local VM tests.
