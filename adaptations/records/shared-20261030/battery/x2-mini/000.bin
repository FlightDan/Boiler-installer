"""Read battery telemetry; X2 charge writes are not authorized."""
from pathlib import Path
ROOT = Path('/sys/class/power_supply')

def status():
    batteries = [path for path in sorted(ROOT.glob('*')) if (path/'type').is_file() and (path/'type').read_text().strip() == 'Battery']
    bat = batteries[0] if batteries else ROOT/'BAT0'
    result = {'charge_control_available': False, 'limit': 100, 'mode': 'auto'}
    for name in ('capacity', 'status', 'power_now', 'energy_now', 'energy_full'):
        try: result[name] = (bat/name).read_text().strip()
        except OSError: result[name] = None
    result['ac'] = any(path.read_text().strip() == '1' for path in ROOT.glob('*/online'))
    return result

def set_value(*args):
    raise RuntimeError('Charge control is not enabled for this X2 model; sleep settings remain available')
