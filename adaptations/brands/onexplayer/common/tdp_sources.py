"""Derive OXP3 and X2 Mini PL1 controls from the immutable public CPU baseline."""
import hashlib

PREFIX = 'brands/onexplayer/oxp3/scripts/controls/'
PROVIDER = 'rapl/provider.py'
HELPER = 'cpu/power_control.py'
PLUGIN = 'cpu/cpu-plugin-index.js'

DEVICE_POLICY = '''
def pl1_minimum(dmi=Path("/sys/class/dmi/id")):
    try:
        vendor = (dmi / "sys_vendor").read_text().strip()
        model = (dmi / "product_name").read_text().strip()
    except OSError:
        return MIN_WATTS
    if vendor == "ONE-NETBOOK" and model in ("ONEXPLAYER 3", "ONEXPLAYER X2Mini", "ONEXPLAYER X2 Mini"):
        return 8
    return MIN_WATTS
'''

def replace(value, old, new):
    if value.count(old) != 1:
        raise ValueError("Unexpected public CPU source anchor: " + old)
    return value.replace(old, new)


def transform(name, raw, locked):
    row = locked[PREFIX + name]
    if len(raw) != row['size'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
        raise ValueError("CPU source differs from the public baseline: " + name)
    value = raw.decode('utf-8')
    if name == PROVIDER:
        value = replace(value, 'class ControllerError', DEVICE_POLICY + '\n\nclass ControllerError')
        value = replace(value, 'self.mutex = threading.RLock()', 'self.mutex = threading.RLock()\n        self.pl1_min_watts = pl1_minimum()')
        value = replace(value, '"pl1_min_watts": MIN_WATTS', '"pl1_min_watts": self.pl1_min_watts')
        value = replace(value, 'not MIN_WATTS <= watts <= MAX_WATTS', 'not self.pl1_min_watts <= watts <= MAX_WATTS')
        value = replace(value, '{MIN_WATTS}..{MAX_WATTS} W', '{self.pl1_min_watts}..{MAX_WATTS} W')
        value = replace(value, 'dbus.UInt32(MIN_WATTS, variant_level=1)', 'dbus.UInt32(controller.pl1_min_watts, variant_level=1)')
        value = replace(value, 'OBJECT_PATH, MIN_WATTS, MAX_WATTS)', 'OBJECT_PATH, controller.pl1_min_watts, MAX_WATTS)')
    elif name == HELPER:
        value = replace(value, 'if type(watts) is not int or not 10 <= watts <= maximum:', "minimum = 8 if operation == 'set-pl1' else 10\n        if type(watts) is not int or not minimum <= watts <= maximum:")
        value = replace(value, 'integer from 10 to {maximum}', 'integer from {minimum} to {maximum}')
        value = replace(value, 'not 10 <= watts <= 40:', 'not 8 <= watts <= 40:')
        value = replace(value, 'PL1 must be an integer from 10 to 40 W', 'PL1 must be an integer from 8 to 40 W')
    elif name == PLUGIN:
        value = replace(value, 'key==="pl1"?10:minimumPL2(power)', 'key==="pl1"?Number(power?.pl1_min_watts??10):minimumPL2(power)')
    else:
        raise ValueError("Unknown CPU source: " + name)
    return value.encode('utf-8')
