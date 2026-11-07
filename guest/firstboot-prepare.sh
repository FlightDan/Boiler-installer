#!/bin/sh
set -eu
. /mnt/payload/tasks/common.sh
test "$(cat /sys/class/dmi/id/sys_vendor)" = QEMU
test "$(cat /sys/block/vdd/size)" = 167772160
python3 - <<'PY'
import sys
sys.path.insert(0, '/mnt/payload/tasks')
from fixture_layout import validate
validate('/dev/vdd')
PY
mkdir -p /mnt/target /mnt/windows
cleanup() {
    sync
    if mountpoint -q /mnt/target; then btrfs property set -ts /mnt/target ro true 2>/dev/null || true; fi
    for relative in tmp run sys proc dev home var; do
        if mountpoint -q /mnt/target/$relative; then umount /mnt/target/$relative || return 1; fi
    done
    if mountpoint -q /mnt/target; then umount /mnt/target || return 1; fi
    umount /mnt/windows 2>/dev/null || true
}
trap cleanup EXIT
role_node() { python3 -B -c "import sys;sys.path.insert(0,'/mnt/payload/tasks');from fixture_layout import role_node;print(role_node('/dev/vdd','$1'))"; }
preserved_node() { python3 -B -c "import sys;sys.path.insert(0,'/mnt/payload/tasks');from fixture_layout import preserved_node;print(preserved_node('/dev/vdd','$1'))"; }
ROOT_A=$(role_node rootfs-A); ROOT_B=$(role_node rootfs-B)
VAR_A=$(role_node var-A); VAR_B=$(role_node var-B); HOME=$(role_node home)
WINDOWS_DATA=$(preserved_node 'Windows data fixture')
for slot in A B; do
    if test "$slot" = A; then root_node="$ROOT_A"; var_node="$VAR_A"; else root_node="$ROOT_B"; var_node="$VAR_B"; fi
    mount -t btrfs "$root_node" /mnt/target
    test "$(btrfs property get -ts /mnt/target ro)" = 'ro=true'
    grep -qx 'VERSION_ID=3.8.14' /mnt/target/etc/os-release
    grep -qx 'BUILD_ID=20260707.10' /mnt/target/etc/os-release
    btrfs property set -ts /mnt/target ro false
    mount "$var_node" /mnt/target/var
    mount "$HOME" /mnt/target/home
    install -d -o 1000 -g 1000 /mnt/target/home/deck/homebrew
    for directory in dev proc sys run; do mount --bind /$directory /mnt/target/$directory; done
    mount -t tmpfs tmpfs /mnt/target/tmp
    python3 -B /mnt/payload/tasks/firstboot-prepare.py
    btrfs property set -ts /mnt/target ro true
    cleanup
    printf '\nOXP_FIRSTBOOT_SLOT_PREPARED=%s\n' "$slot"
done
mount -t ntfs-3g -o ro "$WINDOWS_DATA" /mnt/windows
test "$(cat /mnt/windows/preserved.txt)" = 'Windows fixture data must survive partition append.'
umount /mnt/windows
printf '\nOXP_TASK_RESULT=passed\n'
