#!/bin/sh
# Do not alter bindings or load glue on unrelated hardware.
set -eu
[ "$(uname -r)" = '7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9' ] || exit 0
[ "$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || :)" = ONE-NETBOOK ] || exit 0
case "$(cat /sys/class/dmi/id/product_name 2>/dev/null || :)" in
  'ONEXPLAYER X2Mini'|'ONEXPLAYER X2 Mini') ;;
  *) exit 0 ;;
esac
attempt=0
while :; do
  count=0
  bound=0
  for device in /sys/bus/spi/devices/*; do
    [ -e "$device/modalias" ] || continue
    case "$(cat "$device/modalias")" in acpi:FTE3600:*) ;; *) continue ;; esac
    count=$((count + 1))
    if [ "$(basename "$(readlink "$device/driver" 2>/dev/null || :)")" = spidev ]; then
      bound=$((bound + 1))
      sensor=$(readlink -f "$device")
    fi
  done
  [ "$count" -eq 1 ] && [ "$bound" -eq 1 ] && break
  [ "$attempt" -ge 5 ] && exit 0
  attempt=$((attempt + 1))
  sleep 1
done
[ "$(cat /sys/module/spidev/parameters/bufsiz 2>/dev/null || :)" = 65536 ] || exit 0
module=/var/lib/oxp3-installer/fingerprint/fte3600/fte3600.ko
if [ ! -f "$module" ] || [ "$(wc -c < "$module")" -ne 647200 ] ||
   [ "$(sha256sum "$module" | cut -d ' ' -f 1)" != 7e608fe13335f8b6781f84af090a7cd829e29531f0b64d032447bf8006efbe22 ]; then
  echo 'Boiler FTE3600: locked kernel module is missing or corrupted' >&2
  exit 1
fi
if [ ! -d /sys/module/fte3600 ]; then
  /sbin/modprobe uio
  /sbin/insmod "$module"
fi
# UIO and metadata must be registered below this exact SPI device before
# systemd constructs fprintd's DeviceAllow policy.
attempt=0
while :; do
  for node in /sys/class/uio/uio*; do
    [ -e "$node/name" ] || continue
    [ "$(cat "$node/name")" = fte3600-irq ] || continue
    [ "$(cat "$node/version" 2>/dev/null || :)" = 2 ] || continue
    glue=$(readlink -f "$node/device" 2>/dev/null || :)
    case "$glue" in "$sensor"/*) ;; *) continue ;; esac
    [ "$(cat "$glue/fte3600_glue_abi" 2>/dev/null || :)" = 2 ] || continue
    [ "$(cat "$glue/fte3600_status" 2>/dev/null || :)" = ready ] || continue
    exit 0
  done
  if [ "$attempt" -ge 5 ]; then
    echo 'Boiler FTE3600: matching ABI 2 UIO device did not become ready' >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 1
done
