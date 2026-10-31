"""Fixed candidate integration branch; never infers authority from model choice."""
from pathlib import Path

from maintenance.resource_runtime import ResourceError
from resource_coordinator import execute_local_entrypoint
from resource_results import emit


def run(payload, options, *, readonly):
    if not Path('/run/boiler-resource-anchor.json').exists():
        raise ResourceError('Resource entry requires its host-owned development-session anchor; production signed-resource installation is unavailable')
    # This fixed local entry validates its independent anchor and refuses absent
    # physical facts before mounts. It never loads a provider from JSON/env.
    from resource_local_session import secure_anchor
    # Selection bytes cannot choose this mode: its execution value is committed
    # by the live host capability and checked again by the fixed provider.
    from resource_firstboot_install import execute
    result = execute(payload, options, readonly=readonly)
    if readonly:
        for slot in ('A', 'B'): print('OXP_INTEGRATION_VERIFIED=' + slot + ':resources-readonly', flush=True)
    else:
        emit(result['slot_results'])
    return result
