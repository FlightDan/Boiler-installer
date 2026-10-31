"""Unified fresh core+files preflight; caller owns authorization and mount lease.

No host/UI entry or physical-facts producer is supplied. Only core:maintenance
and files-v1 resources are supported here. Other generic/hardware handlers fail
before writes. Cross-domain commits are deliberately not advertised as atomic.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid
import re

from protocol.manifest import validate_manifest, verify_file, confined, compatible
from maintenance.engine import Engine, _json_write
from maintenance.adoption_blobs import observe, _directory, MAX_FILE, MAX_TOTAL, MAX_OPERATIONS
from maintenance.resource_runtime import ResourceSession, ResourcePool, ResourceError, resolve
from resource_results import retained_core, require_clean, preflight_metadata
from fresh_resource_views import inspect as inspect_views, verify_active


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _overlap(a, b):
    a, b = a.casefold(), b.casefold()
    return a == b or a.startswith(b+'/') or b.startswith(a+'/')


def _physical(slot, target, policy):
    if target == 'home' or target.startswith('home/'):
        return 'shared-home', target.removeprefix('home/')
    if policy['opt_offload'] and (target == 'opt' or target.startswith('opt/')):
        return 'shared-home', '.steamos/offload/'+target
    if policy['etc_overlay'] and (target == 'etc' or target.startswith('etc/')):
        return 'var-'+slot, 'lib/overlays/etc/upper/'+target.removeprefix('etc/')
    if target == 'var' or target.startswith('var/'):
        return 'var-'+slot, target.removeprefix('var/')
    return 'rootfs-'+slot, target


def _graph(core, resources):
    ids = {core['id'], *(r['id'] for r in resources)}
    rows = [core, *resources]; dependencies = {}
    for row in rows:
        required = list(row.get('requires', row.get('dependencies', [])))
        external = row.get('external_component_dependencies', [])
        conflicts = row.get('conflicts', [])
        if not isinstance(external,list) or not isinstance(conflicts,list): raise ResourceError('Invalid dependency/conflict list')
        required += external
        if row['id'] != core['id']: required.append(core['id'])
        if any(not isinstance(x, str) for x in required): raise ResourceError('Invalid dependency identity')
        missing = set(required)-ids
        if missing: raise ResourceError('Missing unified dependency: '+','.join(sorted(missing)))
        if set(conflicts) & ids: raise ResourceError('Generic/resource component conflict')
        dependencies[row['id']] = required
    visiting, done = set(), set()
    def visit(identity):
        if identity in visiting: raise ResourceError('Unified dependency cycle')
        if identity in done: return
        visiting.add(identity)
        for dependency in dependencies[identity]: visit(dependency)
        visiting.remove(identity); done.add(identity)
    for identity in ids: visit(identity)


class FreshPlan:
    def __init__(self, payload, pool, references, selected, slots, home, core, prepared, policies, observations, states, resources, identities):
        self.payload, self.pool = Path(payload), pool
        self.references, self.selected = copy.deepcopy(references), list(selected)
        self.slots, self.home = copy.deepcopy(slots), Path(home)
        self.core, self.prepared, self.policies = copy.deepcopy(core), copy.deepcopy(prepared), copy.deepcopy(policies)
        self.observations, self.states = observations, states
        self.identities = copy.deepcopy(identities)
        self.commitment = digest(dict(core=core, prepared=prepared, policies=policies,
            observations=observations, states=states, selected=self.selected,
            targets={s:dict(root=str(t['root']),environment=t['environment'],hardware=t['hardware']) for s,t in slots.items()},
            home=str(home),payload=str(payload),pool=str(pool.root),resources=resources,identities=identities))


def preflight(payload, pool, references, selected, *, slots, shared_home):
    """Read-only: no mkdir, lock, recovery, overlay initialization or journal."""
    if set(slots) != {'A', 'B'}: raise ResourceError('Fresh installation requires both probed slots')
    if pool.local_test is not True:
        raise ResourceError('This isolated fresh subpath does not implement production pool authorization')
    payload=Path(payload).absolute()
    pool=ResourcePool(Path(pool.root).absolute(),pool.catalog,local_test=True)
    slots=copy.deepcopy(slots)
    for target in slots.values():target['root']=Path(target['root']).absolute()
    home = Path(shared_home).absolute()
    identities = {}
    for name,path in [('home',home), *((s,Path(t['root'])) for s,t in slots.items()),
                      *((s+'-var',Path(t['root'])/'var') for s,t in slots.items())]:
        with _directory(path): pass
        info=path.stat()
        if not path.is_dir(): raise ResourceError('Missing mounted transaction domain')
        identities[name]=[info.st_dev,info.st_ino]
    for target in slots.values():
        with _directory(Path(target['root'])/'home'): pass
    if len({tuple(v) for v in identities.values()}) != 5:
        raise ResourceError('Root/var/shared-home physical domains alias')
    policies = {s: inspect_views(t['root'], home) for s,t in slots.items()}
    for slot,target in slots.items():
        policy=policies[slot];root=Path(target['root'])
        if policy['etc_overlay'] and not policy['fresh_etc'] and not (root/'etc').is_mount():
            raise ResourceError('Existing persistent etc view must be mounted before review')
        if policy['opt_offload'] and not policy['fresh_opt']:
            if not (root/'opt').is_mount() or not os.path.samestat((root/'opt').stat(),(home/'.steamos/offload/opt').stat()):
                raise ResourceError('Existing shared opt view must be bound before review')
    if len({p['opt_offload'] for p in policies.values()}) != 1:
        raise ResourceError('A/B opt physical ownership differs')
    targets = copy.deepcopy(slots)
    for slot in targets: targets[slot]['opt_offload'] = policies[slot]['opt_offload']
    core = retained_core(payload)
    resources = resolve(pool, references, selected)
    if not resources: raise ResourceError('Select at least one reviewed file resource')
    for row in resources:
        if row['install']['kind'] != 'files-v1':
            raise ResourceError('Fresh subpath supports fixed file resources only; specialized handler remains blocked')
    _graph(core, resources)
    session = ResourceSession(pool, targets, home, read_only=True,planned_core=True)
    require_clean(session); preflight_metadata({s:t['root'] for s,t in targets.items()})
    if any(e._state()['components'] or e._state()['owners'] or list(e.journals.glob('*.json'))
           for e in [*session.engines.values(), session.shared]):
        raise ResourceError('Fresh path refuses existing managed components; use retained maintenance')
    if list((session.shared.state_dir/'resource-actions').glob('*.json')):
        raise ResourceError('Fresh path refuses historical resource groups')
    prepared = session.prepare(references, selected)
    projected, observations, states = [], {}, {}
    for slot, engine in session.engines.items():
        if not compatible(core, targets[slot]['environment']): raise ResourceError('Core incompatible with target slot')
        states[slot] = digest(engine._state())
        for entry in core['files']:
            if _overlap(entry['target'],'var/lib/overlays/etc'):
                raise ResourceError('Core cannot write persistent overlay backing storage directly')
            verify_file(confined(Path(payload)/'integration', entry['source']), entry)
            projected.append((*_physical(slot,entry['target'],policies[slot]), core['id']))
            observations[slot+':'+entry['target']] = observe(engine, engine._target(entry['target']))
    states['shared-home'] = digest(session.shared._state())
    for root, shared in prepared:
        for slot, engine in session.engines.items():
            for entry in root['files']:
                projected.append((*_physical(slot,entry['target'],policies[slot]), root['id']))
                observations[slot+':'+entry['target']] = observe(engine,engine._target(entry['target']))
        for entry in shared['files']:
            if _overlap(entry['target'],'.boiler'):
                raise ResourceError('Resources cannot own private workflow/retention metadata')
            projected.append(('shared-home',entry['target'],shared['id']))
            observations['shared-home:'+entry['target']] = observe(session.shared,session.shared._target(entry['target']))
    for label,manifest in [('core-'+s,core) for s in slots]+[(s+':'+m['id'],m) for pair in prepared for s,m in [('A',pair[0]),('B',pair[0]),('shared-home',pair[1])]]:
        total=0
        if len(manifest['files'])>MAX_OPERATIONS: raise ResourceError('Unified operation quota exceeded')
        for entry in manifest['files']:
            if entry['size']>MAX_FILE: raise ResourceError('Unified file quota exceeded')
            prefix=label.removeprefix('core-') if label.startswith('core-') else label.split(':',1)[0]
            before=observations[prefix+':'+entry['target']]
            total+=entry['size']+before.get('size',0)
        if total>MAX_TOTAL: raise ResourceError('Unified backup quota exceeded')
    for i,(domain,path,identity) in enumerate(projected):
        if domain.startswith('var-') and _overlap(path,'lib/overlays/etc') and not path.startswith('lib/overlays/etc/upper/'):
            raise ResourceError('Direct persistent overlay storage target is unsupported')
        for other_domain,other_path,other_id in projected[:i]:
            if domain == other_domain and _overlap(path,other_path):
                raise ResourceError('Unified physical target conflict: '+identity+' / '+other_id)
    from protocol.resource_pool import validate_pool, inventory
    if validate_pool(pool.root) != pool.catalog:
        raise ResourceError('Effective pool differs from the authenticated local catalog bytes')
    prefix='usr/local/lib/boiler/cache/resource-pool/'
    retained={e['target'].removeprefix(prefix):(e['size'],e['sha256']) for e in core['files'] if e['target'].startswith(prefix)}
    actual={e['path']:(e['size'],e['sha256']) for e in inventory(pool.root)}
    if retained != actual:
        raise ResourceError('Retained core must contain the exact selected single resource pool')
    plan = FreshPlan(payload,pool,references,selected,targets,home,core,prepared,policies,observations,states,resources,identities)
    plan.physical_targets = projected
    return plan


def unavailable_authorization(*args):
    raise ResourceError('Fresh deployment requires the existing authenticated source/target lease; UI remains closed')


def install(plan, *, authorize=None):
    """Only a trusted embedder can provide the existing live authorization scope.

    Its activate(plan) remounts/initializes views only after unified preflight;
    revalidate(plan) checks the same held image/target/source and current facts.
    No provider is discovered from JSON or environment variables.
    """
    if type(plan) is not FreshPlan: raise ResourceError('A live unified plan is required')
    with (authorize or unavailable_authorization)(plan) as authority:
        if plan.pool.local_test and getattr(authority,'mode',None) not in ('explicit-file-fixture','development-session'):
            raise ResourceError('Unsigned pool needs an explicit development authorization scope')
        if getattr(authority,'mode',None) != 'explicit-file-fixture':
            if any(not Path(p).is_mount() for p in [plan.home,*(t['root'] for t in plan.slots.values()),
                                                    *(Path(t['root'])/'var' for t in plan.slots.values())]):
                raise ResourceError('The trusted mount owner must supply mounted A/B, vars and home')
            if any(not (Path(t['root'])/'home').is_mount() or
                   not os.path.samestat((Path(t['root'])/'home').stat(),plan.home.stat()) for t in plan.slots.values()):
                raise ResourceError('Each slot home must bind the same probed shared-home domain')
        if any(shared['files'] for root,shared in plan.prepared):
            if any(not os.path.samestat((Path(t['root'])/'home').stat(),plan.home.stat()) for t in plan.slots.values()):
                raise ResourceError('Shared resources require the actual A/B shared-home binding')
        authority.revalidate(plan)
        again = preflight(plan.payload,plan.pool,plan.references,plan.selected,slots=plan.slots,shared_home=plan.home)
        if again.commitment != plan.commitment: raise ResourceError('Unified review changed before activation')
        authority.activate(plan)
        for slot,target in plan.slots.items(): verify_active(target['root'],plan.home,plan.policies[slot])
        # Overlay/offload initialization can change directory existence, but
        # no reviewed file, payload, account, dependency or ownership may change.
        active = preflight(plan.payload,plan.pool,plan.references,plan.selected,slots=plan.slots,shared_home=plan.home)
        if (active.core != plan.core or active.prepared != plan.prepared or active.observations != plan.observations
                or active.states != plan.states or active.identities != plan.identities):
            raise ResourceError('Unified targets changed during view activation')
        authority.revalidate(plan)
        identity = uuid.uuid4().hex
        directory = plan.home/'.boiler/fresh-resource-installs'
        with _directory(directory,create=True): pass
        path = directory/(identity+'.json')
        receipt = dict(kind='FreshResourceInstall', id=identity, status='prepared', plan_sha256=plan.commitment,
            targets={s:str(t['root']) for s,t in plan.slots.items()}, shared_root=str(plan.home),
            core=[], resource_group=None, cross_domain_atomic=False, runtime='not_validated')
        _json_write(path,receipt)
        try:
            from maintenance.core_retention import seed_root
            for target in plan.slots.values():seed_root(Engine(target['root']))
            for slot,target in plan.slots.items():
                authority.revalidate(plan)
                receipt.update(status='installing-core', active_slot=slot); _json_write(path,receipt)
                result = Engine(target['root']).apply(plan.core,plan.payload/'integration',environment=target['environment'])
                receipt['core'].append(dict(result,scope=slot)); _json_write(path,receipt)
            receipt.update(status='installing-resources',active_slot=None); _json_write(path,receipt)
            authority.revalidate(plan)
            resources = ResourceSession(plan.pool,plan.slots,plan.home).install(plan.references,plan.selected)
            receipt.update(resource_group=resources['id'],resource_receipt=resources)
            for slot,target in plan.slots.items():
                if not Engine(target['root'],read_only=True).verify_readonly('core:maintenance')['ok']:
                    raise ResourceError('New core files failed readonly verification')
            proof = ResourceSession(plan.pool,plan.slots,plan.home,read_only=True).verify(plan.selected)
            if not proof or any(not r['ok'] for r in proof): raise ResourceError('New resources failed readonly verification')
            receipt.update(status='committed',verification=proof); _json_write(path,receipt)
        except BaseException as error:
            receipt.update(status='interrupted',error_type=type(error).__name__)
            _json_write(path,receipt)
            raise
        return dict(receipt,receipt_path=str(path))


def inspect_install(receipt_path, *, slots, shared_home, pool):
    """Read-only interruption evidence; never infer or resume an unknown commit.

    Existing Engine/resource-group journals remain the recovery authority.
    A process kill between Engine commit and workflow receipt is reported for
    explicit review instead of inventing an operation receipt or replaying it.
    """
    from maintenance.adoption_blobs import _file
    path=Path(receipt_path).absolute();home=Path(shared_home).absolute()
    if path.parent != home/'.boiler/fresh-resource-installs' or not re.fullmatch('[0-9a-f]{32}\\.json',path.name):
        raise ResourceError('Fresh receipt is outside the supplied target')
    with _file(path,private=True) as (stream,info):
        if info.st_size>1024*1024:raise ResourceError('Fresh receipt exceeds limit')
        record=json.loads(stream.read(1024*1024+1))
    if (record.get('kind')!='FreshResourceInstall' or record.get('id')!=path.stem
            or record.get('targets')!={s:str(Path(t['root']).absolute()) for s,t in slots.items()}
            or record.get('shared_root')!=str(home)):
        raise ResourceError('Fresh receipt target binding differs')
    expected={r['scope']:r for r in record['core']};core=[];complete=record['status']=='committed'
    for slot,target in slots.items():
        engine=Engine(target['root'],read_only=True);current=engine._state()['components'].get('core:maintenance')
        tracked=expected.get(slot);same=bool(current and tracked and current['transaction']==tracked['transaction'])
        try:verified=bool(same and engine.verify_readonly('core:maintenance')['ok'])
        except Exception:verified=False
        core.append(dict(slot=slot,recorded_transaction=tracked.get('transaction') if tracked else None,
            observed_transaction=current.get('transaction') if current else None,verified=verified))
        complete=complete and verified
    resources=None
    if record.get('resource_group'):
        session=ResourceSession(pool,slots,home,read_only=True)
        actual=session._bound_receipt(record['resource_group'])
        if actual!=record.get('resource_receipt'):complete=False
        try:
            resources=session.verify(actual['resources'])
            complete=complete and bool(resources) and all(r['ok'] for r in resources)
        except Exception:complete=False
    else:complete=False
    return dict(kind='FreshInstallReadonlyInspection',id=record['id'],recorded_status=record['status'],
        status='files-verified-readonly' if complete else 'needs-explicit-owned-journal-review',
        core=core,resources=resources,writes_performed=False,automatic_resume_available=False)
