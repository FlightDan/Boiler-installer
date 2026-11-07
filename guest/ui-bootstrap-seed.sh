#!/bin/sh
set -eu
. /mnt/payload/tasks/common.sh
test "$(cat /sys/class/dmi/id/sys_vendor)" = QEMU
test "$(cat /sys/block/vdd/size)" = 167772160
python3 - <<'PY'
import sys
sys.path.insert(0, '/mnt/payload/tasks')
from fixture_layout import validate
validate('/dev/vdd', 'seed')
PY
OXP_INTENT_INITIALIZE=1 . /mnt/payload/tasks/mount-intent.sh
test ! -e /mnt/session-state/oxp3/installation-intent.json
umount /mnt/session-state
printf '\nOXP_TASK_RESULT=passed\n'
