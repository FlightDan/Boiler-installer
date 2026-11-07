#!/bin/sh
set -eu
. /mnt/payload/tasks/common.sh
_boiler_integration_start=$(cut -d' ' -f1 /proc/uptime)
python3 -B /mnt/payload/tasks/integration-image.py
sync
printf 'BOILER_TIMING=integration.sh:total:%s\n' "$(awk -v s="$_boiler_integration_start" '{printf "%.3f", $1-s}' /proc/uptime)"
printf '\nOXP_TASK_RESULT=passed\n'
