#!/bin/sh
set -eu
. /mnt/payload/tasks/common.sh
python3 -B /mnt/payload/tasks/vm-boot-serial.py
printf '\nOXP_TASK_RESULT=passed\n'
