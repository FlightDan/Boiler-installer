"""Verify the full OXP3 provider against its own reviewed code closure."""
import importlib.util
import json
import sys
from pathlib import Path

if __package__:
    from .oxp3_efi_provider import _verify
else:
    spec=importlib.util.spec_from_file_location('boiler_fixed_efi_provider_common',Path(__file__).with_name('oxp3_efi_provider.py'))
    common=importlib.util.module_from_spec(spec);spec.loader.exec_module(common)
    _verify=common._verify


def verify(package,lock_file):
    managed_root=str(Path(__file__).resolve().parents[1])
    if not sys.path or sys.path[0]!=managed_root: sys.path.insert(0,managed_root)
    from protocol.model_catalog import document
    package=Path(package); manifest_path=package/'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file(): raise ValueError('Missing full OXP3 manifest')
    manifest=document(manifest_path.read_bytes())
    if (manifest.get('id')!='device:oxp3' or manifest.get('oxp3_full_efi_contract') !=
            {'kind':'oxp3-full-split-lock-efi-v1','component':'oxp3:legacy-adaptation'}):
        raise ValueError('Fixed full OXP3 provider requires the OXP3 package contract')
    result=_verify(package,lock_file,'FixedFullOxp3EfiProviderSourceLock')
    names={row['path'] for row in json.loads(Path(lock_file).read_bytes())['files']}
    directory=package/'payload/legacy/maintenance/app/oxp3_maintenance'
    if directory.is_symlink() or not directory.is_dir(): raise ValueError('Unsafe OXP3 maintenance import directory')
    for path in directory.rglob('*'):
        if path.is_symlink(): raise ValueError('Linked OXP3 maintenance import')
        if path.is_file() and path.suffix in ('.py','.pyc','.pyd','.so'):
            if path.suffix!='.py' or path.relative_to(package).as_posix() not in names:
                raise ValueError('Unexpected OXP3 maintenance importable module: '+str(path))
    return result
