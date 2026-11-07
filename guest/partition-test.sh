#!/bin/sh
set -eu
. /mnt/payload/tasks/common.sh
# This path is wired only by the development harness to its NEW 80 GiB qcow2 disk.
# Refuse execution outside that QEMU fixture; this is not a production installer.
test "$(cat /sys/class/dmi/id/sys_vendor)" = QEMU
test "$(cat /sys/block/vdd/size)" = 167772160
test ! -e /dev/vdd1
PROFILE=production
if test -f /mnt/payload/selection.json; then
    PROFILE=$(python3 -B -c "import json;print(json.load(open('/mnt/payload/selection.json')).get('fixture_profile','production'))")
fi
export FIXTURE_PROFILE="$PROFILE"
python3 -B /mnt/payload/tasks/fixture_layout.py create /dev/vdd "$PROFILE"
python3 - <<'PY'
import json, os, subprocess, sys
sys.path.insert(0, '/mnt/payload/tasks')
from layout import plan, GIB, MIB
from fixture_layout import table, profile, validate, preserved_files
profile_name = os.environ['FIXTURE_PROFILE']
before = table('/dev/vdd')
expected = profile(profile_name)
preserved = before['partitions'][:len(expected['preserved'])]
if expected.get('range'):
    start = expected['range']['start_mib'] * MIB
    size = expected['range']['size_mib'] * MIB
    bad_ranges = [(start, 32 * GIB), (start, 100 * GIB)]
    if preserved:
        bad_ranges.insert(0, (MIB, size))
    for bad_start, bad_size in bad_ranges:
        try:
            plan(dict(before, partitions=preserved), bad_start, bad_size)
        except ValueError:
            pass
        else:
            raise RuntimeError('Invalid selected space was not rejected')
    planned = plan(dict(before, partitions=preserved), start, size)
    selected = before['partitions'][len(preserved):]
    for part, expected_part in zip(selected, planned):
        for key in ('name', 'start', 'size', 'type'):
            if str(part.get(key)).lower() != str(expected_part[key]).lower():
                raise RuntimeError('Appended partition differs from selected plan')
validate('/dev/vdd', profile_name)
print('OXP_PARTITION_TEST=metadata-preserved')
PY
mdev -s
python3 - <<'PY'
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, '/mnt/payload/tasks')
from fixture_layout import preserved_files
profile_name = os.environ['FIXTURE_PROFILE']
mount = Path('/mnt/fixture-verify')
mount.mkdir(parents=True, exist_ok=True)
for part in preserved_files(profile_name):
    node = '/dev/vdd%d' % part['number']
    kind = 'vfat' if part['fs'] == 'vfat' else 'ntfs-3g'
    subprocess.run(['mount', '-t', kind, '-o', 'ro', node, str(mount)], check=True)
    try:
        value = (mount / part['sentinel']).read_text()
    finally:
        subprocess.run(['umount', str(mount)], check=True)
    if value != part['content']:
        raise RuntimeError('Preserved Windows fixture changed: ' + part['name'])
PY
printf '\nOXP_TASK_RESULT=passed\n'
