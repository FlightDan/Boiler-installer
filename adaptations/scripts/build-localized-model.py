"""Derive an unsigned local-test model package with reviewed localized UIs.

Run from a committed canonical adaptation checkout. BOILER_GIT_EXE must identify
its trusted Git executable when Git is not on PATH. The result is not a release.
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

CHECKOUT = Path(__file__).resolve().parents[1]

FRONTENDS = {
    'restart': ('common/plugin-i18n/restart-windows/dist/index.js', 'source-lock.json',
                'common/scripts/windows-reboot/dist/index.js'),
    'fingerprint': ('common/plugin-i18n/fingerprint/index.js', 'fingerprint-source-lock.json',
                    'common/fingerprint/bridge/index.js'),
    'battery': ('brands/onexplayer/common/plugin-i18n/battery/index.js', 'current-source-lock.json',
                'brands/onexplayer/common/battery/battery-plugin-index.js'),
    'cpu': ('brands/onexplayer/oxp3/current/plugin-i18n/cpu/index.js', 'source-lock.json',
            'brands/onexplayer/oxp3/scripts/controls/cpu/cpu-plugin-index.js'),
    'fan': ('brands/onexplayer/oxp3/current/plugin-i18n/fan/index.js', None, None),
    'light': ('brands/onexplayer/oxp3/current/plugin-i18n/light/index.js', 'source-lock.json',
              'brands/onexplayer/oxp3/scripts/light/dist/index.js'),
    'opti': ('common/plugin-i18n/opti2xmx/index.js', None, None),
}
FRONTEND_BASES = {
    'fan': {'size': 2880, 'sha256': 'a585fb60b5fdb86af7840f56d6bcf4ef6694a9d89a78c01601582705f64ce746'},
    'opti': {'size': 72874, 'sha256': '580ec4d08e91d4666402a501d458353218fe74932a0b37755a1b72a8f8f7b949'},
}
LEGACY = {
    'battery': ('oxp3:charging', 'charging', 'OXP3-battery'),
    'cpu': ('oxp3:oxp3-cpu', 'oxp3-cpu', 'OXP3-CPU'),
    'fan': ('oxp3:oxp3-fan', 'oxp3-fan', 'OXP3-Fan'),
    'light': ('oxp3:oxp3-light', 'oxp3-light', 'OXP3-Light'),
    'opti': ('oxp3:opti2xmx', 'opti2xmx', 'opti2xmx'),
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def document(raw):
    return json.loads(raw.decode('utf-8'))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def canonical_sources(checkout, commit, confined):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Supply a full lowercase canonical source commit')
    def git(*args):
        return subprocess.check_output([os.environ.get('BOILER_GIT_EXE', 'git'), '-c', 'safe.directory=' + checkout.as_posix(), '-C', str(checkout), *args], stderr=subprocess.PIPE)
    if git('remote', 'get-url', 'origin').decode().strip() != 'https://github.com/FlightDan/Boiler-adaptations.git':
        raise ValueError('Expected canonical FlightDan/Boiler-adaptations origin')
    if git('cat-file', '-t', commit).strip() != b'commit':
        raise ValueError('Source revision must identify a commit')
    def committed(path):
        raw = git('show', commit + ':' + path)
        if confined(checkout, path).read_bytes() != raw:
            raise ValueError('Source checkout differs from requested commit: ' + path)
        return raw
    entries = document(committed('model-source-files.json'))['files']
    inventory = {entry['path']: entry for entry in entries}
    if len(inventory) != len(entries):
        raise ValueError('Duplicate canonical inventory path')
    def locked(path):
        raw = committed(path)
        row = inventory[path]
        if (len(raw), sha(raw)) != (row['size'], row['sha256']):
            raise ValueError('Canonical inventory mismatch: ' + path)
        return raw
    old_locks = {}
    for name in {row[1] for row in FRONTENDS.values() if row[1]}:
        rows = document(locked(name))['files']
        old_locks[name] = {row['path']: row for row in rows}
        if len(old_locks[name]) != len(rows):
            raise ValueError('Duplicate historical source lock entry: ' + name)
    result = {}
    for name, (new_path, lock, old_path) in FRONTENDS.items():
        old = old_locks[lock][old_path] if lock else FRONTEND_BASES[name]
        result[name] = (locked(new_path), old)
    return result


def build(base, base_sha256, installer_root, output, version, source_commit):
    installer_root = Path(installer_root).resolve()
    sys.path.insert(0, str(installer_root))
    from protocol import validate_package
    from protocol.manifest import confined
    from protocol.capability_gates import assert_gates
    from maintenance.updates import _version_key
    from components import oxp3_full_efi_provider, x2_efi_provider

    base, output = Path(base).absolute(), Path(output).absolute()
    if output.exists() or any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in (output, *output.parents)):
        raise ValueError('Use a fresh unlinked output directory')
    if any(output.resolve().is_relative_to(p.resolve()) for p in (base, CHECKOUT, installer_root)):
        raise ValueError('Keep output outside package and source trees')
    manifest = validate_package(base, expected_manifest_sha256=base_sha256)
    if manifest.get('trust') != {'status': 'unsigned-development', 'official_release': False}:
        raise ValueError('Base must retain unsigned-development trust')
    if not re.fullmatch(r'\d+(?:\.\d+){1,3}-localtest\.[A-Za-z0-9.-]+', version):
        raise ValueError('Use an explicit localtest version')
    if _version_key(version) <= _version_key(manifest['version']):
        raise ValueError('Candidate package version must advance')
    if manifest['id'] == 'device:oxp3':
        verifier = oxp3_full_efi_provider
        provider_lock = installer_root / 'components/oxp3_full_efi_provider.lock.json'
    elif manifest['id'] == 'device:x2-mini':
        verifier = x2_efi_provider
        provider_lock = installer_root / 'components/x2_efi_provider.lock.json'
    else:
        raise ValueError('Only reviewed OXP3 and X2 Mini packages are supported')
    verifier.verify(base, provider_lock)
    assert_gates(manifest, 'base package')
    sources = canonical_sources(CHECKOUT, source_commit, confined)
    inventory = {row['path']: row for row in manifest['files']}
    capabilities = {row['id']: row for row in manifest.get('legacy_capabilities', [])}
    if len(capabilities) != len(manifest.get('legacy_capabilities', [])):
        raise ValueError('Duplicate capability')
    integration_path = 'payload/legacy/integration/manifest.json'
    integration = document(confined(base, integration_path).read_bytes())
    replacements = {}
    localized_files = {}
    def replace(name, path):
        if path in replacements or path not in inventory:
            raise ValueError('Duplicate or absent frontend package member: ' + path)
        new, old = sources[name]
        raw = confined(base, path).read_bytes()
        if (len(raw), sha(raw)) != (old['size'], old['sha256']):
            raise ValueError('Existing frontend differs from historical locked source: ' + name)
        replacements[path] = new
        localized_files[path] = FRONTENDS[name][0]
    def available(capability):
        return capabilities.get(capability, {}).get('available') is True
    for name, (capability, group, plugin) in LEGACY.items():
        if not available(capability):
            continue
        target = 'home/deck/homebrew/plugins/' + plugin + '/dist/index.js'
        matches = [row for row in integration.get(group, {}).get('files', []) if row.get('target') == target]
        if len(matches) != 1:
            raise ValueError('Expected exactly one legacy frontend target: ' + target)
        path = 'payload/legacy/integration/' + matches[0]['source']
        replace(name, path)
        row = matches[0]
        if 'size' in row:
            row['size'] = len(replacements[path])
        if 'sha256' in row:
            row['sha256'] = sha(replacements[path])
    if available('oxp3:windows-reboot'):
        replace('restart', 'payload/legacy/components/windows-reboot/dist/index.js')
    components = {row['id']: row for row in manifest['components']}
    fingerprint = components.get('oxp3:fingerprint-unlock')
    fingerprint_changed = False
    if fingerprint is not None:
        matches = [row for row in fingerprint.get('files', [])
                   if row.get('source') == 'fingerprint/profile/common/fingerprint/bridge/index.js'
                   and row.get('target', '').endswith('/Boiler-Fingerprint-Unlock/dist/index.js')]
        if len(matches) != 1:
            raise ValueError('Expected exactly one fingerprint frontend binding')
        replace('fingerprint', matches[0]['source'])
        fingerprint_changed = True
    original_inner = confined(base, integration_path).read_bytes()
    if integration != document(original_inner):
        replacements[integration_path] = encoded(integration)
    if not replacements:
        raise ValueError('No available localized capability in package')
    if fingerprint_changed:
        if _version_key(version) <= _version_key(fingerprint['version']):
            raise ValueError('Fingerprint component version must advance')
        fingerprint['version'] = version
    legacy = components.get('oxp3:legacy-adaptation')
    if not legacy:
        raise ValueError('Missing legacy adaptation component')
    if any(path.startswith('payload/legacy/') for path in replacements):
        if _version_key(version) <= _version_key(legacy['version']):
            raise ValueError('Legacy component version must advance')
        legacy['version'] = version
    manifest['version'] = version
    manifest['localization'] = {
        'repository': 'FlightDan/Boiler-adaptations',
        'commit': source_commit,
        'base_manifest_sha256': base_sha256,
        'frontends': localized_files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.localized-model-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'candidate'
        package = stage / 'package'
        package.mkdir(parents=True)
        for name, row in inventory.items():
            target = confined(package, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            if name in replacements:
                raw = replacements[name]
                target.write_bytes(raw)
                row['size'], row['sha256'] = len(raw), sha(raw)
            else:
                shutil.copyfile(confined(base, name), target)
        for component in manifest['components']:
            for row in component.get('files', []):
                inventory_row = inventory[row['source']]
                row['size'], row['sha256'] = inventory_row['size'], inventory_row['sha256']
        raw = encoded(manifest)
        (package / 'manifest.json').write_bytes(raw)
        anchor = sha(raw)
        validate_package(package, expected_manifest_sha256=anchor)
        assert_gates(manifest, 'localized package')
        verifier.verify(package, provider_lock)
        result = {'id': manifest['id'], 'version': version, 'manifest_sha256': anchor,
                  'base_manifest_sha256': base_sha256, 'source_commit': source_commit,
                  'changed': sorted(replacements), 'trust': 'unsigned-development',
                  'official_release': False, 'maintenance_upgrade_tested': False, 'hardware_tested': False}
        (stage / 'verification.json').write_bytes(encoded(result))
        stage.rename(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--base-manifest-sha256', required=True)
    parser.add_argument('--installer-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--source-commit', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.base_manifest_sha256, args.installer_root,
                           args.output, args.version, args.source_commit), ensure_ascii=False))


if __name__ == '__main__':
    main()
