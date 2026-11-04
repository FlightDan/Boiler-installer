"""Build a TOON-selected local ONEXPLAYER brand archive.

Inputs are the project's pinned canonical snapshot and two explicitly supplied
existing DevicePackages. The output is a development candidate, not a signed
release. An unresolved capability is listed in brand.toon and cannot be imported.
"""
from __future__ import annotations
import argparse
import ast
import copy
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from protocol.manifest import confined, validate_manifest, validate_package
from protocol.firmware_contract import validate_contract as validate_firmware_contract
from components import oxp3_full_efi_provider, x2_efi_provider
from maintenance.updates import _version_key
from protocol.resource_pool import decoder, json_document, validate_pool
from resources import model_source

GUIDES = (
    ('device:oxp3', 'brands/onexplayer/oxp3/oxp3.toon'),
    ('device:x2-mini', 'brands/onexplayer/x2-mini/x2-mini.toon'),
)


def collect_sources(snapshot, guide, guide_path, toon, inventory):
    """Resolve explicit TOON source paths; missing external items remain visible."""
    selected = {guide_path}
    unresolved, pending = [], []
    for component in guide['components']:
        name = component['id']
        sources = component.get('sources', [])
        if not sources and component.get('status') in ('implementation_missing', 'external_plugin', 'external_boiler_core'):
            pending.append({'component': name, 'status': component['status']})
        for path in sources:
            selected.add(path)
    spec = guide['resource_model']
    common = spec.get('common_references', {})
    for item in common.get('resources', []):
        path = item['source']
        selected.add(path)
        source = toon.loads(confined(snapshot, path).read_text(encoding='utf-8'), strict=True)
        if source.get('id') != item['id']:
            raise ValueError('Common source identity differs: ' + path)
        base = PurePosixPath(path).parent
        for row in source.get('files', []):
            target = str(base / row['path'])
            record = inventory.get('adaptations/' + target)
            if record is not None:
                if (record['size'], record['sha256']) != (row['size'], row['sha256']):
                    raise ValueError('Canonical common source differs from its TOON record: ' + target)
                selected.add(target)
            else:
                unresolved.append({'model': guide['id'], 'resource': item['id'], 'path': target,
                                   'reason': 'common-source input absent from pinned snapshot'})
        for path in source.get('licenses', []):
            selected.add(str(base / path))
    for policy in common.get('boot_policies', []):
        selected.add(policy['source'])
    for path in sorted(selected):
        if not path.startswith(('common/', 'brands/onexplayer/')):
            raise ValueError('Selected source outside canonical brand or common directory: ' + path)
        if not confined(snapshot, path).is_file():
            unresolved.append({'model': guide['id'], 'path': path, 'reason': 'canonical source absent'})
    return selected, unresolved, pending


def selected_components(guide, manifest):
    """Project the package components requested by the canonical TOON."""
    declaration = guide['package']
    wanted = set(declaration.get('inherited_components', []))
    wanted.update(row['id'] for row in declaration.get('definitions', []))
    if any(row['id'] == 'fingerprint-unlock' for row in guide['components']):
        wanted.add('oxp3:fingerprint-unlock')
    if guide['resource_model'].get('common_references', {}).get('boot_policies'):
        wanted.update(c['id'] for c in manifest['components'] if 'ssd_boot_policy' in c)
    found = {c['id']: c for c in manifest['components']}
    missing = sorted(wanted - found.keys())
    components = [copy.deepcopy(c) for c in manifest['components'] if c['id'] in wanted]
    return components, missing


def load_resource_pool(root, snapshot_inventory):
    if root is None:
        return None
    root = Path(root).resolve()
    catalog = validate_pool(root)
    proof = json_document(confined(root, 'provenance/model-resource-sources.json').read_bytes())
    if proof.get('kind') != 'ModelResourceSources':
        raise ValueError('Resource pool lacks canonical model source records')
    expected = {path for _, path in GUIDES}
    rows = proof.get('files', [])
    if {row.get('path', '').removeprefix('adaptations/') for row in rows} != expected:
        raise ValueError('Resource pool model set differs from TOONs')
    for row in rows:
        source = snapshot_inventory.get(row['path'])
        if source is None or (source['size'], source['sha256']) != (row['size'], row['sha256']):
            raise ValueError('Resource pool model source differs from current snapshot')
    return catalog


def equivalent_resource(resource, old_rows, old_sources):
    """Match every installed file, mode, and missing-only rule to the existing runtime."""
    payload = {row['path']: row for row in resource['payload']['files']}
    expected = {}
    for row in resource['install']['files']:
        item = payload[row['source']]
        key = (row['target'], item['size'], item['sha256'], row.get('mode', 420),
               row.get('missing_only', False))
        if key in expected:
            raise ValueError('Duplicate resource install target')
        member = row['source']
        if member.startswith('devices/Audio/microphone-mute/'):
            member = 'brands/onexplayer/common/microphone/' + member.removeprefix('devices/Audio/microphone-mute/')
        expected[key] = (row['source'], member)
    actual = {}
    for row in old_sources:
        logical = row['source']
        record = old_rows[logical]
        key = (row['target'], record['size'], record['sha256'], row.get('mode', 420),
               row.get('missing_only', False))
        if key in actual:
            raise ValueError('Duplicate legacy installation target')
        actual[key] = logical
    if not expected or expected.keys() != actual.keys():
        return None
    return {actual[key]: expected[key] for key in expected}


def fixed_provider_files(project, package_dir, identity, rows):
    if identity == 'device:oxp3':
        path = project / 'components/oxp3_full_efi_provider.lock.json'
        verifier = oxp3_full_efi_provider.verify
    elif identity == 'device:x2-mini':
        path = project / 'components/x2_efi_provider.lock.json'
        verifier = x2_efi_provider.verify
    else:
        raise ValueError('Unsupported brand provider identity: ' + identity)
    # Use the existing provider verifier and its existing inventory. The normal
    # package validator has already verified the supplied package bytes.
    verifier(package_dir, path)
    records = json_document(path.read_bytes())['files']
    selected = set()
    for entry in records:
        old = rows.get(entry['path'])
        if not old or (old['size'], old['sha256']) != (entry['size'], entry['sha256']):
            raise ValueError('Provider member differs from existing lock: ' + entry['path'])
        selected.add(entry['path'])
    return selected


def resource_record_destination(path, identity):
    # Resource outputs are derived artifacts. Device discovery remains in
    # devices/, so ONEXPLAYER-specific records are shipped under the brand.
    if path.startswith(('common/', 'brands/onexplayer/')):
        return path
    if path.startswith('licenses/'):
        return 'common/' + path
    if path.startswith('resources/'):
        return 'brands/onexplayer/common/resource-records/' + identity.removeprefix('resource:') + '/' + path.removeprefix('resources/')
    aliases = {
        'devices/EC/onexplayer/light/': 'brands/onexplayer/common/light/',
        'devices/EC/onexplayer/battery/': 'brands/onexplayer/common/battery/',
        'devices/Audio/microphone-mute/': 'brands/onexplayer/common/microphone/',
        'devices/CPU/Intel/G3E/': 'brands/onexplayer/common/resource-records/intel-g3e/',
    }
    for prefix, target in aliases.items():
        if path.startswith(prefix):
            return target + path.removeprefix(prefix)
    raise ValueError('Resource record has no brand or common location: ' + identity + ': ' + path)


def add_resource_gap(unresolved, pending, model, resource, pool_row, mapped):
    reason = ('runtime assets matched; new resource install and restore are not verified'
              if mapped else 'new resource runtime and lifecycle are not connected')
    # Generated pool-model enabled=false is a statement about the new resource
    # flow only; it must never change the legacy package defaults or mark a
    # currently available legacy capability unavailable.
    if pool_row and pool_row.get('enabled') is False:
        pending.append({'model': model, 'resource': resource, 'status': 'disabled-resource-not-integrated',
                        'payload_included': bool(mapped), 'reason': reason})
    else:
        unresolved.append({'model': model, 'resource': resource, 'reason': reason})


def legacy_capability_roots(guide, manifest, integration, package_dir):
    # These ten capabilities use fixed branches in the already-reviewed
    # integration/maintenance implementation rather than integration files
    # records. Platform-owned files accessed on the target are not package data.
    base = 'payload/legacy/components/'
    special = {
        'slot-switch': ('slot_switch.py', 'slot-switch/boot.py', 'slot-switch/desktop.py',
                        'slot-switch/oxp3-switch-slot', 'slot-switch/49-oxp3-slot-switch.rules',
                        'slot-switch/oxp3-slot-switch.desktop'),
        'partition-mount': ('partition_mount.py', 'partition-mount/mounts.py',
                            'partition-mount/desktop.py', 'partition-mount/oxp3-partition-mount',
                            'partition-mount/oxp3-partition-mount.desktop'),
        'windows-reboot': ('windows_reboot.py', 'windows-reboot/main.py', 'windows-reboot/plugin.json',
                           'windows-reboot/package.json', 'windows-reboot/dist/index.js',
                           'windows-reboot/boot.py', 'windows-reboot/desktop.py',
                           'windows-reboot/oxp3-reboot-windows',
                           'windows-reboot/org.oxp3.restart-windows.policy',
                           'windows-reboot/49-oxp3-restart-windows.rules',
                           'windows-reboot/oxp3-reboot-windows.desktop'),
        'flathub-ustc': ('flathub.flatpakrepo',),
        'hdr': ('hdr/installer.py', 'hdr/generate.py', 'hdr/upstream.lua', 'hdr/UPSTREAM_LICENSE'),
        'keyboard': (),
        'multifunction': (),
        'split-lock': ('split_lock.py', 'split-lock/UPSTREAM_LICENSE'),
        'decky': ('plugin_installer.py',),
        'waydroid': ('waydroid_setup.py',),
    }
    requested = set(guide['package'].get('inherited_legacy_capabilities', []))
    available = {row['id'].removeprefix('oxp3:') for row in manifest.get('legacy_capabilities', [])
                 if row.get('available') is True and row['id'] in requested}
    roots = {'selection.json', 'adapter.py', 'maintenance_adapter.py', 'scripts/maintenance.sh',
             base + 'options.json', base + 'integration.py', base + 'integration_runtime.py',
             'payload/legacy/integration/manifest.json', 'payload/legacy/maintenance/manifest.json'}
    unknown = []
    for name in sorted(available):
        if name in integration:
            group = integration[name]
            roots.update('payload/legacy/integration/' + entry['source'] for entry in group.get('files', []))
            if group.get('runtime_archive'):
                roots.add('payload/legacy/integration/' + group['runtime_archive'])
        elif name in special:
            roots.update(base + path for path in special[name])
        else:
            unknown.append(name)
    if 'decky' in available:
        roots.add('payload/legacy/plugins/decky/PluginLoader')
    if 'waydroid' in available:
        profile = 'payload/legacy/waydroid/profile.json'
        roots.add(profile)
        info = json_document(confined(package_dir, profile).read_bytes())
        roots.add('payload/legacy/waydroid/' + info['archive'])
    return roots, unknown, sorted(available)


def legacy_runtime_roots(package_dir):
    # The legacy integration explicitly publishes these fixed helpers on every
    # successful transaction. Read the fixed literal lists, not the entire
    # model tree, so a missing lifecycle input is visible before packaging.
    name = 'payload/legacy/components/integration.py'
    tree = ast.parse(confined(package_dir, name).read_bytes(), filename=name)
    roots, found = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name) or node.target.id != 'name':
            continue
        try:
            values = ast.literal_eval(node.iter)
        except (ValueError, TypeError):
            continue
        if not isinstance(values, tuple) or not all(isinstance(value, str) for value in values):
            continue
        if 'integration_runtime.py' in values and 'release_notes.py' in values:
            prefix = 'payload/legacy/components/'
            found.add('runtime')
        elif {'installer.py', 'generate.py', 'upstream.lua', 'UPSTREAM_LICENSE'} == set(values):
            prefix = 'payload/legacy/components/hdr/'
            found.add('hdr')
        else:
            continue
        roots.update(prefix + value for value in values)
    if found != {'runtime', 'hdr'}:
        raise ValueError('Cannot identify fixed legacy runtime input lists')
    roots.add('payload/legacy/components/split_lock.py')
    roots.add('payload/legacy/components/split-lock/UPSTREAM_LICENSE')
    return roots


def legacy_python_closure(package_dir, inventory, initial):
    # Discover imports without executing legacy code; dynamic asset selection is
    # independently reported until each branch has been checked.
    root = 'payload/legacy/components/'
    queue = list(initial)
    selected = set(initial)
    while queue:
        name = queue.pop()
        if not name.endswith('.py') or name not in inventory:
            continue
        tree = ast.parse(confined(package_dir, name).read_bytes(), filename=name)
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [entry.name for entry in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
                modules += [node.module + '.' + entry.name for entry in node.names]
            for module in modules:
                relative = module.replace('.', '/')
                for candidate in (root + relative + '.py', root + relative + '/__init__.py'):
                    if candidate in inventory and candidate not in selected:
                        selected.add(candidate)
                        queue.append(candidate)
    return selected


def matches_reviewed_decky_transform(project, snapshot, package_dir, source, old, rows):
    # The upstream build-upstream-repairs.py applies precisely this source
    # transformation when preparing the existing locked Decky 3.3.1 provider.
    lock = json_document((project / 'components/locks/decky.json').read_bytes())
    if lock.get('version') != '3.3.1':
        return False
    binary = rows.get('payload/legacy/plugins/decky/PluginLoader')
    expected = lock['file']
    if not binary or (binary['size'], binary['sha256']) != (expected['size'], expected['sha256']):
        return False
    for item in lock['notices']:
        name = 'payload/legacy/plugins/decky/' + item['source']
        if name not in rows or (rows[name]['size'], rows[name]['sha256']) != (item['size'], item['sha256']):
            return False
    original = confined(snapshot, source).read_bytes()
    before, after = b"b'3.2.10\\n'", b"b'3.3.1\\n'"
    if original.count(before) != 1:
        return False
    return original.replace(before, after) == confined(package_dir, old).read_bytes()


def matches_serial_policy(resource, pool_root, package_dir, helper):
    # serial_console.files() is a fixed literal DROPIN and DATA pair. Parse the
    # constants without executing the package; compare to the validated pool.
    tree = ast.parse(confined(package_dir, helper).read_bytes(), filename=helper)
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ('DROPIN', 'DATA'):
                values[node.targets[0].id] = ast.literal_eval(node.value)
    files = resource.get('install', {}).get('files', [])
    if len(files) != 1 or set(values) != {'DROPIN', 'DATA'}:
        return None
    row = files[0]
    if (row['target'] != values['DROPIN'] or row.get('mode', 420) != 420
            or row.get('missing_only', False)
            or confined(pool_root, row['source']).read_bytes() != values['DATA']):
        return None
    return row['source']


def mapped_resource_assets(resource, old_rows, prefix):
    # Select only bytes under the component directory named by the requested
    # capability. Content identity here uses the already validated inventories.
    result = {}
    for item in resource['payload']['files']:
        matches = [name for name, row in old_rows.items()
                   if name.startswith(prefix)
                   and (row['size'], row['sha256']) == (item['size'], item['sha256'])]
        original = item['path']
        if len(matches) > 1:
            filename = PurePosixPath(original).name
            if len(filename) > 4 and filename[:3].isdigit() and filename[3] == '-':
                filename = filename[4:]
            matches = [name for name in matches if PurePosixPath(name).name == filename]
        if len(matches) != 1:
            return None
        member = original
        brand_prefix = 'devices/EC/onexplayer/light/'
        if member.startswith(brand_prefix):
            member = 'brands/onexplayer/common/light/' + member.removeprefix(brand_prefix)
        result[matches[0]] = (original, member)
    return result if result else None


def legacy_closure_issues(guide, manifest, package_dir, selected, provider_roots, rows, integration):
    """Check the actual legacy inputs selected for this model, without running hooks."""
    selected = set(selected)
    required, unknown, available = legacy_capability_roots(guide, manifest, integration, package_dir)
    required.update(legacy_runtime_roots(package_dir))
    required.update(provider_roots)
    required.update(legacy_python_closure(package_dir, rows, selected))
    legacy = [row for row in manifest['components'] if row['id'] == 'oxp3:legacy-adaptation']
    if len(legacy) != 1 or set(legacy[0].get('hooks', {})) != {'apply', 'verify', 'restore'}:
        return [{'reason': 'legacy lifecycle hook declarations are incomplete'}]
    required.update(legacy[0]['hooks'].values())
    required.update(row['source'] for row in legacy[0].get('files', []))
    maintenance_name = 'payload/legacy/maintenance/manifest.json'
    required.add(maintenance_name)
    maintenance = json_document(confined(package_dir, maintenance_name).read_bytes())
    for name, digest in maintenance['files'].items():
        path = 'payload/legacy/maintenance/app/' + name
        old = rows.get(path)
        if old is None or old['sha256'] != digest:
            return [{'reason': 'retained maintenance inventory differs', 'path': path}]
        required.add(path)
    issues = []
    if unknown:
        issues.append({'reason': 'unmapped available legacy capabilities', 'capabilities': unknown})
    missing = sorted(required - selected)
    if missing:
        issues.append({'reason': 'required legacy inputs not selected', 'paths': missing})
    return issues


def require_new_version(version, previous):
    if not isinstance(version, str) or not version:
        raise ValueError('An explicit brand candidate version is required')
    if _version_key(version) <= _version_key(previous):
        raise ValueError('Brand candidate version must be newer than the input model package')
    return version


class SingleVersion(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error('--version must be supplied exactly once')
        setattr(namespace, self.dest, values)


def prepare_models(project, packages, *, version, resource_pool=None, allow_incomplete=False):
    project = Path(project).resolve()
    model_source.sources(project)
    snapshot = project / 'adaptations'
    lock = json_document((snapshot / 'canonical-model-snapshot.json').read_bytes())
    inventory = {row['path']: row for row in lock['files']}
    pool = load_resource_pool(resource_pool, inventory)
    pool_resources = {row['id']: row for row in pool['resources']} if pool else {}
    pool_models = {row['id']: row for row in pool['models']} if pool else {}
    pool_refs = {row['id']: row for row in pool['resource_refs']} if pool else {}
    pool_inventory = {row['path']: row for row in pool['files']} if pool else {}
    toon = decoder(snapshot)
    oxp_guide = toon.loads(confined(snapshot, GUIDES[0][1]).read_text(encoding='utf-8'), strict=True)
    opti_plugin_pin = oxp_guide['build']['plugin_pins']['sha256']
    source_files, models, unresolved, pending = set(), [], [], []
    for identity, guide_path in GUIDES:
        guide = toon.loads(confined(snapshot, guide_path).read_text(encoding='utf-8'), strict=True)
        if guide['runtime']['id'] != identity:
            raise ValueError('Model guide identity differs: ' + guide_path)
        sources, missing, postponed = collect_sources(snapshot, guide, guide_path, toon, inventory)
        source_files.update(sources)
        unresolved.extend(missing)
        pending.extend(dict(model=identity, **entry) for entry in postponed)
        # Package validation retains the existing developer-input checks.
        package_dir = Path(packages[identity]).resolve()
        manifest = validate_package(package_dir, allow_unsigned=True)
        if manifest['id'] != identity:
            raise ValueError('Explicit package identity differs: ' + identity)
        require_new_version(version, manifest['version'])
        components, missing_components = selected_components(guide, manifest)
        for name in missing_components:
            unresolved.append({'model': identity, 'component': name, 'reason': 'requested package component missing'})
        rows = {row['path']: row for row in manifest['files']}
        provider_roots = fixed_provider_files(project, package_dir, identity, rows)
        chosen, source_mappings = set(provider_roots), {}
        # Every bundled fingerprint runtime needs the notices from its existing
        # validated input package; these are not test or device evidence files.
        if any(c['id'] == 'oxp3:fingerprint-unlock' for c in components):
            chosen.update(name for name in rows if name.startswith('fingerprint/licenses/'))
        # TOON source records are authoritative. Map only byte-identical
        # explicitly requested files from the old package, and report changes.
        for component in guide['components']:
            for source in component.get('sources', []):
                for prefix in ('brands/onexplayer/oxp3/scripts/', 'common/scripts/'):
                    if not source.startswith(prefix):
                        continue
                    old = 'payload/legacy/components/' + source.removeprefix(prefix)
                    current, previous = inventory.get('adaptations/' + source), rows.get(old)
                    if current is None or previous is None:
                        unresolved.append({'model': identity, 'component': component['id'],
                                           'path': source, 'reason': 'legacy requested source not present'})
                    elif (current['size'], current['sha256']) != (previous['size'], previous['sha256']):
                        if (source.endswith('/plugin_installer.py') and
                                matches_reviewed_decky_transform(project, snapshot, package_dir, source, old, rows)):
                            chosen.add(old)
                            chosen.add('payload/legacy/plugins/decky/PluginLoader')
                            decky_lock = json_document((project / 'components/locks/decky.json').read_bytes())
                            for notice in decky_lock['notices']:
                                chosen.add('payload/legacy/plugins/decky/' + notice['source'])
                            if 'payload/legacy/licenses/runtime-inventory.json' in rows:
                                chosen.add('payload/legacy/licenses/runtime-inventory.json')
                        else:
                            unresolved.append({'model': identity, 'component': component['id'],
                                               'path': source, 'reason': 'legacy source differs from current TOON'})
                    else:
                        chosen.add(old)
                        if old not in source_mappings or source.startswith('common/'):
                            source_mappings[old] = source
        for component in components:
            chosen.update(row['source'] for row in component.get('files', []))
            chosen.update(component.get('hooks', {}).values())
            if component.get('hooks') and component['id'] != 'oxp3:legacy-adaptation':
                unresolved.append({'model': identity, 'component': component['id'],
                                   'reason': 'unmapped component hook'})
        if any(c['id'] == 'oxp3:legacy-adaptation' for c in components):
            # These fixed inventories bind a subset of the dynamic legacy hook's
            # actual inputs to TOON-requested capabilities and retained maintenance.
            declared = set(guide['package'].get('inherited_legacy_capabilities', []))
            actual = {row['id'] for row in manifest.get('legacy_capabilities', [])}
            if declared != actual:
                raise ValueError('Legacy capabilities differ from canonical TOON: ' + identity)
            integration_name = 'payload/legacy/integration/manifest.json'
            integration = json_document(confined(package_dir, integration_name).read_bytes())
            chosen.add(integration_name)
            available = {row['id'].removeprefix('oxp3:') for row in manifest.get('legacy_capabilities', [])
                         if row.get('available') is True}
            capability_names = {name.removeprefix('oxp3:') for name in declared} & available
            for name, group in integration.items():
                if name not in capability_names:
                    continue
                for entry in group.get('files', []):
                    chosen.add('payload/legacy/integration/' + entry['source'])
                if group.get('runtime_archive'):
                    chosen.add('payload/legacy/integration/' + group['runtime_archive'])
            if 'waydroid' in capability_names:
                profile_name = 'payload/legacy/waydroid/profile.json'
                profile = json_document(confined(package_dir, profile_name).read_bytes())
                chosen.add(profile_name)
                for key in ('archive', 'source_archive'):
                    if profile.get(key):
                        chosen.add('payload/legacy/waydroid/' + profile[key])
                if 'payload/legacy/waydroid/intel-ibt-kernel-calls.patch' in rows:
                    chosen.add('payload/legacy/waydroid/intel-ibt-kernel-calls.patch')
            maintenance_name = 'payload/legacy/maintenance/manifest.json'
            maintenance = json_document(confined(package_dir, maintenance_name).read_bytes())
            chosen.add(maintenance_name)
            for name, digest in maintenance['files'].items():
                path = 'payload/legacy/maintenance/app/' + name
                if path not in rows or rows[path]['sha256'] != digest:
                    raise ValueError('Retained maintenance inventory differs: ' + path)
                chosen.add(path)
            # The legacy adapter reads selection.json when neither an explicit
            # caller selection nor an existing selection record is available.
            chosen.add('selection.json')
            for name in ('scripts/maintenance.sh', 'adapter.py', 'maintenance_adapter.py',
                         'payload/legacy/components/integration.py',
                         'payload/legacy/components/integration_runtime.py',
                         'payload/legacy/components/options.json', 'payload/legacy/components/catalog.json',
                         'payload/legacy/LICENSE', 'payload/legacy/THIRD_PARTY_NOTICES.md'):
                if name in rows:
                    chosen.add(name)
            runtime_roots = legacy_runtime_roots(package_dir)
            capability_roots, unknown_capabilities, audited_capabilities = legacy_capability_roots(
                guide, manifest, integration, package_dir)
            if unknown_capabilities:
                unresolved.append({'model': identity, 'component': 'oxp3:legacy-adaptation',
                                   'reason': 'unmapped available legacy capabilities',
                                   'capabilities': unknown_capabilities})
            missing_roots = sorted((runtime_roots | capability_roots) - rows.keys())
            if missing_roots:
                unresolved.append({'model': identity, 'component': 'oxp3:legacy-adaptation',
                                   'reason': 'legacy capability input missing from package',
                                   'paths': missing_roots})
            chosen.update((runtime_roots | capability_roots) & rows.keys())
            chosen.update(legacy_python_closure(package_dir, rows, chosen))
            # Imported helper modules are dependencies of TOON-selected code.
            # Prefer their original canonical physical path when bytes match.
            for old in sorted(chosen):
                prefix = 'payload/legacy/components/'
                if not old.startswith(prefix) or old in source_mappings:
                    continue
                suffix = old.removeprefix(prefix)
                for source in ('common/scripts/' + suffix, 'brands/onexplayer/oxp3/scripts/' + suffix):
                    current = inventory.get('adaptations/' + source)
                    previous = rows.get(old)
                    if current and previous and (current['size'], current['sha256']) == (previous['size'], previous['sha256']):
                        source_mappings[old] = source
                        break
            if 'oxp3:opti2xmx' in declared:
                deployment_name = 'payload/legacy/components/opti2xmx/deployment.json'
                deployment = json_document(confined(package_dir, deployment_name).read_bytes())
                runtime_name = 'payload/legacy/integration/opti2xmx/runtime.tar.gz'
                runtime = deployment['runtime']
                if deployment.get('plugin_sha256') != opti_plugin_pin:
                    raise ValueError('Existing Opti deployment differs from canonical TOON')
                if (runtime_name not in rows or rows[runtime_name]['size'] != runtime['size_bytes']
                        or rows[runtime_name]['sha256'] != runtime['sha256']):
                    raise ValueError('Existing Opti runtime differs from its deployment inventory')
                chosen.update((deployment_name, runtime_name))
                for name in ('payload/legacy/components/opti2xmx/LICENSE',
                             'payload/legacy/components/opti2xmx/opti-archive-files.json'):
                    if name in rows:
                        chosen.add(name)
        maintenance = manifest.get('maintenance_entry')
        if isinstance(maintenance, str):
            chosen.add(maintenance)
        for name in sorted(chosen):
            if name not in rows:
                raise ValueError('Component file missing from input manifest: ' + name)
        resource_mappings, resource_extras, resource_records = {}, {}, {}
        be201_descriptor = None
        declared_resources = {row['id'] for row in guide['resource_model'].get('resources', [])}
        if pool:
            pool_model = pool_models.get(identity)
            if not pool_model or {row['resource'] for row in pool_model['resources']} != declared_resources:
                raise ValueError('Resource pool model differs from canonical TOON: ' + identity)
        pool_rows = {row['resource']: row for row in pool_model['resources']} if pool else {}
        direct = {'resource:onexplayer-microphone': 'oxp3:microphone',
                  'resource:oxp3-speaker-route': 'oxp3:speaker-route',
                  'resource:intel-xevmafix-724': 'oxp3:xevmafix'}
        for resource in sorted(declared_resources):
            candidate = pool_resources.get(resource)
            if resource == 'resource:intel-be201-firmware' and candidate:
                validate_firmware_contract(candidate)
                reference = pool_refs[resource]['path']
                if reference != 'common/network/intel/be201/resource.toon':
                    raise ValueError('Unexpected BE201 resource record path')
                if any(c.get('id') == 'oxp3:wifi' and (c.get('available') or c.get('default'))
                       for c in manifest.get('legacy_capabilities', [])):
                    raise ValueError('BE201 cannot share ownership with legacy Wi-Fi')
                payload = {row['path']: row for row in candidate['payload']['files']}
                inputs = {}
                for item in candidate['install']['files']:
                    name = item['source']
                    canonical = 'common/network/intel/be201/inputs/' + PurePosixPath(item['target']).name
                    source = inventory.get('adaptations/' + canonical)
                    if name not in payload or not source or (source['size'], source['sha256']) != (payload[name]['size'], payload[name]['sha256']):
                        raise ValueError('BE201 input differs from canonical source: ' + canonical)
                    inputs.setdefault(name, canonical)
                if set(inputs) != set(payload):
                    raise ValueError('BE201 source references differ from payload')
                for name, canonical in inputs.items():
                    entry = pool_inventory.get(name)
                    if not entry or (entry['size'], entry['sha256']) != (payload[name]['size'], payload[name]['sha256']):
                        raise ValueError('BE201 payload differs from pool inventory: ' + name)
                    rows[name] = entry
                    chosen.add(name)
                    source_mappings[name] = canonical
                for name in [reference, *candidate['license_files']]:
                    entry = pool_inventory.get(name)
                    if not entry:
                        raise ValueError('BE201 resource metadata absent from pool: ' + name)
                    rows[name] = entry
                    chosen.add(name)
                    resource_mappings[name] = (name, name)
                be201_descriptor = {'kind': 'intel-be201-firmware-v1', 'resource': reference}
                pending.append({'model': identity, 'resource': resource, 'status': 'hardware-and-lifecycle-validation-pending',
                                'payload_included': True, 'reason': 'hardware matching and lifecycle require validation'})
                continue
            old_sources = []
            if resource in direct:
                old = next((c for c in components if c['id'] == direct[resource]), None)
                old_sources = old.get('files', []) if old else []
            elif (resource == 'resource:intel-be201-firmware'
                  and 'oxp3:legacy-adaptation' in {c['id'] for c in components}
                  and 'wifi' in capability_names):
                old_sources = [dict(item, source='payload/legacy/integration/' + item['source'])
                               for item in integration.get('wifi', {}).get('files', [])]
            mapping = equivalent_resource(candidate, rows, old_sources) if candidate and old_sources else None
            if resource == 'resource:common-serial-console-policy' and candidate:
                helper = 'payload/legacy/components/serial_console.py'
                if helper in source_mappings:
                    source = matches_serial_policy(candidate, Path(resource_pool), package_dir, helper)
                    if source is not None:
                        resource_extras[source] = source
                        continue
            if mapping is None:
                # The three legacy dynamic file generators and the brand light
                # component have verifiable input assets, but their complete
                # install/restore semantics still need integration verification.
                dynamic = {
                    'resource:common-slot-switch': 'payload/legacy/components/slot-switch/',
                    'resource:common-partition-mount': 'payload/legacy/components/partition-mount/',
                    'resource:common-windows-reboot': 'payload/legacy/components/windows-reboot/',
                    'resource:onexplayer-light': 'payload/legacy/components/light/',
                }
                mapped = mapped_resource_assets(candidate, rows, dynamic[resource]) if candidate and resource in dynamic else None
                if mapped:
                    chosen.update(mapped)
                    resource_mappings.update(mapped)
                # The fixed legacy helpers have a dedicated synthetic regression
                # comparing every output target, bytes and mode to the pool.
                helpers = {
                    'resource:common-slot-switch': 'payload/legacy/components/slot_switch.py',
                    'resource:common-partition-mount': 'payload/legacy/components/partition_mount.py',
                    'resource:common-windows-reboot': 'payload/legacy/components/windows_reboot.py',
                }
                helper = helpers.get(resource)
                if not (mapped and helper and helper in source_mappings):
                    add_resource_gap(unresolved, pending, identity, resource, pool_rows.get(resource), mapped)
                    if candidate and pool_rows.get(resource, {}).get('enabled') is False:
                        metadata = [pool_refs[resource]['path'], *candidate.get('license_files', [])]
                        members = []
                        for original in metadata:
                            if original not in pool_inventory:
                                raise ValueError('Pending resource record missing from pool inventory: ' + original)
                            member = resource_record_destination(original, resource)
                            entry = pool_inventory[original]
                            resource_records[member] = (original, entry['size'], entry['sha256'])
                            members.append(member)
                        pending[-1]['definition'] = members[0]
                        pending[-1]['licenses'] = members[1:]
            else:
                chosen.update(mapping)
                resource_mappings.update(mapping)
        if any(c['id'] == 'oxp3:legacy-adaptation' for c in components):
            issues = legacy_closure_issues(guide, manifest, package_dir, chosen, provider_roots, rows, integration)
            if issues:
                unresolved.extend(dict(model=identity, component='oxp3:legacy-adaptation', **issue) for issue in issues)
            else:
                # Static selection does not establish actual Linux transactions.
                pending.append({'model': identity, 'component': 'oxp3:legacy-adaptation',
                                'status': 'linux-lifecycle-not-verified',
                                'reason': 'static inputs checked; Linux installation, maintenance and recovery require validation'})
        generated = copy.deepcopy(manifest)
        # This is a new local development candidate; the original package
        # identity and its historical inventory remain untouched.
        generated['version'] = version
        generated['name'] = guide['runtime']['name']
        if be201_descriptor:
            generated['be201_firmware'] = be201_descriptor
        generated['components'] = components
        generated['files'] = [rows[name] for name in sorted(chosen)]
        validate_manifest(generated, 'DevicePackage')
        models.append({'id': identity, 'guide': guide_path, 'generated': generated,
                       'package_dir': package_dir, 'rows': rows,
                       'resource_mappings': resource_mappings, 'resource_extras': resource_extras,
                       'resource_records': resource_records, 'source_mappings': source_mappings})
    if unresolved and not allow_incomplete:
        raise ValueError('Cannot create installable brand package: unresolved TOON runtime dependencies; use --report')
    return lock, toon, source_files, models, unresolved, pending


def report(project, packages, *, version, resource_pool=None):
    lock, _, sources, models, unresolved, pending = prepare_models(
        project, packages, version=version, resource_pool=resource_pool, allow_incomplete=True)
    return {'source_repository': lock['repository'], 'source_commit': lock['commit'], 'version': version,
            'models': [{'id': m['id'], 'guide': m['guide'], 'components': len(m['generated']['components']),
                        'selected_runtime_files': len(m['generated']['files'])} for m in models],
            'selected_source_files': len(sources), 'unresolved': unresolved, 'pending': pending}


def build(project, packages, output, *, version, resource_pool=None, allow_incomplete=False):
    output = Path(output).absolute()
    if output.exists():
        raise FileExistsError('Use a fresh output file')
    lock, toon, sources, models, unresolved, pending = prepare_models(
        project, packages, version=version, resource_pool=resource_pool, allow_incomplete=allow_incomplete)
    snapshot = Path(project).resolve() / 'adaptations'
    # Package bytes are emitted only once for matching existing manifest entries.
    entries, entry_keys, file_owners, brand_models = {}, {}, {}, []
    snapshot_inventory = {row['path'].removeprefix('adaptations/'): row
                          for row in lock.get('files', [])}
    for path in sorted(sources):
        file = confined(snapshot, path)
        if file.is_file():
            entries[path] = file
            row = snapshot_inventory.get(path)
            if row is not None:
                entry_keys[path] = (row['size'], row['sha256'])
    for model in models:
        for row in model['generated']['files']:
            file_owners.setdefault((row['path'], row['size'], row['sha256']), []).append(model['id'])
    for model in models:
        for member, (original, size, digest) in model.get('resource_records', {}).items():
            expected = (size, digest)
            if member in entry_keys and entry_keys[member] != expected:
                raise ValueError('Conflicting pending resource record: ' + member)
            if member not in entries:
                entries[member] = confined(Path(resource_pool), original)
                entry_keys[member] = expected
        for original, member in model.get('resource_extras', {}).items():
            if member not in entries:
                entries[member] = confined(Path(resource_pool), original)
        name = model['id'].removeprefix('device:')
        manifest_name = 'brands/onexplayer/' + ('oxp3' if name == 'oxp3' else 'x2-mini') + '/runtime/manifest.json'
        manifest = (json.dumps(model['generated'], ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        entries[manifest_name] = manifest
        mappings = []
        for row in model['generated']['files']:
            key = (row['path'], row['size'], row['sha256'])
            shared = len(file_owners[key]) == len(models)
            logical = row['path']
            source = confined(model['package_dir'], logical)
            # When the TOON explicitly names these bytes, reuse their canonical
            # physical member; no second copy of a shared source is needed.
            match = None
            selected_source = model.get('source_mappings', {}).get(logical)
            if selected_source:
                record = snapshot_inventory.get(selected_source)
                if not record or (record['size'], record['sha256']) != key[1:]:
                    raise ValueError('Selected source differs from canonical inventory')
                if selected_source not in entries:
                    entries[selected_source] = confined(snapshot, selected_source)
                    entry_keys[selected_source] = key[1:]
                match = selected_source
            pooled = model.get('resource_mappings', {}).get(logical) if match is None else None
            if pooled:
                original, member = pooled
                pool_file = confined(Path(resource_pool), original)
                expected = key[1:]
                if member in entry_keys and entry_keys[member] != expected:
                    raise ValueError('Conflicting resource output member: ' + member)
                if member not in entries:
                    entries[member] = pool_file
                    entry_keys[member] = expected
                match = member
            if match is None and logical.startswith('fingerprint/profile/common/'):
                candidate = logical.removeprefix('fingerprint/profile/')
                record = snapshot_inventory.get(candidate)
                if record and (record['size'], record['sha256']) == key[1:]:
                    match = candidate
                    if candidate not in entries:
                        entries[candidate] = confined(snapshot, candidate)
                        entry_keys[candidate] = key[1:]
            families = {
                'upstream-repairs/speaker/': 'common/audio/realtek/alc245/speaker-route/',
                'upstream-repairs/xevmafix/': 'common/gpu/intel/xe/xevmafix-724/',
            }
            family = next((root for prefix, root in families.items() if logical.startswith(prefix)), None)
            if match is None and family:
                candidates = [path for path, record in snapshot_inventory.items()
                              if path in entries and path.startswith(family)
                              and (record['size'], record['sha256']) == key[1:]
                              and PurePosixPath(path).name == PurePosixPath(logical).name]
                if len(candidates) == 1:
                    match = candidates[0]
            if match is not None:
                member = match
            elif shared and family:
                member = family + 'runtime/' + PurePosixPath(logical).name
            elif shared and logical.startswith('fingerprint/'):
                member = 'common/fingerprint/runtime/' + logical.removeprefix('fingerprint/')
            elif shared and logical.startswith('payload/legacy/integration/gyro/'):
                member = 'common/sensors/bosch/bmi260/gyro/runtime/' + PurePosixPath(logical).name
            else:
                scope = 'common' if shared else ('oxp3' if name == 'oxp3' else 'x2-mini')
                member = 'brands/onexplayer/' + scope + '/runtime/' + logical
            expected = key[1:]
            if member in entry_keys and entry_keys[member] != expected:
                raise ValueError('Conflicting brand output member: ' + member)
            if member not in entries:
                entries[member] = source
                entry_keys[member] = expected
            mappings.append({'path': logical, 'member': member})
        brand_models.append({'id': model['id'], 'guide': model['guide'],
                             'manifest': manifest_name, 'files': mappings})
    index = {'schema_version': 1, 'kind': 'BoilerBrandBundle', 'brand': 'onexplayer', 'version': version,
             'source_repository': lock['repository'], 'source_commit': lock['commit'],
             'models': brand_models, 'unresolved': unresolved, 'pending': pending}
    entries['brand.toon'] = (toon.dumps(index) + '\n').encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for name, source in sorted(entries.items()):
            if isinstance(source, bytes):
                archive.writestr(name, source)
            else:
                archive.write(source, name)
    return {'output': str(output), 'models': len(models), 'members': len(entries),
            'unresolved': len(unresolved), 'importable': not unresolved,
            'linux_lifecycle_accepted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--oxp3-package', type=Path, required=True)
    parser.add_argument('--x2-package', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--version', required=True, action=SingleVersion,
                        help='unique candidate version newer than both input packages')
    parser.add_argument('--resource-pool', type=Path,
                        help='explicit resource pool built from the current canonical snapshot')
    parser.add_argument('--report', action='store_true')
    parser.add_argument('--allow-incomplete', action='store_true',
                        help='write a non-installable draft when dependencies remain unresolved')
    args = parser.parse_args()
    packages = {'device:oxp3': args.oxp3_package, 'device:x2-mini': args.x2_package}
    if args.report:
        result = report(args.project, packages, version=args.version, resource_pool=args.resource_pool)
    else:
        if args.output is None:
            parser.error('--output is required unless --report is supplied')
        result = build(args.project, packages, args.output, version=args.version, resource_pool=args.resource_pool,
                       allow_incomplete=args.allow_incomplete)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
