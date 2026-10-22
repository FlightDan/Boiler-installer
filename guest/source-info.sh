#!/bin/sh
set -eu
. /mnt/payload/tasks/common.sh
python3 -B /mnt/payload/tasks/source-info.py
printf '\nOXP_TASK_RESULT=passed\n'
