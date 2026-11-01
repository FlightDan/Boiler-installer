#!/bin/sh
set -eu
. /mnt/payload/tasks/common.sh
test "$(cat /sys/class/dmi/id/sys_vendor)" = QEMU
test "$(cat /sys/block/vda/serial)" = boiler-scratch
test "$(cat /sys/block/vda/ro)" = 0
test "$(blockdev --getsize64 /dev/vda)" -ge 4294967296
mkdir -p /mnt/integration-fixtures
mkfs.ext4 -F -q -E lazy_itable_init=1,lazy_journal_init=1 /dev/vda
mount /dev/vda /mnt/integration-fixtures
trap 'umount /mnt/integration-fixtures' EXIT
export TMPDIR=/mnt/integration-fixtures
export PYTHONPATH=/mnt/payload/components:/mnt/payload
cd /mnt/payload/tasks
python3 -B -m unittest -v test_boiler_integration
printf '\nOXP_TASK_RESULT=passed\n'
