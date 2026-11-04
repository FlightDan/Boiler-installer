"""Resolve the fixed BE201 resource from an authenticated model package.

The caller must supply the host's package authenticator for production.
Local testing is an explicit separate mode. This module does not authorize
hardware or open targets; the existing resource runtime owns those operations.
"""
from pathlib import Path
import hashlib

from maintenance.resource_runtime import ResourceError, ResourcePool
from protocol.firmware_contract import IDENTITY, KIND, validate_contract
from protocol.manifest import confined, validate_package
from protocol.resource_pool import decoder

RESOURCE = 'common/network/intel/be201/resource.toon'
DESCRIPTOR = {'kind': KIND, 'resource': RESOURCE}


def model_resource_pool(root, *, authenticate_package=None, trust_local_test=False, expected_manifest_sha256=None):
    """Return the existing pool interface for a model-authorized fixed resource."""
    if authenticate_package is None and trust_local_test is not True:
        raise ResourceError('A verified index or explicit local-test trust is required')
    if authenticate_package is not None and trust_local_test is True:
        raise ResourceError('Production and local-test trust cannot be combined')
    root = Path(root)
    # Production authentication is owned by the same host verifier that
    # authenticates the signed index and package, never by package data.
    package = (authenticate_package(root) if authenticate_package is not None
               else validate_package(root, allow_unsigned=True, expected_manifest_sha256=expected_manifest_sha256))
    if package.get('be201_firmware') != DESCRIPTOR and not any(row['id'] == IDENTITY and row['path'] == RESOURCE for row in package.get('resources', [])):
        raise ResourceError('Model package does not authorize the fixed BE201 resource')
    inventory = {item['path']: item for item in package['files']}
    if RESOURCE not in inventory:
        raise ResourceError('BE201 resource is absent from the package inventory')
    raw = confined(root, RESOURCE).read_bytes()
    entry = inventory[RESOURCE]
    if len(raw) != entry['size'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ResourceError('BE201 resource differs from the package inventory')
    record = decoder().loads(raw.decode('utf-8'), strict=True)
    validate_contract(record)
    for item in record['payload']['files']:
        actual = inventory.get(item['path'])
        if actual is None or any(actual[key] != item[key] for key in ('size', 'sha256')):
            raise ResourceError('BE201 payload differs from the package inventory')
    if any(path not in inventory for path in record.get('license_files', [])):
        raise ResourceError('BE201 license is absent from the package inventory')
    catalog = {'resources': [{'id': IDENTITY, 'record': record}]}
    return ResourcePool(root, catalog, local_test=trust_local_test is True)
