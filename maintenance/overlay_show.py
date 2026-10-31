"""Read the overlay cache without depending on the removable resource payload."""
import json
import math
import os
import re
from pathlib import Path
import time

SELECTORS = ('domains', 'limits', 'fan')

def cache_values(*, cache=None, boot_id=None, now=None):
    try:
        cache = Path(cache) if cache is not None else Path('/run/user') / str(os.getuid()) / 'oxp3-overlay.json'
        boot_id = boot_id if boot_id is not None else Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        now = now if now is not None else time.clock_gettime(time.CLOCK_BOOTTIME)
        data = json.loads(cache.read_text())
        stamp = data['boottime']
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):raise ValueError('invalid timestamp')
        if not boot_id or data['boot_id'] != boot_id or not 0 <= now-stamp <= 3:raise ValueError('stale cache')
        display=data['display']
        if not isinstance(display,dict) or set(display)!=set(SELECTORS):raise ValueError('invalid selectors')
        power=r'(?:N/A|[0-9]+(?:\.[0-9]+)?W)'
        if (not all(isinstance(value,str) and len(value)<=80 for value in display.values())
            or not re.fullmatch(power+r' / '+power,display['domains'])
            or not re.fullmatch(power+r' / '+power+r'(?: \*)?',display['limits'])
            or not re.fullmatch(r'(?:N/A|[0-9]{1,5} RPM)',display['fan'])):raise ValueError('invalid display')
        return display
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):return None

def read(which, *, cache=None, boot_id=None, now=None):
    if which not in SELECTORS:return 'N/A'
    values=cache_values(cache=cache,boot_id=boot_id,now=now)
    return values[which] if values is not None else 'N/A'

def main(which):
    print(read(which))
