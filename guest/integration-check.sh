#!/bin/sh
set -eu
. /mnt/payload/tasks/common.sh
export PYTHONPATH=/mnt/payload/components:/mnt/payload
cd /mnt/payload/tasks
python3 -B -m unittest -v test_boiler_integration
printf '\nOXP_TASK_RESULT=passed\n'