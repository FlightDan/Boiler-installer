"""On-device file maintenance using only the pinned public model payload.

The generic caller authenticates/retains this package and owns the Engine lock
and SteamOS protection window. This adapter owns a separate durable Store; it
never calls the guest installer, replaces the old maintainer, or mounts a slot.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import types

PACKAGE = Path(__file__).resolve().parent
CONTRACT = {'kind': 'boiler-model-maintenance-v1', 'entry': 'scripts/maintenance.sh'}
STATE = 'home/.boiler/model-maintenance/oxp3'
GUARD = 'usr/local/lib/boiler/models/oxp3/guard.py'
ENABLE = 'etc/systemd/system/multi-user.target.d/90-boiler-oxp3-model.conf'
SPECIAL = {
    'refind': 'rEFInd is a separate boot-manager operation, not live model maintenance.',
    'waydroid': 'Waydroid needs a separate transactional host-package and Android-image lifecycle.',
    'gm7-resume': 'GM7 repair needs a reviewed EFI/VFAT boot-file transaction; this file-only adapter does not own EFI.',
    'split-lock': 'Existing split-lock boot settings are retained. Linux maintenance does not modify EFI/VFAT boot files until their disk linkage and transaction metadata are validated.',
}
THIRD_PARTY = {'steamgriddb', 'protondb', 'tabmaster', 'css-loader', 'autoflatpaks',
               'deck-settings', 'hltb', 'animation-changer'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _pairs(entries):
    result = {}
    for key, value in entries:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def read_json(path, maximum=2*1024*1024):
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                           getattr(os, 'O_NONBLOCK', 0)), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError('Expected bounded regular JSON file')
        raw = stream.read(maximum+1)
    if len(raw) > maximum:
        raise ValueError('JSON exceeds size limit')
    return json.loads(raw, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))


def modules(package=PACKAGE):
    """Isolate public payload imports from the installed legacy maintainer."""
    directory = Path(package)/'payload/legacy/maintenance/app/oxp3_maintenance'
    name = '_boiler_pinned_oxp3_maintenance'
    previous = sys.modules.get(name)
    if previous is None or Path(previous.__path__[0]) != directory:
        for key in list(sys.modules):
            if key == name or key.startswith(name+'.'):
                del sys.modules[key]
        module = types.ModuleType(name)
        module.__path__ = [str(directory)]
        sys.modules[name] = module
    return {part: __import__(name+'.'+part, fromlist=[part])
            for part in ('components', 'transactions', 'adoption', 'probe', 'compatibility')}


def target(root, api):
    root = Path(root).absolute()
    for current in (root, *root.parents):
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise RuntimeError('Target root must not traverse links')
    if root == Path('/'):
        return api['probe'].live()
    # Mounted-root validation uses the actual block-device topology. Request
    # JSON cannot supply or override a slot label or partition identity.
    command = api['probe'].run
    mounts = json.loads(command('findmnt', '--json', '--target', str(root), '--output',
                                'TARGET,SOURCE,MAJ:MIN,FSTYPE,UUID'))['filesystems']
    if len(mounts) != 1 or mounts[0].get('target') != str(root):
        raise RuntimeError('Explicit mounted target root is required')
    devices = json.loads(command('lsblk', '--json', '--tree', '--paths', '--output',
                                 'PATH,TYPE,MAJ:MIN,PARTUUID,PARTLABEL,TRAN,MODEL'))['blockdevices']
    verified = None
    if mounts[0].get('fstype') == 'btrfs':
        verified = api['probe']._single_btrfs_device(mounts[0].get('uuid'),
                                                    api['probe']._canonical_block(mounts[0].get('source')))
    topology = api['probe'].identify_root(mounts[0], devices, verified_device=verified)
    kernels = [item.name for item in (root/'usr/lib/modules').iterdir() if item.is_dir() and not item.is_symlink()]
    if len(kernels) != 1:
        raise RuntimeError('Mounted target kernel identity is ambiguous')
    return api['probe'].inspect(root, kernel=kernels[0], dmi={}, bios='', topology=topology)


def selection(request, package=PACKAGE):
    if not isinstance(request, dict) or type(request.get('schema_version')) is not int or request['schema_version'] != 1:
        raise ValueError('Model request schema_version 1 is required')
    choice = request.get('selection')
    if not isinstance(choice, dict):
        raise ValueError('Explicit model selection is required')
    # The on-device wire has no slot. Add only the guest validator's inert data
    # field internally; active partition authority always comes from probe.
    choice = dict(choice)
    if 'slot' in choice and choice['slot'] != 'guest':
        raise ValueError('The model selection cannot supply an authoritative slot')
    choice['slot'] = 'guest'
    choice['kde_language'] = {'zh-CN': 'zh_CN', 'en-US': 'en_US'}.get(choice.get('kde_language'), choice.get('kde_language'))
    spec = importlib.util.spec_from_file_location('_boiler_oxp3_selection', Path(package)/'adapter.py')
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    catalog = read_json(Path(package)/'payload/legacy/components/options.json')
    options = adapter.selection(choice, catalog)
    manifest = read_json(Path(package)/'manifest.json')
    if manifest.get('id') != 'device:oxp3' or manifest.get('maintenance_contract') != CONTRACT:
        raise ValueError('Authenticated package does not declare the on-device maintenance contract')
    if manifest.get('selection_contract') != {'kind': 'oxp3-legacy-options-v1', 'component': 'oxp3:legacy-adaptation'}:
        raise ValueError('Dynamic model selection contract is required')
    known = {item['id']: item for item in manifest['legacy_capabilities']}
    for category in ('repairs', 'plugins'):
        for identity in choice[category]:
            item = known.get(identity)
            if item is None or item.get('category') != category or item.get('available') is not True:
                raise ValueError('Unknown or unavailable packaged capability: '+identity)
    return public_selection(adapter.canonical_selection(choice, catalog)), options, manifest, catalog


def public_selection(choice):
    if not isinstance(choice, dict):
        return None
    output = {key: value for key, value in choice.items() if key != 'slot'}
    output['kde_language'] = {'zh_CN': 'zh-CN', 'en_US': 'en-US'}.get(output.get('kde_language'), output.get('kde_language'))
    return output


def installer_selection(root, system, package, api, *, consumed=()):
    """Read completed installed choices; never use an A/B label as authority."""
    label = system['slot'].get('label')
    if label not in ('A', 'B'):
        return None
    directory = Path(root)/'home/.oxp3-installer/integration'/label
    if not os.path.lexists(directory/'request.json'):
        return None
    for name in ('request.json', 'result.json'):
        api['adoption']._trusted(directory/name, Path(root)/'home')
    request, result = read_json(directory/'request.json'), read_json(directory/'result.json')
    if request.get('schema') != 1 or result.get('schema') != 1 or result.get('slot') != label or result.get('options') != request:
        raise RuntimeError('Installed legacy request/result selection proof is incomplete')
    catalog = read_json(Path(package)/'payload/legacy/components/options.json')
    choice = dict(schema_version=1, installer_language=request.get('installer_language'),
                  kde_language=request.get('kde_language'), acknowledge_legacy_update_hold=True)
    for category in ('repairs', 'plugins'):
        allowed = {item['id'] for item in catalog[category]}
        requested = request.get(category)
        if not isinstance(requested, list) or any(type(name) is not str or name not in allowed for name in requested):
            raise RuntimeError('Installed legacy selection contains unknown component identities')
        choice[category] = ['oxp3:'+name for name in requested
                            if name not in consumed and result['components'].get(name, {}).get('status') == 'files_installed']
    if not choice['repairs'] and not choice['plugins']:
        return None
    return public_selection(choice)


def _check_state(state, *, enforce_owner=True):
    for current in reversed((Path(state), *Path(state).parents)):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or (hasattr(current, 'is_junction') and current.is_junction()):
            raise RuntimeError('Unsafe model state directory')
        if enforce_owner and (info.st_uid != 0 or info.st_mode & 0o022):
            raise RuntimeError('Model state ancestors must be protected and root-owned')
    if Path(state).exists() and enforce_owner and stat.S_IMODE(Path(state).stat().st_mode) != 0o700:
        raise RuntimeError('Model state must have mode 0700')


def _managed(index):
    paths, components = {}, {}
    for slot, record in index['slots'].items():
        for path, entry in (record.get('current') or {}).get('paths', {}).items():
            paths[path] = dict(entry, slots=[slot])
            components.setdefault(entry['component'], {'slots': []})['slots'].append(slot)
    return {'paths': paths, 'components': components}


def _guard_bytes(partuuid):
    # Standalone boot guard has no dependency on retained caches or old runtime.
    return ('''"""Boiler model boot guard: active root partition only."""
import os, pathlib, subprocess, sys
env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','LC_ALL':'C'}
try:
    if os.geteuid()!=0: raise RuntimeError('root required')
    actual=subprocess.check_output(['/usr/bin/findmnt','-n','-o','PARTUUID','--target','/'],env=env,timeout=30,text=True).strip().lower()
    if actual!=%r: raise RuntimeError('different root partition')
    values=dict(line.split('=',1) for line in pathlib.Path('/etc/os-release').read_text().splitlines() if '=' in line)
    expected={'ID':'steamos','VERSION_ID':'3.10','BUILD_ID':'20260924.1000'}
    if any(values.get(key,'').strip('"')!=value for key,value in expected.items()): raise RuntimeError('different OS baseline')
    if os.uname().release!='7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9': raise RuntimeError('different kernel')
except Exception:
    sys.exit(1)
''' % partuuid.lower()).encode()


def _file_summary(files):
    return {path: {key: value for key, value in dict(spec, size=len(spec['data']),
            sha256=hashlib.sha256(spec['data']).hexdigest()).items() if key != 'data'}
            for path, spec in sorted(files.items())}


def _require_root():
    if os.name != 'posix' or os.geteuid() != 0:
        raise RuntimeError('On-device maintenance writes require Linux root')


def _hold_status(root, package):
    """Confirm the existing pinned policy; the Store never writes updater files."""
    spec = importlib.util.spec_from_file_location('_boiler_oxp3_hold', Path(package)/'payload/legacy/components/update_hold.py')
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        expected = module.files(root)
        for relative, entry in expected.items():
            path = Path(root)/relative
            if not path.is_file() or path.is_symlink() or path.read_bytes() != entry['data']:
                return False, 'The original acknowledged OXP3 OS-image update hold is not fully installed; live maintenance preserves updater policy and cannot install it.'
    except (OSError, ValueError, RuntimeError) as error:
        return False, 'Existing OXP3 update-hold verification failed: '+str(error)
    return True, ''


def _language_files(root, language, api):
    if language == 'system':
        return {}
    existing = api['components']._existing
    merge = api['components']._merge_group
    relative = 'home/deck/.config/plasma-localerc'
    data = merge(existing(root, relative), 'Translations', {'LANGUAGE': language})
    locale = language+'.UTF-8'
    data = merge(data, 'Formats', {'LANG': locale, 'LC_MESSAGES': locale})
    lines = (existing(root, 'etc/locale.conf') or b'').decode().splitlines()
    for key in ('LANG', 'LC_MESSAGES'):
        matches = [index for index, line in enumerate(lines) if line.split('=', 1)[0].strip() == key]
        if len(matches) > 1:
            raise RuntimeError('Ambiguous system locale setting')
        if matches:
            lines[matches[0]] = key+'='+locale
        else:
            lines.append(key+'='+locale)
    return {relative: dict(data=data, user=True, component='kde-language'),
            'etc/locale.conf': dict(data=('\n'.join(lines)+'\n').encode(), component='kde-language')}


def _legacy_conflicts(root, system, api, selected, *, enforce_owner):
    directory = Path(root)/'home/.oxp3-maintenance'
    if not directory.exists():
        return
    old = api['transactions'].Store(root, directory, uid=system['uid'], gid=system['gid'], enforce_owner=enforce_owner)
    _check_state(directory, enforce_owner=enforce_owner)
    if old._state_read('pending.json') is not None:
        raise RuntimeError('Recover the existing legacy maintainer transaction first')
    index = old._index()
    current = index['slots'].get(system['slot']['id'], {}).get('current') or {}
    if set(selected) & set(current.get('selected', [])):
        raise RuntimeError('Selected components remain owned by the existing legacy maintainer; restore them there before Boiler installation')
    return index


def prepare(root, request, *, package=PACKAGE, api=None, system=None, enforce_owner=True, inspection=False):
    """Strictly read-only plan, including file hashes and source originals."""
    package, root = Path(package), Path(root).absolute()
    choice, options, manifest, catalog = selection(request, package)
    api = api or modules(package)
    system = system or target(root, api)
    state = root/STATE
    _check_state(state, enforce_owner=enforce_owner)
    store = api['transactions'].Store(root, state, uid=system['uid'], gid=system['gid'], enforce_owner=enforce_owner)
    pending = store._state_read('pending.json')
    if pending is not None and not inspection:
        raise RuntimeError('Pending model file transaction requires explicit recovery through restore')
    index = store._index()
    current = index['slots'].get(system['slot']['id'], {}).get('current') or {}
    actual = {path: store._read(path, user=entry['user']) for path, entry in current.get('paths', {}).items()}
    edited = {path for path, entry in current.get('paths', {}).items() if actual[path] != entry['snapshot']}
    if edited and not inspection:
        raise RuntimeError('Model-managed files changed; preserve edits before maintenance: '+', '.join(sorted(edited)))
    selected = options['repairs']+options['plugins']
    adoption_selected = selected+(['kde-language'] if options['kde_language'] != 'system' else [])
    legacy_index = _legacy_conflicts(root, system, api, adoption_selected, enforce_owner=enforce_owner)
    # Store retains legacy_adopted through explicit restoration. Once accepted,
    # its protected originals remain authoritative, while the old installer
    # journal deliberately keeps its stale applied image unchanged. Reimporting
    # that stale image would reject a valid restore -> reinstall cycle.
    consumed = set(current.get('legacy_adopted', []))
    originals = api['adoption'].import_originals(root, system, [name for name in adoption_selected if name not in consumed]) if not pending else {}
    originals = {path: entry for path, entry in originals.items() if path not in current.get('paths', {})}
    api['adoption'].validate_current(root, originals, system)
    managed = _managed(index)
    for path, entry in originals.items():
        managed['paths'].setdefault(path, dict(component=entry['component']))
    system = dict(system)
    # Upstream probe mounts inactive roots read-only with log replay disabled;
    # no target ownership/files are changed. Failure remains a visible blocker.
    system['transactional_packages'] = False
    if request.get('scope') != 'active-root-and-shared-home':
        raise ValueError('Explicit active-root-and-shared-home scope is required')
    system['shared_compatible'] = api['probe'].verify_shared(system)
    system['shared_os_compatible'] = api['probe'].verify_shared_os(system)
    hold_ok, hold_reason = _hold_status(root, package)
    planned = api['components'].build_plan(package/'payload/legacy', root, selected, system, managed)
    rows = []
    for category in ('repairs', 'plugins'):
        for item in catalog[category]:
            identity = item['id']
            policy = api['compatibility'].evaluate(identity, system)
            reason = SPECIAL.get(identity) or ('Third-party Decky plugins are managed by Decky; this adapter does not replace their directories.' if identity in THIRD_PARTY else '')
            if not item.get('available', True):
                reason = item.get('unavailable_en') or 'Not available in the pinned model catalog.'
            if not policy['supported']:
                reason = reason or policy['reason']
            if identity not in planned['components'] and not reason:
                eligibility = api['components'].build_plan(package/'payload/legacy', root, [identity], system, managed)['components'][identity]
                if eligibility['status'] not in ('ready', 'not_applicable'):
                    reason = eligibility['reason']
            packaged = next(row for row in manifest['legacy_capabilities'] if row['id']=='oxp3:'+identity)
            row = dict(packaged, id='oxp3:'+identity, category=category, available=not bool(reason), reason=reason,
                       selected=identity in selected, dependencies=['oxp3:'+name for name in item['dependencies']])
            if identity in planned['components']:
                report = planned['components'][identity]
                row.update(status=report['status'], reason=reason or report['reason'])
                row['available'] = not reason and report['status'] in ('ready', 'not_applicable')
                if reason:
                    report.update(status='blocked', reason=reason, units=[])
            rows.append(row)
    files = {path: entry for path, entry in planned['files'].items()
             if entry['component'] not in SPECIAL and entry['component'] not in THIRD_PARTY}
    if options['kde_language'] != 'system':
        if system['shared_os_compatible']:
            files.update(_language_files(root, options['kde_language'], api))
            planned['selected'].append('kde-language')
        else:
            hold_ok = False
            hold_reason = 'Shared SteamOS compatibility is unverified for the selected desktop language.'
    # Pin service guards to this adapter's own identity; leave existing old
    # maintainer runtime, key, installer journals and service links untouched.
    units = sorted({unit for report in planned['components'].values() if report['status']=='ready' for unit in report.get('units', [])})
    for path, spec in files.items():
        if path.endswith('.service'):
            spec['data'] = spec['data'].replace(api['components'].GUARD,
                ('ExecCondition=+/usr/bin/python3 -I -B /'+GUARD).encode())
    if files:
        files[GUARD] = dict(data=_guard_bytes(system['slot']['partuuid']), mode=0o644, component='boiler-model-runtime')
    if units:
        if any(not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service', unit) for unit in units):
            raise RuntimeError('Invalid planned service identity')
        files[ENABLE] = dict(data=('[Unit]\nWants='+' '.join(units)+'\n').encode(), mode=0o644, component='boiler-model-runtime')
    # Slot-local files in another root are independent. Shared home/opt bytes
    # have one physical owner even when its Store generation belongs to an
    # inactive root, so the live adapter must not silently take them over.
    legacy_path_conflicts = []
    for owner_slot, record in (legacy_index or {}).get('slots', {}).items():
        generation = record.get('current') or {}
        for path, entry in generation.get('paths', {}).items():
            if path in files and (owner_slot == system['slot']['id'] or path.startswith(('home/', 'opt/'))):
                reason = 'Target remains owned by legacy maintainer slot '+owner_slot+' ('+entry['component']+'): '+path
                legacy_path_conflicts.append(dict(path=path, slot=owner_slot, component=entry['component'], reason=reason))
                selected_component = files[path]['component']
                if selected_component in planned['components']:
                    planned['components'][selected_component].update(status='blocked', reason=reason, units=[])
                for row in rows:
                    if row['id'] == 'oxp3:'+selected_component:
                        row.update(available=False, status='blocked', reason=reason)
    change = public_selection(current.get('release', {}).get('boiler_selection'))
    if current.get('paths') and change is not None and change != choice:
        replacement = True
    else:
        replacement = False
    body = dict(schema_version=1, kind='OXP3MaintenancePlan', selection=choice,
                package_manifest_sha256=hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest(),
                target=dict(root=str(root), **system['slot']), state_sha256=digest(index),
                files=_file_summary(files), originals_sha256=digest(originals),
                observed_sha256=digest(actual), capabilities=rows,
                components=planned['components'], selected=planned['selected'],
                can_apply=not pending and not edited and hold_ok and not legacy_path_conflicts and bool(selected) and not replacement and all(row['available'] for row in rows if row['selected']),
                replacement_requires_restore=replacement, activation='next-boot',
                os_image_update_hold='existing policy preserved; no updater policy is installed or removed by live maintenance',
                scope='active root partition and declared shared home/opt files; inactive root is not modified',
                shared_compatibility_verified=system['shared_compatible'],
                reasons=([] if hold_ok else [hold_reason])+[entry['reason'] for entry in legacy_path_conflicts],
                legacy_path_conflicts=legacy_path_conflicts,
                restore_groups=planned.get('restore_groups', []))
    body['plan_sha256'] = digest(body)
    return body, files, originals, store, system, api


def action_plan(action, root, *, package=PACKAGE, api=None, system=None, enforce_owner=True, recovery_requested=False):
    """Read-only restore/verify review remains available after interrupted apply."""
    api = api or modules(package)
    root = Path(root).absolute()
    system = system or target(root, api)
    _check_state(root/STATE, enforce_owner=enforce_owner)
    store = api['transactions'].Store(root, root/STATE, uid=system['uid'], gid=system['gid'], enforce_owner=enforce_owner)
    index, pending = store._index(), store._state_read('pending.json')
    slot = system['slot']['id']
    current = index['slots'].get(slot, {}).get('current') or {}
    if pending is not None and pending.get('slot') != slot:
        raise RuntimeError('Pending model transaction belongs to another root partition')
    # The generic root-private intent is durable before this subprocess starts.
    # A crash in that interval has no Store operation to recover. Only its
    # explicit recovery request may clear that outer barrier, without claiming
    # or touching an existing legacy installation.
    no_file_recovery = action == 'restore' and recovery_requested is True and not current.get('paths') and pending is None
    if no_file_recovery:
        body = dict(ok=True, kind='OXP3MaintenanceActionPlan', action=action,
                    target=dict(root=str(root), **system['slot']), state_sha256=digest(index),
                    pending_sha256=digest(None), observed_sha256=digest({}), executable=True,
                    recovery_requested=True, no_file_transaction=True, modified=[], warnings=[],
                    summary=dict(action='restore', recovery='no-owned-file-transaction cleanup',
                                 files=0, recovery_required=True, legacy_files='unchanged'))
        body['plan_sha256'] = digest(body)
        return body, store, system, api
    if not current.get('paths') and any((record.get('current') or {}).get('paths') for key, record in index['slots'].items() if key != slot):
        raise RuntimeError('The installed model generation belongs to another root partition')
    if action == 'restore' and not current.get('paths') and not pending and installer_selection(root, system, package, api, consumed=current.get('legacy_adopted', [])):
        raise RuntimeError('Legacy installer choices are not Boiler-owned; Apply their reviewed selection to adopt originals before Boiler Restore')
    observed = {path: store._read(path, user=entry['user']) for path, entry in current.get('paths', {}).items()}
    modified = {path for path, entry in current.get('paths', {}).items() if observed[path] != entry['snapshot']}
    if pending:
        for operation in pending['operations']:
            path = operation['path']
            observed[path] = store._read(path, user=operation['user'])
            # A pending restore may already have written an original image.
            # Store's durable before/after pair is authority during recovery;
            # comparing only the still-current generation would trap it.
            if observed[path] in (operation['before'], operation['after']):
                modified.discard(path)
            else:
                modified.add(path)
    modified = sorted(modified)
    body = dict(ok=True, kind='OXP3MaintenanceActionPlan', action=action, target=dict(root=str(root), **system['slot']),
                state_sha256=digest(index), pending_sha256=digest(pending), observed_sha256=digest(observed),
                executable=action=='verify' and not pending or action=='restore' and not modified,
                summary=dict(action=action, generation=current.get('id'), files=len(current.get('paths', {})),
                             recovery_required=pending is not None, activation='next-boot'),
                warnings=['Changed model files must be preserved before restore.'] if modified else [], modified=modified)
    body['plan_sha256'] = digest(body)
    return body, store, system, api


def execute(action, root, request, *, package=PACKAGE, api=None, system=None, enforce_owner=True):
    if action not in ('inspect', 'plan', 'apply', 'verify', 'restore'):
        raise ValueError('Unsupported maintenance action')
    if action == 'plan' and request.get('action') in ('verify', 'restore'):
        return action_plan(request['action'], root, package=package, api=api, system=system, enforce_owner=enforce_owner,
                           recovery_requested=request.get('recovery_requested') is True)[0]
    if action == 'inspect' and 'selection' not in request:
        # Catalog discovery needs no requested adaptation. Show the current
        # protected choice where present, otherwise the packaged default.
        api = api or modules(package)
        system = system or target(root, api)
        _check_state(Path(root)/STATE, enforce_owner=enforce_owner)
        store = api['transactions'].Store(root, Path(root)/STATE, uid=system['uid'], gid=system['gid'], enforce_owner=enforce_owner)
        current = store._index()['slots'].get(system['slot']['id'], {}).get('current') or {}
        pending = store._state_read('pending.json')
        future = (pending or {}).get('index_after', {}).get('slots', {}).get(system['slot']['id'], {}).get('current') or {}
        saved_choice = public_selection((current.get('release', {}).get('boiler_selection') if current.get('paths') else None) or future.get('release', {}).get('boiler_selection'))
        legacy_choice = installer_selection(root, system, package, api, consumed=current.get('legacy_adopted', [])) if not saved_choice and not pending else None
        choice = saved_choice or legacy_choice or read_json(Path(package)/'selection.json')
        report, *_ = prepare(root, dict(request, selection=choice), package=package, api=api, system=system, enforce_owner=enforce_owner, inspection=True)
        shown_choice = saved_choice or legacy_choice
        if shown_choice is not None:
            selected_ids = set(shown_choice['repairs']+shown_choice['plugins'])
            for row in report['capabilities']:
                row['selected'] = row['id'] in selected_ids
        return dict(ok=True, executable=bool(current.get('paths') or pending) or not bool(report['reasons']), capabilities=report['capabilities'],
                    current=shown_choice, current_source='boiler-model' if saved_choice else 'legacy-installer' if legacy_choice else None,
                    target=report['target'], reasons=report['reasons'])
    if action in ('inspect', 'plan', 'apply'):
        plan, files, originals, store, system, api = prepare(root, request, package=package, api=api, system=system, enforce_owner=enforce_owner)
        if action != 'apply':
            return dict(plan, ok=True, executable=plan['can_apply'],
                        summary=dict(selected=plan['selected'], file_count=len(plan['files']), activation=plan['activation']),
                        warnings=plan['reasons'])
        _require_root()
        if request.get('expected_plan_sha256') != plan['plan_sha256']:
            raise RuntimeError('Reviewed model plan changed; review it again')
        if not plan['can_apply']:
            raise RuntimeError('Model choices are blocked or require an explicit restore of the current selection')
        with store.lock():
            fresh, files, originals, _, _, _ = prepare(root, request, package=package, api=api, system=system, enforce_owner=enforce_owner)
            if fresh != plan:
                raise RuntimeError('Model state changed after review')
            release = dict(version=read_json(Path(package)/'manifest.json')['version'],
                           boiler_selection=plan['selection'], plan_sha256=plan['plan_sha256'])
            result = store.apply(files, release, plan['selected']+['boiler-model-runtime'], system['slot']['id'], adopt=originals)
        return dict(ok=True, status='files-installed', activation='next-boot', plan_sha256=plan['plan_sha256'], transaction=result)
    review, store, system, api = action_plan(action, root, package=package, api=api, system=system, enforce_owner=enforce_owner,
                                           recovery_requested=request.get('recovery_requested') is True)
    if request.get('expected_plan_sha256') is not None and request['expected_plan_sha256'] != review['plan_sha256']:
        raise RuntimeError('Reviewed model action became stale')
    root = Path(root).absolute()
    index = store._index()
    current = index['slots'].get(system['slot']['id'], {}).get('current')
    pending = store._state_read('pending.json')
    if action == 'verify':
        if pending is not None:
            raise RuntimeError('Pending model transaction requires explicit recovery')
        if not current or not current.get('paths'):
            return dict(ok=True, status='not-installed', verified=False, target=system['slot'])
        edited = review['modified']
        return dict(ok=True, status='verified' if not edited else 'modified', verified=not edited,
                    modified=sorted(edited), target=system['slot'], generation=current['id'],
                    selection=public_selection(current['release'].get('boiler_selection')), activation='next-boot-or-hardware-verification-required')
    _require_root()
    if review.get('no_file_transaction') is True:
        return dict(ok=True, status='not-managed', recovery='no-file-transaction-to-restore',
                    target=review['target'], legacy_files='unchanged')
    # A retained Store pending record safely rolls forward to its reviewed
    # generation, then explicit restoration uses the true originals. Never
    # drop ownership of edited paths (Store normally preserves/unmanages them).
    with store.lock():
        if not review['executable']:
            raise RuntimeError('Restore refused changed model files')
        recovery = store.recover(system['slot']['id']) if pending is not None else None
        index = store._index()
        current = index['slots'].get(system['slot']['id'], {}).get('current')
        if current:
            _, edited = store._live_checked(current)
            if edited:
                raise RuntimeError('Restore refused changed model files: '+', '.join(sorted(edited)))
        result = store.restore(system['slot']['id'])
    return dict(ok=True, status='restored', transaction=result, recovery=recovery, activation='next-boot')


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 3:
            raise ValueError('Usage: maintenance.sh ACTION ROOT REQUEST_JSON_PATH')
        action, root, request_path = args
        if os.environ.get('BOILER_TARGET_ROOT') != str(Path(root).absolute()):
            raise RuntimeError('Explicit authenticated target context is required')
        if action in ('apply', 'restore') and not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', os.environ.get('BOILER_TRANSACTION_ID', '')):
            raise RuntimeError('Explicit model transaction context is required')
        result = execute(action, root, read_json(request_path))
        print('BOILER_MODEL_RESULT='+json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except Exception as error:
        print('BOILER_MODEL_RESULT='+json.dumps(dict(ok=False, error=str(error)), ensure_ascii=False), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
