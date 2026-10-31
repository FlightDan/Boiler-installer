"""Firstboot adapter to the one existing shared-home keyboard ledger."""
from pathlib import Path
from . import keyboard_resource as bridge
from .resource_runtime import ResourceError


def _intent(root, identity, selection):
    from .resource_firstboot import checked_intent
    value=checked_intent(root)
    if identity != bridge.IDENTITY or identity not in selection or set(selection) != set(value['selection']['components']):
        raise ResourceError('Keyboard selection differs from the authenticated intent')
    return value


def activate(identity, *, root=Path('/'), pool, facts, selection, record_before_write, approve):
    intent=_intent(root,identity,selection)
    reviewed=bridge.prepare(pool,intent,'apply',root=root)
    recovery=dict(kind='BoilerKeyboardRecovery',generation=intent['generation'],slot=intent['slot'],
                  review_sha256=reviewed['review_sha256'],status='prepared')
    record_before_write(recovery)
    approve(identity)
    result=bridge.activate(pool,intent,reviewed,reviewed_sha256=reviewed['review_sha256'],root=root)
    return dict(status='configured',runtime='configuration-applied',session_activation='not-validated',
                recovery=recovery,keyboard=result,hardware_accepted=False)


def verify(identity, *, root=Path('/'), pool, facts, selection, state=None):
    intent=_intent(root,identity,selection)
    if not state or state.get('generation')!=intent['generation'] or state.get('slot')!=intent['slot']:
        raise ResourceError('Keyboard activation ownership differs from this slot')
    result=bridge.verify(pool,intent,root=root)
    if not result.get('installed'): raise ResourceError('Keyboard setting is no longer managed in this slot')
    return dict(status='configured',runtime='user-edited-preserved' if result.get('preserved') else 'configuration-applied',session_activation='not-validated',keyboard_verify=result,
                user_edits_preserved=bool(result.get('preserved')),hardware_accepted=False)


def restore(identity, *, root=Path('/'), state, record_before_write=None):
    from .resource_firstboot import checked_intent,open_pool
    intent=checked_intent(root)
    if identity!=bridge.IDENTITY or state.get('generation')!=intent['generation'] or state.get('slot')!=intent['slot']:
        raise ResourceError('Keyboard recovery belongs to another slot or generation')
    with open_pool(root,intent) as (pool,references):
        reviewed=bridge.prepare(pool,intent,'restore',root=root)
        if record_before_write is not None:
            record_before_write(dict(kind='BoilerKeyboardRestore',generation=intent['generation'],
                                     slot=intent['slot'],review_sha256=reviewed['review_sha256']))
        result=bridge.restore(pool,intent,reviewed,reviewed_sha256=reviewed['review_sha256'],root=root)
    return dict(status='restored',keyboard=result,reboot_required=False)


def reconcile(pool, intent, *, root=Path('/')):
    """Explicit retry reconciles the canonical Store, never a second ledger."""
    service=bridge.Keyboard(root)
    options=service.options()
    if options.get('lifecycle_reason'):
        raise ResourceError(options['lifecycle_reason'])
    recovered=None
    if options['pending']:
        reviewed=bridge.prepare(pool,intent,'recover',root=root)
        recovered=bridge.retry(pool,intent,reviewed,reviewed_sha256=reviewed['review_sha256'],root=root)
    try:
        current=bridge.verify(pool,intent,root=root)
    except FileNotFoundError:
        current={'installed':False,'ok':False,'preserved':[]}
    return dict(recovered=recovered,verification=current)
