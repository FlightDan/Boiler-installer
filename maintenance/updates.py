"""Signed, reviewed updates for already-owned manifest file components.

Discovery and planning never change target state. Execution rechecks the signed
snapshot, immutable package digest, real target environment and private state.
Script packages require an explicit engine-hooks-v1 maintenance contract. Only
the signed, unchanged reviewed plan can authorize Engine hooks, using a private
authenticated snapshot. There is no unsigned CLI hook execution switch.
"""
from __future__ import annotations

import hashlib
import contextlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlparse

from protocol import compatible, resolve_plan, validate_package, validate_manifest
from protocol.fetch import _cache_lock, cached_index, fetch_index, fetch_package
from protocol.manifest import confined, load_json, relative_path, _decode_json, MAX_MANIFEST
from .engine import Engine, MaintenanceError, _json_write, _read


class UpdateError(MaintenanceError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _guest_path(root, relative):
    """Resolve target-internal symlinks as guest paths, never host absolute paths."""
    relative_path(relative)
    queue, resolved, links = relative.split('/'), [], 0
    while queue:
        part = queue.pop(0)
        if part in ('', '.'):
            continue
        if part == '..':
            if not resolved:
                raise UpdateError('Target metadata link escapes the mounted root')
            resolved.pop()
            continue
        if '\\' in part or ':' in part:
            raise UpdateError('Unsafe target metadata link')
        path = root.joinpath(*resolved, part)
        if path.is_symlink():
            links += 1
            if links > 16:
                raise UpdateError('Target metadata link cycle')
            target = os.readlink(path)
            if target.startswith('/'):
                resolved = []
            queue = target.split('/') + queue
        else:
            resolved.append(part)
    return confined(root, '/'.join(resolved))


def target_environment(root, *, device_id=None):
    root = Path(root).resolve()
    release_path = _guest_path(root, 'etc/os-release')
    if not release_path.is_file() or release_path.stat().st_size > 65536:
        raise UpdateError('Target OS release metadata is missing or oversized')
    release = {}
    for line in release_path.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if not re.fullmatch(r'[A-Z_]+', key) or key in release:
            raise UpdateError('Invalid target OS release metadata')
        release[key] = value.strip().strip('"').strip("'")
    if not release.get('ID') or not release.get('VERSION_ID'):
        raise UpdateError('Target OS identity is incomplete')
    architecture = None
    for name in ('usr/bin/env', 'usr/bin/bash', 'bin/sh'):
        try:
            path = _guest_path(root, name)
            with path.open('rb') as stream:
                header = stream.read(20)
        except (FileNotFoundError, NotADirectoryError):
            continue
        if len(header) >= 20 and header[:4] == b'\x7fELF' and header[5] in (1, 2):
            machine = int.from_bytes(header[18:20], 'little' if header[5] == 1 else 'big')
            architecture = {62: 'x86_64', 183: 'aarch64', 3: 'i686'}.get(machine)
            if architecture:
                break
    if architecture is None:
        raise UpdateError('Target architecture could not be established from its ELF binaries')
    modules = _guest_path(root, 'usr/lib/modules')
    kernels = []
    if modules.exists():
        if not modules.is_dir():
            raise UpdateError('Target kernel directory is invalid')
        for item in modules.iterdir():
            if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
                raise UpdateError('Target kernel directory contains a redirected entry')
            if item.is_dir():
                kernels.append(item.name)
    environment = {'architecture': architecture, 'os_id': release['ID'], 'os_version': release['VERSION_ID'],
                   'os_build': release.get('BUILD_ID'), 'kernels': sorted(kernels)}
    if device_id is not None:
        if not isinstance(device_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_.-]*', device_id):
            raise UpdateError('Invalid explicitly selected device identity')
        environment['device_id'] = device_id
    return environment


def _provenance(engine):
    path = confined(engine.state_dir, 'updates.json')
    if not path.exists():
        return {'schema_version': 1, 'packages': {}, 'registries': {}}
    value = _read(path)
    if value.get('schema_version') != 1 or not isinstance(value.get('packages'), dict) or not isinstance(value.get('registries'), dict):
        raise UpdateError('Unsupported updater provenance state')
    return value


def inventory(root, state_dir=None, *, device_id=None):
    """Read installed identities and live verification without mkdir, lock or recovery."""
    engine = Engine(root, state_dir, read_only=True)
    state = engine._state()
    pending = []
    for path in sorted(engine.journals.glob('*.json')):
        if path.is_symlink():
            raise UpdateError('Unsafe maintenance journal')
        if _read(path).get('status') not in ('committed', 'restored', 'rolled_back'):
            pending.append(path.stem)
    components = {}
    for identity, item in sorted(state['components'].items()):
        status = {'ok': False, 'modified': [], 'pending_recovery': bool(pending)}
        if not pending:
            status = engine.verify_readonly(identity)
        components[identity] = {'version': item['version'], 'transaction': item['transaction'],
                                'verified': status['ok'], 'modified': status.get('modified', [])}
    provenance = _provenance(engine)
    info = engine.root.stat()
    return {'schema_version': 1, 'kind': 'InstalledInventory', 'target': {'root': str(engine.root), 'device': info.st_dev, 'inode': info.st_ino},
            'environment': target_environment(engine.root, device_id=device_id), 'components': components,
            'pending_transactions': pending, 'state_sha256': _digest(state), 'provenance_sha256': _digest(provenance),
            'packages': provenance['packages']}


def _signed_snapshot(cache, public_key, openssl):
    cache = Path(cache)
    if not cache.is_dir():
        raise UpdateError('A verified registry cache is required')
    with _cache_lock(cache):
        index = cached_index(cache, public_key=public_key, openssl=openssl)
        pointer = load_json(confined(cache, 'current.json'))
        key = Path(public_key) if public_key else Path(__file__).resolve().parents[1]/'protocol/trusted-public-key.pem'
        return index, {'id': index['id'], 'version': index['version'], 'sequence': index['sequence'],
                       'sha256': pointer['sha256'], 'trusted_public_key_sha256': _file_digest(key)}


def _check_registry(provenance, anchor):
    previous = provenance['registries'].get(anchor['id'])
    if previous and (previous['trusted_public_key_sha256'] != anchor['trusted_public_key_sha256'] or
                     anchor['sequence'] < previous['sequence'] or
                     (anchor['sequence'] == previous['sequence'] and anchor['sha256'] != previous['sha256'])):
        raise UpdateError('Registry rollback, sequence reuse or implicit signing-key replacement refused')


def discover(root, index_cache, state_dir=None, *, identity=None, device_id=None, public_key=None, openssl='openssl'):
    current = inventory(root, state_dir, device_id=device_id)
    index, anchor = _signed_snapshot(index_cache, public_key, openssl)
    engine = Engine(root, state_dir, read_only=True)
    _check_registry(_provenance(engine), anchor)
    candidates = []
    for entry in index['packages']:
        if identity and entry['id'] != identity:
            continue
        candidate = {key: entry[key] for key in ('id', 'version', 'source', 'sha256', 'size')}
        candidate['compatible'] = compatible(entry, current['environment'])
        candidate['revoked'] = entry['sha256'] in index.get('revocations', [])
        candidate['installed_version'] = current['packages'].get(entry['id'], {}).get('version')
        candidates.append(candidate)
    return {'schema_version': 1, 'kind': 'UpdateDiscovery', 'index': anchor, 'environment': current['environment'],
            'candidates': candidates, 'scope': 'signed cached discovery; package compatibility and ownership require a reviewed plan'}


def refresh_registry(url, signature_url, cache, *, public_key=None, openssl='openssl'):
    """Explicit network discovery uses the protocol's atomic, locked index cache."""
    return fetch_index(url, signature_url, cache, public_key=public_key, openssl=openssl)


def acquire_package(index_cache, identity, version, destination, *, public_key=None, openssl='openssl'):
    return fetch_package(index_cache, identity, version, destination, public_key=public_key, openssl=openssl)


def _version_key(version):
    match = re.fullmatch(r'(\d+(?:\.\d+){1,3})(?:-([A-Za-z0-9.-]+))?(?:\+([A-Za-z0-9.-]+))?', version)
    if not match:
        raise UpdateError('Unsupported installed or candidate version')
    numbers = tuple(map(int, match[1].split('.')))
    numbers += (0,)*(4-len(numbers))
    pre = tuple((0, int(part)) if part.isdigit() else (1, part) for part in (match[2] or '').split('.'))
    revision = ()
    if match[3] and re.fullmatch(r'maintenance\.\d+', match[3]):
        revision = (int(match[3].split('.')[1]),)
    return numbers, 1 if match[2] is None else 0, pre if match[2] else (), revision


def _source_identity(source):
    if source['kind'] == 'github':
        return 'github:'+source['repository'].lower()
    if source['kind'] != 'https':
        raise UpdateError('Normal updates cannot use a local developer source')
    parsed = urlparse(source['url'])
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise UpdateError('Update source must be a fixed credential-free HTTPS URL')
    parts = parsed.path.strip('/').split('/')
    if parsed.hostname.lower() == 'github.com' and len(parts) >= 2:
        return 'github:'+'/'.join(parts[:2]).lower()
    return 'https://'+parsed.netloc.lower()+parsed.path.rsplit('/', 1)[0]


def _unchanged_component(engine, state, component):
    current = state['components'][component['id']]
    journal = _read(engine._journal(current['transaction']))
    old = {item['target']: item['after'] for item in journal['operations']}
    new = {item['target']: item for item in component.get('files', [])}
    return (old.keys() == new.keys() and journal['component'].get('hooks', {}) == component.get('hooks', {}) and
            all(old[path].get('sha256') == entry['sha256'] and old[path].get('mode') == entry.get('mode', 420) for path, entry in new.items()))


def _maintenance_entry(package):
    entry = package.get('maintenance_entry')
    if entry is None:
        return None
    if isinstance(entry, dict):
        if entry != {'kind': 'engine-hooks-v1'}:
            raise UpdateError('Unsupported structured model maintenance entry')
        return entry
    if not isinstance(entry, str) or not entry.startswith('scripts/') or not entry.endswith('.sh'):
        raise UpdateError('Invalid declared model maintenance entry')
    relative_path(entry)
    if entry not in {item['path'] for item in package['files']}:
        raise UpdateError('Model maintenance entry is absent from authenticated inventory')
    return entry


@contextlib.contextmanager
def _regular_stream(path):
    """Do not follow a swapped link or wait for a FIFO in a writable cache."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) |
                         getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise UpdateError('Authenticated package source must be a regular file')
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            yield stream
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _protected_staging_path(path):
    if os.name == 'nt':
        return
    path = Path(path).absolute()
    # Check top down: once a directory is validated, an unprivileged user
    # cannot replace its protected child while subsequent paths are inspected.
    for current in reversed([path, *path.parents]):
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()):
            raise UpdateError('Authenticated staging path has an untrusted ancestor')
        if stat.S_IMODE(info.st_mode) & 0o022:
            # /tmp is safe only because root-owned sticky parents prevent a
            # user from renaming somebody else's root/euid-owned child.
            if current == path or info.st_uid != 0 or not info.st_mode & stat.S_ISVTX:
                raise UpdateError('Authenticated staging path has a writable ancestor')


@contextlib.contextmanager
def _authenticated_payload(package_dir, manifest_sha256, staging_parent):
    """Detach execution from a caller-writable cache, copying only signed inventory.

    The temporary parent is mode 0700 on Linux. Every copied byte is bounded and
    verified against the anchored manifest before Engine can open a hook. Source
    files changing during the copy either fail verification or produce the exact
    signed bytes; arbitrary cache additions are never copied.
    """
    source = Path(package_dir).resolve()
    with _regular_stream(confined(source, 'manifest.json')) as incoming:
        manifest_bytes = incoming.read(MAX_MANIFEST+1)
    if len(manifest_bytes) > MAX_MANIFEST or hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
        raise UpdateError('Authenticated package manifest changed before snapshot')
    package = validate_manifest(_decode_json(manifest_bytes), 'DevicePackage')
    parent = Path(staging_parent)
    _protected_staging_path(parent)
    with tempfile.TemporaryDirectory(prefix='.authenticated-update-', dir=parent) as temporary:
        snapshot = Path(temporary)
        (snapshot/'manifest.json').write_bytes(manifest_bytes)
        for entry in package['files']:
            target = confined(snapshot, entry['path'])
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            digest, written = hashlib.sha256(), 0
            with _regular_stream(confined(source, entry['path'])) as incoming, target.open('xb') as outgoing:
                while True:
                    block = incoming.read(min(1024*1024, entry['size']-written+1))
                    if not block:
                        break
                    written += len(block)
                    if written > entry['size']:
                        raise UpdateError('Authenticated package file grew while staging')
                    digest.update(block)
                    outgoing.write(block)
            if written != entry['size'] or digest.hexdigest() != entry['sha256']:
                raise UpdateError('Authenticated package file changed while staging')
        validate_package(snapshot, expected_manifest_sha256=manifest_sha256)
        yield snapshot


def _execution_context(root, index_cache, state_dir, plan, public_key, openssl, payload):
    """Recheck revocation/source identity, actual target metadata and snapshot bytes."""
    _protected_staging_path(payload)
    if target_environment(root, device_id=plan['environment'].get('device_id')) != plan['environment']:
        raise UpdateError('Actual target environment changed during the reviewed update')
    _, anchor = _signed_snapshot(index_cache, public_key, openssl)
    if anchor != plan['index']:
        raise UpdateError('Signed registry changed during the reviewed update; review again')
    validate_package(payload, expected_manifest_sha256=plan['package']['manifest_sha256'])
    if _digest(_provenance(Engine(root, state_dir, read_only=True))) != plan['provenance_sha256']:
        raise UpdateError('Updater provenance changed during the reviewed update')


def plan_update(root, index_cache, package_dir, identity, version, selected=None, state_dir=None,
                *, device_id=None, public_key=None, openssl='openssl'):
    current = inventory(root, state_dir, device_id=device_id)
    if current['pending_transactions']:
        raise UpdateError('Recover or inspect pending maintenance transactions before planning an update')
    engine = Engine(root, state_dir, read_only=True)
    state, provenance = engine._state(), _provenance(engine)
    index, anchor = _signed_snapshot(index_cache, public_key, openssl)
    _check_registry(provenance, anchor)
    entry = next((item for item in index['packages'] if item['id'] == identity and item['version'] == version), None)
    if entry is None or entry['sha256'] in index.get('revocations', []):
        raise UpdateError('Package is absent from the signed snapshot or revoked')
    source_identity = _source_identity(entry['source'])
    package = validate_package(package_dir, expected_manifest_sha256=entry['sha256'])
    if (package['id'], package['version']) != (identity, version) or (Path(package_dir)/'manifest.json').stat().st_size != entry['size']:
        raise UpdateError('Package identity or size differs from signed discovery')
    if package.get('trust', {}).get('status') == 'unsigned-development' or package['source']['kind'] == 'local':
        raise UpdateError('Developer snapshots cannot become normal installed updates')
    declared_source_identity = _source_identity(package['source'])
    if not compatible(entry, current['environment']) or not compatible(package, current['environment']):
        raise UpdateError('Package is incompatible with the actual target OS, architecture or kernels')
    prior = provenance['packages'].get(identity)
    if prior and (prior['source_identity'] != source_identity or _version_key(version) < _version_key(prior['version'])):
        raise UpdateError('Package downgrade or implicit source repository change refused')
    if prior and _source_identity(prior['declared_source']) != declared_source_identity:
        raise UpdateError('Implicit declared source repository change refused')
    if selected is None:
        selected = sorted(item['id'] for item in package['components'] if item['id'] in state['components'])
    if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
        raise UpdateError('Select at least one uniquely identified installed component')
    available = list(package['components'])
    package_ids = {item['id'] for item in available}
    for cid, installed in state['components'].items():
        if cid not in package_ids:
            available.append(_read(engine._journal(installed['transaction']))['component'])
    if set(selected) - package_ids:
        raise UpdateError('Selected update component is not declared by the authenticated package')
    resolved = resolve_plan(available, selected, current['environment'])
    actions = []
    for component in resolved['components']:
        cid = component['id']
        if cid not in state['components']:
            raise UpdateError('Unowned component requires explicit installation or adoption: '+cid)
        if not current['components'][cid]['verified']:
            raise UpdateError('Installed component changed; preserve edits before upgrade: '+cid)
        old_version = state['components'][cid]['version']
        old_key, new_key = _version_key(old_version), _version_key(component['version'])
        if new_key < old_key:
            raise UpdateError('Component downgrade refused: '+cid)
        same = _unchanged_component(engine, state, component)
        if new_key == old_key and component.get('hooks') and cid in package_ids:
            # Old journals record hook paths, not the old script bytes. A path
            # match cannot prove that a same-version hook is unchanged.
            raise UpdateError('Equal-precedence scripted component requires a new version: '+cid)
        if new_key == old_key and not same:
            raise UpdateError('Equal-precedence component version changes bytes or scripts: '+cid)
        old_paths = {name for name, owner in state['owners'].items() if owner['component'] == cid}
        new_paths = {item['target'] for item in component.get('files', [])}
        if old_paths - new_paths:
            raise UpdateError('Update drops managed paths; restore explicitly first: '+cid)
        for item in component.get('files', []):
            path = engine._target(item['target'])
            # Ordinary component upgrades are not a signing-key rotation
            # protocol. Preserve installed anchors, including the legacy
            # maintainer's versioned public key, byte for byte.
            if Path(item['target']).name == 'trusted-public-key.pem':
                if path.exists() and _file_digest(path) != item['sha256']:
                    raise UpdateError('Ordinary updates cannot replace an installed signing key')
                if item['target'] == 'usr/local/lib/boiler/protocol/trusted-public-key.pem' and item['sha256'] != anchor['trusted_public_key_sha256']:
                    raise UpdateError('Canonical runtime key must match the verified registry trust anchor')
            owner = state['owners'].get(item['target'])
            folded = item['target'].casefold()
            for owned, installed_owner in state['owners'].items():
                other = owned.casefold()
                if installed_owner['component'] != cid and (folded == other or folded.startswith(other+'/') or other.startswith(folded+'/')):
                    raise UpdateError('Update target overlaps another installed component')
            if owner and owner['component'] != cid:
                raise UpdateError('Update target belongs to another component')
            if owner is None and (path.exists() or path.is_symlink()):
                raise UpdateError('Refusing implicit adoption of an existing target: '+item['target'])
        for conflict in component.get('conflicts', []):
            if conflict in state['components']:
                raise UpdateError('Installed component conflict: '+conflict)
        actions.append({'id': cid, 'from_version': old_version, 'to_version': component['version'],
                        'action': 'unchanged' if same and new_key == old_key else 'upgrade', 'scripted': bool(component.get('hooks'))})
    scripted = any(item['scripted'] and item['action'] == 'upgrade' and item['id'] in package_ids for item in actions)
    maintenance_entry = _maintenance_entry(package)
    execution = 'engine-hooks-v1' if scripted and maintenance_entry == {'kind': 'engine-hooks-v1'} else 'files-v1' if not scripted else 'unsupported-model-entry'
    plan = {'schema_version': 1, 'kind': 'ReviewedUpdatePlan', 'target': current['target'], 'environment': current['environment'],
            'state_sha256': current['state_sha256'], 'provenance_sha256': current['provenance_sha256'], 'index': anchor,
            'package': {'id': identity, 'version': version, 'manifest_sha256': entry['sha256'], 'manifest_size': entry['size'],
                        'source': entry['source'], 'source_identity': source_identity, 'declared_source': package['source'], 'component_ids': sorted(package_ids)},
            'selected': selected, 'components': resolved['components'], 'actions': actions, 'scripted': scripted,
            'maintenance_entry': maintenance_entry, 'execution': execution, 'executable': execution != 'unsupported-model-entry',
            'scope': 'already-owned components; authenticated Engine apply/verify hooks only when declared; per-component transactions, no automatic rollback of a multi-component batch or arbitrary hook effects'}
    plan['review_sha256'] = _digest(plan)
    return plan


def execute_plan(root, reviewed_plan, index_cache, package_dir, *, reviewed_sha256, state_dir=None, public_key=None, openssl='openssl'):
    plan = load_json(reviewed_plan) if isinstance(reviewed_plan, (str, Path)) else reviewed_plan
    if not isinstance(plan, dict):
        raise UpdateError('Reviewed update plan is required')
    claimed = plan.get('review_sha256')
    body = {key: value for key, value in plan.items() if key != 'review_sha256'}
    if not re.fullmatch(r'[0-9a-f]{64}', reviewed_sha256 or '') or reviewed_sha256 != claimed or _digest(body) != reviewed_sha256:
        raise UpdateError('Explicit reviewed plan hash does not match')
    if not plan.get('executable') or (plan.get('scripted') and
            (plan.get('execution') != 'engine-hooks-v1' or plan.get('maintenance_entry') != {'kind': 'engine-hooks-v1'})):
        raise UpdateError('Unsupported model maintenance entry; scripted updates require the explicit engine-hooks-v1 contract')
    engine = Engine(root, state_dir)
    cache_path = Path(index_cache).resolve()
    if cache_path == engine.state_dir.resolve() or cache_path in engine.state_dir.resolve().parents or engine.state_dir.resolve() in cache_path.parents:
        raise UpdateError('Registry cache and target maintenance state must use separate directories')
    _protected_staging_path(engine.state_dir)
    with _cache_lock(engine.state_dir), _authenticated_payload(package_dir, plan['package']['manifest_sha256'], engine.state_dir) as payload:
        fresh = plan_update(root, index_cache, payload, plan['package']['id'], plan['package']['version'], plan['selected'], state_dir,
                            device_id=plan['environment'].get('device_id'), public_key=public_key, openssl=openssl)
        if fresh != plan:
            raise UpdateError('Reviewed update plan became stale; review a fresh plan')
        receipt_dir = engine.state_dir/'updates'
        receipt_dir.mkdir(exist_ok=True)
        receipt_path = confined(receipt_dir, reviewed_sha256+'.json')
        if receipt_path.exists():
            previous = _read(receipt_path)
            if previous.get('status') != 'failed-preserved' or previous.get('transactions'):
                raise UpdateError('This reviewed batch already has a receipt; inspect it instead of replaying')
            # A new explicit execution may retry only after the freshly checked
            # target/registry/package/plan is identical and no commit occurred.
            # Preserve every failed attempt rather than replacing its receipt.
            attempt = 2
            while (receipt_dir/(reviewed_sha256+'-retry-'+str(attempt)+'.json')).exists():
                attempt += 1
            receipt_path = confined(receipt_dir, reviewed_sha256+'-retry-'+str(attempt)+'.json')
        receipt = {'schema_version': 1, 'kind': 'UpdateExecutionReceipt', 'plan_sha256': reviewed_sha256,
                   'status': 'applying', 'package': plan['package'], 'index': plan['index'], 'transactions': []}
        _json_write(receipt_path, receipt)
        expected_state = plan['state_sha256']
        try:
            for component, action in zip(plan['components'], plan['actions']):
                if action['action'] == 'unchanged':
                    continue
                # Engine checks this digest while holding its own transaction
                # lock, preventing concurrent mutation from causing downgrade.
                _execution_context(root, index_cache, state_dir, plan, public_key, openssl, payload)
                authorized_hooks = bool(component.get('hooks')) and plan['execution'] == 'engine-hooks-v1' and component['id'] in plan['package']['component_ids']
                if component.get('hooks') and not authorized_hooks:
                    raise UpdateError('Hook component is outside the authenticated reviewed package')
                result = engine.apply(component, payload, allow_hooks=authorized_hooks,
                                      environment=plan['environment'], expected_state_sha256=expected_state)
                expected_state = result['state_sha256']
                receipt['transactions'].append(result)
                _json_write(receipt_path, receipt)
            _execution_context(root, index_cache, state_dir, plan, public_key, openssl, payload)
            with engine._lock():
                if _digest(engine._state()) != expected_state:
                    raise UpdateError('Maintenance ownership changed before update provenance commit')
                provenance = _provenance(engine)
                if _digest(provenance) != plan['provenance_sha256']:
                    raise UpdateError('Updater provenance changed before commit')
                provenance['registries'][plan['index']['id']] = plan['index']
                provenance['packages'][plan['package']['id']] = dict(plan['package'], index=plan['index'],
                                                                   components=[item['id'] for item in plan['components'] if item['id'] in plan['package']['component_ids']])
                _json_write(engine.state_dir/'updates.json', provenance)
                receipt['status'] = 'committed'
                _json_write(receipt_path, receipt)
        except BaseException as error:
            receipt['status'] = 'failed-preserved'
            receipt['error'] = type(error).__name__
            _json_write(receipt_path, receipt)
            raise
        return {'status': 'committed', 'plan_sha256': reviewed_sha256, 'transactions': receipt['transactions'],
                'receipt': str(receipt_path), 'scope': plan['scope']}
