"""Explicit local-session import of a resource pool; never execute its contents."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile

from .local import MAX_ARCHIVE, _copy_file, _safe_path
from .manifest import ProtocolError, safe_extract_archive
from .resource_pool import inventory, local, validate_pool


def import_resource_pool(source, destination, *, trust_local_test=False):
    if trust_local_test is not True:
        raise ProtocolError('Explicit local-session resource trust is required')
    source, destination = _safe_path(source), _safe_path(destination)
    if not source.exists() or os.path.lexists(destination):
        raise ProtocolError('Missing source or existing resource destination')
    if source.is_dir() and (destination == source or source in destination.parents):
        raise ProtocolError('Resource destination cannot be inside its source')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.boiler-resource-import-', dir=destination.parent) as temporary:
        temporary = Path(temporary)
        if source.is_dir():
            root = source
        else:
            if not source.name.lower().endswith(('.zip', '.tar', '.tar.gz', '.tgz', '.tar.xz', '.txz', '.tar.bz2', '.tbz2')):
                raise ProtocolError('Use a resource folder or supported archive')
            snapshot = temporary/'archive'
            _copy_file(source, snapshot, MAX_ARCHIVE)
            root = safe_extract_archive(snapshot, temporary/'extracted')
            children = list(root.iterdir())
            if not (root/'catalog.toon').is_file() and len(children) == 1 and children[0].is_dir():
                root = children[0]
        original = local(root, 'catalog.toon').read_bytes()
        anchor = hashlib.sha256(original).hexdigest()
        validate_pool(root, expected_catalog_sha256=anchor)
        entries = inventory(root)
        ready = temporary/'verified'
        ready.mkdir()
        for entry in entries:
            path = ready/entry['path']; path.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(root/entry['path'], path, entry['size'])
        result = validate_pool(ready, expected_catalog_sha256=anchor)
        if os.path.lexists(destination):
            raise ProtocolError('Resource destination already exists')
        _safe_path(destination)
        os.rename(ready, destination)
    return dict(kind='LocalResourcePoolPreview', directory=str(destination), catalog_sha256=anchor,
                resource_count=len(result['resources']), model_count=len(result['models']),
                trust='local-session-test', deployment_available=False)
