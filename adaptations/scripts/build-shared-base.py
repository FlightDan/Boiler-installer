"""Rebuild the historical OXP3 .3 base from explicit, authenticated public inputs.

The .2 package, public Opti 0.3.1 ZIP and three original OptiScaler archives
are required arguments. No cache, archive directory, sibling or private helper
is discovered. Plugin/guest code is never executed; this does not sign/deploy.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import types

ADAPTATIONS = Path(__file__).resolve().parents[1]
INPUT_LOCK = ADAPTATIONS/'records/oxp3-shared-base/opti031-inputs.json'
INPUT_LOCK_SHA256 = 'f8cf3f4007e1ff6f436b0b914bf10762fe3737c0ec013d2bacc36bcd222abfa6'
BRIDGE_SHA256 = '4bf4fac5f58ac6ba4ea4e6789c675e5c2b22c5226b849ddcbc8209c99b3c4e32'
BASE_SHA256 = '510342dec0bfe6ce39389f5e13cae0d504755aa4072d90b73641b4dd2d5aead9'
FINAL_SHA256 = 'c80efd9cce07d35c5f8828d17b5fab8c15fbc177a10476754cfac04db3f27485'
ARCHIVE_SHA256 = '46c1913e5ce935df0a766687f7ffd825ee556dc4f25a54721dbe0eb4ff944871'
BASE_VERSION = '0.1.0-localtest.20261030.2'
FINAL_VERSION = '0.1.0-localtest.20261030.3'
# Historical manifest provenance, never a directory to discover or recreate.
HISTORICAL_SOURCE_LABEL = 'Boiler-oxp3'
CPU_FILES = {
    'cpu-plugin-index.js': 'a1cd67a64fc69b4f279b17ffe8fc5948b37fb7f4a233a10745fa01698492fa44',
    'cpu-plugin-main.py': '36e6ceacda689a4856486c5fe2fd541094b4f603b8282b79886ad7519f30736b',
    'cpu_control.py': '7dabf1846c473fbd3a17f4453e835fd5116d74abe6d0e3aee528bc74dcaa4872',
    'deploy-cpu.py': '3120cfd39c794d5475ee400acb90cb000842269fef71388c31783221913e9352',
    'power_control.py': 'd5c5efd2f847d443bbff04dd30a3af847576d867eebcfb9b25c94d8e2b31654f',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_path(path):
    path = Path(path).absolute()
    require(not any(part.is_symlink() or getattr(part, 'is_junction', lambda: False)()
                    for part in (path, *path.parents)), 'Linked explicit input/output path')
    return path


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8')


def load_bridge(project_root):
    path = safe_path(project_root/'adaptations/brands/onexplayer/oxp3/bridge/scripts/build.py')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == BRIDGE_SHA256,
            'Public bridge source differs from its reviewed SHA-256')
    # Compile authenticated source bytes, never a preexisting .pyc or helper.
    module = types.ModuleType('oxp3_shared_base_public_bridge')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def mutable(name):
    return name.startswith(('payload/legacy/components/opti2xmx/',
        'payload/legacy/integration/sources/opti2xmx/',
        'payload/legacy/integration/opti2xmx/')) or name in (
        'payload/legacy/components/opti2xmx-release.json',
        'payload/legacy/components/optiscaler-release.json',
        'payload/legacy/integration/manifest.json')


def build(project_root, base, plugin_zip, archives, output, *, input_lock=INPUT_LOCK):
    project_root, base, plugin_zip, output, input_lock = map(safe_path,
        (project_root, base, plugin_zip, output, input_lock))
    archives = [safe_path(path) for path in archives]
    require(not output.exists(), 'Use a new output directory')
    require(not output.resolve(strict=False).is_relative_to(base.resolve(strict=False)), 'Output cannot be inside the input package')
    require(digest(input_lock) == INPUT_LOCK_SHA256, 'Reviewed public Opti 0.3.1 input lock differs')
    pins = json.loads(input_lock.read_bytes())
    require(pins['plugin_archive']['version'] == '0.3.1'
            and pins['plugin_release']['source_commit'] == 'c99638e3768ddd50326eade01cdd024031096c92',
            'Unexpected historical public plugin identity')
    sys.path.insert(0, str(project_root))
    from protocol.manifest import validate_package, confined
    from protocol.local import import_local
    original = validate_package(base, expected_manifest_sha256=BASE_SHA256)
    require(original['id'] == 'device:oxp3' and original['version'] == BASE_VERSION
            and len(original['files']) == 505
            and original['source']['path'] == HISTORICAL_SOURCE_LABEL,
            'Unexpected historical .2 package/provenance')
    for name, expected in CPU_FILES.items():
        require(digest(base/'payload/legacy/components/controls/cpu'/name) == expected,
                'CPU bytes differ from the immutable public 72f864e baseline')
    for row in original['files']:
        parts = row['path'].casefold().split('/')
        require(not set(parts).intersection(('.local', '.git', '.codex', '.agents', '.aws', 'research')),
                'Private/research source entered the historical package')
    bridge = load_bridge(project_root)
    records, offline = bridge.read_opti_inputs(plugin_zip, archives, pins)
    resources_before = json.loads((base/'payload/legacy/integration/manifest.json').read_bytes())
    output.mkdir(parents=True)
    package = output/'package'
    package.mkdir()
    # Only the authenticated, closed manifest inventory is copied.
    for row in original['files']:
        destination = confined(package, row['path'])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(confined(base, row['path']), destination)
    (package/'manifest.json').write_bytes((base/'manifest.json').read_bytes())
    validate_package(package, expected_manifest_sha256=BASE_SHA256)
    resources = copy.deepcopy(resources_before)
    update = bridge.apply_opti_update(package, resources, records, offline, pins)
    manifest = copy.deepcopy(original)
    manifest['version'] = FINAL_VERSION
    manifest['components'][0]['version'] = FINAL_VERSION
    manifest['payload_updates'] = [update]
    for capability in manifest['legacy_capabilities']:
        if capability['id'] == 'oxp3:opti2xmx':
            capability.update(payload_version='0.3.1',
                targets=[row['target'] for row in resources['opti2xmx']['files']],
                offline_optiscaler_versions=resources['opti2xmx']['offline_optiscaler_versions'])
    manifest['files'] = [dict(path=path.relative_to(package).as_posix(), size=path.stat().st_size,
        sha256=digest(path)) for path in sorted(package.rglob('*'))
        if path.is_file() and path != package/'manifest.json']
    before = {row['path']: row for row in original['files'] if not mutable(row['path'])}
    after = {row['path']: row for row in manifest['files'] if not mutable(row['path'])}
    require(before == after, 'A non-Opti file changed')
    require({k:v for k,v in resources_before.items() if k != 'opti2xmx'} ==
            {k:v for k,v in resources.items() if k != 'opti2xmx'}, 'A non-Opti legacy component changed')
    runtime = package/'payload/legacy/integration/opti2xmx/runtime.tar.gz'
    require(digest(runtime) == pins['plugin_release']['runtime']['sha256'], 'Published runtime changed')
    (package/'manifest.json').write_bytes(json_bytes(manifest))
    require(digest(package/'manifest.json') == FINAL_SHA256, 'Rebuilt .3 manifest differs from the historical anchor')
    validate_package(package, expected_manifest_sha256=FINAL_SHA256)
    archive = output/('boiler-device-oxp3-'+FINAL_VERSION+'.zip')
    bridge.deterministic_zip(package, archive)
    require(digest(archive) == ARCHIVE_SHA256 and archive.stat().st_size == 420343373,
            'Rebuilt .3 ZIP differs from the historical deterministic artifact')
    imported = import_local(archive, output/'verified-import', developer=True)
    require(imported['manifest_sha256'] == FINAL_SHA256, 'Final ZIP import differs')
    result = dict(kind='Oxp3SharedBaseSourceBuild', id=manifest['id'], version=FINAL_VERSION,
        directory=str(package), path=str(archive), manifest_sha256=FINAL_SHA256,
        size=archive.stat().st_size, sha256=ARCHIVE_SHA256, files=len(manifest['files']),
        base_manifest_sha256=BASE_SHA256, input_lock_sha256=INPUT_LOCK_SHA256,
        bridge_source_sha256=BRIDGE_SHA256, source_label=HISTORICAL_SOURCE_LABEL,
        plugin_version='0.3.1', plugin_source_commit=pins['plugin_release']['source_commit'],
        unchanged_other_components=True, unchanged_public_cpu=True, unchanged_runtime=True,
        safe_protocol_import=True, official_release=False, hardware_accepted=False,
        deployment_executed=False)
    (output/'verification.json').write_bytes(json_bytes(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--base', type=Path, required=True, help='Complete explicit .2 package with anchor 510342de...')
    parser.add_argument('--plugin-zip', type=Path, required=True, help='Public Opti2XMX 0.3.1 ZIP')
    parser.add_argument('--optiscaler-archive', type=Path, action='append', required=True,
                        help='Supply each of the three original locked archives')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.project_root, args.base, args.plugin_zip, args.optiscaler_archive, args.output), indent=2))
