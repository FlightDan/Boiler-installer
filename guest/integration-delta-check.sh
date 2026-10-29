#!/bin/sh
set -eu
. /mnt/payload/tasks/common.sh
export PYTHONPATH=/mnt/payload/components:/mnt/payload
cd /mnt/payload/tasks
# Exercise Boiler's current offline component transactions and separate A/B
# device-package EFI mounts. These fixtures use temporary roots, never disks.
python3 -B -m unittest -v test_boiler_integration test_integration_efi_mounts
printf '\nOXP_TASK_RESULT=passed\n'
