"""First-boot activation of the standard vendor CPU frequency driver."""
import argparse
import json
from pathlib import Path

VENDORS = {'intel': ('GenuineIntel', '/sys/devices/system/cpu/intel_pstate/status', {'off', 'passive'}),
           'amd': ('AuthenticAMD', '/sys/devices/system/cpu/amd_pstate/status', {'disable', 'passive', 'guided'})}


def activate(vendor, read=None, write=None):
    """Read and write only fixed system paths; injectable I/O supports isolated tests."""
    identity, path, eligible = VENDORS[vendor]
    read = read or (lambda name: Path(name).read_text(encoding='ascii'))
    write = write or (lambda name, value: Path(name).write_text(value, encoding='ascii'))
    try:
        cpuinfo = read('/proc/cpuinfo')
    except OSError:
        return {'status': 'unavailable', 'actual': None, 'reason': 'cpuinfo_unavailable'}
    ids = {line.split(':', 1)[1].strip() for line in cpuinfo.splitlines()
           if ':' in line and line.split(':', 1)[0].strip() == 'vendor_id'}
    if ids != {identity}:
        return {'status': 'skipped', 'actual': None, 'reason': 'vendor_mismatch'}
    try:
        actual = read(path).strip()
    except OSError:
        return {'status': 'unavailable', 'actual': None, 'reason': 'interface_unavailable'}
    if actual == 'active':
        return {'status': 'active', 'actual': actual}
    if actual not in eligible:
        return {'status': 'unavailable', 'actual': actual, 'reason': 'unknown_status'}
    failed = False
    try:
        write(path, 'active')
    except OSError:
        failed = True
    try:
        actual = read(path).strip()
    except OSError:
        actual = None
    if failed or actual != 'active':
        return {'status': 'activation_failed', 'actual': actual}
    return {'status': 'active', 'actual': actual}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--vendor', required=True, choices=sorted(VENDORS))
    result = activate(parser.parse_args().vendor)
    print(json.dumps(result, sort_keys=True))
    return 1 if result['status'] == 'activation_failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
