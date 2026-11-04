"""Candidate shared entrypoints for components.integration and maintenance.models.

The caller supplies an already probed/mounted A/B target set under its existing
disk lease. Never build that set from JSON model parameters or options.
"""
from pathlib import Path
try:
    from .resource_runtime import ResourcePool, ResourceError, installer_install, maintenance_action
except ImportError:  # Standalone isolated candidate harness.
    from resource_runtime import ResourcePool, ResourceError, installer_install, maintenance_action


def model_resource_pool(root, identity, path, *, trust_local_test=False, expected_manifest_sha256=None):
    """Load a supported fixed handler from the authenticated model reference."""
    if identity == 'resource:intel-be201-firmware' and path == 'common/network/intel/be201/resource.toon':
        from .be201_model_resource import model_resource_pool as load
        return load(root, trust_local_test=trust_local_test, expected_manifest_sha256=expected_manifest_sha256)
    raise ResourceError('Unsupported model resource: ' + identity)


def model_references(pool, model_id):
    matches = [row for row in pool.catalog['models'] if row['id'] == model_id]
    if len(matches) != 1:
        raise ResourceError('A unique resource model is required')
    return matches[0]['resources']


def stage_resources(payload, options, *, slots, shared_home, trust_local_test=False):
    """Batch installer phase after core:maintenance retention, before final verify.

    payload is the guest integration directory. Packaging materializes the
    top-level resource pool here once. Does not read a device package.
    """
    if options.get('device_package') or options.get('device_components'):
        raise ResourceError('Resource selection cannot execute an old model package')
    if 'core:maintenance' not in options.get('plugins', []):
        raise ResourceError('Resource installation requires retained core:maintenance')
    pool = ResourcePool.for_local_test(Path(payload) / 'resource-pool', trust_local_test=trust_local_test)
    references = model_references(pool, options['resource_model'])
    from .hdr_resources import install
    return install(pool, references, options['resource_components'], slots=slots, shared_home=shared_home)


def resource_action(runtime_root, model_id, selected, *, action, slots, shared_home,
                    trust_local_test=False, receipt=None, scope=None):
    """Offline maintenance consumes the same retained pool and fixed engine."""
    pool = ResourcePool.for_local_test(Path(runtime_root) / 'cache/resource-pool', trust_local_test=trust_local_test)
    from .hdr_resources import action as hdr_action
    return hdr_action(pool, model_references(pool, model_id), selected,
        slots=slots, shared_home=shared_home, action=action, receipt=receipt, scope=scope)


def prepare_cache_only_stage(payload, model_id, selected, *, slots, shared_home, trust_local_test=False):
    """Separate fresh staging purpose; never returns an install authorization."""
    from .resource_staging import preflight_cache_stage
    pool=ResourcePool.for_local_test(Path(payload)/'integration/resource-pool',trust_local_test=trust_local_test)
    return preflight_cache_stage(payload,pool,model_id,selected,slots=slots,shared_home=shared_home)


def commit_cache_only_stage(plan, *, authorize=None):
    from .resource_staging import stage_cache
    return stage_cache(plan,authorize=authorize)


def verify_cache_only_stage(receipt_path, *, slots, shared_home):
    """Retained maintenance checks the current core/cache binding, read-only."""
    from .resource_staging import inspect_cache_stage
    return inspect_cache_stage(receipt_path,slots=slots,shared_home=shared_home)
