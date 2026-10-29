"""Plan an adjustable profile's missing firmware candidates, preserving the image.

Candidate coverage is deliberately distinct from physical GPU support. The
installed driver and native firmware inventory are bound to each run; neither
the baseline nor a successful file transaction establishes runtime coverage.
"""
import hashlib
import os
import re
from pathlib import Path

from payload_lock import checked, relative, regular
from wireless import existing_firmware, native_matches, native_path, validate_record_metadata


def selected(options):
    identity = options.get('graphics_profile')
    if identity is None:
        return None
    if not isinstance(identity, str) or not re.fullmatch(r'graphics:(?:amd|intel)-[a-z0-9-]{1,80}', identity):
        raise ValueError('Invalid graphics profile selection')
    from graphics_catalog import load_catalog
    if identity not in {profile['id'] for profile in load_catalog(Path(__file__).resolve().parents[1])['profiles']}:
        raise ValueError('Unknown graphics profile selection')
    return identity


def validate_metadata(bundle, profile=None):
    identity = profile
    if identity is None and isinstance(bundle, dict):
        identity = bundle.get('id')
    if not isinstance(identity, str) or not re.fullmatch(r'graphics:(?:amd|intel)-[a-z0-9-]{1,80}', identity):
        raise ValueError('Invalid graphics candidate identity')
    if (not isinstance(bundle, dict) or bundle.get('schema') != 1
            or bundle.get('kind') != 'GraphicsFirmwareBundle' or bundle.get('id') != identity
            or not isinstance(bundle.get('version'), str) or not bundle['version']
            or bundle.get('runtime') != 'not_validated' or bundle.get('status') != 'experimental-baseline'
            or bundle.get('delivery') != 'on-demand' or bundle.get('available') is not True):
        raise ValueError('Missing or invalid graphics candidate bundle: ' + identity)
    vendor = identity.split(':', 1)[1].split('-', 1)[0]
    if vendor not in ('amd', 'intel') or bundle.get('drivers') != (['amdgpu'] if vendor == 'amd' else ['xe']):
        raise ValueError('Graphics profile has an unreviewed driver')
    provenance = bundle.get('provenance', {})
    if (not isinstance(provenance, dict) or not re.fullmatch('[0-9a-f]{40}', str(provenance.get('revision', '')))
            or not isinstance(provenance.get('url'), str) or not provenance['url'].startswith('https://')
            or not isinstance(bundle.get('baseline'), dict) or not bundle['baseline']):
        raise ValueError('Graphics candidate baseline provenance is required')
    files = bundle.get('files')
    if not isinstance(files, list) or not files or len(files) > 4096:
        raise ValueError('Graphics profile requires an explicit bounded candidate inventory')
    records = {}
    prefixes = ('usr/lib/firmware/amdgpu/',) if vendor == 'amd' else ('usr/lib/firmware/xe/', 'usr/lib/firmware/i915/')
    for item in files:
        validate_record_metadata(item)
        target = relative(item['target']).as_posix()
        if (not target.startswith(prefixes) or re.search(r'[*?\[\\]', target)
                or re.search(r'\.ko(?:\.(?:xz|zst|gz))?$', target)
                or target.endswith(('.xz', '.zst', '.gz'))):
            raise ValueError('Graphics candidate must be an exact uncompressed firmware path')
        if target in records:
            raise ValueError('Duplicate graphics firmware destination')
        records[target] = item
    licenses = bundle.get('licenses')
    if not isinstance(licenses, list) or not licenses:
        raise ValueError('Graphics firmware redistribution notices are required')
    for item in licenses:
        validate_record_metadata(item)
        if not item['target'].startswith('licenses/'):
            raise ValueError('Invalid graphics license destination')
    return records


def _fingerprint(path):
    size = path.stat().st_size
    if size > 128 * 1024 * 1024:
        raise ValueError('Graphics inventory file exceeds inspection limit')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return dict(size=size, sha256=digest.hexdigest())


def module_inventory(root, drivers, probe=None):
    directory = Path(root) / 'usr/lib/modules'
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Target kernel modules are missing')
    modules = {}
    for path in sorted(directory.rglob('*')):
        if not re.fullmatch(r'(?:' + '|'.join(drivers) + r')\.ko(?:\.(?:xz|zst|gz))?', path.name):
            continue
        name = path.relative_to(root).as_posix()
        regular(root, name)
        modules[name] = _fingerprint(path)
        if probe is not None:
            # Optional test/diagnostic inspection never expands the candidate list.
            probe(path)
    if not modules:
        raise ValueError('Selected graphics profile has no existing target driver: ' + ','.join(drivers))
    return modules


def require_quiescent(root):
    """A fresh plan must never fingerprint a transaction awaiting recovery."""
    from maintenance.engine import Engine, MaintenanceError, _read
    engine = Engine(root, read_only=True)
    if os.path.lexists(engine.state_dir / 'adoption-pending.json'):
        raise MaintenanceError('Pending maintenance adoption requires explicit recovery and fresh graphics planning')
    for path in engine.journals.glob('*.json'):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise MaintenanceError('Unsafe maintenance journal during graphics planning')
        if _read(path).get('status') not in ('committed', 'restored', 'rolled_back'):
            raise MaintenanceError('Pending maintenance transaction requires explicit recovery and fresh graphics planning')
    engine._state()


def plan_required(root, manifest, profile, probe=None):
    if profile is None:
        return {}, {}
    root = Path(root)
    require_quiescent(root)
    bundle = manifest.get(profile)
    records = validate_metadata(bundle, profile)
    modules = module_inventory(root, bundle['drivers'], probe)
    required, preserved, native_inventory = {}, [], {}
    for target, record in records.items():
        # A broken parent alias or directory leaf must never be mistaken for a
        # missing candidate and silently replaced during materialization.
        for suffix in ('', '.xz', '.zst', '.gz'):
            candidate = target + suffix
            parts = relative(candidate).parts
            for index in range(1, len(parts)):
                lexical = root.joinpath(*parts[:index])
                resolved = native_path(root, '/'.join(parts[:index]))
                if lexical.is_symlink() and not resolved.is_dir():
                    raise ValueError('Graphics candidate has a dangling native directory alias: ' + candidate)
            resolved = native_path(root, candidate)
            if resolved.is_dir():
                raise ValueError('Graphics candidate destination is an existing directory: ' + candidate)
        native = [path.relative_to(root).as_posix() for suffix in ('', '.xz', '.zst', '.gz')
                  for path in native_matches(root, target + suffix)]
        if native:
            for name in native:
                if not existing_firmware(root, name):
                    raise ValueError('Graphics candidate has a dangling native firmware alias: ' + name)
                preserved.append(name)
                native_inventory[name] = dict(resolved=native_path(root, name).relative_to(root).as_posix(),
                                              **_fingerprint(native_path(root, name)))
        else:
            required[target] = record
    candidates = sorted(records)
    plan = dict(modules=sorted(modules), module_fingerprints=modules, drivers=bundle['drivers'],
                files=sorted(required), preserved=sorted(set(preserved)), preserved_fingerprints=native_inventory,
                shared_files=[], requires=[], requirements=[name.removeprefix('usr/lib/firmware/') for name in candidates],
                resolved_requirements=[name.removeprefix('usr/lib/firmware/') for name in candidates],
                unavailable_requirements=[], alternatives={}, coverage='runtime_dependent',
                runtime='not_validated', early_boot='not_validated', runtime_candidates=candidates,
                unavailable_candidates=[], firmware_scope='selected-profile-candidates',
                runtime_reason='Profile candidates require boot-time validation on the physical GPU',
                baseline=bundle['baseline'], payload_version=bundle['version'], provenance=bundle['provenance'])
    return required, {profile: plan}


def resolve(root, payload, manifest, profile, probe=None):
    records, plans = plan_required(root, manifest, profile, probe)
    return {target: checked(payload, item) for target, item in records.items()}, plans


def verify_inventory(root, plan):
    """Verify the native evidence separately from newly owned firmware files."""
    root = Path(root)
    if plan.get('coverage') != 'runtime_dependent' or plan.get('runtime') != 'not_validated':
        raise ValueError('Graphics physical GPU coverage must remain runtime dependent')
    if module_inventory(root, plan['drivers']) != plan.get('module_fingerprints'):
        raise ValueError('Graphics target driver inventory changed')
    observed = {}
    for name in plan.get('preserved', []):
        if not existing_firmware(root, name):
            raise ValueError('Preserved graphics firmware is missing')
        path = native_path(root, name)
        observed[name] = dict(resolved=path.relative_to(root).as_posix(), **_fingerprint(path))
    if observed != plan.get('preserved_fingerprints'):
        raise ValueError('Preserved graphics firmware inventory changed')
    return True


def boot_candidates(root, plan, writes):
    """Materialize every selected candidate for initramfs without replacing native files."""
    root = Path(root)
    verify_inventory(root, plan)
    candidates = {target: writes[target] for target in plan['files']}
    for name, fingerprint in plan['preserved_fingerprints'].items():
        path = native_path(root, name)
        with path.open('rb') as stream:
            data = stream.read(fingerprint['size'] + 1)
        if (len(data) != fingerprint['size'] or hashlib.sha256(data).hexdigest() != fingerprint['sha256']
                or path.relative_to(root).as_posix() != fingerprint['resolved']):
            raise ValueError('Preserved graphics firmware changed during early-boot preparation')
        # Keep the lexical firmware name (including its compression suffix),
        # while materializing native symlink content as an ordinary archive file.
        candidates[name] = data
    return candidates
