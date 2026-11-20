"""Apply the locked X2 telemetry/sleep profile to a verified legacy package."""
import hashlib
import json
from pathlib import Path
import shutil

LOCK_SHA = '089a7c31bd1e96a4b7396f40adbd142ce14af7abd5c1858ef9aba86a955ea7a0'
SOURCES = ('brands/onexplayer/x2-mini/current/battery_control.py',
 'brands/onexplayer/common/battery/sleep_control.py',
 'brands/onexplayer/common/battery/sleep-router.conf',
 'brands/onexplayer/common/battery/battery-plugin-main.py',
 'brands/onexplayer/common/battery/battery-plugin-index.js',
 'brands/onexplayer/common/battery/templates/plugin.json',
 'brands/onexplayer/common/battery/templates/package.json')

def digest(data): return hashlib.sha256(data).hexdigest()

def finalize(directory, adaptations, regular):
    """Return changed and removed package paths; caller refreshes all inventories."""
    directory, adaptations = Path(directory), Path(adaptations)
    raw = regular(adaptations, 'records/shared-20261030/inputs.json').read_bytes()
    if digest(raw) != LOCK_SHA: raise ValueError('Battery profile input lock changed')
    models = json.loads(raw)['components']['battery']
    old_pins, pins = models['oxp3']['files'], models['x2-mini']['files']
    if len(old_pins) != 11 or len(pins) != len(SOURCES): raise ValueError('Unexpected Battery profile size')
    manifest_name = 'payload/legacy/integration/manifest.json'
    path = regular(directory, manifest_name)
    value = json.loads(path.read_bytes()); resource = value['charging']; old = resource['files']
    if len(old) != 11 or [x['target'] for x in old] != [x['target'] for x in old_pins] or [x['source'] for x in old] != [f'charging/{i:03d}.bin' for i in range(11)]:
        raise ValueError('Unexpected inherited Battery profile')
    prefix = 'payload/legacy/integration/'
    for row, pin in zip(old, old_pins):
        data = regular(directory, prefix+row['source']).read_bytes()
        if len(data) != pin['size'] or digest(data) != pin['sha256']: raise ValueError('Inherited Battery bytes changed')
    pending = []
    for i, (pin, source) in enumerate(zip(pins, SOURCES)):
        if pin['source'] != f'battery/x2-mini/{i:03d}.bin': raise ValueError('Unexpected X2 profile order')
        data = regular(adaptations, source).read_bytes()
        if len(data) != pin['size'] or digest(data) != pin['sha256']: raise ValueError('X2 Battery source changed')
        pending.append((pin, data))
    # The locked legacy lifecycle starts and stops this unit.  The X2 unit
    # is a no-op and owns no charging or hardware interface.
    unit_source = 'brands/onexplayer/x2-mini/current/battery-compat.service'
    unit = regular(adaptations, unit_source).read_bytes()
    if len(unit) != 125 or digest(unit) != '45770addac8a494935187be3760b73fe1ad93ad1794cb9e31b762d7c360ba3dd': raise ValueError('X2 compatibility unit changed')
    unit_pin = dict(old_pins[4], size=125, sha256='45770addac8a494935187be3760b73fe1ad93ad1794cb9e31b762d7c360ba3dd')
    pending.append((unit_pin, unit))
    records = []
    for i, (pin, data) in enumerate(pending):
        name = next(row['source'] for row in old if row['target'] == pin['target'])
        regular(directory, prefix+name).write_bytes(data)
        records.append(dict(target=pin['target'], source=name, mode=pin['mode'], user=pin['target'].startswith('home/deck/')))
    removed = {prefix+row['source'] for row in old if row['target'] not in {pin['target'] for pin, _ in pending}}
    for name in removed: regular(directory, name).unlink()
    value['charging'] = dict(resource, files=records, version=models['x2-mini']['version'])
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    return {manifest_name} | {prefix+row['source'] for row in records}, removed
