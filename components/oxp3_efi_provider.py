"""Check the fixed OXP3 bridge against the installer-owned source allowlist.

This check complements, and never substitutes for, package authentication by
the outer installer. The expected hashes come from this project's generated
reviewed-source lock, not from the package being checked.
"""
import hashlib
import json
import sys
from pathlib import Path


def verify(package, lock_file):
    return _verify(package, lock_file, 'FixedOxp3EfiProviderSourceLock')


def _recovery_lock(manifest, lock_file):
    lock_file = Path(lock_file)
    contract = manifest.get('legacy_recovery_contract')
    if contract is None:
        return lock_file
    if not isinstance(contract, dict) or contract.get('kind') != 'boiler-embedded-recovery-v1':
        raise ValueError('Invalid embedded recovery provider contract')
    if not lock_file.name.endswith('.recovery.lock.json'):
        suffix = '.release.recovery.lock.json' if manifest.get('source', {}).get('kind') == 'github' else '.recovery.lock.json'
        lock_file = lock_file.with_name(lock_file.name.replace('.lock.json', suffix))
    if lock_file.is_symlink() or not lock_file.is_file():
        raise ValueError('The embedded recovery provider lock is missing')
    if json.loads(lock_file.read_bytes())['provenance'].get('embedded_recovery') != contract:
        raise ValueError('The embedded recovery provider contract differs from its installed lock')
    return lock_file


def _verify(package, lock_file, kind):
    package, lock_file = Path(package), Path(lock_file)
    if package.is_symlink() or not package.is_dir() or lock_file.is_symlink() or not lock_file.is_file():
        raise ValueError('Missing fixed OXP3 provider source or trusted lock')
    lock = json.loads(lock_file.read_bytes())
    if (lock.get('schema') != 1 or lock.get('kind') != kind
            or lock.get('public_commit') != '72f864e0498c10366493c74608c5ffed62cc99ca'
            or not isinstance(lock.get('files'), list)):
        raise ValueError('Invalid fixed OXP3 provider lock')
    names = set()
    for row in lock['files']:
        name = row.get('path')
        if not isinstance(name, str) or not name or name.startswith('/') or '\\' in name or any(p in ('', '.', '..') for p in name.split('/')) or name in names:
            raise ValueError('Invalid fixed provider source path')
        names.add(name)
        current = package
        for part in name.split('/'):
            current /= part
            if current.is_symlink(): raise ValueError('Fixed provider source traverses a link')
        if not current.is_file() or current.stat().st_size != row.get('size'):
            raise ValueError('Fixed provider source is missing or differs: ' + name)
        if hashlib.sha256(current.read_bytes()).hexdigest() != row.get('sha256'):
            raise ValueError('Fixed provider source differs: ' + name)
    components = package / 'payload/legacy/components'
    expected_dirs = {'autoflatpaks-compat', 'controls', 'desktop-hdr', 'gyro', 'hdr', 'i18n', 'light',
                     'opti2xmx', 'partition-mount', 'refind', 'slot-switch', 'split-lock', 'tabmaster-compat',
                     'waydroid', 'windows-reboot'}
    for path in components.iterdir():
        if path.is_symlink() or path.is_dir() and path.name not in expected_dirs:
            raise ValueError('Unexpected provider import path: ' + str(path))
        if path.suffix in ('.so', '.pyd', '.pyc'):
            raise ValueError('Unexpected provider executable module: ' + str(path))
    for directory in (components, components/'hdr'):
        if directory.is_symlink() or not directory.is_dir(): raise ValueError('Unsafe fixed provider module directory')
        for path in directory.iterdir():
            if path.is_symlink() or path.suffix in ('.so', '.pyd', '.pyc'):
                raise ValueError('Unsafe provider importable module: ' + str(path))
            if path.suffix == '.py' and not path.name.startswith('test_'):
                if path.is_symlink() or path.relative_to(package).as_posix() not in names:
                    raise ValueError('Unexpected provider importable module: ' + str(path))
    adoption_dir = package / 'payload/legacy/maintenance/app/oxp3_maintenance'
    if adoption_dir.is_symlink() or not adoption_dir.is_dir():
        raise ValueError('Unsafe fixed provider decoder directory')
    # The decoder has only standard-library imports and is loaded by exact path.
    # Its neighboring legacy maintenance modules are never an import search root.
    if any(Path(entry or '.').resolve() == adoption_dir.resolve() for entry in sys.path):
        raise ValueError('Unsafe provider decoder import search path')
    return hashlib.sha256(lock_file.read_bytes()).hexdigest()
