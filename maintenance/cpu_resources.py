"""Bounded mutable-config support for the fixed, inactive CPU staging handler."""
from protocol.cpu_contract import CONFIG, mutable_target
from pathlib import Path
from protocol.manifest import confined

_KEY = object()


class _CpuPermit:
    def __init__(self, key, engine, pool, slots, slot, recovery):
        if key is not _KEY: raise ValueError('Fixed host CPU authorization is required')
        self.root, self.pool, self.slots, self.slot, self.recovery = engine.root, pool, slots, slot, recovery


def fixed_component(component):
    from protocol.cpu_contract import IDENTITY, TARGETS
    if component.get('id') != IDENTITY or mutable_target(component) != CONFIG or component.get('hooks'):
        raise ValueError('Only the fixed public CPU file manifest is supported')
    files = component.get('files', [])
    if len(files) != 8 or {r['target'] for r in files} != set(TARGETS): raise ValueError('CPU component target set differs')
    for row in files:
        if row['sha256'] != TARGETS[row['target']] or row['mode'] != (0o600 if row['target'] == CONFIG else 0o644):
            raise ValueError('CPU component payload/mode differs from public baseline')


def authorize(engine, pool, slots, slot, *, recovery):
    from maintenance.resource_runtime import guard_cpu
    from protocol.cpu_contract import IDENTITY
    if engine.target_domain != 'root' or slot not in slots or Path(slots[slot]['root']).resolve() != engine.root:
        raise ValueError('CPU host authorization target differs')
    guard_cpu(pool, slots, {IDENTITY}, recovery=recovery)
    return _CpuPermit(_KEY, engine, pool, slots, slot, recovery)


def permitted(engine, permit, component=None, *, recovery):
    if type(permit) is not _CpuPermit or permit.root != engine.root or (not recovery and permit.recovery): return False
    authorize(engine, permit.pool, permit.slots, permit.slot, recovery=recovery)
    if component is not None: fixed_component(component)
    return True


def quiescent_layout(root):
    root = Path(root).absolute()
    if root == Path(root.anchor): raise ValueError('CPU staging requires an offline mounted target root')
    for name in ('etc/systemd/system', 'usr/lib/systemd/system', 'run/systemd/system'):
        directory = confined(root, name)
        if not directory.exists(): continue
        for child in directory.iterdir():
            if not child.name.endswith(('.wants', '.requires')): continue
            current = confined(root, name+'/'+child.name)
            if not current.is_dir(): raise ValueError('Unexpected CPU service dependency directory')
            target = current/'oxp3-cpu.service'
            if target.exists() or target.is_symlink():
                raise ValueError('CPU service is enabled by an existing dependency; staging refused')


def configuration_plan(engine, component):
    from maintenance.adoption_blobs import observe
    from maintenance.engine import MaintenanceError
    from protocol.manifest import load_json
    if mutable_target(component) != CONFIG: raise ValueError('A fixed CPU config manifest is required')
    for path in engine.journals.glob('*.json'):
        if load_json(path).get('status') not in ('committed', 'restored', 'rolled_back'):
            raise MaintenanceError('CPU config planning requires writable transaction recovery first')
    current = observe(engine, engine._target(CONFIG)); owner = engine._state()['owners'].get(CONFIG)
    action = 'preserve-existing' if current['exists'] else 'preserve-user-removal' if owner else 'create-disabled-private-default'
    return dict(target=CONFIG, action=action, current=current,
        resulting_mode=current.get('mode', 0o600), resulting_uid=current.get('uid', 0), resulting_gid=current.get('gid', 0))


def mutable_operation(component, item):
    marker = item.get('cpu_mutable_config')
    if marker is None: return False
    if marker is not True or mutable_target(component) != CONFIG or item['target'] != CONFIG:
        raise ValueError('Invalid fixed CPU mutable-config operation')
    return True


def matches(engine, component, item):
    from maintenance.adoption_blobs import observe
    from maintenance.engine import _same
    if mutable_operation(component, item):
        # A regular, safely owned config may be edited or removed by its user.
        # observe still rejects links, untrusted ownership and writable ancestry.
        observe(engine, engine._target(item['target']))
        return True
    current = engine._observed(engine._target(item['target']), item['after'])
    return _same(current, item['after'])


def preserve_for_restore(engine, component, item):
    from maintenance.adoption_blobs import capture, observe
    from maintenance.engine import _same
    if not mutable_operation(component, item): return item
    current = observe(engine, engine._target(CONFIG))
    if item['before'].get('exists') or item.get('retained') or not _same(current, item['after']):
        snapshot = capture(engine, engine._target(CONFIG), current)
        return dict(item, before=snapshot, after=dict(snapshot), retained=True, mutable_preserved=True)
    # Delete a newly created default only while bytes/mode/owner still match.
    return item
