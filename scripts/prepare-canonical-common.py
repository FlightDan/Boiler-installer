"""Prepare an unsigned common-device source and payload-input stage.

Consumes only the pinned public source archive and explicit, project-locked bytes.
This is not a deployable pool or a device compatibility decision.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.resource_pool import decoder, local
from scripts.resources import xevma_proof

COMMIT = 'fb102eb212c08a714ab7bb8e5de0a09b6df067f6'
ARCHIVE_SHA = 'c6431ee949d5ba81c9522d1e3584d5c4a538c74465fa1f7c0f2cb9fb8218cc0d'
PREFIXES = ('common/gpu/intel/xe/xevmafix-724/', 'common/network/intel/be201/',
            'common/audio/realtek/alc245/speaker-route/', 'common/sensors/bosch/bmi260/gyro/',
            'common/ssd/acer/gm7/')
RECORDS = ('common-device-source-index.json', 'brands/onexplayer/oxp3/common-device-sources.toon',
           'brands/onexplayer/x2-mini/common-device-sources.toon',
           'records/migrations/common-device-resource-ownership-20261103.json',
           'records/migrations/common-bmi260-inputplumber-20261103.json')
LOCK = 'adaptations/resource-build-inputs.json'
GYRO_ASSET = dict(size=555684389, sha256='d8bee8bad14f4cd136c814ebcaa3299fa59202b6cdc6d26ec9c52af0e055f4d4')
GYRO_MEMBERS = {
    'cache/integration/gyro/000.bin': (426384, '94db735bde8e6d310b5056fb8faf6fac58d06fa54cf3a4b908273384f22b2e52'),
    'cache/integration/gyro/001.bin': (10727640, '1107d95c34863c7865cac64183673135098d77265a32763c22fc7efbc1893aa3'),
    'cache/integration/sources/gyro/inputplumber-0.78.0-oxp3-gyro.patch': (31057, 'e1fd31a17aaacb4bb35b1158dae4471f0ea74f21861ef581ac9985bba9d1bc2d'),
    'cache/integration/sources/gyro/InputPlumber-LICENSE': (35151, '7fe14716ed31cc71fc3bf9af293c368d464a853a70ea86e9995388f6b9e929e5'),
}


def sha(data): return hashlib.sha256(data).hexdigest()


def source_archive(path):
    raw = Path(path).read_bytes()
    if sha(raw) != ARCHIVE_SHA: raise ValueError('Canonical public archive SHA-256 differs')
    import io
    archive = zipfile.ZipFile(io.BytesIO(raw))
    names = [i.filename for i in archive.infolist() if not i.is_dir()]
    if len(names) != len(set(names)): raise ValueError('Duplicate archive member')
    for name in names:
        parts = PurePosixPath(name).parts
        if not parts or name.startswith('/') or '\\' in name or ':' in name or any(p in ('.', '..') for p in parts):
            raise ValueError('Unsafe archive member')
    return archive, set(names)


def locked_input(project, rows, name):
    if name not in rows: raise ValueError('Required input is not locked: '+name)
    raw = local(project, name).read_bytes(); row = rows[name]
    if len(raw) != row['size'] or sha(raw) != row['sha256']:
        raise ValueError('Locked input differs: '+name)
    return raw


def gyro_public_members(project, archive_path):
    # Reuse the bridge's authenticated held-handle archive reader. It checks the
    # fixed asset before and after reading and never discovers another checkout.
    bridge = local(project, 'adaptations/brands/onexplayer/oxp3/bridge/scripts/build.py')
    spec = importlib.util.spec_from_file_location('_canonical_gyro_bridge', bridge)
    implementation = importlib.util.module_from_spec(spec); spec.loader.exec_module(implementation)
    result = {}
    with implementation.public_input(Path(archive_path), GYRO_ASSET) as stream:
        with zipfile.ZipFile(stream) as archive:
            for name, (size, digest) in GYRO_MEMBERS.items():
                matches = [member for member in archive.infolist() if member.filename == name]
                if len(matches) != 1 or matches[0].is_dir() or matches[0].file_size != size:
                    raise ValueError('Public gyro archive member missing or differs: '+name)
                data = archive.read(matches[0])
                if len(data) != size or sha(data) != digest:
                    raise ValueError('Public gyro archive member hash differs: '+name)
                result[name] = data
    return result


def validate_gyro_sources(contents, gyro):
    comparisons = {
        PREFIXES[3]+'sources/inputplumber-0.78.0-oxp3-gyro.patch':
            'cache/integration/sources/gyro/inputplumber-0.78.0-oxp3-gyro.patch',
        PREFIXES[3]+'licenses/InputPlumber-LICENSE':
            'cache/integration/sources/gyro/InputPlumber-LICENSE',
    }
    for canonical, public in comparisons.items():
        if canonical not in contents or public not in gyro or contents[canonical] != gyro[public]:
            raise ValueError('Canonical BMI source differs from authenticated public input: '+canonical)


def prepare(project, archive_path, gyro_archive_path, output):
    project, output = Path(project), Path(output)
    if os.path.lexists(output): raise FileExistsError('Use a fresh output directory')
    archive, names = source_archive(archive_path)
    index = json.loads(archive.read(RECORDS[0]))
    toon = decoder(project/'adaptations')
    lock_raw = local(project, LOCK).read_bytes(); lock = json.loads(lock_raw)
    if lock.get('kind') != 'BoilerProjectResourceBuildInputs' or lock.get('public_baseline') != '72f864e0498c10366493c74608c5ffed62cc99ca':
        raise ValueError('Unexpected project input lock')
    rows = {r['path']: r for r in lock['files']}
    if len(rows) != len(lock['files']): raise ValueError('Duplicate project input')
    members = {n for n in names if n.startswith(PREFIXES)} | set(RECORDS)
    if not members <= names: raise ValueError('Canonical source records missing')
    contents = {n: archive.read(n) for n in members}
    expected_ids = {'resource:intel-xevmafix-724', 'resource:intel-be201-firmware', 'resource:oxp3-speaker-route', 'resource:bmi260-gyro'}
    if {r['id'] for r in index['resources']} != expected_ids or len(index['resources']) != 4:
        raise ValueError('Canonical resource set differs')
    source_values = {}
    for row in index['resources']:
        name = row['source']; raw = contents[name]
        if sha(raw) != row['source_sha256']: raise ValueError('Canonical resource record differs')
        value = toon.loads(raw.decode('utf-8'), strict=True)
        if value.get('id') != row['id'] or value.get('source_only') is not True: raise ValueError('Invalid source record')
        base = str(PurePosixPath(name).parent) + '/'
        for item in value['files']:
            if item['availability'] == 'checked-in':
                data = contents[base+item['path']]
                if len(data) != item['size'] or sha(data) != item['sha256']: raise ValueError('Canonical source member differs')
        for name_license in value['licenses']:
            if not contents[base+name_license]: raise ValueError('Empty canonical license')
        source_values[row['id']] = value
    for model in index['models']:
        raw = contents[model['source']]
        if sha(raw) != model['source_sha256']: raise ValueError('Canonical model reference differs')
        value = toon.loads(raw.decode('utf-8'), strict=True)
        if value['model_id'] != model['model_id'] or {r['id'] for r in value['resources']} != expected_ids:
            raise ValueError('Canonical model references differ')
    if {m['model_id'] for m in index['models']} != {'device:oxp3', 'device:x2-mini'}: raise ValueError('Canonical model set differs')
    if sha(contents[index['boot_policy']['source']]) != index['boot_policy']['source_sha256']: raise ValueError('GM7 policy differs')
    # Independently check the retained public rebuild evidence and original source locks.
    xevma_proof.verified_rebuild(project)
    module = locked_input(project, rows, lock['xevma_module'])
    module_lock = json.loads(contents[PREFIXES[0]+'sources/module-lock.json'])['module']
    if len(module) != module_lock['size'] or sha(module) != module_lock['sha256'] or ('vermagic='+module_lock['vermagic']).encode() not in module:
        raise ValueError('xe module differs from canonical module lock')
    firmware = {}
    for item in source_values['resource:intel-be201-firmware']['files']:
        name = PurePosixPath(item['path']).name
        matches = [p for p in rows if p.startswith('adaptations/common/resource-inputs/intel-be201/inputs/') and PurePosixPath(p).name[4:] == name]
        if len(matches) != 1: raise ValueError('Missing unique BE201 input: '+name)
        data = locked_input(project, rows, matches[0])
        if len(data) != item['size'] or sha(data) != item['sha256']: raise ValueError('BE201 canonical input differs: '+name)
        firmware[name] = data
    if len(firmware) != 9: raise ValueError('Expected nine BE201 firmware inputs')
    gyro = gyro_public_members(project, gyro_archive_path)
    validate_gyro_sources(contents, gyro)
    # Source snapshot and candidate payload bytes only; no generated runtime manifest.
    staged = {'source/'+name: data for name, data in contents.items()}
    staged['payload-inputs/xevma/xevmafix.ko'] = module
    staged['payload-inputs/xevma/load.py'] = contents[PREFIXES[0]+'final/load.py']
    staged['payload-inputs/alc245/speaker_route.py'] = contents[PREFIXES[2]+'final/speaker_route.py']
    staged.update({'payload-inputs/be201/'+name: data for name, data in firmware.items()})
    staged.update({'payload-inputs/bmi260/'+name.removeprefix('cache/integration/'): data for name, data in gyro.items()})
    inventory = [dict(path=n, size=len(data), sha256=sha(data)) for n, data in sorted(staged.items())]
    proof = dict(kind='CanonicalCommonPreparation', source_repository='FlightDan/Boiler-devices', source_commit=COMMIT,
        source_archive_sha256=ARCHIVE_SHA, project_input_lock=LOCK, project_input_lock_sha256=sha(lock_raw),
        gyro_public_input=dict(repository='FlightDan/OneXPlayer3-SteamOS', commit='e181a9055e1c94e8d758e2b9575f60fb5fc5109c',
            release_tag='v1.0.6+installer.20261022.220437', asset='oxp3-steamos-installer-1.0.6-20261022-220437.zip',
            size=GYRO_ASSET['size'], sha256=GYRO_ASSET['sha256'], members=[dict(path=n,size=v[0],sha256=v[1]) for n,v in GYRO_MEMBERS.items()]),
        official_release=False, deployable=False, files=inventory,
        blocked=['BMI260 requires implementation and validation of its exact kernel, device identity, InputPlumber and recovery contract',
                 'Runtime integration, actual device identity and physical acceptance remain pending'])
    output.mkdir(parents=True)
    for name, data in staged.items():
        target = local(output, name); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    (output/'preparation.json').write_text(json.dumps(proof, indent=2)+'\n', encoding='utf-8')
    return proof


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', required=True); p.add_argument('--gyro-public-archive', required=True)
    p.add_argument('--output', required=True); p.add_argument('--project', default=str(ROOT))
    args = p.parse_args()
    proof = prepare(args.project, args.archive, args.gyro_public_archive, args.output)
    print(json.dumps({k: v for k, v in proof.items() if k != 'files'}, indent=2))


if __name__ == '__main__': main()
