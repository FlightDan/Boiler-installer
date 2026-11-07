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
    umount -R /mnt/target 2>/dev/null || true
    umount /mnt/windows 2>/dev/null || true
}
trap cleanup EXIT
role_node() { python3 -B -c "import sys;sys.path.insert(0,'/mnt/payload/tasks');from fixture_layout import role_node;print(role_node('/dev/vdd','$1'))"; }
preserved_node() { python3 -B -c "import sys;sys.path.insert(0,'/mnt/payload/tasks');from fixture_layout import preserved_node;print(preserved_node('/dev/vdd','$1'))"; }
ROOT_NODE=$(role_node rootfs-A); VAR_NODE=$(role_node var-A); HOME_NODE=$(role_node home)
WINDOWS_DATA=$(preserved_node 'Windows data fixture')
mount -t btrfs "$ROOT_NODE" /mnt/target
# An interrupted earlier fixture run can leave the test subvolume writable.
# The fixture always has a read-only baseline; restore it before retrying.
btrfs property set -ts /mnt/target ro true
test "$(btrfs property get -ts /mnt/target ro)" = 'ro=true'
grep -qx 'VERSION_ID=3.8.14' /mnt/target/etc/os-release
grep -qx 'BUILD_ID=20260707.10' /mnt/target/etc/os-release
btrfs property set -ts /mnt/target ro false
mount "$VAR_NODE" /mnt/target/var
mount "$HOME_NODE" /mnt/target/home
install -d -o 1000 -g 1000 /mnt/target/home/deck/homebrew
for directory in dev proc sys run; do mount --bind /$directory /mnt/target/$directory; done
mount -t tmpfs tmpfs /mnt/target/tmp
ip link set lo up
python3 -B /mnt/payload/tasks/plugin-test.py
btrfs property set -ts /mnt/target ro true
cleanup
mount -t ntfs-3g -o ro "$WINDOWS_DATA" /mnt/windows
test "$(cat /mnt/windows/preserved.txt)" = 'Windows fixture data must survive partition append.'
umount /mnt/windows
printf '\nOXP_TASK_RESULT=passed\n'
