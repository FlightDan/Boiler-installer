"""Validate an explicitly anchored native fingerprint runtime input.

This checks input integrity and recorded evidence, not release authorization or
physical-device compatibility. It writes no output and executes no target code.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

TARGET = {'os_id': 'steamos', 'os_version': '3.10', 'architecture': 'x86_64'}
REQUIRED = {'libexec/fprintd', *(f'bin/fprintd-{x}' for x in ('verify', 'enroll', 'list', 'delete'))}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def member(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Invalid runtime member')
    parts = name.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('Invalid runtime member')
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('Linked runtime member')
    if not path.is_file():
        raise ValueError('Missing runtime member: ' + name)
    return path


def validate(directory, manifest_sha256):
    root = Path(directory).absolute()
    if root.is_symlink() or getattr(root, 'is_junction', lambda: False)():
        raise ValueError('Linked runtime root')
    if not isinstance(manifest_sha256, str) or not re.fullmatch('[0-9a-f]{64}', manifest_sha256):
        raise ValueError('Externally anchored manifest SHA-256 required')
    raw = member(root, 'runtime-manifest.json').read_bytes()
    if sha(raw) != manifest_sha256:
        raise ValueError('Native manifest differs from explicit anchor')
    manifest = json.loads(raw)
    if (manifest.get('schema_version') != 1 or manifest.get('kind') != 'BoilerFingerprintNativeRuntime'
            or manifest.get('target') != TARGET or not isinstance(manifest.get('source_provenance'), dict)
            or not manifest['source_provenance']):
        raise ValueError('Invalid native runtime manifest')
    driver = manifest['source_provenance'].get('driver')
    if (not isinstance(driver, dict) or driver.get('repository') != 'pythontyphon/libfprint-bf63112-oxp3'
            or driver.get('commit') != '6a19d67d2a072dac4b6a467434966c55af76b602'
            or not isinstance(driver.get('archive_sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', driver['archive_sha256'])):
        raise ValueError('Explicit pinned BF63112 source provenance required')
    rows = manifest.get('files')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Missing native runtime files')
    seen, elf = set(), set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'size', 'sha256', 'mode'}:
            raise ValueError('Invalid native inventory row')
        name = row['path']
        if ((name.startswith(('bin/', 'libexec/')) and name not in REQUIRED)
                or (name.startswith('lib/') and name.count('/') != 1)
                or (name.startswith('config/') and name not in {
                    'config/net.reactivated.Fprint.conf', 'config/net.reactivated.fprint.device.policy'})):
            raise ValueError('Unsupported native layout: ' + name)
        if name in seen or not (name.startswith(('bin/', 'lib/', 'libexec/', 'licenses/', 'config/')) or name == 'closure-proof.json'):
            raise ValueError('Unexpected or duplicate native member')
        seen.add(name)
        data = member(root, name).read_bytes()
        if (type(row['size']) is not int or row['size'] != len(data) or not data
                or row['sha256'] != sha(data) or type(row['mode']) is not int or row['mode'] not in (0o644, 0o755)):
            raise ValueError('Native member changed: ' + name)
        if data.startswith(b'\x7fELF'):
            if len(data) < 20 or data[4:6] != b'\x02\x01' or int.from_bytes(data[18:20], 'little') != 62:
                raise ValueError('Unsupported ELF: ' + name)
            elf.add(name)
    if (not REQUIRED <= elf or not {'config/net.reactivated.Fprint.conf', 'config/net.reactivated.fprint.device.policy'} <= seen
            or not any(x.startswith('lib/libfprint') for x in elf) or not any(x.startswith('lib/libgusb') for x in elf)):
        raise ValueError('Native fingerprint runtime is incomplete')
    if not any(x.startswith('licenses/') for x in seen):
        raise ValueError('Native license inventory missing')
    proof_ref = manifest.get('closure_proof')
    if not isinstance(proof_ref, dict) or set(proof_ref) != {'path', 'sha256'} or proof_ref['path'] != 'closure-proof.json' or proof_ref['path'] not in seen:
        raise ValueError('Closure proof reference missing')
    proof_raw = member(root, proof_ref['path']).read_bytes()
    if sha(proof_raw) != proof_ref['sha256']:
        raise ValueError('Closure proof changed')
    proof = json.loads(proof_raw)
    if (proof.get('schema_version') != 1 or proof.get('kind') != 'BoilerFingerprintClosureProof'
            or proof.get('target') != TARGET or proof.get('inspection') != 'target-ldd'
            or proof.get('library_resolution') != 'packaged-runpath-and-system-defaults'
            or proof.get('unresolved') != [] or not isinstance(proof.get('executables'), list)
            or set(proof['executables']) != elf or len(proof['executables']) != len(elf)):
        raise ValueError('Target dependency closure proof incomplete')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual != seen | {'runtime-manifest.json'}:
        raise ValueError('Uninventoried native runtime files')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    result = validate(args.runtime, args.manifest_sha256)
    print(json.dumps({'valid': True, 'files': len(result['files']), 'hardware_accepted': False}))
