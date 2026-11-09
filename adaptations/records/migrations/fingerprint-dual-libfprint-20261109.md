# Dual-backend libfprint: build recipe and provenance

This record describes how to reproduce the merged libfprint that carries both
sensor backends, and what has been proven about it. It is a build recipe, not a
shipped artifact: no merged source tree, binary or package is stored here.

## Why a merge is needed

fprintd exposes exactly one D-Bus name, `net.reactivated.Fprint`. Two separate
libfprint builds cannot both serve it, so BF63112 and FTE3600 have to coexist in
one library. The maintained upstreams provide one driver each.

## Inputs

| Input | Identity |
| --- | --- |
| Base tree | `SamSeven777/libfprint-fte3600` @ `6efa37679265f97973f6af15a18b375e3c5020a5` |
| BF driver | `pythontyphon/libfprint-bf63112-oxp3` @ `6a19d67d2a072dac4b6a467434966c55af76b602` |
| BF source archive | `libfprint-bf63112-6a19d67.tar.gz`, SHA-256 `07d8400b4986870a209bce4c0740ca99b84a06639c6509f70395b5e1d38b6374` |

The BF archive digest matches the value already anchored by the legacy native
runtime build script, so both come from the same reviewed bytes.

Both trees are libfprint `1.94.100` and already declare `['c', 'cpp']`.

## File attribution

Determined by normalized per-file comparison (BF-only / FTE-only unique lines),
not by assumption. See the counts in the table.

| File | Taken from | Reason |
| --- | --- | --- |
| `libfprint/fpi-print.c` | BF | BF adds `fpi_print_set_image` and `fpi_print_copy_raw_data`; the two FTE-only lines are the equivalent inline form BF already factored out |
| `libfprint/fpi-print.h` | BF | Declares the two added functions (5 BF-only lines, no FTE-only) |
| `libfprint/fpi-image-device.c` | BF | BF adds the optional custom-matcher path; without `image_to_print` the extra branch is inert for FTE |
| `libfprint/fpi-image-device.h` | BF | Adds `image_to_print` / `add_print` / `match` slots (23 BF-only, no FTE-only) |
| `libfprint/fp-image-device.c` | BF | BF's `fpi_print_set_type` handling covers both; the two FTE-only lines are the pre-refactor inline form |
| `libfprint/fpi-spi-transfer.c` | BF | BF adds `fpi_spi_transfer_configure` and `fpi_spi_transfer_get_block_size`; FTE's differing block-size reads are the same logic inlined |
| `libfprint/fpi-spi-transfer.h` | BF | Declares the two added functions (6 BF-only, no FTE-only) |
| `libfprint/fpi-device.h` | FTE | Adds exact `spi_acpi_hid` and keeps `spi_acpi_id`, which the BF driver still uses (`bf63112.c:1431`) |
| `libfprint/fp-context.c` | FTE | `spi_device_matches()` matches an exact ACPI HID and still honours the legacy `spi_acpi_id` substring path |
| 11 BF-only files | BF | Driver, SIGFM matcher, model tests, replay example, README |

BF-only files: `drivers/bf63112.c`, `drivers/bf63112-sigfm.c/.h`,
`sigfm/{sigfm.cpp,sigfm-ransac.cpp,sigfm.hpp,binary.hpp,img-info.hpp}`,
`tests/test-bf63112-sigfm.c`, `examples/bf63112-replay.c`, `README-BF63112.md`.

## Meson wiring

- `meson.build`: add `'bf63112': { 'spi': true, 'helper': ['udev','sigfm'], 'optional': true }`
  to `drivers_info`; add an `elif i == 'sigfm'` helper branch resolving
  `opencv4 >= 4.4` with an `opencv5` fallback.
- `libfprint/meson.build`: add the `bf63112` entry to `driver_sources` and
  `sigfm` to `helper_sources`; pass `cpp_args: sigfm_cpp_args` to `libfprint_drivers`
  (OpenCV headers trip `-Warray-bounds` / `-Wstringop-*` on newer GCC).
- `tests/meson.build`, `examples/meson.build`: carry BF's test and replay
  example, which build the driver internals directly because they are not exported.

`opencv_dep` is defined in the top-level helper loop, which runs before
`subdir('tests')` and `subdir('examples')`; both trees order subdirs the same way.

Build: `meson setup … -Ddrivers=bf63112,fte3600`.

## Proven results

Both drivers selected in one configuration:

```
Drivers : bf63112
          fte3600
Run-time dependency opencv4 found: YES 4.12.0
Build targets in project: 39
```

The resulting `libfprint-2.so.2.0.0` (666504 bytes) lists both sensors:

```
BF63112 | - | Betterlife BF63112 Fingerprint Sensor
FTE3600 | - | FocalTech FTE3600 (host BRISK)
```

Tests from that build: `Ok: 6, Fail: 0`, covering `bf63112-sigfm` and the FTE
`auth`, `install-firmware`, `pair`, `device-session` and `fw9369-release` suites.

A separate `-Ddrivers=fte3600` build of the same tree also passed (ninja
`151/151`), which is what shows the BF-derived core files do not break FTE.

## Not proven

- **Enumeration semantics.** In `fp-context.c` the FTE form binds *every* spidev
  node matching one ID entry (`do { … } while (entry->udev_types == SPIDEV)`),
  where the BF form bound only the first. This is a real behaviour change for
  BF and needs a regression, which requires `umockdev` or a real device.
- **The three FTE wrap tests.** `test-fte3600-linux-board`,
  `test-fte3600-linux-runtime` and `test-fte3600-fw9369-runtime` intercept libc
  through `-Wl,--wrap=`, which resolves via glibc `__wrap_*` thunks. They cannot
  link under musl and were not run; a glibc build environment still has to run them.
- Anything about a real sensor: no capture, enrollment, match or unlock was
  performed against this merged library.

## Licensing

libfprint core and both drivers: LGPL-2.1-or-later. FTE kernel glue:
GPL-2.0-only. Both upstream copyright headers and license files are kept in the
merged tree. The merge introduces no new repository.
