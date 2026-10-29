"""Rebuild current Battery/microphone static inputs using only this source root.

No deployment generator, shell text, target script or legacy prepare tool runs.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_ROOT = ROOT.parent
sys.path.insert(0, str(INSTALLER_ROOT))
from protocol.manifest import confined

BATTERY = 'brands/onexplayer/common/battery/'
MICROPHONE = 'brands/onexplayer/common/microphone/'
BATTERY_TARGETS = {
    'var/lib/oxp3-battery/battery_control.py': 'battery_control.py',
    'var/lib/oxp3-battery/battery_provider.py': 'battery_provider.py',
    'var/lib/oxp3-battery/sleep_control.py': 'sleep_control.py',
    'etc/systemd/system/systemd-suspend.service.d/90-oxp3-battery.conf': 'sleep-router.conf',
    'etc/systemd/system/oxp3-battery.service': 'templates/oxp3-battery.service',
    'etc/dbus-1/system.d/org.onexplayer.SteamOSManager.Battery.conf': 'templates/battery-dbus.conf',
    'etc/steamos-manager/remotes.d/oxp3-battery.toml': 'templates/oxp3-battery.toml',
    'home/deck/homebrew/plugins/OXP3-battery/main.py': 'battery-plugin-main.py',
    'home/deck/homebrew/plugins/OXP3-battery/dist/index.js': 'battery-plugin-index.js',
    'home/deck/homebrew/plugins/OXP3-battery/plugin.json': 'templates/plugin.json',
    'home/deck/homebrew/plugins/OXP3-battery/package.json': 'templates/package.json',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_sources(root=ROOT):
    lock = json.loads((root/'current-source-lock.json').read_bytes())
    if lock.get('kind') != 'BoilerCurrentAdaptationSources' or lock.get('schema_version') != 1:
        raise ValueError('Current adaptation source inventory required')
    seen = set()
    for entry in lock['files']:
        if entry['path'].casefold() in seen:
            raise ValueError('Duplicate current adaptation source')
        seen.add(entry['path'].casefold())
        data = confined(root, entry['path']).read_bytes()
        if len(data) != entry['size'] or digest(data) != entry['sha256']:
            raise ValueError('Current adaptation source differs from reviewed inventory: '+entry['path'])
    return lock


def prepare(output, root=ROOT):
    root, output = Path(root).resolve(), Path(output).absolute()
    verify_sources(root)
    lock = json.loads((root/'records/shared-20261030/inputs.json').read_bytes())
    microphone = json.loads((root/MICROPHONE/'install.json').read_bytes())
    mic_sources = {row['target'].lstrip('/'): MICROPHONE+row['source'] for row in microphone['files']}
    if set(lock['components']) != {'battery', 'microphone'}:
        raise ValueError('Only explicitly reviewed Battery and microphone inputs are supported')
    pending = []
    for feature, models in lock['components'].items():
        if set(models) != {'oxp3', 'x2-mini'}:
            raise ValueError('Unexpected model in current source lock')
        for model, part in models.items():
            for entry in part['files']:
                if feature == 'microphone':
                    source = mic_sources[entry['target']]
                elif model == 'x2-mini' and entry['target'] == 'var/lib/oxp3-battery/battery_control.py':
                    source = 'brands/onexplayer/x2-mini/current/battery_control.py'
                else:
                    source = BATTERY+BATTERY_TARGETS[entry['target']]
                data = confined(root, source).read_bytes()
                # This migration verifies byte parity. Future changes need a
                # new reviewed input record and version, never silently relabel it.
                if len(data) != entry['size'] or digest(data) != entry['sha256']:
                    raise ValueError('Regenerated static bytes differ from shipped input: '+source)
                pending.append((entry['source'], data))
    if output.exists() or output.is_symlink():
        raise ValueError('Use a new explicit input directory')
    output.mkdir(parents=True)
    for relative, data in pending:
        target = confined(output, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    raw = (root/'records/shared-20261030/inputs.json').read_bytes()
    (output/'inputs.json').write_bytes(raw)
    return dict(kind='CurrentAdaptationInputVerification', files=len(pending),
                battery_version='0.2.2', input_lock_sha256=digest(raw),
                source_inventory_sha256=digest((root/'current-source-lock.json').read_bytes()),
                deployment_executed=False, old_workspace_required=False)


def check_shipped(root=ROOT):
    installer = root.parent
    with tempfile.TemporaryDirectory(prefix='boiler-current-source-check-') as temporary:
        output = Path(temporary)/'inputs'
        report = prepare(output, root)
    lock = json.loads((root/'records/shared-20261030/inputs.json').read_bytes())
    for model in ('oxp3', 'x2-mini'):
        package = installer/'cache/model-bundles/packages'/model
        if model == 'oxp3':
            resources = json.loads((package/'payload/legacy/integration/manifest.json').read_bytes())['charging']
            charging = {row['target']: package/'payload/legacy/integration'/row['source'] for row in resources['files']}
        else:
            manifest = json.loads((package/'manifest.json').read_bytes())
            charging = {row['target']: package/row['source'] for component in manifest['components']
                        if component['id'] == 'x2-mini:battery' for row in component['files']}
        for entry in lock['components']['battery'][model]['files']:
            data = charging[entry['target']].read_bytes()
            if len(data) != entry['size'] or digest(data) != entry['sha256']:
                raise ValueError('Shipped Battery bytes differ: '+model+'/'+entry['target'])
        manifest = json.loads((package/'manifest.json').read_bytes())
        mic = {row['target']: package/row['source'] for component in manifest['components']
               if component['id'] == model+':microphone' for row in component['files']}
        for entry in lock['components']['microphone'][model]['files']:
            data = mic[entry['target']].read_bytes()
            if len(data) != entry['size'] or digest(data) != entry['sha256']:
                raise ValueError('Shipped microphone bytes differ: '+model+'/'+entry['target'])
    report['shipped_models_checked'] = ['oxp3', 'x2-mini']
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check-shipped', action='store_true')
    args = parser.parse_args()
    if args.check_shipped:
        result = check_shipped()
    elif args.output:
        result = prepare(args.output)
    else:
        parser.error('Provide --output or --check-shipped')
    print(json.dumps(result, indent=2))
