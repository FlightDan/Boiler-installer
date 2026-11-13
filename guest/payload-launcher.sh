#!/bin/sh
set -eu
umask 022

count=$1
task=$2
secure=$3
case "$count" in [1-8]) ;; *) exit 1 ;; esac
case "$task" in ''|*[!a-z0-9-]*) exit 1 ;; esac
case "$secure" in 0|1) ;; *) exit 1 ;; esac

transport_device() {
    found=
    for path in /sys/block/vd*; do
        serial=$(cat "$path/serial" 2>/dev/null || cat "$path/device/serial" 2>/dev/null) || continue
        [ "$serial" = "$1" ] || continue
        [ -z "$found" ] || return 1
        found=/dev/${path##*/}
    done
    [ -n "$found" ] || return 1
    printf '%s\n' "$found"
}

[ "$(transport_device boiler-payload-0)" = /dev/vdb ]
mkdir -p /mnt/payload
if [ "$count" = 1 ]; then
    mount -t vfat -o ro /dev/vdb1 /mnt/payload
else
    volume=1
    while [ "$volume" -lt "$count" ]; do
        device=$(transport_device "boiler-payload-$volume")
        mkdir -p "/mnt/boiler-volume-$volume"
        mount -t vfat -o ro "${device}1" "/mnt/boiler-volume-$volume"
        volume=$((volume + 1))
    done
    printf '%s\n' /media/sr0/apks > /etc/apk/repositories
    mkdir -p /var/cache/apk
    apk --cache-dir /var/cache/apk add --no-network --no-progress /mnt/boiler-volume-0/apks/*.apk
    touch /var/cache/apk/.boiler-apk-prepared
    modprobe ext4
    # Only the dedicated disposable transport has this serial.
    merge=$(transport_device boiler-payload-merge)
    mkfs.ext4 -q -F "$merge"
    mount -t ext4 "$merge" /mnt/payload
    volume=0
    while [ "$volume" -lt "$count" ]; do
        cp -R "/mnt/boiler-volume-$volume/." /mnt/payload/
        volume=$((volume + 1))
    done
    sync
    mount -o remount,ro /mnt/payload
fi
printf '\nOXP_PAYLOAD_VOLUMES=%s\n' "$count"

if [ "$secure" = 1 ]; then
    stty sane -echo
    trap 'rm -f /tmp/oxp-deck-initial-password; stty echo' EXIT
    printf '\nOXP_SECRET_INPUT_READY\n'
    IFS= read -r oxp_setup_input
    (umask 077; printf '%s' "$oxp_setup_input" | tr -d '\r' | base64 -d > /tmp/oxp-deck-initial-password)
    unset oxp_setup_input
    sh "/mnt/payload/tasks/$task.sh"
else
    exec sh "/mnt/payload/tasks/$task.sh"
fi
