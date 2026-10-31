"""Fixed resource bridge to the existing keyboard transaction ledger.

The caller authenticates the pool and resolves the model selection before
calling this module. No catalog field can provide an executable or path.
The legacy component identity is deliberately retained inside the Store.
"""
from pathlib import Path

from .resource_runtime import ResourceError
from .keyboard_runtime.service import Keyboard, TARGET
from protocol.keyboard_resource_contract import IDENTITY, INSTALL, VERIFY, RESTORE, validate_contract

def validate(resource):
    """Reject even relocked data which attempts to supply behavior or paths."""
    try:
        return validate_contract(resource)
    except ValueError as error:
        raise ResourceError(str(error)) from error


def authorize(pool, intent):
    """Require the reviewed installation selection and exact fixed contract.

    The caller must first check intent against the live partition, authenticate
    the pool, and verify this resource is a reference of the chosen model.
    """
    selection = intent.get('selection', {})
    components = selection.get('components')
    if (not isinstance(components, list) or components.count(IDENTITY) != 1
            or intent.get('slot') not in ('A', 'B')):
        raise ResourceError('Keyboard resource is not in the reviewed installation')
    validate(pool.resource(IDENTITY))
    return intent['slot']


def plan(pool, intent, action, *, root=Path('/')):
    slot = authorize(pool, intent)
    return Keyboard(root).plan(action, slot)


def execute(pool, intent, reviewed, *, reviewed_sha256, root=Path('/')):
    slot = authorize(pool, intent)
    if reviewed.get('target_binding', {}).get('label') != slot:
        raise ResourceError('Keyboard reviewed plan targets another slot')
    return Keyboard(root).execute(reviewed, reviewed_sha256=reviewed_sha256)


def verify_readonly(pool, intent, *, root=Path('/')):
    slot = authorize(pool, intent)
    return Keyboard(root).verify_readonly(slot)


def prepare(pool, intent, action, *, root=Path('/')):
    return plan(pool, intent, action, root=root)


def _execute_action(pool, intent, action, reviewed, reviewed_sha256, root):
    if reviewed.get('action') != action:
        raise ResourceError('Keyboard reviewed action does not match request')
    return execute(pool, intent, reviewed, reviewed_sha256=reviewed_sha256, root=root)


def activate(pool, intent, reviewed, *, reviewed_sha256, root=Path('/')):
    return _execute_action(pool, intent, 'apply', reviewed, reviewed_sha256, root)


def verify(pool, intent, *, root=Path('/')):
    return verify_readonly(pool, intent, root=root)


def restore(pool, intent, reviewed, *, reviewed_sha256, root=Path('/')):
    return _execute_action(pool, intent, 'restore', reviewed, reviewed_sha256, root)


def retry(pool, intent, reviewed, *, reviewed_sha256, root=Path('/')):
    # Recovery is explicit and bound to the slot where the interruption began;
    # the underlying Store enforces that authority before touching its journal.
    return _execute_action(pool, intent, 'recover', reviewed, reviewed_sha256, root)
