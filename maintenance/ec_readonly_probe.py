"""Read-only EC interface candidate; no module load or writes.

Only probe() is a product entry point. _inspect is used by owned fixtures.
The result proves interface availability, not ownership or physical layout.
"""
import os
from pathlib import Path
import re

PROFILE = 'oxp3-g1i-v1'
KERNEL = '7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'

class ECProbeError(RuntimeError): pass


def _read(path):
    try:
        value = path.read_text(encoding='ascii').strip()
    except (OSError, UnicodeError) as exc:
        raise ECProbeError('Cannot read fixed EC interface: ' + str(path)) from exc
    if not value or len(value) > 256:
        raise ECProbeError('Invalid fixed EC interface: ' + str(path))
    return value


def _number(path, lo, hi):
    value = _read(path)
    if not re.fullmatch(r'[0-9]+', value) or not lo <= int(value) <= hi:
        raise ECProbeError('Invalid numeric EC value: ' + str(path))
    return int(value)


def _inspect(root):
    root = Path(root)
    acpi = list((root / 'sys/bus/acpi/devices').glob('PNP0C09:*'))
    if not acpi or not any(_read(p / 'hid') == 'PNP0C09' and
                           _number(p / 'status', 0, 255) & 1 for p in acpi):
        raise ECProbeError('ACPI embedded controller is unavailable')
    module = root / 'sys/module/oxpec'
    if not module.is_dir():
        raise ECProbeError('oxpec module is not loaded')
    profile_path = module / 'parameters/board_profile'
    if not profile_path.is_file():
        raise ECProbeError('Existing oxpec has no explicit profile; do not unload it automatically')
    if _read(profile_path) != PROFILE:
        raise ECProbeError('Existing oxpec profile conflicts with reviewed selection')
    hwmons = []
    for path in (root / 'sys/class/hwmon').glob('hwmon[0-9]*'):
        if _read(path / 'name') == 'oxp_ec':
            hwmons.append(path)
    if len(hwmons) != 1:
        raise ECProbeError('Expected one applicable oxp_ec hwmon')
    hw = hwmons[0]
    try:
        driver = (hw / 'device/driver').resolve(strict=True).name
    except OSError as exc:
        raise ECProbeError('oxp_ec driver binding unavailable') from exc
    if driver != 'oxp-platform':
        raise ECProbeError('oxp_ec is bound to an unexpected driver')
    values = {'fan_rpm': _number(hw / 'fan1_input', 0, 65535),
              'pwm': _number(hw / 'pwm1', 0, 255),
              'pwm_enable': _number(hw / 'pwm1_enable', 0, 2)}
    batteries = []
    for battery in (root / 'sys/class/power_supply').glob('BAT*'):
        if (battery / 'charge_control_end_threshold').exists() and (battery / 'charge_behaviour').exists():
            batteries.append(battery)
    if len(batteries) != 1:
        raise ECProbeError('Expected one applicable charge-control interface')
    battery = batteries[0]
    values['charge_limit'] = _number(battery / 'charge_control_end_threshold', 0, 100)
    behaviour = _read(battery / 'charge_behaviour')
    active = re.findall(r'\[([a-z-]+)\]', behaviour)
    if len(active) != 1 or active[0] not in ('auto', 'inhibit-charge', 'inhibit-charge-awake'):
        raise ECProbeError('Invalid charge behaviour')
    values['charge_behaviour'] = active[0]
    return {'profile': PROFILE, 'hwmon': hw.name, 'battery': battery.name,
            'driver': driver, 'values': values, 'ownership': 'unclaimed'}


def probe():
    if os.name != 'posix' or os.geteuid() != 0 or os.uname().release != KERNEL:
        raise ECProbeError('Probe requires root on the exact supported Linux kernel')
    return _inspect(Path('/'))


