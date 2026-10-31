# Locked xevma source rebuild

This recipe rebuilds the unchanged public `xevmafix.c` and Makefile from
`HHHHanasak1/onexplayer3-steamos-setup` commit
`e72e814da1944da3792076689995b6fe961f5768`. It targets only kernel
`7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9`.

`xevma-rebuild-inputs.json` lists the exact immutable source/notice URLs, sizes,
SHA-256 and Git blob identities, plus all 60 official headers/toolchain package
URLs, sizes and SHA-256 values. It also records the public module-lock URL/hash,
the compiler build environment and expected artifact identity. Acquire these
inputs separately and place each package under its recorded `local_file` name.
In particular, the official headers archive's cache name is `headers.pkg.tar.zst`.
Only the existing public `xevmafix.c`, `Makefile`, `LICENSE` and
`UPSTREAM_LICENSE` files named in the recipe are consumed from the source directory.

Run the build only in a disposable QEMU Linux VM with no virtual network
interface, no physical-disk passthrough and read-only input media. The script
requires root and explicit acknowledgement of this environment. Python 3,
GNU tar with zstd support, `mount`, `umount` and `chroot` are bootstrap tools;
the GCC/make/binutils and libraries used for the module come from the locked
official packages. No package installation hook is run. The script performs
no downloads, system installation, service activation or module loading.

From the project root, verify inputs first:

```sh
python3 scripts/rebuild-xevma.py \
  --recipe adaptations/common/resource-inputs/intel-xevma-724/xevma-rebuild-inputs.json \
  --source-dir adaptations/brands/onexplayer/oxp3/current/xevma-20261029 \
  --package-dir /mnt/locked-packages --verify-only
```

Inside the disposable offline VM, use a fresh output path outside both input
trees. No old `.o` or `xevmafix.ko` is accepted as a build input:

```sh
python3 scripts/rebuild-xevma.py \
  --recipe adaptations/common/resource-inputs/intel-xevma-724/xevma-rebuild-inputs.json \
  --source-dir adaptations/brands/onexplayer/oxp3/current/xevma-20261029 \
  --package-dir /mnt/locked-packages --output /tmp/xevma-source-build \
  --disposable-offline-vm
```

The tool snapshots and checks package bytes, extracts them into a fresh chroot,
copies only the original C/Makefile into `/w`, and invokes:

```text
make -C /usr/lib/modules/7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9/build M=/w CONFIG_DEBUG_INFO_BTF_MODULES= modules
KBUILD_BUILD_TIMESTAMP=2026-10-30 00:00:00 UTC
KBUILD_BUILD_USER=boiler
KBUILD_BUILD_HOST=offline-audit
```

It checks output size/SHA-256, ELF64/x86-64, exact vermagic and the module's GPL
declaration, then writes `rebuilt-xevmafix.ko`, `build-result.json` and `build.log`
under the output directory. The known output is 397512 bytes, SHA-256
`10a473fca8031ab43206f7a9146413cf781355ddedd3ff5a66cf59bb5369461b`.
The pahole warning is expected because module BTF is disabled by the recorded
build option; it is retained in the log.

The C source declares `MODULE_LICENSE("GPL")`; its retained GPL v2 text and the
upstream repository's separate MIT notice both remain unchanged. The MIT notice
does not convert the kernel module to an MIT-only module. The exact GPL variant
is not inferred solely from `MODULE_LICENSE`. The rebuild script itself uses
the project's AGPL-3.0-only license.

Successful reproduction establishes correspondence of these source/package
bytes with the locked module. It does not establish physical GPU compatibility,
boot/service/suspend/performance or recovery acceptance, signing/lockdown policy,
or production release readiness. The module has no exit handler: restoring its
files or stopping a oneshot service does not unload an active copy. No loading
or unloading step is part of this recipe.
