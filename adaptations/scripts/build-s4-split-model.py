"""Derive an unsigned power-only model package with S2/S4 and the locked fingerprint bridge.

Inputs are an already verified local-test package directory and an exact canonical
adaptation commit. The output is a fresh directory containing package/ and
verification.json; this does not publish, sign, or package an archive.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def build(base, anchor, checkout, commit, installer_root, output, version):
    base, checkout, installer_root, output = map(lambda p: Path(p).absolute(), (base, checkout, installer_root, output))
    sys.path.insert(0, str(installer_root))
    from protocol import validate_package
    from protocol.manifest import confined, validate_manifest
    from protocol.capability_gates import assert_gates
    from protocol.resource_pool import decoder
    from maintenance.updates import _version_key
    from components import oxp3_full_efi_provider, x2_efi_provider

    if output.exists() or any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in (output, *output.parents)):
        raise ValueError('Use a fresh unlinked output directory')
    if any(output.resolve().is_relative_to(p.resolve()) for p in (base, checkout, installer_root)):
        raise ValueError('Output must be outside source and base trees')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Expected full lowercase source commit')
    if not re.fullmatch(r'\d+(?:\.\d+){1,3}-localtest\.[A-Za-z0-9.-]+', version):
        raise ValueError('Use an explicit localtest version')
    git_exe = os.environ.get('BOILER_GIT_EXE')
    if not git_exe or not Path(git_exe).is_file():
        raise ValueError('BOILER_GIT_EXE must name the retained trusted Git executable')
    def git(*args):
        return subprocess.check_output([git_exe, '-c', 'safe.directory=' + checkout.as_posix(), '-C', str(checkout), *args], stderr=subprocess.PIPE)
    if git('remote', 'get-url', 'origin').decode().strip() != 'https://github.com/FlightDan/Boiler-adaptations.git':
        raise ValueError('Expected canonical FlightDan/Boiler-adaptations origin')
    if git('cat-file', '-t', commit).strip() != b'commit':
        raise ValueError('Source revision must identify a commit')
    def committed(path):
        raw = git('show', commit + ':' + path)
        if confined(checkout, path).read_bytes() != raw:
            raise ValueError('Checkout differs from canonical commit: ' + path)
        return raw
    entries = json.loads(committed('model-source-files.json'))['files']
    source_inventory = {r['path']: r for r in entries}
    if len(source_inventory) != len(entries):
        raise ValueError('Duplicate canonical inventory path')
    def locked(path):
        raw = committed(path)
        row = source_inventory[path]
        if (len(raw), digest(raw)) != (row['size'], row['sha256']):
            raise ValueError('Canonical source inventory mismatch: ' + path)
        return raw

    manifest = validate_package(base, expected_manifest_sha256=anchor)
    if manifest.get('trust') != {'status': 'unsigned-development', 'official_release': False}:
        raise ValueError('Base must retain unsigned-development trust')
    if _version_key(version) <= _version_key(manifest['version']):
        raise ValueError('Candidate version must advance')
    if manifest['id'] == 'device:oxp3':
        verifier, lock = oxp3_full_efi_provider, installer_root / 'components/oxp3_full_efi_provider.lock.json'
    elif manifest['id'] == 'device:x2-mini':
        verifier, lock = x2_efi_provider, installer_root / 'components/x2_efi_provider.lock.json'
    else:
        raise ValueError('Only OXP3 and X2 Mini are supported')
    verifier.verify(base, lock)
    assert_gates(manifest, 'base package')
    inventory = {r['path']: r for r in manifest['files']}
    components = {r['id']: r for r in manifest['components']}
    if len(inventory) != len(manifest['files']) or len(components) != len(manifest['components']):
        raise ValueError('Duplicate package member or component')
    legacy = components['oxp3:legacy-adaptation']
    if _version_key(version) <= _version_key(legacy['version']):
        raise ValueError('Legacy component version must advance')
    integration_path = 'payload/legacy/integration/manifest.json'
    integration = json.loads(confined(base, integration_path).read_bytes())
    group = integration['charging']
    by_target = {r['target']: r for r in group['files']}
    if len(by_target) != len(group['files']):
        raise ValueError('Duplicate charging target')
    prefix = 'payload/legacy/integration/'
    localized = manifest.get('localization', {})
    frontends = localized.get('frontends', {})
    old_front = 'brands/onexplayer/common/plugin-i18n/battery/index.js'
    old_commit = localized.get('commit', '')
    if not re.fullmatch(r'[0-9a-f]{40}', old_commit):
        raise ValueError('Base must have exact localized source commit')
    old_index = git('show', old_commit + ':' + old_front)
    changed = {}
    removed = set()
    def path_for(target):
        return prefix + by_target[target]['source']
    def replace(target, source, expected_old=None):
        path = path_for(target)
        if expected_old is not None and confined(base, path).read_bytes() != expected_old:
            raise ValueError('Unexpected existing battery source: ' + target)
        changed[path] = locked(source)
        row = by_target[target]
        if 'size' in row: row['size'] = len(changed[path])
        if 'sha256' in row: row['sha256'] = digest(changed[path])
        return path
    frontend_target = 'home/deck/homebrew/plugins/OXP3-battery/dist/index.js'
    frontend_path = path_for(frontend_target)
    if frontends.get(frontend_path) != old_front:
        raise ValueError('Base does not contain expected localized battery frontend')
    replace(frontend_target, 'brands/onexplayer/common/battery/power/battery-plugin-index.js', old_index)
    replace('home/deck/homebrew/plugins/OXP3-battery/main.py', 'brands/onexplayer/common/battery/power/battery-plugin-main.py')
    frontends[frontend_path] = 'brands/onexplayer/common/battery/power/battery-plugin-index.js'
    removed_targets = {'var/lib/oxp3-battery/sleep_control.py', 'etc/systemd/system/systemd-suspend.service.d/90-oxp3-battery.conf'}
    if manifest['id'] == 'device:x2-mini':
        replace('var/lib/oxp3-battery/battery_control.py', 'brands/onexplayer/x2-mini/current/power/battery_control.py')
        replace('etc/systemd/system/oxp3-battery.service', 'brands/onexplayer/x2-mini/current/battery-compat.service')
        removed_targets |= {'var/lib/oxp3-battery/battery_provider.py', 'etc/dbus-1/system.d/org.onexplayer.SteamOSManager.Battery.conf', 'etc/steamos-manager/remotes.d/oxp3-battery.toml'}
        group['packages'].pop('steamos-manager', None)
    if not removed_targets <= by_target.keys():
        raise ValueError('Missing expected old charging targets')
    removed = {path_for(t) for t in removed_targets}
    if any(sum(r['source'] == by_target[t]['source'] for g in integration.values() if isinstance(g, dict) for r in g.get('files', [])) != 1 for t in removed_targets):
        raise ValueError('Cannot remove shared integration source')
    group['files'] = [r for r in group['files'] if r['target'] not in removed_targets]
    group['version'] = version
    caps = [c for c in manifest['legacy_capabilities'] if c['id'] == 'oxp3:charging']
    if len(caps) != 1: raise ValueError('Expected charging capability')
    cap = caps[0]
    cap['targets'] = [r['target'] for r in group['files']]
    cap['payload_version'] = version
    cap['name'] = {'en': 'Battery telemetry' if manifest['id'] == 'device:x2-mini' else 'Battery charging controls', 'zh': '电池状态' if manifest['id'] == 'device:x2-mini' else '电池充电控制'}
    cap['en'], cap['zh'] = cap['name']['en'], cap['name']['zh']
    cap['legacy_notes'] = 'Read-only battery telemetry; charging writes are disabled.' if manifest['id'] == 'device:x2-mini' else 'Battery charge limit and modes; physical-device acceptance remains pending.'
    # Derive the two runtime implementations from the locked canonical policy.
    # The localized frontend has its own locked source and base provenance.
    policy_path = 'brands/onexplayer/common/tdp_sources.py'
    policy_file = Path(__file__).resolve().parent.parent / policy_path
    if policy_file.read_bytes() != locked(policy_path):
        raise ValueError('Builder policy differs from locked canonical source')
    import importlib.util
    spec = importlib.util.spec_from_file_location('boiler_tdp_sources', policy_file)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    baseline = json.loads(locked('source-lock.json'))
    if baseline['source']['commit'] != '72f864e0498c10366493c74608c5ffed62cc99ca':
        raise ValueError('Unexpected CPU baseline commit')
    baseline_rows = {row['path']: row for row in baseline['files']}
    if len(baseline_rows) != len(baseline['files']):
        raise ValueError('Duplicate CPU baseline source')
    def update_row(row, data):
        if 'size' in row: row['size'] = len(data)
        if 'sha256' in row: row['sha256'] = digest(data)
    for group_name, target, source in (
        ('native-tdp', 'var/lib/oxp3-rapl/provider.py', 'rapl/provider.py'),
        ('oxp3-cpu', 'var/lib/oxp3-cpu/power_control.py', 'cpu/power_control.py'),
    ):
        rows = [row for row in integration[group_name]['files'] if row['target'] == target]
        if len(rows) != 1:
            raise ValueError('Expected one power target: ' + target)
        path = prefix + rows[0]['source']
        changed[path] = policy.transform(source, confined(base, path).read_bytes(), baseline_rows)
        update_row(rows[0], changed[path])
        integration[group_name]['version'] = version
    cpu_target = 'home/deck/homebrew/plugins/OXP3-CPU/dist/index.js'
    cpu_rows = [row for row in integration['oxp3-cpu']['files'] if row['target'] == cpu_target]
    if len(cpu_rows) != 1:
        raise ValueError('Expected one localized CPU frontend')
    cpu_path = prefix + cpu_rows[0]['source']
    cpu_source = 'brands/onexplayer/oxp3/current/plugin-i18n/cpu/index.js'
    if frontends.get(cpu_path) != cpu_source or confined(base, cpu_path).read_bytes() != git('show', old_commit + ':' + cpu_source):
        raise ValueError('Unexpected existing localized CPU frontend')
    changed[cpu_path] = locked(cpu_source)
    update_row(cpu_rows[0], changed[cpu_path])
    test_target = 'var/lib/oxp3-cpu/test_power_control.py'
    test_rows = [row for row in integration['oxp3-cpu']['files'] if row['target'] == test_target]
    if len(test_rows) != 1:
        raise ValueError('Expected one power helper test')
    test_path = prefix + test_rows[0]['source']
    test_data = confined(base, test_path).read_bytes()
    old_test = b'(True, 9, 41,'
    if test_data.count(old_test) != 1:
        raise ValueError('Unexpected power helper test: ' + test_path)
    changed[test_path] = test_data.replace(old_test, b'(True, 7, 41,')
    update_row(test_rows[0], changed[test_path])
    tdp_caps = [c for c in manifest['legacy_capabilities'] if c['id'] == 'oxp3:native-tdp']
    if len(tdp_caps) != 1:
        raise ValueError('Expected native TDP capability')
    tdp_cap = tdp_caps[0]
    tdp_cap['name'] = {'zh': '原生 TDP 8–40W 控制', 'en': 'Native TDP controls · 8–40 W'}
    tdp_cap['zh'], tdp_cap['en'] = tdp_cap['name']['zh'], tdp_cap['name']['en']
    tdp_cap['payload_version'] = version
    tdp_cap['legacy_notes'] = '8–40W 支持仅限精确 OXP3 与 X2 Mini；默认不改变功率。实体设备、安装及维护恢复尚待验收。'
    cpu_caps = [c for c in manifest['legacy_capabilities'] if c['id'] == 'oxp3:oxp3-cpu']
    if len(cpu_caps) != 1:
        raise ValueError('Expected CPU capability')
    cpu_caps[0]['payload_version'] = version
    metadata_path = 'payload/legacy/components/catalog.json'
    metadata = json.loads(confined(base, metadata_path).read_bytes())
    matches = [row for row in metadata['components'] if row['id'] == 'native-tdp']
    if len(matches) != 1:
        raise ValueError('Expected native TDP catalog metadata')
    matches[0]['name'] = '原生 TDP 8–40W'
    matches[0]['reason'] = tdp_cap['legacy_notes']
    changed[metadata_path] = encoded(metadata)
    changed[integration_path] = encoded(integration)

    component = json.loads(locked('common/sleep/install.json'))
    validate_manifest(component, 'ComponentManifest')
    if component.get('id') != 'boiler:s2s4' or component.get('default') is not False or component.get('optional') is not True or component.get('requires') or component.get('hooks'):
        raise ValueError('Unexpected S2/S4 component contract')
    model = manifest['id'].removeprefix('device:')
    guide = decoder(checkout).loads(locked(f'devices/onexplayer/{model}.toon').decode('utf-8'), strict=True)
    choices = [row['default'] for row in guide['components'] if row['id'] == 's2s4']
    if len(choices) != 1 or type(choices[0]) is not bool:
        raise ValueError('Expected one S2/S4 default in model TOON')
    if choices[0]:
        component['default'] = True
        del component['selection_policy']
    if component['id'] in components:
        raise ValueError('Base already contains S2/S4 component')
    for row in component['files']:
        name = row['source']
        if not name.startswith('common/sleep/runtime/') or name in inventory or name in changed:
            raise ValueError('Invalid or duplicate S2/S4 runtime source')
        raw = locked(name)
        if (row['size'], row['sha256']) != (len(raw), digest(raw)):
            raise ValueError('S2/S4 install manifest differs from locked runtime: ' + name)
        changed[name] = raw
        inventory[name] = {'path': name, 'size': len(raw), 'sha256': digest(raw)}
    if not component['files']: raise ValueError('S2/S4 runtime is empty')
    manifest['components'].append(component)
    fingerprint = components['oxp3:fingerprint-unlock']
    bridge_lock = 'common/fingerprint/public-bridge-lock.json'
    import fingerprint_provenance
    source_lock = 'fingerprint-source-lock.json'
    for evidence, source in (('bridge-source-lock.json', source_lock), ('public-bridge-lock.json', bridge_lock)):
        path = 'fingerprint/evidence/' + evidence
        if path not in inventory:
            raise ValueError('Missing fingerprint source evidence: ' + path)
        changed[path] = locked(source)
    bridge = fingerprint_provenance.validate(checkout, json.loads(locked(source_lock)), confined)
    if _version_key(version) <= _version_key(fingerprint['version']):
        raise ValueError('Fingerprint component version must advance')
    for row in bridge['files']:
        source = row['local_path']
        path = 'fingerprint/profile/' + source
        if sum(member['source'] == path for member in fingerprint['files']) != 1:
            raise ValueError('Expected one fingerprint bridge binding: ' + path)
        changed[path] = locked(source)
        frontends.pop(path, None)
    for source in ('profile.json', 'boiler-fprintd.service', 'net.reactivated.Fprint.service'):
        name = 'common/fingerprint/runtime-profile/' + source
        path = 'fingerprint/profile/' + name
        if sum(member['source'] == path for member in fingerprint['files']) != 1:
            raise ValueError('Expected one fingerprint profile binding: ' + path)
        changed[path] = locked(name)
    keyboard = fingerprint_provenance.keyboard_sources(locked)
    for name, data in keyboard.items():
        target = fingerprint_provenance.KEYBOARD_TARGETS.get(name)
        if target:
            path = 'fingerprint/profile/' + fingerprint_provenance.KEYBOARD_ROOT + name
            fingerprint['files'].append(dict(source=path, target=target[0], mode=target[1]))
        else:
            path = 'fingerprint/licenses/kde-keyboard/' + name
        if path in inventory or path in changed:
            raise ValueError('Duplicate fingerprint keyboard member: ' + path)
        changed[path] = data
        inventory[path] = dict(path=path, size=len(data), sha256=digest(data))
    keyboard_lock = 'fingerprint/licenses/kde-keyboard/source-lock.json'
    changed[keyboard_lock] = locked(fingerprint_provenance.KEYBOARD_ROOT + 'source-lock.json')
    inventory[keyboard_lock] = dict(path=keyboard_lock, size=len(changed[keyboard_lock]), sha256=digest(changed[keyboard_lock]))
    fingerprint['version'] = version
    localized['fingerprint_source_commit'] = bridge['commit']
    shared = manifest.setdefault('shared_adaptations', {'kind': 'boiler-shared-static-v1', 'components': [], 'warnings': []})
    if shared.get('kind') not in ('boiler-shared-static-v1', 'boiler-shared-host-v1') or component['id'] in shared['components']:
        raise ValueError('Unexpected shared adaptation metadata')
    shared['components'].append(component['id'])
    for name in removed:
        if name not in inventory: raise ValueError('Missing removed payload: ' + name)
        del inventory[name]
    legacy['version'] = version
    manifest['version'] = version
    manifest['localization']['power_source_commit'] = commit
    manifest['power_split'] = {'repository': 'FlightDan/Boiler-adaptations', 'commit': commit, 'base_manifest_sha256': anchor}
    manifest['files'] = list(inventory.values())
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.s4-split-model-', dir=output.parent) as temp:
        stage = Path(temp) / 'candidate'; package = stage / 'package'; package.mkdir(parents=True)
        for name, row in inventory.items():
            target = confined(package, name); target.parent.mkdir(parents=True, exist_ok=True)
            if name in changed:
                raw = changed[name]; target.write_bytes(raw); row['size'], row['sha256'] = len(raw), digest(raw)
            else:
                shutil.copyfile(confined(base, name), target)
        for item in manifest['components']:
            for row in item.get('files', []):
                inv = inventory[row['source']]; row['size'], row['sha256'] = inv['size'], inv['sha256']
        raw = encoded(manifest); (package / 'manifest.json').write_bytes(raw); result_anchor = digest(raw)
        validate_package(package, expected_manifest_sha256=result_anchor)
        assert_gates(manifest, 'power-split package')
        verifier.verify(package, lock)
        result = {'id': manifest['id'], 'version': version, 'manifest_sha256': result_anchor, 'base_manifest_sha256': anchor,
                  'source_commit': commit, 'changed': sorted(changed), 'removed': sorted(removed),
                  'trust': 'unsigned-development', 'official_release': False, 'maintenance_upgrade_tested': False, 'hardware_tested': False}
        (stage / 'verification.json').write_bytes(encoded(result))
        stage.rename(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('base', 'base-manifest-sha256', 'canonical-checkout', 'source-commit', 'installer-root', 'output', 'version'):
        parser.add_argument('--' + option, required=True)
    a = parser.parse_args()
    print(json.dumps(build(a.base, a.base_manifest_sha256, a.canonical_checkout, a.source_commit, a.installer_root, a.output, a.version), ensure_ascii=False))


if __name__ == '__main__': main()
