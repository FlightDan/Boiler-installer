"""Build an unsigned model candidate without the retired OXP3 desktop tool."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from components import oxp3_full_efi_provider, x2_efi_provider
from maintenance.updates import _version_key
from protocol import validate_package
from protocol.capability_gates import apply_gates, assert_gates
from protocol.local import import_local
from protocol.manifest import confined


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def archive_candidate(package, output, manifest, anchor):
    archive = output / ('boiler-' + manifest['id'].removeprefix('device:') + '-' + manifest['version'] + '.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for name in ['manifest.json', *(row['path'] for row in manifest['files'])]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with confined(package, name).open('rb') as source, bundle.open(info, 'w', force_zip64=True) as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
    verified = import_local(archive, output / 'verified-import', developer=True)
    if verified['manifest_sha256'] != anchor:
        raise ValueError('Final archive manifest differs from the verified package')
    with archive.open('rb') as stream:
        archive_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(name=archive.name, size=archive.stat().st_size, sha256=archive_sha256)


def canonical_transform(checkout, revision):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(checkout), *args], stderr=subprocess.PIPE)

    if (not re.fullmatch(r'[0-9a-f]{40}', revision)
            or git('remote', 'get-url', 'origin').decode().strip() != 'https://github.com/FlightDan/Boiler-adaptations.git'):
        raise ValueError('An explicit exact-revision canonical adaptation checkout is required')
    kind = git('cat-file', '-t', revision).decode().strip()
    if kind not in ('commit', 'tree'):
        raise ValueError('Expected a canonical commit or local staged source tree')
    inventory = json.loads(git('show', revision + ':model-source-files.json'))['files']
    locked = {row['path']: row for row in inventory}
    derivative = 'brands/onexplayer/oxp3/bridge/embedded-maintenance-tool.py'
    if kind == 'tree':
        derivative = 'brands/onexplayer/oxp3/scripts/maintenance_tool.py'
    for name in ('scripts/derive-legacy-maintenance.py', derivative):
        raw = confined(checkout, name).read_bytes()
        if (raw != git('show', revision + ':' + name)
                or digest(raw) != locked[name]['sha256'] or len(raw) != locked[name]['size']):
            raise ValueError('Canonical transform member differs from the committed inventory: ' + name)
    path = confined(checkout, 'scripts/derive-legacy-maintenance.py')
    spec = importlib.util.spec_from_file_location('boiler_derive_legacy_maintenance', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, kind


def build(base, base_sha256, output, version, checkout, revision, *, release=False):
    base, output, checkout = map(Path, (base, output, checkout))
    if output.exists() or any(path.is_symlink() or getattr(path, 'is_junction', lambda: False)()
                              for path in (output, *output.parents)):
        raise ValueError('Use a fresh unlinked output directory')
    if any(output.resolve().is_relative_to(path.resolve()) for path in (base, checkout)):
        raise ValueError('Keep output outside the package and canonical source')
    manifest = validate_package(base, expected_manifest_sha256=base_sha256)
    if _version_key(version) <= _version_key(manifest['version']):
        raise ValueError('Candidate version must advance')
    if manifest['id'] == 'device:oxp3':
        lock = ROOT / 'components/oxp3_full_efi_provider.lock.json'
        verify = oxp3_full_efi_provider.verify
    elif manifest['id'] == 'device:x2-mini':
        lock = x2_efi_provider.lock_for(manifest, ROOT / 'components/x2_efi_provider.lock.json')
        verify = x2_efi_provider.verify
    else:
        raise ValueError('Only the reviewed OXP3 and X2 Mini packages can be derived')
    original_lock_sha256 = verify(base, lock)
    transform, source_kind = canonical_transform(checkout, revision)
    if release and (source_kind != 'commit' or manifest['id'] != 'device:x2-mini'
                    or lock.name != 'x2_efi_provider.hdr1100.lock.json'):
        raise ValueError('Release requires the reviewed HDR1100 base and an exact canonical commit')
    changed, removed = transform.derive_package(manifest, lambda name: confined(base, name).read_bytes())
    original = {row['path']: row for row in manifest['files']}
    if (set(changed) | removed) - original.keys():
        raise ValueError('Transform references an undeclared package member')
    package = output / 'package'
    package.mkdir(parents=True)
    rows = {}
    for name, old in original.items():
        if name in removed:
            continue
        target = confined(package, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if name in changed:
            raw = changed[name]
            target.write_bytes(raw)
            rows[name] = dict(path=name, size=len(raw), sha256=digest(raw))
        else:
            shutil.copyfile(confined(base, name), target)
            rows[name] = old
    manifest['files'] = [rows[name] for name in sorted(rows)]
    manifest['version'] = version
    for component in manifest['components']:
        for file in component.get('files', []):
            row = rows[file['source']]
            file.update(size=row['size'], sha256=row['sha256'])
        if component['id'] == 'oxp3:legacy-adaptation':
            component['version'] = version
    # A derived candidate must not inherit a selectable gated handler from its
    # base. The shared table owns that decision; this stage records the evidence.
    manifest, _ = apply_gates(manifest, None)
    contract = {'kind': 'boiler-embedded-recovery-v1', 'source_revision': revision, 'source_kind': source_kind}
    manifest['legacy_recovery_contract'] = contract
    if release:
        prior_source = manifest['source']
        manifest['hdr_overlay_sha256'] = prior_source['hdr_overlay_sha256']
        manifest['bridge_overlay_sha256'] = prior_source['bridge_overlay_sha256']
        manifest['source'] = {'kind': 'github', 'repository': 'FlightDan/Boiler-adaptations', 'commit': revision}
        manifest['trust'] = {'status': 'index-signature-required', 'official_release': False}
        manifest['minimum_engine_version'] = '0.1.5'
        for component in manifest['components']:
            if component['id'] == 'oxp3:fingerprint-unlock':
                component['minimum_engine_version'] = '0.1.5'
    raw_manifest = encoded(manifest)
    (package / 'manifest.json').write_bytes(raw_manifest)
    anchor = digest(raw_manifest)
    validate_package(package, expected_manifest_sha256=anchor)
    assert_gates(manifest, 'the derived recovery candidate')
    new_lock = json.loads(lock.read_bytes())
    new_lock['provenance']['embedded_recovery'] = contract
    new_lock['provenance']['base_provider_lock_sha256'] = original_lock_sha256
    new_lock['provenance']['base_package_manifest_sha256'] = base_sha256
    new_lock['files'] = [rows[row['path']] for row in new_lock['files'] if row['path'] not in removed]
    suffix = '.release.recovery.lock.json' if release else '.recovery.lock.json'
    derived_lock = output / lock.name.replace('.lock.json', suffix)
    derived_lock.write_bytes(encoded(new_lock))
    verify(package, derived_lock)
    report = dict(id=manifest['id'], version=version, manifest_sha256=anchor, removed=sorted(removed),
                  provider_lock=derived_lock.name, archive=archive_candidate(package, output, manifest, anchor),
                  official_release=False, linux_acceptance=False)
    (output / 'verification.json').write_bytes(encoded(report))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--base-manifest-sha256', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--source-checkout', required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--release', action='store_true', help='Prepare an unsigned candidate for the official device index')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.base_manifest_sha256, args.output, args.version,
                           args.source_checkout, args.source_revision, release=args.release), ensure_ascii=False))
