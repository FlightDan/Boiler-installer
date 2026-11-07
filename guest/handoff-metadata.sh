#!/bin/sh
set -eu
. /mnt/payload/tasks/common.sh
test "$(cat /sys/class/dmi/id/sys_vendor)" = QEMU
test "$(cat /sys/block/vdd/size)" = 167772160
test ! -e /dev/vdd1
python3 -B /mnt/payload/tasks/fixture_layout.py create /dev/vdd seed
python3 - <<'PY'
import json, subprocess
from pathlib import Path
import sys
sys.path.insert(0, '/mnt/payload/tasks')
from layout import MIB
from fixture_layout import table, validate
validate('/dev/vdd', 'seed')
table_value = table('/dev/vdd')
windows = [{'number': 27, 'guid': table_value['id'], 'sectorSize': table_value['sectorsize'],
            'model': 'Windows-like QEMU metadata fixture', 'size': int(Path('/sys/block/vdd/size').read_text()) * 512,
            'style': 'GPT', 'bus': 'NVMe', 'partitions': [
                {'number': index + 1, 'guid': part['uuid'], 'gptType': part['type'],
                 'offset': part['start'] * 512, 'size': part['size'] * 512}
                for index, part in enumerate(table_value['partitions'])]}]
print('OXP_HANDOFF_SNAPSHOT=' + json.dumps(windows, separators=(',', ':')))
PY
printf '\nOXP_TASK_RESULT=passed\n'
