#!/bin/sh
set -eu
# All tools are supplied by the host on a read-only virtual FAT disk.
_boiler_clock() { cut -d' ' -f1 /proc/uptime; }
_boiler_common_start=$(_boiler_clock)
printf '%s\n' /media/sr0/apks > /etc/apk/repositories
mkdir -p /var/cache/apk
ln -s /var/cache/apk /etc/apk/cache
# The multi-volume mount command installs the same locked packages before it
# assembles /mnt/payload. Do not repeat that work in every task.
if [ ! -f /var/cache/apk/.boiler-apk-prepared ]; then
    apk add --no-network --no-progress /mnt/payload/apks/*.apk
fi
printf 'BOILER_TIMING=common:apk:%s\n' "$(awk -v s="$_boiler_common_start" '{printf "%.3f", $1-s}' /proc/uptime)"
modprobe btrfs
modprobe ext4
printf 'BOILER_TIMING=common:modules:%s\n' "$(awk -v s="$_boiler_common_start" '{printf "%.3f", $1-s}' /proc/uptime)"
