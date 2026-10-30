"""Load the opt-in xe allocation workaround on the reviewed OXP3 baseline."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shlex
import subprocess

KERNEL = '7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
MODULE = '/usr/lib/modules/' + KERNEL + '/extra/xevmafix.ko'
MODULE_SHA256 = '10a473fca8031ab43206f7a9146413cf781355ddedd3ff5a66cf59bb5369461b'

def validate(root: Path = Path('/')) -> Path:
    def read(path: str) -> str:
        return (root / path.lstrip('/')).read_text(encoding='utf-8').strip()
    if read('/proc/sys/kernel/osrelease') != KERNEL:
        raise RuntimeError('xevmafix requires the reviewed exact 7.2.4 kernel')
    values = {}
    for line in read('/etc/os-release').splitlines():
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        parsed = shlex.split(value, comments=True)
        if len(parsed) != 1 or key in values:
            raise RuntimeError('ambiguous os-release')
        values[key] = parsed[0]
    if any(values.get(key) != value for key, value in {
        'ID': 'steamos', 'VERSION_ID': '3.10', 'BUILD_ID': '20260924.1000'
    }.items()):
        raise RuntimeError('xevmafix requires SteamOS 3.10 build 20260924.1000')
    if (read('/sys/class/dmi/id/sys_vendor'), read('/sys/class/dmi/id/product_name')) != (
        'ONE-NETBOOK', 'ONEXPLAYER 3'
    ):
        raise RuntimeError('xevmafix requires ONE-NETBOOK / ONEXPLAYER 3')
    module = root / str(MODULE).lstrip('/')
    if module.is_symlink() or not module.is_file():
        raise RuntimeError('xevmafix module is missing or is a symlink')
    if hashlib.sha256(module.read_bytes()).hexdigest() != MODULE_SHA256:
        raise RuntimeError('xevmafix module digest differs from the reviewed build')
    return module

def main() -> None:
    if os.geteuid() != 0:
        raise RuntimeError('xevmafix loader requires root')
    module = validate()
    if Path('/sys/module/xevmafix').is_dir():
        print('xevmafix already loaded: active build is not verified; reboot required for locked build validation')
        return
    subprocess.run(['/usr/bin/kmod', 'modprobe', 'xe'], check=True)
    subprocess.run(['/usr/bin/kmod', 'insmod', str(module)], check=True)

if __name__ == '__main__':
    main()
