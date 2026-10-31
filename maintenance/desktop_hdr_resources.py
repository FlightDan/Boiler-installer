"""Fixed desktop HDR adapter; legacy detection is read-only."""
from pathlib import Path
import copy
from .resource_runtime import ResourceError

IDENTITY = 'resource:gamma22-hdr-desktop'
PACKAGES = {'kwin':'6.7.3-1.6','qt6-base':'6.11.1-1','qt6-declarative':'6.11.1-3'}

def preflight(pool, selected, slots):
    """Check each retained target package DB before desktop resource writes."""
    if IDENTITY not in selected:return
    if set(slots)!={'A','B'}:raise ResourceError('Desktop HDR installation requires both target slots')
    from .resource_activation import _package
    for slot,entry in slots.items():
        root=Path(entry['root'])
        for name,version in PACKAGES.items():
            if _package(root,name)!=version:
                raise ResourceError('Desktop HDR target '+slot+' package ABI mismatch: '+name+' '+version+' required')

def check_legacy(shared_home, selected, roots=(), *, verified_owned_roots=False):
    if IDENTITY not in selected: return
    home = Path(shared_home)
    paths = [home/'deck/.config/systemd/user/plasma-kwin_wayland.service.d/90-oxp3-gamma22.conf',
             home/'deck/.local/lib/oxp3-desktop-hdr-runtime/launch.sh',
             home/'deck/.local/share/applications/oxp3-mapped-hdr.desktop']
    if not verified_owned_roots:
        paths.extend(Path(root)/'opt/oxp3-desktop-gamma22' for root in roots)
    for path in paths:
        if path.exists() or path.is_symlink():
            raise ResourceError('Existing legacy desktop HDR path requires explicit review: ' + str(path))

def check_restore(session, receipt):
    from protocol.manifest import load_json
    from .resource_runtime import component
    session._desktop_opt_proof()
    if not isinstance(receipt,dict) or not receipt.get('id'):
        raise ResourceError('Retained desktop HDR resource group required')
    group=session._bound_receipt(receipt['id'])
    for row in group['transactions']:
        if row['component'] != IDENTITY: continue
        engine=session.shared if row['scope']=='shared-home' else session.engines[row['scope']]
        journal=load_json(engine._journal(row['transaction']))
        actual=copy.deepcopy(journal['component']);actual.pop('resource_group',None)
        expected=component(session.pool.resource(IDENTITY),'shared-home' if row['scope']=='shared-home' else 'root-slots',shared_opt=session.shared_opt)
        from .core_retention import is_resource,decorate
        if is_resource(actual):expected=decorate(expected,shared=(row['scope']=='shared-home'))
        for dependency in session.pool.resource(IDENTITY).get('dependencies',[]):
            expected['requires'].append(dependency)
        if actual!=expected:
            raise ResourceError('Desktop restore differs from authorized retained profile and bundle')
