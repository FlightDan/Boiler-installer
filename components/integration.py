"""Generic offline integration, independent from device-specific policy.

Only explicitly selected components are written. Imported images are preserved.
Payload absence is an error, never an implicit successful installation.
"""
import json
from pathlib import Path
import re
import sys
import time
from payload_lock import checked, relative
from wireless import resolve as resolve_wireless, selected as selected_wireless, plan_required
import graphics
import system_presets

CORE = {'core:decky', 'core:maintenance'}
SSD_REPAIR = 'ssd:gm7-boot-repair'
WIRELESS = {transport + ':' + vendor for transport in ('wifi', 'bluetooth') for vendor in ('intel', 'realtek', 'mediatek')}


def wireless_identity(selection):
    return selection if ':' in selection else 'wifi:' + selection


def validate_options(options):
    if not isinstance(options, dict) or options.get('schema') != 1:
        raise ValueError('SelectedOptions schema 1 required')
    graphics_profile = graphics.selected(options)
    for key in ('repairs', 'plugins', 'device_components'):
        values = options.get(key, [])
        if key == 'device_components' and values is None: values = []
        if not isinstance(values, list) or any(type(value) is not str for value in values) or len(values) != len(set(values)):
            raise ValueError('Invalid or duplicate ' + key)
    if set(options.get('plugins', [])) - CORE:
        raise ValueError('No other generic plugin payloads have been approved')
    if SSD_REPAIR in options.get('repairs', []):
        if options.get('install_mode', 'custom') != 'custom' or 'core:maintenance' not in options.get('plugins', []):
            raise ValueError('The selected SSD repair requires custom installation and retained maintenance')
        legacy = options.get('device_legacy_options') or {}
        if 'oxp3:gm7-resume' in legacy.get('repairs', []):
            raise ValueError('The legacy GM7 repair remains unavailable')
    if options.get('install_mode', 'custom') not in ('custom', 'vanilla', 'imported'):
        raise ValueError('Unknown installation mode')
    package = options.get('device_package')
    legacy = options.get('device_legacy_options')
    if legacy is not None:
        if not package or not isinstance(legacy, dict) or set(legacy) != {'schema_version', 'repairs', 'plugins', 'acknowledge_legacy_update_hold'}:
            raise ValueError('Explicit device legacy selection requires a trusted package and supported fields')
        if type(legacy['schema_version']) is not int or legacy['schema_version'] != 1 or legacy['acknowledge_legacy_update_hold'] is not True:
            raise ValueError('Explicit legacy OS-image update hold acknowledgement is required')
        for category in ('repairs', 'plugins'):
            values = legacy[category]
            if not isinstance(values, list) or any(type(value) is not str for value in values) or len(values) != len(set(values)):
                raise ValueError('Invalid legacy ' + category)
    if package is not None and (not isinstance(package, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', package)):
        raise ValueError('Invalid device package ID')
    if package:
        if options.get('device_trust') not in ('signed-index', 'developer-session'):
            raise ValueError('Device package trust must be established by the host')
        if not isinstance(options.get('device_manifest_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', options['device_manifest_sha256']):
            raise ValueError('Device package manifest anchor required')
    wireless = selected_wireless(options)
    if wireless and (package or options.get('install_mode', 'custom') not in ('custom', 'vanilla')):
        raise ValueError('Wireless firmware requires generic installation without a device package')
    if graphics_profile and (package or options.get('install_mode', 'custom') not in ('custom', 'vanilla')):
        raise ValueError('Graphics firmware requires generic installation without a device package')
    system_presets.validate(options)
    if options.get('storage_mounts'):
        raise ValueError('Storage mounts require an approved generic policy')
    return options


def merged_firmware_plan(root, manifest, options):
    """One image-bound plan for wireless and the explicitly selected GPU profile."""
    records, plans = plan_required(root, manifest, selected_wireless(options))
    gpu_records, gpu_plans = graphics.plan_required(root, manifest, graphics.selected(options))
    overlap = set(records).intersection(gpu_records)
    if overlap:
        raise ValueError('Conflicting firmware component ownership: ' + ', '.join(sorted(overlap)))
    return dict(records, **gpu_records), dict(plans, **gpu_plans)


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
        boot_record = plans.get('early_boot_proof', {}).get('artifacts', {}).get(target)
        records.append(record(source, writes[target], target=target,
                              mode=boot_record['mode'] if boot_record else 0o644))
    for source, target, data in licenses:
        records.append(record(source, data, target=target, mode=0o644))
    return {'schema_version': 1, 'kind': 'ComponentManifest', 'id': component_id,
            'version': '0.1.0', 'name': component_id, 'files': records,
            'requires': plans.get('requires', []), 'conflicts': [], 'hooks': {},
            'coverage': plans['coverage'], 'unavailable_requirements': plans['unavailable_requirements'],
            'resolved_requirements': plans['resolved_requirements'], 'requirements': plans['requirements'],
            'alternatives': plans['alternatives'], 'payload_version': plans['payload_version'],
            'unavailable_companions': plans.get('unavailable_companions', []),
            **{key: plans[key] for key in ('declared_coverage', 'runtime_candidates', 'unavailable_candidates', 'runtime_reason',
                                         'baseline', 'drivers', 'modules', 'module_fingerprints', 'preserved_fingerprints',
                                         'firmware_scope', 'early_boot', 'early_boot_proof', 'firmware_files') if key in plans},
            'provenance': plans['provenance'], 'runtime': 'not_validated'}


def device_plan(payload, options, source):
    package_id = options['device_package']
    sys.path.insert(0, str(payload))
    from protocol import validate_package, resolve_plan, compatible, device_advisory
    folder = payload / 'device-packages' / package_id
    package = validate_package(folder, expected_manifest_sha256=options['device_manifest_sha256'])
    if package.get('purpose') == 'maintenance':
        raise ValueError('Maintenance-only packages cannot be selected for image installation')
    hardware_id = package.get('device_id', package['id'].split(':', 1)[-1])
    environment = dict(architecture='x86_64', os_id=source['ID'], os_version=source['VERSION_ID'], device_id=hardware_id,
                       kernels=source['KERNELS'])
    if not compatible(package, environment): raise ValueError('Selected device package is incompatible with the actual image')
    from maintenance.shared_models import check_fingerprint_model
    for component in package['components']:
        check_fingerprint_model(component, package['id'])
    selected = (options.get('device_components') or []) + [identity for identity in options.get('repairs', []) if identity not in CORE | WIRELESS | {SSD_REPAIR}]
    resource_ids = {row['id'] for row in package.get('resources', [])}
    chosen_resources = [identity for identity in selected if identity in resource_ids]
    ordinary = [identity for identity in selected if identity not in resource_ids]
    plan = resolve_plan(package['components'], ordinary, environment)
    if chosen_resources:
        if options.get('device_trust') != 'developer-session':
            raise ValueError('Model resource requires target hardware verification')
        if 'core:maintenance' not in options.get('plugins', []):
            raise ValueError('Model resource requires retained maintenance')
        from maintenance.resource_entrypoints import model_resource_pool
        from maintenance.resource_runtime import resolve, component
        for identity in chosen_resources:
            path = next(row['path'] for row in package['resources'] if row['id'] == identity)
            pool = model_resource_pool(folder, identity, path, trust_local_test=True,
                                       expected_manifest_sha256=options['device_manifest_sha256'])
            for resource in resolve(pool, [{'resource': identity}], [identity]):
                if resource.get('dependencies') or any(row.get('scope') != 'root-slots' for row in resource['install']['files']):
                    raise ValueError('Model resource requires unsupported cross-slot coordination')
                if not compatible(resource, environment):
                    raise ValueError('Model resource is incompatible with the image')
                plan['components'].append(component(resource, 'root-slots'))
    legacy_choice = device_legacy_selection(package, options, plan['components'])
    if package.get('legacy_recovery_contract') and any(item['id'] == 'oxp3:legacy-adaptation' for item in plan['components']):
        if 'core:maintenance' not in options.get('plugins', []):
            raise ValueError('The reviewed model selection must include Boiler Maintenance')
        if package['id'] == 'device:oxp3':
            from oxp3_full_efi_provider import verify
            verify(folder, payload / 'components/oxp3_full_efi_provider.lock.json')
        elif package['id'] == 'device:x2-mini':
            from x2_efi_provider import verify
            verify(folder, payload / 'components/x2_efi_provider.lock.json')
        else:
            raise ValueError('The embedded recovery contract is unsupported for this model')
    validate_fingerprint_provider(plan['components'], options)
    warning = device_advisory(package, environment)
    descriptor = dict(id=package['id'], version=package['version'],
                      manifest_sha256=options['device_manifest_sha256'], trust=options['device_trust'],
                      device_warnings=([warning] if warning else []) + plan['device_warnings'])
    ssd_providers = [item['id'] for item in package['components']
        if item.get('ssd_boot_policy') == {'id': SSD_REPAIR, 'version': 1}
        and item.get('available', True) is True and compatible(item, environment)]
    if ssd_providers: descriptor['ssd_providers'] = ssd_providers
    # The fixed BE201 source is part of the already verified model package.
    if package.get('be201_firmware') is not None:
        descriptor['be201_firmware'] = package['be201_firmware']
    if package.get('resources'):
        descriptor['resources'] = package['resources']
    if package.get('oxp3_efi_contract') is not None:
        descriptor['oxp3_efi_contract'] = package['oxp3_efi_contract']
    if package.get('x2_efi_contract') is not None:
        descriptor['x2_efi_contract'] = package['x2_efi_contract']
    if package.get('oxp3_full_efi_contract') is not None:
        descriptor['oxp3_full_efi_contract'] = package['oxp3_full_efi_contract']
    if package.get('legacy_recovery_contract') is not None:
        descriptor['legacy_recovery_contract'] = package['legacy_recovery_contract']
    if legacy_choice is not None:
        descriptor['legacy_selection'] = legacy_choice
    return folder, plan['components'], environment, descriptor


def selected_ssd_provider_owner(identity, components, descriptor):
    """Return only an explicitly selected authenticated package provider."""
    if identity is None:
        return None
    providers=[item for item in components if item.get('id') == identity
               and item.get('ssd_boot_policy') == {'id':SSD_REPAIR,'version':1}]
    if (len(providers) != 1 or not isinstance(descriptor,dict)
            or identity not in descriptor.get('ssd_providers',[])):
        raise ValueError('Selected SSD provider is not in the authenticated package plan')
    from maintenance.ssd_boot import _provider_owner
    return _provider_owner(dict(component_id=identity,manifest_sha256=descriptor.get('manifest_sha256')))


def validate_fingerprint_provider(components, options):
    """The managed model plugin store requires its legacy Decky provider."""
    if not any(item['id'] == 'oxp3:fingerprint-unlock' for item in components):
        return
    legacy = options.get('device_legacy_options')
    if (not isinstance(legacy, dict) or 'oxp3:decky' not in legacy.get('plugins', [])
            or not any(item['id'] == 'oxp3:legacy-adaptation' for item in components)):
        raise ValueError('Fingerprint unlock requires the selected device Decky provider')


def device_legacy_selection(package, options, components):
    """Translate only the package's declared OXP3 data contract, never source code."""
    requested = options.get('device_legacy_options')
    if requested is None:
        return None
    contract = {'kind': 'oxp3-legacy-options-v1', 'component': 'oxp3:legacy-adaptation'}
    if package.get('id') not in ('device:oxp3', 'device:x2-mini') or package.get('selection_contract') != contract:
        raise ValueError('Device package does not support legacy option selection')
    if contract['component'] not in {item['id'] for item in components}:
        raise ValueError('Legacy selection requires the adaptation component')
    from protocol.model_options import validated_selection
    chosen = set(requested['repairs'] + requested['plugins'])
    if 'oxp3:decky' in chosen and 'core:decky' in set(options.get('repairs', []) + options.get('plugins', [])):
        raise ValueError('Legacy and generic Decky installations conflict')
    choice = validated_selection(package, requested['repairs'], requested['plugins'],
        installer_language=options.get('installer_language'), kde_language=options.get('kde_language'),
        acknowledge_update_hold=requested.get('acknowledge_legacy_update_hold'))
    choice['kde_language'] = choice['kde_language'].replace('-', '_')
    if choice['kde_language'] not in package.get('legacy_kde_languages', []):
        raise ValueError('Unsupported legacy language selection')
    choice['slot'] = 'guest'
    if len(json.dumps(choice, sort_keys=True, separators=(',', ':'))) > 4096:
        raise ValueError('Legacy selection exceeds hook context limit')
    return choice


def device_hook_environment(descriptor, component, efi_review_path=None):
    choice = descriptor.get('legacy_selection')
    if choice is None or component['id'] != 'oxp3:legacy-adaptation':
        return None
    environment = {'BOILER_OXP3_SELECTION': json.dumps(choice, sort_keys=True, separators=(',', ':'))}
    if efi_review_path is not None:
        environment['BOILER_OXP3_EFI_REVIEW'] = str(efi_review_path)
    return environment


def authenticate_oxp3_efi(root, payload, options):
    """Authenticate the one fixed split-lock bridge before any guest write."""
    from maintenance.ssd_boot import digest
    from protocol import validate_package
    from oxp3_efi_provider import verify
    package_root, components, _, descriptor = device_plan(Path(payload), options, release(root))
    choice = descriptor.get('legacy_selection')
    if (descriptor.get('id') != 'device:oxp3'
            or descriptor.get('oxp3_efi_contract') != {'kind': 'oxp3-split-lock-efi-v1', 'component': 'oxp3:legacy-adaptation'}
            or not isinstance(choice, dict) or 'oxp3:split-lock' not in choice.get('repairs', [])
            or 'oxp3:gm7-resume' in choice.get('repairs', [])
            or not any(item['id'] == 'oxp3:legacy-adaptation' for item in components)):
        raise ValueError('A reviewed fixed OXP3 split-lock bridge is required')
    package = validate_package(package_root, expected_manifest_sha256=options['device_manifest_sha256'])
    gm7 = [item for item in package.get('legacy_capabilities', []) if item.get('id') == 'oxp3:gm7-resume']
    if len(gm7) != 1 or gm7[0].get('available') is not False:
        raise ValueError('Legacy GM7 ownership must remain unavailable')
    verify(package_root, Path(payload)/'components/oxp3_efi_provider.lock.json')
    source = dict(package_id='device:oxp3', manifest_sha256=options['device_manifest_sha256'],
                  selection_sha256=digest(choice))
    return dict(source=source, selection=choice)


def authenticate_x2_efi(root, payload, options):
    """Authenticate the X2-owned fixed bridge and its independently pinned closure."""
    from maintenance.ssd_boot import digest
    from protocol import validate_package
    from x2_efi_provider import verify
    package_root, components, _, descriptor = device_plan(Path(payload), options, release(root))
    choice = descriptor.get('legacy_selection')
    if (descriptor.get('id') != 'device:x2-mini'
            or descriptor.get('x2_efi_contract') != {'kind': 'x2-split-lock-efi-v1', 'component': 'oxp3:legacy-adaptation'}
            or not isinstance(choice, dict) or 'oxp3:split-lock' not in choice.get('repairs', [])
            or 'oxp3:gm7-resume' in choice.get('repairs', [])
            or not any(item['id'] == 'oxp3:legacy-adaptation' for item in components)):
        raise ValueError('A reviewed fixed X2 split-lock bridge is required')
    package = validate_package(package_root, expected_manifest_sha256=options['device_manifest_sha256'])
    gm7 = [item for item in package.get('legacy_capabilities', []) if item.get('id') == 'oxp3:gm7-resume']
    if len(gm7) != 1 or gm7[0].get('available') is not False:
        raise ValueError('Legacy GM7 ownership must remain unavailable')
    verify(package_root, Path(payload)/'components/x2_efi_provider.lock.json')
    return dict(source=dict(package_id='device:x2-mini', manifest_sha256=options['device_manifest_sha256'],
                            selection_sha256=digest(choice)), selection=choice)


def authenticate_oxp3_full_efi(root, payload, options):
    from maintenance.ssd_boot import digest
    from protocol import validate_package
    from oxp3_full_efi_provider import verify
    package_root, components, _, descriptor = device_plan(Path(payload), options, release(root))
    choice = descriptor.get('legacy_selection')
    if (descriptor.get('id') != 'device:oxp3'
            or descriptor.get('oxp3_full_efi_contract') != {'kind':'oxp3-full-split-lock-efi-v1','component':'oxp3:legacy-adaptation'}
            or not isinstance(choice,dict) or 'oxp3:split-lock' not in choice.get('repairs',[])
            or 'oxp3:gm7-resume' in choice.get('repairs',[])
            or not any(item['id']=='oxp3:legacy-adaptation' for item in components)):
        raise ValueError('A reviewed full OXP3 split-lock bridge is required')
    package=validate_package(package_root,expected_manifest_sha256=options['device_manifest_sha256'])
    gm7=[item for item in package.get('legacy_capabilities',[]) if item.get('id')=='oxp3:gm7-resume']
    if len(gm7)!=1 or gm7[0].get('available') is not False:
        raise ValueError('Legacy GM7 ownership must remain unavailable')
    verify(package_root,Path(payload)/'components/oxp3_full_efi_provider.lock.json')
    return dict(source=dict(package_id='device:oxp3',manifest_sha256=options['device_manifest_sha256'],
                            selection_sha256=digest(choice)),selection=choice)


def authenticate_model_efi(root, payload, options):
    _, _, _, descriptor = device_plan(Path(payload), options, release(root))
    if descriptor['id'] == 'device:oxp3':
        if descriptor.get('oxp3_full_efi_contract'):
            return authenticate_oxp3_full_efi(root,payload,options)
        return authenticate_oxp3_efi(root, payload, options)
    if descriptor['id'] == 'device:x2-mini':
        return authenticate_x2_efi(root, payload, options)
    raise ValueError('No reviewed fixed EFI provider for selected model')


def install_plan(options, source, slot):
    return dict(schema_version=1, kind='InstallPlan',
                mode='vanilla' if options.get('install_mode') == 'vanilla' else 'normal',
                slot=slot, components=[],
                source=dict(id=source['ID'], version=source['VERSION_ID'], build=source['BUILD_ID'], kernels=source['KERNELS']),
                environment=dict(architecture='x86_64', os_id=source['ID'], os_version=source['VERSION_ID'], kernels=source['KERNELS']),
                maintenance=dict(id='core:maintenance', version='0.1.0', state_schema=1))


def verify_device_components(root, payload, options, slot, installed, model_review_path=None):
    """Only authenticated verify hooks may run against the caller's readonly image."""
    import subprocess
    root, payload = Path(root), Path(payload)
    from maintenance.engine import build_hook_environment
    from protocol.manifest import confined
    validate_options(options)
    if slot not in ('A', 'B'): raise ValueError('Unknown verification slot')
    selected_graphics = graphics.selected(options)
    recorded = set(installed) - CORE - WIRELESS - {system_presets.IDENTITY} - ({selected_graphics} if selected_graphics else set())
    if SSD_REPAIR not in (options.get('device_components') or []):
        recorded.discard(SSD_REPAIR)
    if not options.get('device_package'):
        if recorded: raise ValueError('Device verification requires an explicitly trusted package')
        return set()
    folder, components, environment, descriptor = device_plan(payload, options, release(root))
    identities = {component['id'] for component in components}
    if recorded != identities: raise ValueError('Installed device components differ from the authenticated selection')
    Engine = maintenance_api(payload)
    engine = Engine(root, Path(root) / 'var/lib/boiler/maintenance', read_only=True)
    for component in components:
        if component.get('ssd_boot_policy'):
            continue  # Independently verified by the fixed SSD handler.
        if component.get('dynamic_handler') or component['id'] == 'x2-mini:keyboard':
            from maintenance.keyboard_models import installer_verify
            installer_verify(root, component, slot)
            print('BOILER_DEVICE_VERIFIED=' + json.dumps(dict(slot=slot,id=component['id'],hook=False,readonly=True)),flush=True)
            continue
        if not engine.verify_readonly(component['id'])['ok']:
            raise ValueError('Device managed files differ: ' + component['id'])
        if component['id'] in {row['id'] for row in descriptor.get('resources', [])}:
            from maintenance.resource_entrypoints import model_resource_pool
            from maintenance.firmware_resources import verification
            from protocol.manifest import load_json
            pool = model_resource_pool(folder, component['id'], next(row['path'] for row in descriptor['resources'] if row['id'] == component['id']), trust_local_test=True, expected_manifest_sha256=descriptor['manifest_sha256'])
            state = engine._state()['components'][component['id']]
            journal = load_json(engine._journal(state['transaction']))
            if not verification(pool.resource(component['id']), component, journal, root)['ok']:
                raise ValueError('Device resource verification failed: ' + component['id'])
        hooks = component.get('hooks', {})
        if hooks:
            if 'verify' not in hooks: raise ValueError('Scripted device component has no readonly verification hook')
            subprocess.run(['/bin/bash', str(confined(folder, hooks['verify'])), str(root)], cwd=folder,
                           env=build_hook_environment(root, 'readonly-' + slot, device_hook_environment(descriptor, component, model_review_path)), stdin=subprocess.DEVNULL,
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


def retained_model_preflight(engine, package_root, options):
    """Authenticate the selected package and protect any retained generation."""
    import os
    from maintenance import models
    from protocol import validate_package
    package = validate_package(package_root, expected_manifest_sha256=options['device_manifest_sha256'])
    if package.get('id') not in models.MODELS or package.get('maintenance_contract') != models.CONTRACT:
        return None
    if models.CONTRACT['entry'] not in {row['path'] for row in package['files']}:
        raise ValueError('Model maintenance entry is outside the authenticated inventory')
    item = dict(directory=str(package_root), manifest_sha256=options['device_manifest_sha256'], package=package)
    pointer = engine.state_dir / 'models' / ('package-' + package['id'].removeprefix('device:') + '.json')
    with engine._lock():
        engine._recover()
        if os.path.lexists(pointer):
            saved = models._private_json(pointer)
            expected = dict(kind='RetainedModelPackage', model_id=package['id'], manifest_sha256=item['manifest_sha256'])
            if saved != expected:
                raise ValueError('A different retained model package requires explicit recovery or replacement')
            # An identical pointer is idempotent only when its payload remains valid.
            models._package(package['id'], engine, Path('/'), trust_local_test=True)
    return item


def _stage_mark(slot, phase, clock):
    # Phase timing is plain log output; nothing below reads these values.
    print('BOILER_TIMING=stage-%s:%s:%.3f' % (slot, phase, time.monotonic() - clock), flush=True)


def stage(root, payload, options, slot, password_hash=None, target_disk=None, ssd_context=None, ssd_plan=None, ssd_identity=None, defer_ssd=False, model_review_path=None):
    clock = time.monotonic()
    validate_options(options)
    if slot not in ('A', 'B'): raise ValueError('Unknown installation slot')
    _stage_mark(slot, 'validated', clock)
    result = {'schema': 1, 'slot': slot, 'options': options, 'components': {}}
    if options.get('install_mode') == 'imported':
        result['status'] = 'preserved-imported-image'
        _stage_mark(slot, 'preserved', clock)
        return result
    root, payload = Path(root), Path(payload)
    result['source'] = release(root)
    result['install_plan'] = install_plan(options, result['source'], slot)
    choices = list(dict.fromkeys(options.get('repairs', []) + options.get('plugins', [])))
    vendors = selected_wireless(options)
    graphics_profile = graphics.selected(options)
    firmware_ids = [wireless_identity(vendor) for vendor in vendors] + ([graphics_profile] if graphics_profile else [])
    unknown = set(choices) - CORE - WIRELESS - {SSD_REPAIR}
    if SSD_REPAIR in choices and ssd_identity != SSD_REPAIR:
        raise ValueError('The selected common SSD repair lacks bound destination evidence')
    if (ssd_identity is not None) != (ssd_context is not None and ssd_plan is not None):
        raise ValueError('The explicit SSD repair requires its reviewed two-slot installer plan')
    package = options.get('device_package')
    if (unknown or options.get('device_components')) and not package:
        raise ValueError('Device components require an explicitly trusted package')
    package_root, device_components, environment = (None, [], {})
    dynamic_plans = {}
    if package:
        package_root, device_components, environment, descriptor = device_plan(payload, options, result['source'])
        providers = descriptor.get('ssd_providers', [])
        if len(providers) > 1: raise ValueError('Multiple SSD boot providers require review')
        if providers and SSD_REPAIR in choices: raise ValueError('Select the package SSD provider instead of the common fallback')
        from maintenance.engine import Engine as DeviceCheckEngine
        from maintenance.shared_models import check_static_component
        for component in device_components:
            if component.get('ssd_boot_policy'):
                if component['id'] != ssd_identity: raise ValueError('Selected package SSD provider lacks bound target evidence')
            elif component.get('dynamic_handler') or component['id'] == 'x2-mini:keyboard':
                from maintenance.keyboard_models import installer_plan
                dynamic_plans[component['id']] = installer_plan(root, component, slot)
                if 'core:maintenance' not in choices: choices.append('core:maintenance')
            else:
                check_static_component(DeviceCheckEngine(root, read_only=True), component)
            if component['id'] != 'x2-mini:keyboard' and any(row.get('target') == 'home/deck/.config/kwinrc' for row in component.get('files', [])):
                raise ValueError('Keyboard target requires its unique fixed handler')
        if dynamic_plans:
            result['install_plan']['dynamic_plans'] = dynamic_plans
        result['install_plan'].update(environment=environment, device_package=descriptor)
        result['install_plan']['device_warnings'] = descriptor['device_warnings']
        for warning in descriptor['device_warnings']:
            print('BOILER_DEVICE_WARNING=' + json.dumps(warning), flush=True)
    _stage_mark(slot, 'device-plan', clock)
    fixed_model = None
    if model_review_path is not None:
        if str(model_review_path) != '/run/boiler-oxp3-efi-' + slot + '.json':
            raise ValueError('Invalid fixed model EFI review path')
        fixed_model = authenticate_model_efi(root, payload, options)
        if fixed_model['source']['package_id'] == 'device:x2-mini':
            kind = descriptor['x2_efi_contract']['kind']
        elif descriptor.get('oxp3_full_efi_contract'):
            kind = descriptor['oxp3_full_efi_contract']['kind']
        else:
            kind = descriptor['oxp3_efi_contract']['kind']
        result['install_plan']['model_efi'] = {'kind': kind}
    if 'oxp3:split-lock' in (options.get('device_legacy_options') or {}).get('repairs', []) and fixed_model is None:
        raise ValueError('Split-lock requires the fixed reviewed EFI transaction')
    if ssd_identity is not None:
        if 'core:maintenance' not in choices or options.get('install_mode', 'custom') != 'custom':
            raise ValueError('SSD repair requires custom selection and retained maintenance')
        if ssd_identity == SSD_REPAIR and SSD_REPAIR in choices:
            if ssd_plan.get('provider_owner') is not None:
                raise ValueError('Common SSD repair cannot acquire a package provider owner')
        else:
            owner=selected_ssd_provider_owner(ssd_identity,device_components,descriptor)
            if ssd_plan.get('provider_owner') != owner:
                raise ValueError('Reviewed SSD provider identity changed after preflight')
    preset_component, preset_files = system_presets.prepare(root, options, password_hash)
    if not choices and not firmware_ids and not device_components and preset_component is None:
        result['status'] = 'no-components-selected'
        _stage_mark(slot, 'no-components', clock)
        return result
    manifest = load_manifest(payload)
    if (package_root and descriptor.get('legacy_recovery_contract') and
            any(component['id'] == 'oxp3:legacy-adaptation' for component in device_components)):
        from maintenance.updates import _version_key
        version = manifest['core:maintenance']['component']['version']
        if _version_key(version) < _version_key('0.2.0+maintenance.18'):
            raise ValueError('The embedded recovery model requires Boiler Maintenance .18 or newer')
    if firmware_ids and any(manifest.get(identity, {}).get('delivery') == 'on-demand' for identity in firmware_ids):
        import hashlib
        plan_path = payload / 'firmware-plan.json'
        if plan_path.is_symlink() or not plan_path.is_file():
            raise ValueError('Selected firmware requires a verified on-demand download plan')
        downloaded = json.loads(plan_path.read_text())
        catalog_hash = hashlib.sha256((payload / 'integration/manifest.json').read_bytes()).hexdigest()
        planned_options = {key: value for key, value in downloaded.get('selection', {}).items() if key not in ('_target_disk', '_ssd_binding', '_ssd_component')}
        current_options = {key: value for key, value in options.items() if key not in ('_target_disk', '_ssd_binding', '_ssd_component')}
        if downloaded.get('schema') != 1 or downloaded.get('catalog_sha256') != catalog_hash or planned_options != current_options:
            raise ValueError('Firmware catalogue or selections changed after download planning')
        records, current_plan = merged_firmware_plan(root, manifest, options)
        if downloaded.get('slots', {}).get(slot) != dict(records=records, plans=current_plan):
            raise ValueError('Target firmware state changed after download planning')
    generic_selected = set(choices) & (CORE | WIRELESS) | set(firmware_ids)
    generic_components = [dict(manifest.get(identity, {}).get('component', {}), id=identity,
                               conflicts=manifest.get(identity, {}).get('component', manifest.get(identity, {})).get('conflicts', []))
                          for identity in generic_selected]
    planned_components = [*generic_components, *device_components, *([preset_component] if preset_component else [])]
    check_conflicts(planned_components,
                    generic_selected | {item['id'] for item in device_components} | ({system_presets.IDENTITY} if preset_component else set()))
    if ssd_identity is not None or fixed_model is not None:
        from maintenance.ssd_boot import TARGET, DROPIN
        boot_paths = [TARGET.casefold()]
        if ssd_identity is not None: boot_paths.append(DROPIN.casefold())
        if fixed_model is not None:
            boot_paths.extend(('etc/default/grub.d/oxp3-splitlock.cfg',
                               'usr/share/licenses/oxp3-split-lock/UPSTREAM_LICENSE'.casefold()))
        for component in planned_components:
            if component.get('ssd_boot_policy'): continue
            for entry in component.get('files', []):
                target = entry['target'].casefold()
                if any(target == path or target.startswith(path + '/') or path.startswith(target + '/') for path in boot_paths):
                    raise ValueError('Selected model component conflicts with SSD boot ownership')
            if ssd_identity is not None and component.get('hooks') and not (fixed_model is not None and component['id'] == 'oxp3:legacy-adaptation'):
                raise ValueError('Model hooks require reviewed boot ownership before SSD repair can be combined')
    if preset_component:
        # Refuse model/core file collisions before any selected component writes.
        targets = {entry['target'].casefold() for entry in preset_component['files']}
        for component in [*generic_components, *device_components]:
            for entry in component.get('files', []):
                name = entry['target'].casefold()
                if any(name == target or name.startswith(target + '/') or target.startswith(name + '/') for target in targets):
                    raise ValueError('System preset target conflicts with selected component')
    # Complete read-only validation precedes target writes.
    for component_id in set(choices) & CORE:
        if not isinstance(manifest.get(component_id), dict) or manifest[component_id].get('available') is not True:
            raise FileNotFoundError('Selected payload unavailable: ' + component_id)
        for record in manifest[component_id].get('files', []): checked(payload / 'integration', record)
    records, plans = merged_firmware_plan(root, manifest, options)
    writes = {target: checked(payload / 'integration', entry) for target, entry in records.items()}
    if graphics_profile:
        import graphics_boot
        graphics_plan = plans[graphics_profile]
        candidates = graphics.boot_candidates(root, graphics_plan, writes)
        boot_writes, proof = graphics_boot.prepare(root, graphics_profile, candidates)
        # Boot outputs are local derivations, added only after the downloaded
        # firmware plan has passed exact image/catalogue/selection revalidation.
        # Precompute every output before creating the Engine or writing a core
        # component; a failed initramfs inspection leaves the image untouched.
        other_targets = set(writes)
        for component in planned_components:
            if component.get('id') != graphics_profile:
                other_targets.update(entry['target'] for entry in component.get('files', []))
        if set(boot_writes).intersection(other_targets):
            raise ValueError('Graphics early-boot output conflicts with another selected component')
        graphics_plan.update(firmware_files=list(graphics_plan['files']),
                             files=sorted([*graphics_plan['files'], *boot_writes]), early_boot_proof=proof,
                             early_boot='firmware-appended-original-preserved' if boot_writes
                             else 'existing-initramfs-coverage-verified')
        writes.update(boot_writes)
    _stage_mark(slot, 'payload-hash', clock)
    licenses = {}
    for identity in firmware_ids:
        if not plans[identity]['files'] and identity != graphics_profile:
            licenses[identity] = []
            continue
        entries = manifest[identity].get('licenses', [])
        if not entries: raise ValueError('Firmware redistribution licenses missing: ' + identity)
        license_directory = identity.replace(':', '/') if identity.startswith(('bluetooth:', 'graphics:')) else identity.removeprefix('wifi:')
        licenses[identity] = [('licenses/' + license_directory + '/' + Path(entry['source']).name,
            'usr/share/licenses/boiler-firmware/' + license_directory + '/' + Path(entry['source']).name,
            checked(payload / 'integration', entry)) for entry in entries]
    if 'core:decky' in choices:
        from plugin_installer import validate_decky
        validate_decky(root, payload / 'integration', manifest['core:decky'])
    _stage_mark(slot, 'validation', clock)
    engine = None
    if plans or device_components or 'core:maintenance' in choices or preset_component:
        Engine = maintenance_api(payload)
        engine = Engine(root, root / 'var/lib/boiler/maintenance')
    retained_item = None
    # Retain a selected BE201 provider even when no other model component was
    # selected; the fixed handler can then be reviewed on the real target.
    if package_root and (device_components or descriptor.get('be201_firmware')) and 'core:maintenance' in choices:
        retained_item = retained_model_preflight(engine, package_root, options)
    _stage_mark(slot, 'model-preflight', clock)
    if 'core:maintenance' in choices:
        component = manifest['core:maintenance']['component']
        operation = engine.apply(component, payload / 'integration')
        result['install_plan']['maintenance']['version'] = component['version']
        result['components']['core:maintenance'] = dict(status='files_installed', operation=operation)
        result['install_plan']['components'].append(component)
    _stage_mark(slot, 'core-maintenance', clock)
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
                if component_id == graphics_profile and component_id in engine._state()['components']:
                    # Fresh planning preserves firmware and initramfs added by a
                    # previous generation. Keep their existing ownership under
                    # the Engine's checked static retention path instead of
                    # silently dropping them from this component generation.
                    component['retain_owned_paths'] = True
                operation = engine.apply(component, staged)
                result['components'][component_id] = dict(status='files_installed', operation=operation, **plan)
                result['install_plan']['components'].append(component)
    _stage_mark(slot, 'firmware', clock)
    for component in device_components:
        if component.get('ssd_boot_policy'): continue
        if component.get('dynamic_handler') or component['id'] == 'x2-mini:keyboard':
            from maintenance.keyboard_models import installer_execute
            operation = installer_execute(root, component, slot, dynamic_plans[component['id']])
            result['components'][component['id']] = dict(status='files_installed',runtime='not_validated',operation=operation)
            result['install_plan']['components'].append(component)
            continue
        if component['id'] in ('x2-mini:battery', 'x2-mini:gamescope-env', 'x2-mini:slot-switch', 'oxp3:fingerprint-unlock'):
            from maintenance.shared_models import prepare_component_dirs
            prepare_component_dirs(engine, component)
        if component['id'] in {row['id'] for row in descriptor.get('resources', [])}:
            from maintenance.resource_entrypoints import model_resource_pool
            from maintenance.firmware_resources import prepare
            pool = model_resource_pool(package_root, component['id'], next(row['path'] for row in descriptor['resources'] if row['id'] == component['id']), trust_local_test=True, expected_manifest_sha256=descriptor['manifest_sha256'])
            component = prepare(pool.resource(component['id']), component, engine)
        operation = engine.apply(component, package_root, allow_hooks=True, environment=environment,
                                 hook_environment=device_hook_environment(descriptor, component, model_review_path if fixed_model is not None else None))
        result['components'][component['id']] = dict(status='files_installed', runtime='not_validated', operation=operation)
        result['install_plan']['components'].append(component)
    _stage_mark(slot, 'device-components', clock)
    if ssd_identity is not None and not defer_ssd:
        # Model hooks run first. A change to either preflighted boot file makes
        # installer_execute refuse instead of creating competing ownership.
        complete_ssd(root, result, ssd_context, ssd_plan, ssd_identity)
    _stage_mark(slot, 'ssd', clock)
    if 'core:decky' in choices:
        from plugin_installer import PluginInstaller
        operation = PluginInstaller(root, payload / 'integration', slot=slot).apply('decky')
        result['components']['core:decky'] = dict(status='files_installed', runtime='not_validated', operation=operation)
        result['install_plan']['components'].append(decky_manifest(root, manifest['core:decky'], slot))
    _stage_mark(slot, 'decky', clock)
    if preset_component:
        operation = system_presets.apply(engine, preset_component, preset_files)
        result['components'][system_presets.IDENTITY] = dict(status='files_installed', operation=operation,
            language=options.get('kde_language', 'system'), password_set=options.get('set_deck_password', False),
            ssh_enabled=options.get('enable_ssh', False), activation='next-boot')
        result['install_plan']['components'].append(preset_component)
    _stage_mark(slot, 'presets', clock)
    if retained_item is not None:
        # Recheck the pointer and reviewed source after component writes, then
        # retain using the existing authenticated snapshot and Engine lock.
        from maintenance import models
        with engine._lock():
            engine._recover()
            from protocol import validate_package
            checked_package = validate_package(package_root, expected_manifest_sha256=retained_item['manifest_sha256'])
            if checked_package != retained_item['package']:
                raise ValueError('Selected model package changed during installation')
            import os
            pointer = engine.state_dir / 'models' / ('package-' + checked_package['id'].removeprefix('device:') + '.json')
            if os.path.lexists(pointer):
                expected = dict(kind='RetainedModelPackage', model_id=checked_package['id'], manifest_sha256=retained_item['manifest_sha256'])
                if models._private_json(pointer) != expected:
                    raise ValueError('Retained model package changed during installation')
            models._retain(engine, retained_item)
    _stage_mark(slot, 'retained', clock)
    result['status'] = 'ssd-pending' if defer_ssd else 'files_installed'
    save_result(root, slot, result)
    _stage_mark(slot, 'total', clock)
    return result


def save_result(root, slot, result):
    from file_transaction import atomic_write, directory_fd
    import os
    state = root / 'home/.boiler/integration' / slot
    for directory in (root / 'home/.boiler', root / 'home/.boiler/integration', state):
        if directory.is_symlink(): raise ValueError('Integration state cannot be a link')
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = directory_fd(directory); os.close(descriptor)
    atomic_write(state / 'result.json', (json.dumps(result, indent=2) + '\n').encode(), 0o600)
    atomic_write(state / 'install-plan.json', (json.dumps(result['install_plan'], indent=2) + '\n').encode(), 0o600)


def complete_ssd(root, result, ssd_context, ssd_plan, ssd_identity, model_preview=None):
    owner=None
    if ssd_plan.get('provider_owner') is not None:
        descriptor=result['install_plan'].get('device_package')
        if not isinstance(descriptor,dict) or ssd_identity not in descriptor.get('ssd_providers',[]):
            raise ValueError('SSD provider descriptor is not retained in the authenticated install plan')
        from maintenance.ssd_boot import _provider_owner
        owner=_provider_owner(dict(component_id=ssd_identity,manifest_sha256=descriptor.get('manifest_sha256')))
    elif ssd_identity != SSD_REPAIR:
        raise ValueError('A package SSD provider requires authenticated ownership')
    if ssd_plan.get('provider_owner') != owner:
        raise ValueError('SSD plan provider differs from the authenticated package')
    if model_preview is None:
        from maintenance.ssd_boot import installer_execute
        operation = installer_execute(root, ssd_context, ssd_plan, provider_owner=owner)
    else:
        from maintenance.ssd_boot import installer_model_ssd_execute
        operation = installer_model_ssd_execute(root, ssd_context, model_preview, ssd_plan, provider_owner=owner)
    if operation.get('ok') is not True or type(operation.get('changed')) is not bool:
        raise ValueError('SSD boot repair did not complete verification')
    result['components'][ssd_identity] = dict(status='files_installed', runtime='not_validated',
        activation='first_boot_required', outcome='applied' if operation['changed'] else 'already_configured',
        operation=operation, target=ssd_plan['target'])
    result['install_plan']['ssd_repair'] = dict(id=ssd_identity,
        root_partuuid=ssd_plan['target']['root_partuuid'], esp_partuuid=ssd_plan['target']['esp_partuuid'],
        review_sha256=ssd_plan['review_sha256'],provider_owner=owner)
