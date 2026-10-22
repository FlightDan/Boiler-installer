"""Generic offline integration, independent from device-specific policy.

Only explicitly selected components are written. Imported images are preserved.
Payload absence is an error, never an implicit successful installation.
"""
import json
from pathlib import Path
import re
import sys
from payload_lock import checked, relative
from wireless import resolve as resolve_wireless, selected as selected_wireless

CORE = {'core:decky', 'core:maintenance'}
WIRELESS = {'wifi:intel', 'wifi:realtek', 'wifi:mediatek'}


def validate_options(options):
    if not isinstance(options, dict) or options.get('schema') != 1:
        raise ValueError('SelectedOptions schema 1 required')
    for key in ('repairs', 'plugins', 'device_components'):
        values = options.get(key, [])
        if key == 'device_components' and values is None: values = []
        if not isinstance(values, list) or any(type(value) is not str for value in values) or len(values) != len(set(values)):
            raise ValueError('Invalid or duplicate ' + key)
    if set(options.get('plugins', [])) - CORE:
        raise ValueError('No other generic plugin payloads have been approved')
    if options.get('install_mode', 'custom') not in ('custom', 'vanilla', 'imported'):
        raise ValueError('Unknown installation mode')
    package = options.get('device_package')
    if package is not None and (not isinstance(package, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', package)):
        raise ValueError('Invalid device package ID')
    if package:
        if options.get('device_trust') not in ('signed-index', 'developer-session'):
            raise ValueError('Device package trust must be established by the host')
        if not isinstance(options.get('device_manifest_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', options['device_manifest_sha256']):
            raise ValueError('Device package manifest anchor required')
    selected_wireless(options)
    if options.get('set_deck_password'):
        raise ValueError('Password changes require an explicitly supported account policy')
    if options.get('enable_ssh') or options.get('storage_mounts') or options.get('kde_language', 'system') != 'system':
        raise ValueError('SSH, storage mounts and KDE language customization require an approved generic policy')
    return options


def release(root):
    from source_profile import inspect_source, release_values
    profile = inspect_source(root)
    values = release_values(root)
    values['KERNELS'] = profile['kernels']
    return values


def load_manifest(payload):
    path = Path(payload) / 'integration/manifest.json'
    if path.is_symlink(): raise ValueError('Integration manifest cannot be a link')
    manifest = json.loads(path.read_text())
    if manifest.get('schema') != 1 or not isinstance(manifest.get('components'), dict):
        raise ValueError('Invalid Boiler integration payload manifest')
    return manifest['components']


def maintenance_api(payload):
    # This runtime is carried from this project's independent maintenance tree.
    sys.path.insert(0, str(Path(payload)))
    from maintenance.engine import Engine
    return Engine


def firmware_component(component_id, plans, writes, payload, licenses=()):
    from payload_lock import record
    records = []
    for target in plans['files']:
        # Byte-verified sources were already resolved by wireless.resolve.
        relative(target)
        source = 'resolved-firmware/' + target.removeprefix('usr/lib/firmware/')
        records.append(record(source, writes[target], target=target, mode=0o644))
    for source, target, data in licenses:
        records.append(record(source, data, target=target, mode=0o644))
    return {'schema_version': 1, 'kind': 'ComponentManifest', 'id': component_id,
            'version': '0.1.0', 'name': component_id, 'files': records,
            'requires': plans.get('requires', []), 'conflicts': [], 'hooks': {},
            'coverage': plans['coverage'], 'unavailable_requirements': plans['unavailable_requirements'],
            'resolved_requirements': plans['resolved_requirements'], 'requirements': plans['requirements'],
            'alternatives': plans['alternatives'], 'payload_version': plans['payload_version'],
            'unavailable_companions': plans.get('unavailable_companions', []),
            'provenance': plans['provenance'], 'runtime': 'not_validated'}


def device_plan(payload, options, source):
    package_id = options['device_package']
    sys.path.insert(0, str(payload))
    from protocol import validate_package, resolve_plan, compatible
    folder = payload / 'device-packages' / package_id
    package = validate_package(folder, expected_manifest_sha256=options['device_manifest_sha256'])
    hardware_id = package.get('device_id', package['id'].split(':', 1)[-1])
    environment = dict(architecture='x86_64', os_id=source['ID'], os_version=source['VERSION_ID'], device_id=hardware_id,
                       kernels=source['KERNELS'])
    if not compatible(package, environment): raise ValueError('Selected device package is incompatible with the actual image')
    selected = (options.get('device_components') or []) + [identity for identity in options.get('repairs', []) if identity not in CORE | WIRELESS]
    plan = resolve_plan(package['components'], selected, environment)
    descriptor = dict(id=package['id'], version=package['version'],
                      manifest_sha256=options['device_manifest_sha256'], trust=options['device_trust'])
    return folder, plan['components'], environment, descriptor


def install_plan(options, source, slot):
    return dict(schema_version=1, kind='InstallPlan',
                mode='vanilla' if options.get('install_mode') == 'vanilla' else 'normal',
                slot=slot, components=[],
                source=dict(id=source['ID'], version=source['VERSION_ID'], build=source['BUILD_ID'], kernels=source['KERNELS']),
                environment=dict(architecture='x86_64', os_id=source['ID'], os_version=source['VERSION_ID'], kernels=source['KERNELS']),
                maintenance=dict(id='core:maintenance', version='0.1.0', state_schema=1))


def verify_device_components(root, payload, options, slot, installed):
    """Only authenticated verify hooks may run against the caller's readonly image."""
    import subprocess
    root, payload = Path(root), Path(payload)
    from maintenance.engine import build_hook_environment
    from protocol.manifest import confined
    validate_options(options)
    if slot not in ('A', 'B'): raise ValueError('Unknown verification slot')
    recorded = set(installed) - CORE - WIRELESS
    if not options.get('device_package'):
        if recorded: raise ValueError('Device verification requires an explicitly trusted package')
        return set()
    folder, components, environment, descriptor = device_plan(payload, options, release(root))
    identities = {component['id'] for component in components}
    if recorded != identities: raise ValueError('Installed device components differ from the authenticated selection')
    Engine = maintenance_api(payload)
    engine = Engine(root, Path(root) / 'var/lib/boiler/maintenance', read_only=True)
    for component in components:
        if not engine.verify_readonly(component['id'])['ok']:
            raise ValueError('Device managed files differ: ' + component['id'])
        hooks = component.get('hooks', {})
        if hooks:
            if 'verify' not in hooks: raise ValueError('Scripted device component has no readonly verification hook')
            subprocess.run(['/bin/bash', str(confined(folder, hooks['verify'])), str(root)], cwd=folder,
                           env=build_hook_environment(root, 'readonly-' + slot), stdin=subprocess.DEVNULL,
                           check=True, timeout=900)
        print('BOILER_DEVICE_VERIFIED=' + json.dumps(dict(slot=slot, id=component['id'], hook=bool(hooks), readonly=True)), flush=True)
    return identities


def decky_manifest(root, metadata, slot):
    from payload_lock import record
    from plugin_installer import UNIT
    targets = ['var/lib/boiler/decky/runtime/services/PluginLoader',
               'var/lib/boiler/decky/runtime/services/VERSION',
               'etc/systemd/system/plugin_loader.service',
               'etc/systemd/system/multi-user.target.d/50-boiler-decky.conf']
    shared = json.loads((root / 'home/.boiler/decky/slots' / (slot + '.json')).read_text())
    if shared['owns_marker']: targets.append(shared['marker'])
    files = []
    for target in targets:
        data = (root / target).read_bytes()
        files.append(record('installed/' + target, data, target=target,
                            mode=(root / target).stat().st_mode & 0o777))
    return dict(schema_version=1, kind='ComponentManifest', id='core:decky', version=metadata['version'],
                files=files, requires=[], conflicts=[],
                provenance=dict(url=metadata['url'], sha256=metadata['files'][0]['sha256']),
                runtime='not_validated', maintenance_adapter='decky-transactions-v1',
                shared_state=dict(registry='home/.boiler/decky/slots/' + slot + '.json',
                                  marker=shared['marker'], owns_marker=shared['owns_marker']))


def check_conflicts(components, selected):
    selected = set(selected)
    for component in components:
        if component['id'] not in selected: continue
        conflicts = selected.intersection(component.get('conflicts', []))
        if conflicts:
            raise ValueError('Conflicting selected components: ' + component['id'] + ' / ' + ', '.join(sorted(conflicts)))


def stage(root, payload, options, slot, password_hash=None, target_disk=None):
    validate_options(options)
    if slot not in ('A', 'B'): raise ValueError('Unknown installation slot')
    result = {'schema': 1, 'slot': slot, 'options': options, 'components': {}}
    if options.get('install_mode') == 'imported':
        result['status'] = 'preserved-imported-image'
        return result
    root, payload = Path(root), Path(payload)
    result['source'] = release(root)
    result['install_plan'] = install_plan(options, result['source'], slot)
    choices = list(dict.fromkeys(options.get('repairs', []) + options.get('plugins', [])))
    vendors = selected_wireless(options)
    unknown = set(choices) - CORE - WIRELESS
    package = options.get('device_package')
    if (unknown or options.get('device_components')) and not package:
        raise ValueError('Device components require an explicitly trusted package')
    package_root, device_components, environment = (None, [], {})
    if package:
        package_root, device_components, environment, descriptor = device_plan(payload, options, result['source'])
        result['install_plan'].update(environment=environment, device_package=descriptor)
    if not choices and not vendors and not device_components:
        result['status'] = 'no-components-selected'
        return result
    manifest = load_manifest(payload)
    generic_selected = set(choices) & (CORE | WIRELESS) | {'wifi:' + vendor for vendor in vendors}
    generic_components = [dict(manifest.get(identity, {}).get('component', {}), id=identity,
                               conflicts=manifest.get(identity, {}).get('component', manifest.get(identity, {})).get('conflicts', []))
                          for identity in generic_selected]
    check_conflicts([*generic_components, *device_components],
                    generic_selected | {item['id'] for item in device_components})
    # Complete read-only validation precedes target writes.
    for component_id in set(choices) & CORE:
        if not isinstance(manifest.get(component_id), dict) or manifest[component_id].get('available') is not True:
            raise FileNotFoundError('Selected payload unavailable: ' + component_id)
        for record in manifest[component_id].get('files', []): checked(payload / 'integration', record)
    writes, plans = resolve_wireless(root, payload / 'integration', manifest, vendors)
    licenses = {}
    for vendor in vendors:
        entries = manifest['wifi:' + vendor].get('licenses', [])
        if not entries: raise ValueError('Firmware redistribution licenses missing: ' + vendor)
        licenses['wifi:' + vendor] = [('licenses/' + vendor + '/' + Path(entry['source']).name,
            'usr/share/licenses/boiler-firmware/' + vendor + '/' + Path(entry['source']).name,
            checked(payload / 'integration', entry)) for entry in entries]
    if 'core:decky' in choices:
        from plugin_installer import validate_decky
        validate_decky(root, payload / 'integration', manifest['core:decky'])
    engine = None
    if plans or device_components or 'core:maintenance' in choices:
        Engine = maintenance_api(payload)
        engine = Engine(root, root / 'var/lib/boiler/maintenance')
    if 'core:maintenance' in choices:
        component = manifest['core:maintenance']['component']
        operation = engine.apply(component, payload / 'integration')
        result['components']['core:maintenance'] = dict(status='files_installed', operation=operation)
        result['install_plan']['components'].append(component)
    if plans:
        # This staging path is on the VM's ephemeral tmpfs, never the image.
        import tempfile
        with tempfile.TemporaryDirectory(prefix='boiler-firmware-') as temporary:
            staged = Path(temporary)
            for target, data in writes.items():
                file = staged / 'resolved-firmware' / target.removeprefix('usr/lib/firmware/')
                file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(data)
            for entries in licenses.values():
                for source, target, data in entries:
                    file = staged / source; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(data)
            for component_id, plan in plans.items():
                component = firmware_component(component_id, plan, writes, staged, licenses[component_id])
                operation = engine.apply(component, staged)
                result['components'][component_id] = dict(status='files_installed', operation=operation, **plan)
                result['install_plan']['components'].append(component)
    for component in device_components:
        operation = engine.apply(component, package_root, allow_hooks=True, environment=environment)
        result['components'][component['id']] = dict(status='files_installed', runtime='not_validated', operation=operation)
        result['install_plan']['components'].append(component)
    if 'core:decky' in choices:
        from plugin_installer import PluginInstaller
        operation = PluginInstaller(root, payload / 'integration', slot=slot).apply('decky')
        result['components']['core:decky'] = dict(status='files_installed', runtime='not_validated', operation=operation)
        result['install_plan']['components'].append(decky_manifest(root, manifest['core:decky'], slot))
    result['status'] = 'files_installed'
    from file_transaction import atomic_write, directory_fd
    import os
    state = root / 'home/.boiler/integration' / slot
    for directory in (root / 'home/.boiler', root / 'home/.boiler/integration', state):
        if directory.is_symlink(): raise ValueError('Integration state cannot be a link')
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = directory_fd(directory); os.close(descriptor)
    atomic_write(state / 'result.json', (json.dumps(result, indent=2) + '\n').encode(), 0o600)
    atomic_write(state / 'install-plan.json', (json.dumps(result['install_plan'], indent=2) + '\n').encode(), 0o600)
    return result
