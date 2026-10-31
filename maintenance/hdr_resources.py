"""Explicit authenticated-model adapter to the existing resource transactions.

Caller retains the existing source/pool authentication, physical-target lease,
trusted facts and mount lifetime. This adapter cannot create those authorities.
"""
import copy
from pathlib import Path
from maintenance.resource_runtime import ResourcePool, ResourceSession, ResourceError, maintenance_action, installer_install
from protocol.hdr_contract import KIND, IDENTITY, PROFILES, render, validate_contract

def bind(pool, references, selected):
    rows = []; refs = copy.deepcopy(references)
    if len({r['resource'] for r in refs}) != len(refs):
        raise ResourceError('Duplicate model resource reference')
    by_id = {r['resource']: r for r in refs}
    originals=pool.catalog['resources']
    if isinstance(originals,dict): originals=originals.values()
    for original in originals:
        row = copy.deepcopy(original.get('record', original))
        if row.get('id')=='resource:gamma22-hdr-desktop':
            from protocol.desktop_hdr_contract import validate_contract as validate_desktop,render as render_desktop
            validate_desktop(row)
            if row['id'] in selected and row['id'] in by_id:
                row=render_desktop(row,by_id[row['id']].get('parameters'))
                by_id[row['id']]['parameters']={}
        if row.get('id') == IDENTITY: validate_contract(row)
        if row.get('install',{}).get('kind') == KIND and row['id'] in selected:
            if row['id'] not in by_id:
                # Unselected/unreferenced HDR does not grant access to a profile.
                continue
            params = by_id[row['id']].get('parameters')
            row = render(row, params)
            by_id[row['id']]['parameters'] = {}
        rows.append(row)
    effective = copy.deepcopy(pool.catalog); effective['resources'] = rows
    return pool.derived(effective), refs

def preflight(pool, references, selected, slots):
    # Validate without directories, journals, locks or recover. This extra
    # unknown-selector gate must precede all writable resource entry paths.
    effective, refs = bind(pool, references, selected)
    if 'resource:gamma22-hdr-desktop' in selected:
        from .desktop_hdr_resources import preflight as desktop_preflight
        desktop_preflight(effective,selected,slots)
    from maintenance.resource_runtime import resolve
    for resource in resolve(effective, refs, selected):
        if resource['id'] == IDENTITY and resource.get('hdr_selected_profile'):
            profile = PROFILES[resource['hdr_selected_profile']]
            if profile['model'] is None or profile['vendor'] is None or profile['timing'] is None:
                raise ResourceError('HDR panel policy not established: Gamescope model/timing evidence is unavailable')
            from maintenance.resource_runtime import check_hardware
            for slot in slots.values(): check_hardware(resource, slot['hardware'])
    return effective, refs

def check_legacy(shared_home,selected):
    legacy=Path(shared_home)/'deck/.config/gamescope/scripts/98-oxp3-oled-hdr.lua'
    if IDENTITY in selected and (legacy.exists() or legacy.is_symlink()):
        raise ResourceError('Existing legacy HDR override requires explicit adoption/removal')

def install(pool, references, selected, *, slots, shared_home):
    effective, refs = preflight(pool, references, selected, slots)
    check_legacy(shared_home,selected)
    if 'resource:gamma22-hdr-desktop' in selected:
        from .desktop_hdr_resources import check_legacy as check_desktop_legacy
        check_desktop_legacy(shared_home,selected,roots=[slot['root'] for slot in slots.values()])
    return installer_install(effective, refs, selected, slots=slots, shared_home=shared_home)

def check_restore(session, receipt):
    """Read-only profile authorization shared by API and reviewed CLI plans."""
    resources=session.pool.catalog['resources']
    identities=set(resources) if isinstance(resources,dict) else {r['id'] for r in resources}
    if IDENTITY in identities:
        from protocol.manifest import load_json
        from maintenance.resource_runtime import component
        if not isinstance(receipt,dict) or not receipt.get('id'):
            raise ResourceError('Retained HDR resource group required')
        group=session._bound_receipt(receipt['id'])
        for row in group['transactions']:
            if row['component'] != IDENTITY: continue
            engine=session.shared if row['scope']=='shared-home' else session.engines[row['scope']]
            journal=load_json(engine._journal(row['transaction']))
            actual=copy.deepcopy(journal['component']);actual.pop('resource_group',None)
            expected=component(session.pool.resource(IDENTITY),'shared-home' if row['scope']=='shared-home' else 'root-slots',shared_opt=session.shared_opt)
            # Restore the durable historical generation, not the current desired
            # version/hash. Its fixed profile and exact owned path contract must
            # still match the authenticated selection.
            projection=lambda value:[{k:r[k] for k in ('source','target','mode')} for r in value['files']]
            if (actual.get('hdr_profile') != expected.get('hdr_profile') or actual.get('hdr_panel_binding')!=expected.get('hdr_panel_binding') or projection(actual)!=projection(expected)
                    or actual.get('requires') != [] or actual.get('conflicts') != [] or actual.get('hooks')):
                raise ResourceError('Restore profile differs from authorized retained manifest')

def action(pool, references, selected, *, slots, shared_home, action, receipt=None, scope=None):
    # Verify/recovery bind the authorized profile to the durable manifest. They
    # need no running compositor; apply alone requires fresh runtime facts.
    if action == 'apply': return install(pool,references,selected,slots=slots,shared_home=shared_home)
    else: effective, refs = bind(pool, references, selected)
    if action == 'restore' and IDENTITY in selected:
        check_restore(ResourceSession(effective,slots,shared_home,read_only=True),receipt)
    if action == 'restore' and 'resource:gamma22-hdr-desktop' in selected:
        from .desktop_hdr_resources import check_restore as check_desktop_restore
        check_desktop_restore(ResourceSession(effective,slots,shared_home,read_only=True),receipt)
    return maintenance_action(effective, refs, selected, slots=slots, shared_home=shared_home,
        action=action, receipt=receipt, scope=scope)


def bind_live_intent(pool,references,selected,binding):
    # Fixed trusted helper; no module name from the pool is imported.
    from .hdr_live_panel import bind_live_intent as bind_live
    return bind_live(pool,references,selected,binding)
