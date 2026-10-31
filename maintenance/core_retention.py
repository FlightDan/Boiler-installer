"""Fixed persistent dependency of fresh file resources on retained maintenance.

The shared-home lock precedes every marked root/shared state lock. No model
path or environment variable selects its location. Binding nonces detect an
unmounted/replaced home instead of silently creating a second guard namespace.
"""
import contextlib
import copy
import json
import re
import uuid
from pathlib import Path

CORE='core:maintenance'
CORE_MARKER={'kind':'fresh-core-shared-home-retention-v1'}
RESOURCE_MARKER={'kind':'fresh-resource-requires-core-v1'}


def _read(path):
    from .adoption_blobs import _file
    with _file(path,private=True) as (stream,info):
        if info.st_size>4096:raise ValueError('Core-retention binding exceeds limit')
        return json.loads(stream.read(4097))


def _identity(home):
    value=_read(Path(home)/'.boiler/core-retention/identity.json')
    if set(value)!= {'kind','nonce'} or value['kind']!='CoreRetentionHome' or not re.fullmatch('[0-9a-f]{32}',value['nonce']):
        raise ValueError('Invalid core-retention home identity')
    return value


def seed_root(engine):
    """Trusted fresh coordinator only, after unified preflight and activation."""
    from .adoption_blobs import _directory
    from .engine import _json_write
    folder=engine.home_root/'.boiler/core-retention'
    with _directory(folder,create=True):pass
    path=folder/'identity.json'
    if path.exists():identity=_identity(engine.home_root)
    else:
        identity=dict(kind='CoreRetentionHome',nonce=uuid.uuid4().hex);_json_write(path,identity)
    binding=engine.state_dir/'core-retention-home.json'
    if binding.exists() and _read(binding)!=identity:raise ValueError('Core-retention binding changed')
    _json_write(binding,identity)


def _bound_home(engine):
    actual=_identity(engine.home_root)
    if engine.target_domain=='root' and _read(engine.state_dir/'core-retention-home.json')!=actual:
        raise ValueError('Mounted home differs from retained core binding')
    return actual


def is_core(component):
    return component.get('id')==CORE and component.get('core_retention')==CORE_MARKER


def is_resource(component):
    return component.get('id','').startswith('resource:') and component.get('core_retention')==RESOURCE_MARKER


def _recorded_component(engine,identity,current):
    from .engine import _read,MaintenanceError
    try:journal=_read(engine._journal(current['transaction']))
    except (OSError,ValueError,KeyError,TypeError) as error:
        raise MaintenanceError('Installed core/resource journal is unavailable or corrupt; preserve target') from error
    component=journal.get('component') if isinstance(journal,dict) else None
    if (not isinstance(component,dict) or component.get('id')!=identity
            or journal.get('root')!=engine.identity):
        raise MaintenanceError('Installed core/resource journal binding is corrupt; preserve target')
    return component


def installed_core(engine):
    current=engine._state()['components'].get(CORE)
    return current is not None and is_core(_recorded_component(engine,CORE,current))


def inherited(engine,identity):
    current=engine._state()['components'].get(identity)
    return current is not None and is_resource(_recorded_component(engine,identity,current))


def decorate(manifest, *, shared):
    value=copy.deepcopy(manifest)
    if not value['files']:return value
    value['core_retention']=dict(RESOURCE_MARKER)
    if shared:value['external_requires']=[CORE]
    elif CORE not in value['requires']:value['requires'].append(CORE)
    return value


def verify_core(engine):
    from .engine import Engine,MaintenanceError
    if not installed_core(engine) or not engine.verify_readonly(CORE)['ok']:
        raise MaintenanceError('Resource requires the verified retained core')
    _bound_home(engine)


def _needed(engine,component):
    from .engine import _read,MaintenanceError
    if component and component.get('core_retention') is not None:return True
    for identity,row in engine._state()['components'].items():
        # The fixed contract cannot mark other generic component IDs. Leave
        # their ownership/migration validation to its established entry path.
        if identity!=CORE and not identity.startswith('resource:'):continue
        if _recorded_component(engine,identity,row).get('core_retention') is not None:return True
    for path in engine.journals.glob('*.json'):
        try:row=_read(path)
        except (OSError,ValueError) as error:
            raise MaintenanceError('Transaction retention cannot be inspected; preserve target') from error
        if not isinstance(row,dict):raise MaintenanceError('Invalid transaction retention record; preserve target')
        if row.get('status') not in ('committed','restored','rolled_back') and row.get('component',{}).get('core_retention') is not None:return True
    return False


@contextlib.contextmanager
def scope(engine,component=None):
    if engine.read_only:yield;return
    if not _needed(engine,component):yield;return
    from .engine import Engine,MaintenanceError
    try:_bound_home(engine)
    except (OSError,ValueError) as error:raise MaintenanceError('Core-retention home binding is unavailable; preserve target') from error
    holder=Engine(engine.home_root,engine.home_root/'.boiler/core-retention',target_domain='shared-home',account_roots=engine.account_roots)
    with holder._state_lock():yield


def guard_apply(engine,component,state):
    from .engine import Engine,MaintenanceError
    prior=state['components'].get(component['id'])
    if prior and (component['id']==CORE or component['id'].startswith('resource:')):
        old=_recorded_component(engine,component['id'],prior)
        if old.get('core_retention') is not None and component.get('core_retention')!=old['core_retention']:
            raise MaintenanceError('Core-retention dependency cannot be dropped by an update')
    marker=component.get('core_retention')
    if marker is None:return
    if is_core(component):
        if engine.target_domain!='root':raise MaintenanceError('Core belongs to a root slot')
        _bound_home(engine);return
    if not is_resource(component) or component.get('hooks') or component.get('dynamic_handler'):
        raise MaintenanceError('Invalid fixed core-retention contract')
    if engine.target_domain=='root':
        if CORE not in component.get('requires',[]):raise MaintenanceError('Root resource must retain core dependency')
        verify_core(Engine(engine.root,read_only=True))
    else:
        if component.get('external_requires')!=[CORE] or len(engine.account_roots)!=2:
            raise MaintenanceError('Shared resource requires both retained root cores')
        roots=[Engine(root,read_only=True) for root in engine.account_roots]
        physical=lambda path:(path.stat().st_dev,path.stat().st_ino)
        home_key=physical(engine.home_root)
        if len({home_key,*(physical(current.root) for current in roots)})!=3:
            raise MaintenanceError('Shared core retention requires two distinct physical roots separate from shared home')
        identity=_identity(engine.home_root)
        for current in roots:
            verify_core(current)
            if physical(current.home_root)!=home_key or _bound_home(current)!=identity:
                raise MaintenanceError('Shared resource/core physical home differs')


def guard_restore(engine,journal):
    from .engine import Engine,MaintenanceError,_read
    if not is_core(journal['component']) or CORE in journal['before_state']['components']:return
    shared=Engine(engine.home_root,engine.home_root/'.boiler/resource-maintenance',read_only=True,
                  target_domain='shared-home',account_roots=[engine.root])
    for row in shared._state()['components'].values():
        component=_read(shared._journal(row['transaction']))['component']
        if is_resource(component):raise MaintenanceError('Shared resource still requires retained core; restore resources first')
    for path in shared.journals.glob('*.json'):
        row=_read(path)
        if row.get('status') not in ('committed','restored','rolled_back') and is_resource(row.get('component',{})):
            raise MaintenanceError('Pending shared resource requires core; recover resources first')
