"""Fixed BE201 file lifecycle shared by installer and maintenance.

The ResourceSession caller owns the authoritative mounts, physical facts and
exclusive target lease. This handler never probes QEMU as hardware, reloads a
driver, starts a service, removes aliases, or executes pool-supplied code.
"""
import copy
import os

from maintenance.engine import MaintenanceError
from protocol.manifest import confined
from protocol.firmware_contract import validate_contract, policy_digest


def presence(root, target):
    result = []
    for suffix in ('', '.zst', '.xz'):
        parent, leaf = (target+suffix).rsplit('/', 1)
        path = confined(root, parent)/leaf
        if os.path.lexists(path):
            if not path.is_symlink() and not path.is_file():
                raise MaintenanceError('Firmware alias is not a file or leaf link')
            result.append(target+suffix)
    return result


def prepare(resource, manifest, engine):
    """Render slot-specific missing-only choices before any group/target write."""
    validate_contract(resource)
    value = copy.deepcopy(manifest)
    state = engine._state()
    planned = {r['target']: r for r in resource['install']['files']}
    files, preserved = [], []
    for row in manifest['files']:
        target = row['target']; owner = state['owners'].get(target)
        observed = presence(engine.root, target)
        if owner and owner['component'] != resource['id'] and not observed:
            raise MaintenanceError('Foreign-owned firmware is missing; inspect its recovery state first')
        if planned[target]['missing_only'] and (observed or owner):
            if not owner or owner['component'] != resource['id']:
                preserved.append(dict(target=target, observed=observed))
            continue
        if not planned[target]['missing_only']:
            parent, leaf = target.rsplit('/', 1)
            if (confined(engine.root, parent)/leaf).is_symlink() or any(n != target for n in observed):
                raise MaintenanceError('Replacement c106 alias requires explicit inspection')
        files.append(row)
    value.update(files=files, firmware_preserved=preserved, firmware_policy_sha256=policy_digest(resource))
    if resource['id'] in state['components']:
        value['retain_owned_paths'] = True
        engine.retained_owned(value, state)
    return value


def verification(resource, manifest, journal, root):
    """Bind recorded slot decisions to this exact policy; Engine checks writes."""
    validate_contract(resource)
    installed = copy.deepcopy(journal['component'])
    installed.pop('resource_group', None)
    digest = installed.pop('firmware_policy_sha256', None)
    preserved = installed.pop('firmware_preserved', None)
    retained = installed.pop('retain_owned_paths', False)
    declared = installed.pop('files', None)
    skeleton = copy.deepcopy(manifest); wanted = {r['target']: r for r in skeleton.pop('files')}
    bad = dict(ok=False, reason='firmware-policy-or-manifest-mismatch')
    if installed != skeleton or digest != policy_digest(resource) or type(retained) is not bool:
        return bad
    if not isinstance(declared, list) or not isinstance(preserved, list): return bad
    targets = [r.get('target') for r in declared]
    if len(set(targets)) != len(targets) or any(wanted.get(r.get('target')) != r for r in declared): return bad
    missing_only = {r['target'] for r in resource['install']['files'] if r['missing_only']}
    kept = []
    for row in preserved:
        if set(row) != {'target', 'observed'} or row['target'] not in missing_only: return bad
        observed = row['observed']; target = row['target']
        if not isinstance(observed, list) or not observed or len(set(observed)) != len(observed) or not set(observed) <= {target, target+'.xz', target+'.zst'}: return bad
        kept.append(target)
    if len(set(kept)) != len(kept) or set(kept) & set(targets): return bad
    operations = [row['target'] for row in journal['operations']]
    if len(set(operations)) != len(operations) or set(operations) != set(wanted)-set(kept): return bad
    missing = [target for target in kept if not presence(root, target)]
    return dict(ok=not missing, preserved_missing=missing)
