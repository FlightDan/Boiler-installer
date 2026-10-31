"""A caller-bound resource group with a durable, resumable deployment plan.

The caller holds the target lease and lifecycle lock. No new group is chosen
here: the lifecycle writeahead owns its identity before this module is entered.
"""
import contextlib
import copy
import hashlib
import json
import re
from pathlib import Path
from .engine import _json_write, _same, _sync_dir
from .adoption_blobs import observe, validate_backups, _directory, _file
from protocol.manifest import confined, load_json, verify_file, compatible, _decode_json, MAX_MANIFEST
from .resource_runtime import ResourceError, CPU_ID, guard_cpu, resolve

TERMINAL=('committed','restored','rolled_back')

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def engines(session):return dict(session.engines, **{'shared-home':session.shared})

def _binding(session,refs,selected):
    return dict(catalog_sha256=digest(session.pool.catalog),references_sha256=digest(refs),
        selected=sorted(selected),targets={s:str(e.root) for s,e in session.engines.items()},
        shared_root=str(session.shared.root),local_test=session.pool.local_test,
        target_hardware={s:copy.deepcopy(v['hardware']) for s,v in session.slots.items()})

def _next(state,journal):
    state=copy.deepcopy(state);component=journal['component'];transaction=journal['id']
    for op in journal['operations']:
        state['owners'][op['target']]=dict(component=component['id'],transaction=transaction,sha256=op['after'].get('sha256'))
    state['components'][component['id']]=dict(version=component['version'],transaction=transaction,
        requires=component.get('requires',[]),conflicts=component.get('conflicts',[]))
    return state

def pending_preflight(engine,allowed):
    """RO closure check; callers must check *every* engine before recovery."""
    found={}
    for path in engine.journals.glob('*.json'):
        journal=load_json(path)
        if journal.get('status') in TERMINAL:continue
        if (journal.get('root')!=engine.identity or journal.get('status') not in ('prepared','applying')
                or not allowed(journal)):
            raise ResourceError('Unrelated pending transaction blocks resource reinstall')
        validate_backups(engine,journal['operations'])
        for op in journal['operations']:
            actual=engine._observed(engine._target(op['target']),op['before'],op['after'])
            if not (_same(actual,op['before']) or _same(actual,op['after'])):
                raise ResourceError('Interrupted reinstall target changed outside its snapshots')
        found[path.stem]=digest(journal)
    return found

def recover_exact(engine,expected,permit=contextlib.nullcontext):
    with permit(),engine._lock(live_recover=True):
        actual=pending_preflight(engine,lambda j: j['id'] in expected)
        if actual!=expected:raise ResourceError('Pending reinstall changed after preflight')
        if actual:engine._recover()

def _prepare(session,refs,selected,group,path):
    # Refuse foreign pending globally before preparing derived firmware/GRUB.
    for engine in engines(session).values():pending_preflight(engine,lambda j:False)
    for resource in resolve(session.pool,refs,selected):
        if resource['install']['kind']=='boiler-boot-settings-v1' and resource.get('parameters',{}).get('gm7_resume'):
            for slot in session.slots.values():
                disk=slot['hardware'].get('target_disk')
                if not isinstance(disk,dict) or not disk.get('bus') or (disk['bus']=='NVMe' and not disk.get('model')):
                    raise ResourceError('Actual target disk facts are required for GM7 retry')
    prepared=session.prepare(refs,selected)
    operations=[];payload=path.with_suffix('.payload')
    with _directory(payload,create=True):pass
    for root,shared in prepared:
        for scope,base in [('shared-home',shared),*[(s,root) for s in session.engines]]:
            engine=engines(session)[scope];key=(base['id'],scope)
            boot=session._boot_prepared.get(key)
            manifest=copy.deepcopy(boot['manifest'] if boot else session._firmware_prepared.get(key,base))
            if not manifest['files']:continue
            manifest['resource_group']=group
            environment=(next(iter(session.slots.values())) if scope=='shared-home' else session.slots[scope])['environment']
            if scope=='shared-home' and any(not compatible(manifest,v['environment']) for v in session.slots.values()):
                raise ResourceError('Shared resource incompatible with a slot')
            folder=payload/str(len(operations))
            with _directory(folder,create=True):pass
            for entry in manifest['files']:
                data=boot['files'][entry['source']] if boot else confined(session.pool.payload_root(base['id']),entry['source']).read_bytes()
                if len(data)!=entry['size'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
                    raise ResourceError('Reinstall payload changed during writeahead')
                target=confined(folder,entry['source']);target.parent.mkdir(parents=True,exist_ok=True)
                # fsync each immutable retained payload before publishing the plan.
                with target.open('wb') as stream:
                    import os
                    stream.write(data);stream.flush()
                    os.fsync(stream.fileno())
                # The retained plan must not outlive a missing payload name.
                # Sync each new nested directory as well as the file contents.
                parent=target.parent
                while parent.is_relative_to(folder):
                    _sync_dir(parent)
                    if parent==folder:break
                    parent=parent.parent
            operations.append(dict(scope=scope,manifest=manifest,payload=str(folder),environment=environment,
                before={entry['target']:observe(engine,engine._target(entry['target'])) for entry in manifest['files']}))
    resources=sorted(r['id'] for r in resolve(session.pool,refs,selected))
    plan=dict(binding=_binding(session,refs,selected),operations=operations,
        before_states={s:e._state() for s,e in engines(session).items()})
    # Bound the plan before publication; the reader enforces the same limit.
    if len((json.dumps(plan,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode())>MAX_MANIFEST:
        raise ResourceError('Reinstall plan exceeds size limit')
    # Publish the immutable plan before the small receipt that binds it.
    _json_write(path.with_suffix('.plan'),plan)
    record=dict(kind='ResourceInstallReceipt',id=group,status='prepared',
        **{k:v for k,v in _binding(session,refs,selected).items() if k in ('targets','shared_root','local_test')},
        before_slots={s:{i:copy.deepcopy(e._state()['components'].get(i)) for i in resources} for s,e in session.engines.items()},
        transactions=[],resources=resources,receipt_path=str(path),plan_sha256=digest(plan))
    _json_write(path,record)
    return record

def inspect(session,refs,selected,group):
    """Authenticate all durable scopes and bytes, without recovery or writes."""
    record=session._bound_receipt(group)
    expected_path=session.shared.state_dir/'resource-actions'/(group+'.json')
    if record.get('receipt_path')!=str(expected_path):raise ResourceError('Reinstall receipt path changed')
    with _file(expected_path.with_suffix('.plan'),private=True) as (stream,info):
        if info.st_size>MAX_MANIFEST:raise ResourceError('Reinstall plan exceeds size limit')
        plan=_decode_json(stream.read(MAX_MANIFEST+1))
    if digest(plan)!=record.get('plan_sha256') or plan.get('binding')!=_binding(session,refs,selected):raise ResourceError('Reinstall plan source or target binding changed')
    known={(op['scope'],op['manifest']['id']):op for op in plan['operations']}
    journals={};pending={}
    for scope,engine in engines(session).items():
        pending[scope]=pending_preflight(engine,lambda j:(scope,j['component']['id']) in known and
            j['component']==known[(scope,j['component']['id'])]['manifest'])
        for path in engine.journals.glob('*.json'):
            journal=load_json(path)
            if journal.get('component',{}).get('resource_group')!=group:continue
            key=(scope,journal['component']['id'])
            if key not in known or journal['component']!=known[key]['manifest']:raise ResourceError('Reinstall journal differs from its durable plan')
            if journal['status']=='rolled_back':continue
            if journal['status'] not in ('committed','prepared','applying'):raise ResourceError('Reinstall transaction was externally restored')
            if key in journals:raise ResourceError('Multiple live transactions for one reinstall operation')
            journals[key]=journal
    from .cpu_resources import matches
    expected=copy.deepcopy(plan['before_states'])
    for op in plan['operations']:
        scope=op['scope'];engine=engines(session)[scope];key=(scope,op['manifest']['id']);journal=journals.get(key)
        folder=Path(op['payload'])
        if folder!=Path(record['receipt_path']).with_suffix('.payload')/str(plan['operations'].index(op)):
            raise ResourceError('Reinstall retained payload path changed')
        for entry in op['manifest']['files']:verify_file(confined(folder,entry['source']),entry)
        if journal:
            if journal['root']!=engine.identity or journal['before_state']!=expected[scope]:raise ResourceError('Reinstall journal state chain changed')
            if journal['status']=='committed':
                if any(not matches(engine,journal['component'],item) for item in journal['operations']):raise ResourceError('Committed reinstall files changed')
                expected[scope]=_next(expected[scope],journal)
        else:
            for target,before in op['before'].items():
                if not _same(observe(engine,engine._target(target)),before):raise ResourceError('Unstarted reinstall target changed')
    for scope,engine in engines(session).items():
        accepted=[expected[scope]]
        for (s,_),journal in journals.items():
            if s==scope and journal['status'] in ('prepared','applying'):
                accepted.append(_next(expected[scope],journal)) # state-write -> journal-commit kill window
        if engine._state() not in accepted:raise ResourceError('Installed state changed outside the bound reinstall')
    return record,plan,journals,pending

def install(session,refs,selected,group):
    if session.read_only:raise ResourceError('Read-only resource session cannot install')
    if not isinstance(group,str) or not re.fullmatch('[0-9a-f]{32}',group):raise ResourceError('Invalid caller-bound reinstall group')
    path=session.shared.state_dir/'resource-actions'/ (group+'.json')
    if not path.exists():
        for engine in engines(session).values():pending_preflight(engine,lambda j:False)
        with _directory(path.parent,create=True):pass
        _prepare(session,refs,selected,group,path)
    record,plan,journals,pending=inspect(session,refs,selected,group)
    cpu=CPU_ID in record['resources']
    guard_cpu(session.pool,session.slots,[CPU_ID] if cpu else [],recovery=True,live_binding=session.cpu_live_binding)
    # Full read-only preflight above precedes the first recovery/write anywhere.
    for scope,engine in engines(session).items():
        permit=(lambda e=engine,s=scope:e._cpu_live_action(session.pool,session.slots,s,operation='restore/recover',binding=session.cpu_live_binding)) if cpu and scope!='shared-home' and session.pool.resource(CPU_ID)['install']['kind']=='intel-g3e-public-cpu-live-v1' else (lambda e=engine,s=scope:e._cpu_action(session.pool,session.slots,s,recovery=True)) if cpu and scope!='shared-home' else contextlib.nullcontext
        recover_exact(engine,pending[scope],permit)
    record,plan,journals,pending=inspect(session,refs,selected,group)
    completed=[]
    for op in plan['operations']:
        scope=op['scope'];engine=engines(session)[scope];manifest=op['manifest'];journal=journals.get((scope,manifest['id']))
        if journal and journal['status']=='committed':receipt=dict(component=manifest['id'],transaction=journal['id'],status='committed')
        elif manifest['id']==CPU_ID:
            receipt=(engine.apply_cpu_live(manifest,op['payload'],pool=session.pool,slots=session.slots,slot=scope,operation='reviewed-reinstall',binding=session.cpu_live_binding,environment=op['environment']) if manifest.get('cpu_live_contract') else engine.apply_cpu(manifest,op['payload'],pool=session.pool,slots=session.slots,slot=scope,environment=op['environment']))
        else:receipt=engine.apply(manifest,op['payload'],environment=op['environment'])
        # Canonical rows deliberately exclude volatile apply-return state hashes.
        completed.append(dict(scope=scope,component=manifest['id'],transaction=receipt['transaction'],status='committed'))
        record.update(status='applying',transactions=copy.deepcopy(completed));_json_write(path,record)
    inspect(session,refs,selected,group)
    record.update(status='committed',transactions=completed);_json_write(path,record)
    return record

def prepare_bootstrap(root,intent,token,folder):
    from .engine import Engine
    from .resource_firstboot import INTENT
    engine=Engine(root);current=engine._state()['components'].get('core:resource-firstboot')
    if not current or not Engine(root,read_only=True).verify_readonly('core:resource-firstboot')['ok']:
        raise ResourceError('Bootstrap files changed before reinstall intent publication')
    manifest=copy.deepcopy(load_json(engine._journal(current['transaction']))['component'])
    manifest['resource_retry']=token
    folder=Path(folder)
    with _directory(folder,create=True):pass
    for index,entry in enumerate(manifest['files']):
        data=(json.dumps(intent,sort_keys=True,separators=(',',':'))+'\n').encode() if entry['target']==INTENT else (root/entry['target']).read_bytes()
        source=str(index)+'.bin';path=folder/source
        with path.open('wb') as stream:
            import os
            stream.write(data);stream.flush()
            os.fsync(stream.fileno())
        _sync_dir(folder)
        entry.update(source=source,size=len(data),sha256=hashlib.sha256(data).hexdigest())
    return dict(root=str(root),manifest=manifest,payload=str(folder),before_state=engine._state(),
        before={e['target']:observe(engine,engine._target(e['target'])) for e in manifest['files']})

def inspect_bootstrap(plan):
    from .engine import Engine
    from .cpu_resources import matches
    engine=Engine(plan['root']);manifest=plan['manifest']
    pending=pending_preflight(engine,lambda j:j['component']==manifest)
    owned=[]
    for path in engine.journals.glob('*.json'):
        journal=load_json(path)
        if journal.get('component',{}).get('resource_retry')!=manifest['resource_retry']:continue
        if journal['component']!=manifest:raise ResourceError('Bootstrap retry ownership changed')
        if journal['status']=='rolled_back':continue
        if journal['status'] not in ('committed','prepared','applying'):raise ResourceError('Bootstrap retry was restored outside this action')
        owned.append(journal)
    if len(owned)>1:raise ResourceError('Duplicate bootstrap retry transaction')
    for entry in manifest['files']:verify_file(confined(plan['payload'],entry['source']),entry)
    accepted=[plan['before_state']];journal=owned[0] if owned else None
    if journal:
        if journal['root']!=engine.identity or journal['before_state']!=plan['before_state']:raise ResourceError('Bootstrap retry state chain changed')
        accepted.append(_next(plan['before_state'],journal))
        if journal['status']=='committed':
            accepted=accepted[1:]
            if any(not matches(engine,manifest,op) for op in journal['operations']):raise ResourceError('Published bootstrap retry files changed')
    else:
        for target,before in plan['before'].items():
            if not _same(observe(engine,engine._target(target)),before):raise ResourceError('Unpublished bootstrap target changed')
    if engine._state() not in accepted:raise ResourceError('Bootstrap retry was superseded')
    return engine,journal,pending

def apply_bootstrap(plan):
    engine,journal,pending=inspect_bootstrap(plan)
    if journal and journal['status']=='committed':return journal['id']
    recover_exact(engine,pending)
    inspect_bootstrap(plan)
    return engine.apply(plan['manifest'],plan['payload'],expected_state_sha256=hashlib.sha256(
        json.dumps(plan['before_state'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())['transaction']
