"""Verify the independently pinned X2 fixed-EFI provider code closure."""
import json
import importlib.util
import sys
from pathlib import Path
if __package__:
    from .oxp3_efi_provider import _recovery_lock, _verify
else:
    # The model adapter loads this module by filename, without adding the
    # components directory to its import search path.
    spec = importlib.util.spec_from_file_location(
        'boiler_fixed_efi_provider_common', Path(__file__).with_name('oxp3_efi_provider.py'))
    common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(common)
    _verify = common._verify
    _recovery_lock = common._recovery_lock


def lock_for(manifest, lock_file):
    lock_file = Path(lock_file)
    if lock_file.name == 'x2_efi_provider.lock.json':
        candidate = lock_file.with_name('x2_efi_provider.hdr1100.lock.json')
        if candidate.is_file() and not candidate.is_symlink():
            expected = json.loads(candidate.read_bytes())['provenance']['hdr_overlay_sha256']
            overlay = (manifest.get('source', {}).get('hdr_overlay_sha256')
                       or manifest.get('hdr_overlay_sha256'))
            if overlay == expected:
                lock_file = candidate
    return _recovery_lock(manifest, lock_file)


def verify(package, lock_file):
    # The installer adapter loads this file before placing the managed payload
    # root on sys.path. The installed maintenance runtime has the same layout.
    managed_root = str(Path(__file__).resolve().parents[1])
    if not sys.path or sys.path[0] != managed_root:
        sys.path.insert(0, managed_root)
    from protocol.model_catalog import document
    package = Path(package)
    manifest_path = package/'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('Missing X2 package manifest')
    manifest = document(manifest_path.read_bytes())
    if (manifest.get('id') != 'device:x2-mini'
            or manifest.get('x2_efi_contract') != {'kind':'x2-split-lock-efi-v1','component':'oxp3:legacy-adaptation'}):
        raise ValueError('Fixed X2 provider requires the X2 package contract')
    lock_file = lock_for(manifest, lock_file)
    result = _verify(package, lock_file, 'FixedX2EfiProviderSourceLock')
    names = {row['path'] for row in json.loads(Path(lock_file).read_bytes())['files']}
    directory = package/'payload/legacy/maintenance/app/oxp3_maintenance'
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Unsafe X2 maintenance import directory')
    for path in directory.rglob('*'):
        if path.is_symlink(): raise ValueError('Linked X2 maintenance import')
        if path.is_file() and path.suffix in ('.py', '.pyc', '.pyd', '.so'):
            if path.suffix != '.py' or path.relative_to(package).as_posix() not in names:
                raise ValueError('Unexpected X2 maintenance importable module: '+str(path))
    return result
