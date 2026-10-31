"""Fixed host guard for the approved full charge backend; performs no writes.

The systemd unit invokes this exact root-owned program without model arguments.
Product names do not grant or deny compatibility. Fixtures call check directly.
"""
import json
import os
from pathlib import Path
import sys

EXPECTED={'ID':'steamos','VERSION_ID':'3.10','BUILD_ID':'20260924.1000'}
KERNEL='7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
MANAGER='26.4.1-2'
MODES=('auto','inhibit-charge-awake','inhibit-charge')


def field(lines,key):
    matches=[i for i,v in enumerate(lines) if v=='%'+key+'%']
    if len(matches)!=1 or matches[0]+1>=len(lines):raise ValueError('Ambiguous Manager package metadata')
    return lines[matches[0]+1]


def check(root='/',*,kernel=None):
    root=Path(root)
    values={}
    for line in (root/'etc/os-release').read_text().splitlines():
        if '=' not in line:continue
        key,value=line.split('=',1)
        if key in values:raise ValueError('Duplicate OS release metadata')
        values[key]=value.strip('"')
    for key,value in EXPECTED.items():
        if values.get(key)!=value:raise ValueError('Charge compatibility refused: '+key)
    if (kernel if kernel is not None else os.uname().release)!=KERNEL:
        raise ValueError('Charge compatibility refused: kernel')
    database=root/'usr/lib/holo/pacmandb/local'
    if not database.is_dir():database=root/'var/lib/pacman/local'
    versions=[]
    for path in database.glob('steamos-manager-*/desc'):
        lines=path.read_text().splitlines()
        if field(lines,'NAME')=='steamos-manager':versions.append(field(lines,'VERSION'))
    if versions!=[MANAGER]:raise ValueError('Charge compatibility refused: steamos-manager')
    battery=root/'sys/class/power_supply/BAT0'
    names=('charge_control_end_threshold','charge_behaviour')
    if not all((battery/n).is_file() for n in names):raise ValueError('Charge-control interfaces are unavailable')
    threshold=(battery/names[0]).read_text().strip()
    if not threshold.isdecimal() or not 0<=int(threshold)<=100:raise ValueError('Unrecognized charge threshold readback')
    tokens=(battery/names[1]).read_text().split()
    selected=[v[1:-1] for v in tokens if v.startswith('[') and v.endswith(']')]
    if len(selected)!=1 or selected[0] not in MODES:raise ValueError('Unrecognized charge behaviour readback')
    return dict(compatible=True,manager=MANAGER,kernel=KERNEL,charge_interfaces=list(names),model_gate=False)


def main():
    if len(sys.argv)!=1:raise ValueError('Fixed charge guard takes no arguments')
    if os.geteuid()!=0:raise ValueError('Charge guard requires root')
    print(json.dumps(check()))


if __name__=='__main__':
    try:main()
    except (OSError,ValueError) as exc:
        print('Charge guard refused: '+str(exc),file=sys.stderr)
        raise SystemExit(1)
