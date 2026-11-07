"""Emit one slot's integration result without duplicating progress records.

Kept in its own module so the ordering contract can be unit-tested without
importing the top-level QEMU-only integration script.
"""
import json


def emit_slot(slot, result, include_result=True, include_plan=True, only=None):
    if include_result:
        print('OXP_INTEGRATION_RESULT=' + json.dumps(result), flush=True)
    if include_plan:
        print('BOILER_INSTALL_PLAN_JSON=' + json.dumps(result['install_plan']), flush=True)
    for name, value in result['components'].items():
        if only is not None and name != only:
            continue
        print('OXP_COMPONENT_RESULT=' + json.dumps(dict(slot=slot, id=name, **value)), flush=True)
