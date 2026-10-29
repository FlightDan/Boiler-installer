"""Build two local model packages from a pinned OXP3 base and explicit repairs.

Only the complete authenticated base and files named by the overlay lock are
read. This tool never scans an integration tree or imports deployment scripts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

INSTALLER_ROOT = Path(__file__).resolve().parents[6]
if not (INSTALLER_ROOT/'protocol/manifest.py').is_file():
    raise ValueError('Run this builder inside the independent Boiler Installer root')
sys.path.insert(0, str(INSTALLER_ROOT))
from protocol.manifest import validate_package, confined, relative_path
from protocol.local import import_local

BASE_MANIFEST = 'c442eef2039779be8e3f432a65bb5cc46c432f8cd7b51734ea76168ab07136e2'
FEATURES = ('battery', 'microphone')
VERSIONS = {'oxp3': '0.1.0-localtest.20261030.2', 'x2-mini': '0.1.0-localtest.20261030.2'}
MIC_TARGETS = {'usr/local/lib/oxp-microphone/microphone.py', 'etc/systemd/user/oxp-microphone.service',
               'etc/systemd/user/default.target.d/90-oxp-microphone.conf'}
BATTERY_FILES = {'var/lib/oxp3-battery/' + name for name in ('battery_control.py', 'battery_provider.py', 'sleep_control.py')}
BATTERY_FILES |= {'etc/systemd/system/systemd-suspend.service.d/90-oxp3-battery.conf',
                 'etc/systemd/system/oxp3-battery.service',
                 'etc/dbus-1/system.d/org.onexplayer.SteamOSManager.Battery.conf',
                 'etc/steamos-manager/remotes.d/oxp3-battery.toml'}
BATTERY_FILES |= {'home/deck/homebrew/plugins/OXP3-battery/' + name for name in ('main.py', 'dist/index.js', 'plugin.json', 'package.json')}


def allowed_target(feature, target):
    if feature == 'microphone':
        return target in MIC_TARGETS
    return target in BATTERY_FILES


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def checked_file(root, entry):
    data = confined(root, entry['source']).read_bytes()
    if len(data) != entry['size'] or digest(data) != entry['sha256']:
        raise ValueError('Overlay source differs from its explicit lock: ' + entry['source'])
    return data


def overlay_inputs(lock_path):
    lock_path = Path(lock_path).resolve()
    lock = json.loads(lock_path.read_bytes())
    if lock.get('kind') != 'BoilerSharedAdaptationsInputs' or lock.get('schema_version') != 1:
        raise ValueError('Explicit shared-adaptations input lock required')
    result = {}
    if set(lock['components']) != set(FEATURES):
        raise ValueError('Only Battery and microphone repair inputs are permitted')
    for model in VERSIONS:
        result[model] = {}
        for feature in FEATURES:
            part = lock['components'][feature][model]
            entries = part['files']
            if not entries or part.get('hooks'):
                raise ValueError('Nonempty static component payload required')
            files, targets = [], set()
            for entry in entries:
                target = relative_path(entry['target'])
                if target in targets or not allowed_target(feature, target):
                    raise ValueError('Duplicate or non-whitelisted repair target')
                targets.add(target)
                data = checked_file(lock_path.parent, entry)
                files.append((target, data, entry.get('mode', 0o644)))
            if feature == 'microphone' and targets != MIC_TARGETS:
                raise ValueError('Complete native microphone integration is required')
            expected_battery = BATTERY_FILES - {'var/lib/oxp3-battery/battery_provider.py',
                'etc/systemd/system/oxp3-battery.service',
                'etc/dbus-1/system.d/org.onexplayer.SteamOSManager.Battery.conf',
                'etc/steamos-manager/remotes.d/oxp3-battery.toml'} if model == 'x2-mini' else BATTERY_FILES
            if feature == 'battery' and targets != expected_battery:
                raise ValueError('Complete model-specific Battery 0.2 payload required')
            if model == 'x2-mini' and feature == 'battery':
                forbidden = ('battery_provider.py', 'oxp3-battery.service', 'oxpec.ko', 'oxp3-battery.toml')
                if any(any(name in target for name in forbidden) for target in targets):
                    raise ValueError('X2 battery must not install OXP3 EC or native provider')
                control = next((data for target, data, _ in files if target.endswith('/battery_control.py')), b'')
                if b'charge_control_available' not in control or b'raise RuntimeError' not in control:
                    raise ValueError('X2 requires a read-only charge-control adapter')
            result[model][feature] = (part, files)
    return lock, result


def inventory(root):
    return [dict(path=path.relative_to(root).as_posix(), size=path.stat().st_size,
                 sha256=digest(path.read_bytes())) for path in sorted(root.rglob('*'))
            if path.is_file() and path != root/'manifest.json']


def component(root, model, feature, part, files):
    records = []
    for index, (target, data, mode) in enumerate(files):
        source = 'shared-adaptations/' + feature + '/' + f'{index:03d}.bin'
        path = root/source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        records.append(dict(source=source, target=target, size=len(data), sha256=digest(data), mode=mode))
    return dict(schema_version=1, kind='ComponentManifest', id=model + ':' + feature,
        version=part['version'], name=part['name'], description=part['description'],
        default=False, optional=True, requires=[], conflicts=[], files=records,
        compatibility=dict(architectures=['x86_64'], os_ids=['steamos'], os_versions=['3.10']))


def build(base, inputs, output):
    base, output = Path(base).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new output directory')
    baseline = validate_package(base, expected_manifest_sha256=BASE_MANIFEST)
    if baseline['id'] != 'device:oxp3' or len(baseline['files']) != 499:
        raise ValueError('Unexpected authenticated OXP3 base')
    lock, repairs = overlay_inputs(inputs)
    output.mkdir(parents=True)
    report = {'kind': 'SharedAdaptationsBuild', 'base_manifest_sha256': BASE_MANIFEST,
              'input_lock_sha256': digest(Path(inputs).read_bytes()), 'packages': []}
    for model in VERSIONS:
        directory = output/'packages'/model
        directory.mkdir(parents=True)
        if model == 'oxp3':
            for entry in baseline['files']:
                path = directory/entry['path']; path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(confined(base, entry['path']), path)
            manifest = json.loads(json_bytes(baseline))
            manifest['version'] = VERSIONS[model]
            # Legacy bridge bytes and default selection are retained. Shared
            # repairs have their own explicit UI and Engine ownership.
            manifest['components'][0]['version'] = VERSIONS[model]
        else:
            manifest = dict(schema_version=1, kind='DevicePackage', id='device:x2-mini',
                version=VERSIONS[model], minimum_engine_version='0.1.0',
                name={'en': 'ONEXPLAYER X2 Mini shared repairs', 'zh': 'ONEXPLAYER X2 Mini 共用修复'},
                source={'kind': 'local', 'path': 'adaptations/brands/onexplayer/oxp3/bridge/shared-adaptations'},
                trust={'status': 'unsigned-development', 'official_release': False}, components=[],
                compatibility=dict(architectures=['x86_64'], os_ids=['steamos'], os_versions=['3.10']))
            for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
                shutil.copyfile(base/'payload/legacy'/name, directory/name)
        if model == 'oxp3':
            # Keep Battery under the existing legacy Store. Updating these
            # resources updates both its installer and maintenance plans.
            resources_path = directory/'payload/legacy/integration/manifest.json'
            resources = json.loads(resources_path.read_bytes())
            old = resources['charging']
            records = []
            for index, (target, data, mode) in enumerate(repairs[model]['battery'][1]):
                source = 'charging/' + f'{index:03d}.bin'
                destination = directory/'payload/legacy/integration'/source
                destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(data)
                records.append(dict(target=target, source=source, mode=mode, user=target.startswith('home/deck/')))
            for record in old['files']:
                if record['source'] not in {row['source'] for row in records}:
                    (directory/'payload/legacy/integration'/record['source']).unlink()
            resources['charging'] = dict(old, files=records, version=repairs[model]['battery'][0]['version'])
            resources_path.write_bytes(json_bytes(resources))
            for capability in manifest.get('legacy_capabilities', []):
                if capability['id'] == 'oxp3:charging':
                    capability['payload_version'] = repairs[model]['battery'][0]['version']
                    capability['targets'] = [row['target'] for row in records]
        features = ('microphone',) if model == 'oxp3' else FEATURES
        added = [component(directory, model, feature, *repairs[model][feature]) for feature in features]
        manifest['components'].extend(added)
        manifest['shared_adaptations'] = dict(kind='boiler-shared-static-v1',
            components=[part['id'] for part in added], input_lock_sha256=report['input_lock_sha256'],
            warnings=(['Battery 0.2 is maintained through the original OXP3 charging option; shared repairs do not take ownership of its files.'] if model == 'oxp3' else []) +
                     ['Local development package; no sleep or X2 hardware acceptance is claimed.'])
        (directory/'shared-adaptations-inputs.json').write_bytes(json_bytes(lock))
        manifest['files'] = inventory(directory)
        (directory/'manifest.json').write_bytes(json_bytes(manifest))
        anchor = digest((directory/'manifest.json').read_bytes())
        validate_package(directory, expected_manifest_sha256=anchor)
        archive = output/('boiler-device-' + model + '-' + VERSIONS[model] + '.zip')
        with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
            for name in sorted(['manifest.json'] + [row['path'] for row in manifest['files']]):
                info = zipfile.ZipInfo(name, date_time=(2026, 10, 30, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(info, (directory/name).read_bytes())
        imported = import_local(archive, output/'verified-import'/model, developer=True)
        if imported['manifest_sha256'] != anchor:
            raise ValueError('Exact ZIP import differs')
        report['packages'].append(dict(id=manifest['id'], version=manifest['version'], directory=str(directory),
            path=str(archive), size=archive.stat().st_size, sha256=digest(archive.read_bytes()),
            manifest_sha256=anchor, files=len(manifest['files']), safe_protocol_import=True))
    (output/'verification.json').write_bytes(json_bytes(report))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.inputs, args.output), indent=2))
