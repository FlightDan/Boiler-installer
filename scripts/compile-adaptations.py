"""Explicit SDK-bound source compiler; output remains an unsigned candidate."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from protocol.resource_pool import decoder,local,require,sha
from resources.source_fragment import build


def verified_sdk(manifest_path, expected_sha256):
    manifest_path=Path(manifest_path).absolute()
    require(manifest_path==ROOT/'sdk-source-contract.toon','Manifest must belong to the executing SDK')
    raw=manifest_path.read_bytes()
    require(isinstance(expected_sha256,str) and len(expected_sha256)==64 and sha(raw)==expected_sha256,'SDK manifest identity mismatch')
    toon=decoder(ROOT/'adaptations');doc=toon.loads(raw.decode('utf-8'),strict=True)
    require(doc.get('schema_version')==1 and doc.get('kind')=='BoilerAdaptationsSdkContract' and doc.get('compiler')=='source-authority-preview-v1','Unsupported SDK contract')
    require(doc.get('capabilities')==['identity-v1', 'toon-json-v1', 'locked-archive-member-v1', 'gamma22-shared-v1', 'fixed-public-cpu-v1', 'fixed-boot-settings-v1', 'fixed-input-r5-v1', 'fixed-public-cpu-live-v1', 'fixed-ec-profile-v1', 'fixed-kde-keyboard-v1', 'fixed-flathub-mirror-v1', 'fixed-telemetry-overlay-v1', 'fixed-desktop-hdr-v1', 'fixed-opti2xmx-v1'],'Unsupported SDK capabilities')
    seen=set()
    for row in doc['files']:
        require(set(row)=={'path','size','sha256'} and row['path'] not in seen,'Invalid SDK inventory');seen.add(row['path'])
        data=local(ROOT,row['path']).read_bytes();require(len(data)==row['size'] and sha(data)==row['sha256'],'SDK identity mismatch: '+row['path'])
    required={'scripts/resources/desktop_hdr.py','protocol/desktop_hdr_contract.py','scripts/compile-adaptations.py','scripts/resources/source_fragment.py','scripts/resources/source_envelope.py','scripts/resources/model_source.py','scripts/resources/additional.py','scripts/resources/rapl.py','scripts/resources/hdr_game.py','scripts/prepare-cpu-resource.py','scripts/prepare-input-resource.py','protocol/resource_pool.py','protocol/cpu_contract.py','protocol/firmware_contract.py','protocol/hdr_contract.py','protocol/input_live_contract.py','maintenance/boot_resources.py','maintenance/input_ownership.py','protocol/cpu_live_contract.py','protocol/ec_profile_contract.py','maintenance/cpu_live_resources.py','maintenance/cpu_live_probe.py','maintenance/cpu_sidecar.py','maintenance/cpu_sidecar_ledger.py','maintenance/cpu_sidecar_store.py','maintenance/cpu_sidecar_recovery.py','maintenance/cpu_live_activation.py','maintenance/fan_live_config.py','maintenance/fan_live_activation.py','maintenance/fan_service_guard.py','maintenance/ec_profile.py','maintenance/ec_initramfs.py','maintenance/ec_profile_activation.py','maintenance/ec_readonly_probe.py','guest/resource_firstboot_install.py','tests/test_cpu_live_contract.py','tests/test_cpu_engine_live.py','tests/test_cpu_sidecar_store.py','tests/test_fan_live_config.py','tests/test_fan_service_guard.py','tests/test_ec_profile_contract.py','adaptations/toon-parser-lock.json'}
    require(required<=seen,'SDK contract omits required compiler inputs')
    return dict(root=ROOT,manifest=manifest_path,sha256=expected_sha256,capabilities=doc['capabilities'])


def compile(source_root, output, *, sdk, desktop_archive=None):
    require(isinstance(sdk,dict) and sdk.get('root')==ROOT,'Verified SDK identity is required')
    verified=verified_sdk(sdk['manifest'],sdk['sha256'])
    toon=decoder(ROOT/'adaptations');repo=toon.loads(local(source_root,'repository.toon').read_text(encoding='utf-8'),strict=True)
    require(repo.get('sdk')==dict(schema_version=1,compiler='source-authority-preview-v1',manifest_sha256=verified['sha256'],capabilities=verified['capabilities']),'Source repository SDK contract mismatch')
    return build(source_root,output,sdk_identity=verified,desktop_archive=desktop_archive)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',required=True);parser.add_argument('--sdk-manifest',required=True);parser.add_argument('--sdk-sha256',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--desktop-native-archive')
    args=parser.parse_args();sdk=verified_sdk(args.sdk_manifest,args.sdk_sha256)
    result=compile(args.source_root,args.output,sdk=sdk,desktop_archive=args.desktop_native_archive)
    print(json.dumps(dict(resources=len(result['resources']),models=len(result['models']),sdk_sha256=sdk['sha256'],trust='unsigned-development')))


