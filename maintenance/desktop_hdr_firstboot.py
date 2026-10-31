"""Fixed desktop panel binding through the existing resumable A/B retry protocol."""
import copy
from pathlib import Path
import uuid
from . import resource_firstboot as firstboot,resource_lifecycle as lifecycle,retry_documents
from .resource_runtime import ResourceError
from .engine import Engine,_json_write

ID='resource:gamma22-hdr-desktop'
MODE='initial-desktop-hdr-binding-v1'
PHASES=('prepared','installing','files_committed','publishing','states','closing','closed')

def _pending(mounts):
    values=[]
    marker=mounts.home/'.boiler/resource-maintenance/lifecycle-action.json'
    paths=[(marker,None),*((root/firstboot.STATE,'maintenance_retry') for root in mounts.roots.values())]
    for path,key in paths:
        if not path.exists():continue
        value=firstboot.read_private(path)
        if key:value=value.get(key)
        if not value or value.get('status')=='closed':continue
        # Compact references carry only the fixed header. Hydrate before
        # inspecting the mode; unrelated maintenance remains its own task.
        if value.get('kind')!='BoilerResourceRetryAction':raise ResourceError('Another shared action requires explicit recovery')
        value=retry_documents.hydrate(value,path)
        if value.get('mode')!=MODE:raise ResourceError('Another resource retry requires explicit recovery')
        values.append(value)
    if not values:return None
    first=values[0]
    fields=('mode','token','group','generation','target_roles','original_intents','original_resources','original_receipts','closure','inputs','identities','desktop_hdr_panel_binding')
    if any(any(value.get(k)!=first.get(k) for k in fields) or value.get('phase') not in PHASES for value in values):
        raise ResourceError('HDR writeahead copies differ')
    return max(values,key=lambda v:PHASES.index(v['phase']))

def prepare(intent,mounts,pool,refs,binding):
    """Called under the actual role lease and only before any resource write."""
    from .resource_reinstall import pending_preflight,engines
    from .desktop_hdr_panel import validate_binding
    from .desktop_hdr_resources import check_legacy,preflight,check_restore
    if ID not in intent['selection']['components'] or pool.resource(ID).get('hdr_selected_profile') not in ('oxp3','x2-mini'):
        raise ResourceError('Panel binding requires a selected desktop HDR profile')
    session,_=lifecycle._session(mounts,intent,readonly=True)
    preflight(session.pool,[ID],session.slots)
    receipt=lifecycle._receipt(intent,ID)
    bound=session._bound_receipt(receipt['id'])
    if bound!=receipt or not any(row['component']==ID for row in bound['transactions']):
        raise ResourceError('Desktop HDR retained installation receipt differs')
    check_restore(session,bound)
    proof=session.verify([ID])
    if not proof or any(not row['ok'] for row in proof):
        raise ResourceError('Desktop HDR retained installation files differ')
    check_legacy(mounts.home,[ID],roots=mounts.roots.values(),verified_owned_roots=True)
    for engine in engines(session).values():pending_preflight(engine,lambda j:False)
    original={};states={}
    for slot,root in mounts.roots.items():
        old=firstboot.checked_intent(root)
        if old.get('desktop_hdr_panel_binding') or old.get('slot')!=slot or any(old.get(k)!=intent.get(k) for k in ('generation','selection','target_roles')):
            raise ResourceError('HDR original A/B intent differs')
        for cid in ('core:maintenance','core:resource-firstboot'):
            if not Engine(root,read_only=True).verify_readonly(cid)['ok']:raise ResourceError('HDR requires intact retained runtime and bootstrap')
        state=firstboot.read_private(root/firstboot.STATE) if (root/firstboot.STATE).exists() else firstboot._new_state(old)
        if any(state.get(k)!=old.get(k) for k in ('generation','slot','root_partuuid')):raise ResourceError('HDR original slot state differs')
        original[slot]=old;states[slot]={ID:copy.deepcopy(state['resources'].get(ID,{}))}
    return dict(kind='BoilerResourceRetryAction',schema_version=1,mode=MODE,token=uuid.uuid4().hex,group=uuid.uuid4().hex,
        phase='prepared',status='reinstalling',generation=intent['generation'],target_roles=copy.deepcopy(intent['target_roles']),
        identities=[ID],closure=[ID],inputs=[],original_intents=original,original_resources=states,
        original_receipts={ID:copy.deepcopy(lifecycle._receipt(intent,ID))},jobs={},desktop_hdr_panel_binding=validate_binding(binding))

def ensure(root,intent):
    """Caller holds firstboot.operation_lock; all mutations use its fixed lease."""
    if Path(root)!=Path('/'):raise ResourceError('Live HDR workflow requires the actual root')
    if ID not in intent['selection']['components']:return intent
    with firstboot.open_pool(root,intent) as (pool,refs):
        if pool.resource(ID).get('hdr_selected_profile') not in ('oxp3','x2-mini'):return intent
    with lifecycle.TargetMounts(intent,readonly=True) as mounts:
        action=_pending(mounts)
        if intent.get('desktop_hdr_panel_binding') and action is None:return intent
        session,refs=lifecycle._session(mounts,intent,readonly=True)
        if ID not in intent['selection']['components'] or session.pool.resource(ID).get('hdr_selected_profile') not in ('oxp3','x2-mini'):
            if action:raise ResourceError('Pending HDR action is outside the selected profile')
            return intent
        if action is None:
            from .desktop_hdr_panel import probe_internal_edp, make_binding
            from .adoption_blobs import _target_account
            uid,gid=_target_account(session.shared, mounts.home/'deck')
            resource=session.pool.resource(ID)
            binding=make_binding(resource['hdr_selected_profile'],probe_internal_edp(),
                generation=intent['generation'],account=dict(name='deck',uid=uid,gid=gid,home='/home/deck'),
                bundle_sha256=resource['desktop_bundle_sha256'])
            game_binding=intent.get('hdr_panel_binding')
            if binding['profile']=='x2-mini' and game_binding:
                from .hdr_live_panel import validate_binding as validate_game_binding
                game_binding=validate_game_binding(game_binding)
                if game_binding['edid']!=binding['edid'] or game_binding['connector'].split('-',1)[1]!=binding['connector']:
                    raise ResourceError('Game and desktop HDR panel bindings differ')
            action=prepare(intent,mounts,session.pool,refs,binding)
        lifecycle._retry_preflight(intent,action,mounts)
        recover_window=bool(mounts.pending_window())
    def record(value):
        state=firstboot.status()
        state['maintenance_retry']=retry_documents.compact(value,Path('/')/firstboot.STATE)
        _json_write(Path('/')/firstboot.STATE,state)
    record(action)
    from .live_root import write_scope
    with write_scope(Engine(Path('/'))):
        with lifecycle.TargetMounts(intent,readonly=False,recover_window=recover_window) as mounts:
            lifecycle._continue_retry(intent,action,mounts,record)
    return firstboot.checked_intent(Path('/'))
