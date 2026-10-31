"""Bind the retained module to an independently executed public source rebuild."""
import hashlib
import json
from pathlib import Path

from protocol.resource_pool import local

ROOT = 'adaptations/common/resource-inputs/intel-xevma-724/'
SOURCE = 'adaptations/brands/onexplayer/oxp3/current/xevma-20261029/'
RECIPE = ROOT+'xevma-rebuild-inputs.json'
PROOF = ROOT+'xevma-rebuild-proof.json'
SCRIPT = 'scripts/rebuild-xevma.py'
LOG = ROOT+'xevma-build.log'
COMMIT = 'e72e814da1944da3792076689995b6fe961f5768'


def sha(raw): return hashlib.sha256(raw).hexdigest()


def checked(project, name, row):
    raw = local(project, name).read_bytes()
    if len(raw) != row['size'] or sha(raw) != row['sha256']:
        raise ValueError('Xevma rebuild input differs: '+name)
    if row.get('git_blob') and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() != row['git_blob']:
        raise ValueError('Xevma source Git blob differs: '+name)
    return raw


def verified_recipe(project):
    raw = local(project, RECIPE).read_bytes(); recipe = json.loads(raw)
    if (recipe.get('schema_version') != 1 or recipe.get('kind') != 'XevmaRebuildInputs'
            or recipe.get('source', {}).get('repository') != 'HHHHanasak1/onexplayer3-steamos-setup'
            or recipe.get('source', {}).get('commit') != COMMIT):
        raise ValueError('Unexpected xevma public rebuild recipe')
    module_raw = checked(project, SOURCE+'module-lock.json', recipe['module_lock']); module_lock = json.loads(module_raw)
    if (recipe['kernel'] != module_lock['kernel'] or recipe['source'] != module_lock['source']
            or recipe['expected_module'] != module_lock['module']
            or recipe['build_environment'] != {k: module_lock['build'][k] for k in ('KBUILD_BUILD_TIMESTAMP', 'KBUILD_BUILD_USER', 'KBUILD_BUILD_HOST')}):
        raise ValueError('Xevma rebuild recipe differs from the retained module lock')
    rows = recipe['sources']
    if len(rows) != 4 or {r['local_file'] for r in rows} != {'xevmafix.c', 'Makefile', 'LICENSE', 'UPSTREAM_LICENSE'}:
        raise ValueError('Xevma rebuild requires the four original source/license members')
    for row in rows:
        name = row['local_file']
        if Path(name).name != name or not row['url'].startswith('https://raw.githubusercontent.com/'):
            raise ValueError('Unexpected xevma public source identity')
        if row['url'] != 'https://raw.githubusercontent.com/'+row['repository']+'/'+row['commit']+'/'+row['path']:
            raise ValueError('Xevma public source URL differs')
        checked(project, SOURCE+name, row)
    expected = [dict(module_lock['official_inputs']['headers_package'], local_file='headers.pkg.tar.zst'), *module_lock['toolchain']['packages']]
    if len(expected) != 60 or recipe['packages'] != expected:
        raise ValueError('Xevma recipe must retain the exact official headers and 59 toolchain inputs')
    for row in expected:
        if Path(row['local_file']).name != row['local_file'] or not row['url'].startswith('https://steamdeck-packages.steamos.cloud/'):
            raise ValueError('Unexpected official xevma package identity')
    return raw, recipe


def verified_rebuild(project):
    recipe_raw, recipe = verified_recipe(project)
    raw = local(project, PROOF).read_bytes(); proof = json.loads(raw)
    if proof.get('schema_version') != 1 or proof.get('kind') != 'IndependentXevmaRebuildProof':
        raise ValueError('Unexpected independent xevma rebuild proof')
    for name, expected in dict(source_binary_correspondence_verified=True, fresh_source_build=True,
            old_module_input=False, public_recipe_executed=True, module_loaded=False,
            hardware_accepted=False, production_release_ready=False).items():
        if proof.get(name) is not expected: raise ValueError('Xevma rebuild proof scope differs: '+name)
    for key, name in (('script', SCRIPT), ('recipe', RECIPE), ('build_log', LOG)):
        row = proof[key]
        if row.get('path') != name: raise ValueError('Xevma proof member path differs')
        checked(project, name, row)
    for name in ('source', 'sources', 'kernel', 'build_environment'):
        if proof[name] != recipe[name]: raise ValueError('Xevma source-build proof differs from recipe: '+name)
    if proof['package_count'] != len(recipe['packages']): raise ValueError('Xevma proof package count differs')
    execution = proof['execution']
    for name, expected in dict(guest_network=False, writable_host_disks=False, host_inputs_read_only=True,
            apk_signatures_verified=True).items():
        if execution.get(name) is not expected: raise ValueError('Xevma build isolation proof differs')
    if execution['guest_exit'] != 0 or execution['qemu_exit'] != 0: raise ValueError('Xevma independent source build did not succeed')
    artifact = proof['artifact']; expected = recipe['expected_module']
    if (any(artifact[key] != expected[key] for key in ('size', 'sha256', 'vermagic', 'has_exit_handler'))
            or artifact['elf64'] is not True or artifact['machine'] != 62 or artifact['module_license'] != 'GPL'):
        raise ValueError('Xevma independent artifact differs from the exact retained module')
    module = checked(project, ROOT+'xevmafix.ko', expected)
    if (not module.startswith(b'\x7fELF\x02') or int.from_bytes(module[18:20], 'little') != 62
            or ('vermagic='+expected['vermagic']).encode() not in module or b'license=GPL\0' not in module):
        raise ValueError('Xevma retained module ELF/vermagic/license differs')
    return raw, proof, recipe_raw
