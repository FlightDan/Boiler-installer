"""Freeze explicit Battery and microphone candidate inputs.

Battery bytes must already match the separately regenerated payload report.
No deployment script is imported and no hardware operation is executed.
"""
import argparse
import hashlib
import json
from pathlib import Path


X2_CONTROL = '''"""Read battery telemetry; X2 charge writes are not authorized."""
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
'''.encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare(windows_root, battery_report, output):
    windows_root, output = Path(windows_root).resolve(), Path(output).resolve()
    report = json.loads(Path(battery_report).read_bytes())
    if report.get('plugin_version') != '0.2.2' or len(report['files']) != 11:
        raise ValueError('Final regenerated Battery 0.2 eleven-target report required')
    if output.exists():
        raise ValueError('Use a new explicit input staging directory')
    staged = {}
    for entry in report['files']:
        data = (windows_root/'cache/integration'/entry['source']).read_bytes()
        if len(data) != entry['size'] or sha(data) != entry['sha256']:
            raise ValueError('Final Battery payload differs from its regeneration report: ' + entry['target'])
        staged[entry['target']] = data
    mic_root = windows_root/'components/microphone'
    mic = json.loads((mic_root/'install.json').read_bytes())
    mic_files = [(row['target'].lstrip('/'), (mic_root/row['source']).read_bytes(), int(row['mode'], 8)) for row in mic['files']]
    metadata = {
        'battery': ('0.2.2', {'en': 'Battery 0.2.2: consistent charge and sleep sliders', 'zh': 'Battery 0.2.2：统一充电与待机滑块'},
                    {'en': 'Charge limit, three charging modes and seven sleep choices use consistent slider sections. Existing preferences are preserved until changed. Saving never initiates sleep. X2 charge controls remain disabled.', 'zh': '充电上限、三档充电方式与七档待机统一使用滑块区块；调整前保留既有偏好，保存不会立即睡眠。X2 不启用充电控制。'}),
        'microphone': ('0.1.0', {'en': 'Microphone native mute synchronization', 'zh': '麦克风原生静音联动'},
                       {'en': 'Event-driven ALSA Capture Switch synchronization; keeps source mute and gain settings. Restart the desktop session to activate.', 'zh': '按音频事件同步 ALSA 录音开关，保留原生静音和增益设置；重新登录桌面后启用。'}),
        }
    lock = dict(schema_version=1, kind='BoilerSharedAdaptationsInputs', components={})
    output.mkdir(parents=True)
    for feature in metadata:
        version, name, description = metadata[feature]
        lock['components'][feature] = {}
        for model in ('oxp3', 'x2-mini'):
            if feature == 'battery':
                files = [(target, data, 0o644) for target, data in staged.items()]
                if model == 'x2-mini':
                    files = [(target, X2_CONTROL if target.endswith('/battery_control.py') else data, mode)
                             for target, data, mode in files if not any(fragment in target for fragment in (
                                 '/battery_provider.py', '/oxp3-battery.service', '/org.onexplayer.SteamOSManager.Battery.conf', '/oxp3-battery.toml'))]
            else:
                files = mic_files
            records = []
            for index, (target, data, mode) in enumerate(files):
                relative = feature + '/' + model + '/' + f'{index:03d}.bin'
                path = output/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
                records.append(dict(source=relative, target=target, size=len(data), sha256=sha(data), mode=mode))
            lock['components'][feature][model] = dict(version=version, name=name, description=description, files=records)
    path = output/'inputs.json'; path.write_text(json.dumps(lock, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return dict(path=str(path), sha256=sha(path.read_bytes()), files=sum(len(part['files']) for feature in lock['components'].values() for part in feature.values()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--windows-root', type=Path, required=True)
    parser.add_argument('--battery-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.windows_root, args.battery_report, args.output), indent=2))
