"""Preflight explicitly anchored fingerprint inputs without touching a target.

No installable component is emitted: service integration and hardware acceptance
remain separate gates. Missing or altered inputs fail before creating output.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
# Bind the provenance checker to this exact authority source tree, even if
# another copy was previously imported by the host process.
provenance_spec = importlib.util.spec_from_file_location('fingerprint_provenance_local', Path(__file__).with_name('fingerprint_provenance.py'))
fingerprint_provenance = importlib.util.module_from_spec(provenance_spec)
provenance_spec.loader.exec_module(fingerprint_provenance)

SOURCE_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fingerprint_native', Path(__file__).with_name('validate-fingerprint-native.py'))
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


def prepare(runtime, native_sha256, source_sha256, output):
    output = Path(output).absolute()
    if output.exists():
        raise ValueError('Fresh output directory required')
    lock_path = SOURCE_ROOT / 'fingerprint-source-lock.json'
    raw = lock_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source_sha256:
        raise ValueError('Fingerprint sources differ from explicit anchor')
    lock = json.loads(raw)
    if lock.get('kind') != 'BoilerFingerprintBridgeSources' or lock.get('schema_version') != 1:
        raise ValueError('Invalid fingerprint source lock')
    source_root = SOURCE_ROOT
    fingerprint_provenance.validate(source_root, lock, native.member)
    upstream_raw = native.member(source_root, lock['upstream_lock']).read_bytes()
    if native.sha(upstream_raw) != lock.get('upstream_lock_sha256'):
        raise ValueError('Fingerprint upstream lock changed')
    upstream = json.loads(upstream_raw)
    if (upstream.get('repository') != 'podothecat/decky-fingerprint-unlock'
            or upstream.get('commit') != 'd5b077c3cfad098909a2849529a27aff55e5354f'):
        raise ValueError('Unexpected fingerprint upstream identity')
    upstream_root = source_root / 'common/fingerprint/upstream'
    seen = set()
    for row in upstream['files']:
        if row['path'] in seen:
            raise ValueError('Duplicate fingerprint upstream source')
        seen.add(row['path'])
        data = native.member(source_root, 'common/fingerprint/upstream/' + row['path']).read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()
        if len(data) != row['size'] or native.sha(data) != row['sha256'] or blob != row['git_blob_sha1']:
            raise ValueError('Fingerprint upstream source changed: ' + row['path'])
    actual = set()
    for directory, dirs, files in os.walk(upstream_root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise ValueError('Linked fingerprint upstream source')
        actual.update((Path(directory)/name).relative_to(upstream_root).as_posix() for name in files)
    if actual != seen:
        raise ValueError('Uninventoried fingerprint upstream source')
    sources = []
    for row in lock['files']:
        source = native.member(source_root, row['path'])
        data = source.read_bytes()
        if len(data) != row['size'] or native.sha(data) != row['sha256']:
            raise ValueError('Fingerprint source changed: ' + row['path'])
        sources.append((row['path'], data))
    manifest = native.validate(runtime, native_sha256)
    # Validation is deliberately complete before creating any output.
    output.mkdir(parents=True)
    for name, data in sources:
        target = output / 'sources' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for row in manifest['files']:
        target = output / 'native' / row['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(native.member(Path(runtime), row['path']), target)
    (output / 'native/runtime-manifest.json').write_bytes((Path(runtime)/'runtime-manifest.json').read_bytes())
    report = {'schema_version': 1, 'kind': 'BoilerFingerprintPreparedInputs',
              'native_manifest_sha256': native_sha256, 'source_lock_sha256': source_sha256,
              'installable': False, 'hardware_accepted': False,
              'remaining': ['service-policy-review', 'model-package-integration', 'physical-device-validation']}
    (output / 'preparation.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--native-sha256', required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.runtime, args.native_sha256, args.source_sha256, args.output), indent=2))
