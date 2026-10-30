# OXP3 xe VMA allocation workaround — 7.2.4 candidate

This optional component changes xe's CPU-only page-table operation array from
kmalloc to kvmalloc through the unchanged upstream kprobe module. It targets only
ONEXPLAYER 3, SteamOS 3.10 build 20260924.1000, and kernel
`7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9`. It is disabled by default.

The source is from HHHHanasak1/onexplayer3-steamos-setup tag 20261029, immutable
commit `e72e814da1944da3792076689995b6fe961f5768`. The original module's three
instruction signatures match the official 7.2.4 xe ELF exactly; its structure
offsets and instruction checks remain unchanged. The module refuses a changed
kernel function. This is a temporary allocation workaround, not a replacement
kernel or a claim that the eventual upstream kernel fix uses this approach.

The boot loader verifies the exact kernel, OS build, DMI identity and module
digest, loads the existing xe driver, then loads only the pinned module file.
It does not run depmod, rebuild initramfs, change boot options or disable a
security policy. A module-loading/lockdown refusal remains a failure.

The module has no unload handler. Removing the component's activation files
prevents loading on later boots; a reboot is required to remove an active copy.
Stopping the oneshot service does not undo the workaround.

Validation covers official ELF signature matching, a fresh offline QEMU kbuild
using exact official headers and the stock GCC16 toolchain, module ELF/vermagic,
and loader rejection fixtures. The module was not loaded in the build VM (whose
kernel differs) or on physical hardware. GPU suspend, game resume, performance,
first boot and recovery remain unaccepted.

Source and licenses are included with the component. `module-lock.json` locks
the official headers/toolchain bytes and built module. The local offline build
recipe extracts these packages into a disposable guest tmpfs and invokes:

```sh
make -C /usr/lib/modules/7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9/build \
  M=/w CONFIG_DEBUG_INFO_BTF_MODULES= modules
```

Use the exact locked inputs, source, compiler and build environment from the
record; the upstream rolling-container build script does not reproduce this
7.2.4 target by itself. No installation or module-loading step belongs to the
build.
