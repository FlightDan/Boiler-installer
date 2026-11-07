#!/bin/sh
set -eu
. /mnt/payload/tasks/common.sh
test "$(cat /sys/class/dmi/id/sys_vendor)" = QEMU
test "$(cat /sys/block/vdd/ro)" = 1
mkdir -p /mnt/target /mnt/windows
cleanup() {
    for part in home var; do if mountpoint -q /mnt/target/$part; then umount /mnt/target/$part; fi; done
    if mountpoint -q /mnt/target; then umount /mnt/target; fi
    if mountpoint -q /mnt/windows; then umount /mnt/windows; fi
}
trap cleanup EXIT
python3 - <<'PY'
import json, stat, subprocess
from pathlib import Path
import sys
sys.path.insert(0, '/mnt/payload/tasks')
from fixture_layout import role_node, preserved_node
root = Path('/mnt/target'); results = []
for slot in ('A', 'B'):
    root_node = role_node('/dev/vdd', 'rootfs-' + slot)
    var_node = role_node('/dev/vdd', 'var-' + slot)
    home_node = role_node('/dev/vdd', 'home')
    subprocess.run(['mount', '-t', 'btrfs', '-o', 'ro,rescue=nologreplay', root_node, str(root)], check=True)
    try:
        if subprocess.check_output(['btrfs', 'property', 'get', '-ts', str(root), 'ro'], text=True).strip() != 'ro=true': raise RuntimeError('Unprotected firstboot root')
        subprocess.run(['mount', '-t', 'ext4', '-o', 'ro,noload', var_node, str(root / 'var')], check=True)
        subprocess.run(['mount', '-t', 'ext4', '-o', 'ro,noload', home_node, str(root / 'home')], check=True)
        directory = root / 'home/.oxp3-installer/firstboot'
        state = json.loads((directory / 'state.json').read_text()); previous = json.loads((directory / 'before-uefi.json').read_text())
        active = state['runs'] > previous['runs'] and state['boot_id'] != previous['boot_id']
        if state['request'] != previous['request']: raise RuntimeError('Firstboot request changed')
        if state['status'] != 'completed_with_refusals' or state['components']['hdr']['status'] != 'preflight_refused' or state['components']['hdr']['files_changed'] is not False: raise RuntimeError('Firstboot incompatibility state incorrect')
        if (root / 'home/deck/.config/gamescope/scripts/98-oxp3-oled-hdr.lua').exists() or (root / 'home/.oxp3-installer/transactions/hdr').exists(): raise RuntimeError('Incompatible HDR changed configuration/state')
        for name in ('request.json', 'state.json'):
            info = (directory / name).lstat()
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600: raise RuntimeError('Firstboot request/result not private root-owned')
        if not (root / 'usr/local/lib/oxp3-firstboot/firstboot.py').is_file() or not (root / 'etc/systemd/system/multi-user.target.wants/oxp3-firstboot.service').is_symlink(): raise RuntimeError('Slot missing bootstrap code/enablement')
        results.append({'slot_view': slot, 'shared_boot_dispatch': active, 'status': state['status'], 'runs': state['runs'], 'component': state['components']['hdr']['status'], 'state_identity': [directory.stat().st_dev, directory.stat().st_ino], 'request': state['request']})
    finally:
        for name in ('home', 'var'):
            if (root / name).is_mount(): subprocess.run(['umount', str(root / name)], check=True)
        subprocess.run(['umount', str(root)], check=True)
if not all(result['shared_boot_dispatch'] for result in results) or results[0]['state_identity'] != results[1]['state_identity'] or results[0]['request'] != results[1]['request'] or results[0]['runs'] != results[1]['runs']: raise RuntimeError('A/B views must see one shared actual-boot result')
print('OXP_FIRSTBOOT_UEFI=actual-systemd-dispatch-new-boot-and-incompatible-component-no-writes')
print('OXP_FIRSTBOOT_RESULT=' + json.dumps(results))
PY
mount -t ntfs-3g -o ro "$(python3 -B -c "import sys;sys.path.insert(0,'/mnt/payload/tasks');from fixture_layout import preserved_node;print(preserved_node('/dev/vdd','Windows data fixture'))")" /mnt/windows
test "$(cat /mnt/windows/preserved.txt)" = 'Windows fixture data must survive partition append.'
umount /mnt/windows
printf '\nOXP_TASK_RESULT=passed\n'