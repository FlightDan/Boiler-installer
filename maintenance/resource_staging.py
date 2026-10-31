"""Fresh core plus inert resource-cache staging, never resource activation.

The caller owns the authenticated source and target lease. A model name,
catalog hash or saved receipt cannot provide that authority. This purpose does
not write resource installation targets or consume physical hardware facts.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from maintenance.engine import Engine,MaintenanceError,runtime_files,_json_write,_read
from maintenance.adoption_blobs import _directory,_file,observe,MAX_FILE,MAX_TOTAL,MAX_OPERATIONS
from maintenance.core_retention import CORE,is_core,seed_root
from maintenance.resource_runtime import ResourcePool,ResourceError,resolve
from protocol.manifest import compatible,confined,verify_file,ProtocolError
from protocol.resource_pool import validate_pool,inventory,json_document

PURPOSE='resource-cache-stage-v1'
CACHE='usr/local/lib/boiler/cache/resource-pool/'
RECEIPTS='.boiler/fresh-resource-installs'

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _physical(path):
    info=path.stat();return [info.st_dev,info.st_ino]

def _domains(slots,home):
    paths={'home':home}
    for slot,target in slots.items():
        root=target['root'];paths.update({slot:root,slot+'-var':root/'var',slot+'-home':root/'home'})
    result={}
    for name,path in paths.items():
        if path==Path(path.anchor):raise ResourceError('Cache staging requires image domains, not a filesystem root')
        with _directory(path):pass
        result[name]=_physical(path)
    keys=['home','A','B','A-var','B-var']
    if len({tuple(result[k]) for k in keys})!=5:
        raise ResourceError('Cache staging physical root/var/home domains alias')
    return result

def _intent(pool,model_id,selected):
    if type(model_id) is not str or type(selected) is not list or not selected or len(selected)>128 or any(type(i) is not str for i in selected) or len(set(selected))!=len(selected):
        raise ResourceError('An explicit source model and resource selection are required')
    models=[m for m in pool.catalog['models'] if m['id']==model_id]
    if len(models)!=1:raise ResourceError('Source model reference is not unique')
    references=models[0]['resources']
    from maintenance.hdr_resources import bind
    effective,bound=bind(pool,references,selected)
    resources=resolve(effective,bound,selected)
    if {r['id'] for r in resources}!=set(selected):raise ResourceError('Explicitly select the complete source dependency closure')
    refs={r['resource']:r for r in references};rows=[]
    for resource in resources:
        original=pool.resource(resource['id'])
        rows.append(dict(id=original['id'],version=original['version'],parameters=copy.deepcopy(refs[original['id']].get('parameters') or {}),
            payload_sha256=digest(original['payload']['files']),status='staged',activation='activation_blocked',
            activation_requirements=dict(hardware=copy.deepcopy(original.get('hardware_requires',{})),
                external_components=copy.deepcopy(original.get('external_component_dependencies',[]))),
            resource_installation_performed=False))
    raw=(pool.root/'catalog.toon').read_bytes()
    return dict(kind='ResourceCacheStageIntent',purpose=PURPOSE,source_model=model_id,
        catalog_sha256=hashlib.sha256(raw).hexdigest(),selected=[r['id'] for r in resources],resources=rows,
        activation='activation_blocked',resource_installation_performed=False,hardware_facts_consumed=False)

def _fixed_core(core,entries):
    if not is_core(core) or core.get('hooks') or core.get('dynamic_handler'):
        raise ResourceError('Cache staging requires the fixed retained maintenance core')
    runtime_root=Path(__file__).resolve().parents[1]
    allowed={'usr/local/lib/boiler/'+path.relative_to(runtime_root).as_posix():0o644 for path in runtime_files(root=runtime_root)}
    allowed.update({'usr/local/bin/boiler-maintenance':0o755,'usr/local/bin/boiler-maintenance-ui':0o755,
                    'usr/share/applications/Boiler-Maintenance.desktop':0o644})
    allowed.update({CACHE+entry['path']:0o644 for entry in entries})
    files=core['files']
    if len(files)!=len(allowed) or {e['target'] for e in files}!=set(allowed) or any(e['mode']!=allowed[e['target']] for e in files):
        raise ResourceError('Cache staging core may write only its fixed runtime, manual launchers and inert pool cache')
    cached={e['target'][len(CACHE):]:(e['size'],e['sha256']) for e in files if e['target'].startswith(CACHE)}
    if cached!={e['path']:(e['size'],e['sha256']) for e in entries}:
        raise ResourceError('Core must retain exactly one complete raw source pool')

class CacheStagePlan:
    def __init__(self,payload,pool,model_id,selected,slots,home,core,intent,entries,states,observations,identities):
        self.payload,self.pool,self.model_id,self.selected=payload,pool,model_id,list(selected)
        self.slots,self.home,self.core,self.intent=copy.deepcopy(slots),home,copy.deepcopy(core),copy.deepcopy(intent)
        self.entries,self.states,self.observations,self.identities=copy.deepcopy(entries),states,observations,identities
        self.purpose=PURPOSE;self.commitment=digest(self.value())
    def value(self):
        return dict(purpose=self.purpose,payload=str(self.payload),pool_root=str(self.pool.root),pool_catalog=self.pool.catalog,
            local_test=self.pool.local_test,model_id=self.model_id,selected=self.selected,home=str(self.home),
            slots={s:dict(root=str(t['root']),environment=t['environment']) for s,t in self.slots.items()},
            core=self.core,intent=self.intent,entries=self.entries,states=self.states,observations=self.observations,identities=self.identities)

def preflight_cache_stage(payload,pool,model_id,selected,*,slots,shared_home):
    """Readonly source/core planning. No resource hardware predicate is granted."""
    if pool.local_test is not True:raise ResourceError('Production source authorization is not implemented by this staging candidate')
    payload=Path(payload).absolute();home=Path(shared_home).absolute()
    validated=validate_pool(Path(pool.root).absolute())
    if digest(validated)!=digest(pool.catalog):raise ResourceError('Source pool differs from its validated catalog bytes')
    pool=ResourcePool(Path(pool.root).absolute(),validated,local_test=True)
    if set(slots)!={'A','B'}:raise ResourceError('Cache staging requires both probed slots')
    # Hardware data is deliberately neither used nor copied into this plan.
    targets={s:dict(root=Path(t['root']).absolute(),environment=copy.deepcopy(t.get('environment',{}))) for s,t in slots.items()}
    identities=_domains(targets,home);intent=_intent(pool,model_id,selected);entries=inventory(pool.root)
    from protocol.manifest import load_json,validate_manifest
    manifest=load_json(payload/'integration/manifest.json');row=manifest['components'].get(CORE)
    if not row or row.get('available') is not True:raise ResourceError('Prepared core is unavailable')
    core=copy.deepcopy(row['component']);validate_manifest(core,'ComponentManifest');_fixed_core(core,entries)
    core['cache_staging_intent']=copy.deepcopy(intent)
    engines={s:Engine(t['root'],read_only=True) for s,t in targets.items()}
    shared=Engine(home,home/'.boiler/resource-maintenance',read_only=True,target_domain='shared-home',account_roots=[t['root'] for t in targets.values()])
    states={};observations={}
    for scope,engine in [*engines.items(),('shared-home',shared)]:
        state=engine._state()
        if state['components'] or state['owners'] or list(engine.journals.glob('*.json')) or os.path.lexists(engine.state_dir/'adoption-pending.json'):
            raise ResourceError('Cache staging is a fresh-only purpose; existing ownership/history needs explicit maintenance')
        states[scope]=digest(state)
    if list((shared.state_dir/'resource-actions').glob('*.json')):raise ResourceError('Historical resource groups require explicit maintenance')
    if len(core['files'])>MAX_OPERATIONS:raise ResourceError('Core operation quota exceeded')
    for slot,engine in engines.items():
        if not compatible(core,targets[slot]['environment']):raise ResourceError('Core is incompatible with this target')
        total=0
        for entry in core['files']:
            verify_file(confined(payload/'integration',entry['source']),entry)
            before=observe(engine,engine._target(entry['target']));observations[slot+':'+entry['target']]=before
            total+=entry['size']+before.get('size',0)
            if entry['size']>MAX_FILE:raise ResourceError('Core file exceeds backup limit')
        if total>MAX_TOTAL:raise ResourceError('Core exceeds backup limit')
    return CacheStagePlan(payload,pool,model_id,selected,targets,home,core,intent,entries,states,observations,identities)

def unavailable_authorization(*args):
    raise ResourceError('Cache staging requires a purpose-bound authenticated source/target lease; production entry remains closed')

def _review(plan):
    if digest(plan.value())!=plan.commitment:raise ResourceError('Cache stage review was modified')
    current=preflight_cache_stage(plan.payload,plan.pool,plan.model_id,plan.selected,slots=plan.slots,shared_home=plan.home)
    if current.commitment!=plan.commitment:raise ResourceError('Cache stage source or targets changed after review')
    return current

def stage_cache(plan,*,authorize=None):
    """Only core transactions occur; this never invokes a resource installer."""
    if type(plan) is not CacheStagePlan:raise ResourceError('A live cache-only staging plan is required')
    with (authorize or unavailable_authorization)(plan) as owner:
        if getattr(owner,'purpose',None)!=PURPOSE or getattr(owner,'mode',None) not in ('explicit-file-fixture','development-session'):
            raise ResourceError('Cache staging needs its separate explicit development purpose')
        reviewed=_review(plan);owner.revalidate(reviewed)
        if owner.mode!='explicit-file-fixture':
            for path in [reviewed.home,*(t['root'] for t in reviewed.slots.values()),*(t['root']/'var' for t in reviewed.slots.values())]:
                if not path.is_mount():raise ResourceError('The mount owner must supply actual A/B, var and home mounts')
            if any(_physical(t['root']/'home')!=_physical(reviewed.home) for t in reviewed.slots.values()):
                raise ResourceError('Both slot homes must bind the actual shared domain')
        owner.activate(reviewed);reviewed=_review(reviewed)
        owner.revalidate(reviewed)
        if digest(reviewed.value())!=reviewed.commitment:raise ResourceError('Cache staging intent changed during authorization')
        identity=uuid.uuid4().hex;directory=reviewed.home/RECEIPTS
        with _directory(directory,create=True):pass
        path=directory/(identity+'.json')
        record=dict(kind='FreshResourceCacheStage',id=identity,purpose=PURPOSE,status='preparing',activation='activation_blocked',
            plan_sha256=reviewed.commitment,expected_core_sha256=digest(reviewed.core),intent=copy.deepcopy(reviewed.intent),
            targets={s:str(t['root']) for s,t in reviewed.slots.items()},shared_root=str(reviewed.home),identities=reviewed.identities,
            core_transactions=[],resource_installation_performed=False,hardware_facts_consumed=False,cross_domain_atomic=False)
        _json_write(path,record)
        try:
            for target in reviewed.slots.values():seed_root(Engine(target['root']))
            for slot,target in reviewed.slots.items():
                owner.revalidate(reviewed)
                if digest(reviewed.value())!=reviewed.commitment:raise ResourceError('Cache staging intent changed')
                result=Engine(target['root']).apply(copy.deepcopy(reviewed.core),reviewed.payload/'integration',environment=target['environment'])
                record['core_transactions'].append(dict(result,scope=slot));_json_write(path,record)
            record['status']='staged';_json_write(path,record)
            proof=inspect_cache_stage(path,slots=reviewed.slots,shared_home=reviewed.home)
            if not proof['currently_staged']:raise ResourceError('Staged core/cache failed readonly binding verification')
        except BaseException as error:
            record.update(status='interrupted',error_type=type(error).__name__);_json_write(path,record);raise
        return dict(record,receipt_path=str(path))

def inspect_cache_stage(receipt_path,*,slots,shared_home):
    """Readonly current binding; a historical staged receipt is not current state."""
    path=Path(receipt_path).absolute();home=Path(shared_home).absolute()
    if path.parent!=home/RECEIPTS or not re.fullmatch(r'[0-9a-f]{32}\.json',path.name):raise ResourceError('Stage receipt is outside the supplied target')
    with _file(path,private=True) as (stream,info):
        if info.st_size>1024*1024:raise ResourceError('Stage receipt exceeds limit')
        record=json_document(stream.read(1024*1024+1))
    if not isinstance(record,dict):raise ResourceError('Invalid cache stage receipt')
    targets={s:dict(root=Path(t['root']).absolute(),environment=copy.deepcopy(t.get('environment',{}))) for s,t in slots.items()}
    if (set(targets)!={'A','B'} or record.get('kind')!='FreshResourceCacheStage' or record.get('id')!=path.stem or record.get('purpose')!=PURPOSE
            or record.get('targets')!={s:str(t['root']) for s,t in targets.items()} or record.get('shared_root')!=str(home)):
        raise ResourceError('Stage receipt target/purpose binding differs')
    valid=record.get('status')=='staged';rows=[];errors=[]
    try:
        if digest(_domains(targets,home))!=digest(record['identities']):raise ResourceError('Stage physical domains changed')
        operations=record['core_transactions']
        if len(operations)!=2 or {r['scope'] for r in operations}!={'A','B'}:raise ResourceError('Stage lacks both recorded core transactions')
        for operation in operations:
            slot=operation['scope'];engine=Engine(targets[slot]['root'],read_only=True)
            current=engine._state()['components'].get(CORE)
            if not current or current['transaction']!=operation['transaction']:raise ResourceError('Stage core generation is no longer current')
            journal=_read(engine._journal(operation['transaction']));component=journal['component']
            if journal.get('status')!='committed' or journal.get('root')!=engine.identity or digest(component)!=record['expected_core_sha256']:
                raise ResourceError('Staged core journal differs from its recorded intent')
            if digest(component.get('cache_staging_intent'))!=digest(record['intent']) or not engine.verify_readonly(CORE)['ok']:
                raise ResourceError('Staged sources or selection binding changed')
            cached=ResourcePool.for_local_test(engine.root/CACHE,trust_local_test=True)
            expected=_intent(cached,record['intent']['source_model'],record['intent']['selected'])
            if digest(expected)!=digest(record['intent']):raise ResourceError('Stage intent does not match the actual retained pool')
            rows.append(dict(scope=slot,transaction=operation['transaction'],cache_verified=True))
    except (OSError,ValueError,KeyError,TypeError,MaintenanceError,ProtocolError) as error:
        valid=False;errors.append(str(error))
    return dict(kind='ResourceCacheStageInspection',purpose=PURPOSE,status='staged' if valid else 'binding_invalid',currently_staged=bool(valid),
        activation='activation_blocked',resource_installation_performed=False,hardware_facts_consumed=False,writes_performed=False,
        resources=copy.deepcopy(record['intent']['resources']) if valid else [],core_transactions=rows,errors=errors)
