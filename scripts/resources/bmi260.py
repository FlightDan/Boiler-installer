"""Prepare the reviewed BMI260 legacy logical files, without creating a package.

Inputs are explicit: the pinned canonical source preparation and the original
public installer archive.  The output is an unsigned build input, not a trust
decision or evidence of device compatibility.
"""
import argparse
import hashlib
import importlib.util
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

SEMANTIC_SHA = '324e5b86a7761f2e2b8eff515940c16692f6a07cc3566d35b35afaaa2a118565'
ARCHIVE_SHA = 'd8bee8bad14f4cd136c814ebcaa3299fa59202b6cdc6d26ec9c52af0e055f4d4'
ARCHIVE_SIZE = 555684389
COMMIT = 'fb102eb212c08a714ab7bb8e5de0a09b6df067f6'
COMMON = 'source/common/sensors/bosch/bmi260/gyro/'
PROFILE_TARGETS = {'007.bin': 'etc/inputplumber/devices.d/49-onexplayer_3-gyro.yaml',
                   '008.bin': 'etc/inputplumber/oxp3-gyro-steer.conf'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def validated_stage(stage):
    stage = Path(stage)
    proof_raw = (stage / 'preparation.json').read_bytes()
    proof = json.loads(proof_raw)
    if proof.get('kind')=='CanonicalCommonSnapshotPreparation':
        project=Path(__file__).resolve().parents[2]
        if __package__:
            from . import canonical_stage
        else:
            import sys
            sys.path.insert(0,str(project))
            from scripts.resources import canonical_stage
        content,provenance=canonical_stage.verify(stage,project)
        needed={'payload-inputs/bmi260/gyro/000.bin','payload-inputs/bmi260/gyro/001.bin',COMMON+'sources/gyro_binding.py'}
        if not needed <= set(content):raise ValueError('Canonical BMI inputs missing')
        return {name:content[name] for name in needed},provenance
    lock_sha = proof.get('project_input_lock_sha256')
    if not isinstance(lock_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', lock_sha):
        raise ValueError('Canonical preparation lock hash is missing or invalid')
    semantic = dict(proof)
    del semantic['project_input_lock_sha256']
    if digest(json.dumps(semantic, sort_keys=True, separators=(',', ':')).encode()) != SEMANTIC_SHA:
        raise ValueError('Canonical preparation semantic proof differs')
    if (proof['source_commit'] != COMMIT or proof['official_release'] is not False
            or proof['deployable'] is not False):
        raise ValueError('Canonical preparation scope differs')
    rows = proof['files']
    if len(rows) != 77 or len({r['path'] for r in rows}) != 77:
        raise ValueError('Canonical preparation inventory differs')
    expected = {'preparation.json'}
    retained = {}
    needed = {'payload-inputs/bmi260/gyro/000.bin', 'payload-inputs/bmi260/gyro/001.bin',
              COMMON + 'sources/gyro_binding.py'}
    for row in rows:
        name = row['path']
        part = PurePosixPath(name)
        if part.is_absolute() or '..' in part.parts or '\\' in name:
            raise ValueError('Invalid canonical input path')
        path = stage.joinpath(*part.parts)
        if path.is_symlink() or not path.is_file():
            raise ValueError('Canonical input differs: ' + name)
        raw = path.read_bytes()
        if len(raw) != row['size'] or digest(raw) != row['sha256']:
            raise ValueError('Canonical input differs: ' + name)
        if name in needed:
            retained[name] = raw
        expected.add(name)
    actual = {p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
    if actual != expected:
        raise ValueError('Canonical preparation has missing or additional files')
    if set(retained) != needed:
        raise ValueError('Canonical BMI inputs missing')
    return retained, dict(source_proof_sha256=digest(proof_raw), project_input_lock=proof['project_input_lock'],
                          project_input_lock_sha256=lock_sha,
                          source_semantic_sha256=SEMANTIC_SHA)


def public_input(path, expected):
    bridge = Path(__file__).resolve().parents[2] / 'adaptations/brands/onexplayer/oxp3/bridge/scripts/build.py'
    spec = importlib.util.spec_from_file_location('_bmi260_public_bridge', bridge)
    implementation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(implementation)
    return implementation.public_input(path, expected)


def prepare(stage, archive):
    """Return {logical target: (bytes, mode)} and independent model profiles."""
    stage, archive = Path(stage), Path(archive)
    retained, provenance = validated_stage(stage)
    with public_input(archive, dict(size=ARCHIVE_SIZE, sha256=ARCHIVE_SHA)) as stream, zipfile.ZipFile(stream) as z:
        names = [i.filename for i in z.infolist()]
        needed = ['cache/integration/gyro/%03d.bin' % i for i in (0, 1, 4, 5, 6, 7, 8)]
        if any(names.count(name) != 1 for name in needed):
            raise ValueError('Original installer gyro members are missing or ambiguous')
        original = {name[-7:]: z.read(name) for name in needed}
    for name in ('000.bin', '001.bin'):
        if original[name] != retained['payload-inputs/bmi260/gyro/' + name]:
            raise ValueError('Canonical gyro input differs from original archive: ' + name)
    helper = retained[COMMON + 'sources/gyro_binding.py']
    prefix = 'var/lib/oxp3-gyro-v1/'
    unit = original['005.bin']
    if len(re.findall(rb'^TimeoutStartSec=.*$', unit, re.M)) != 1 or len(re.findall(rb'^TimeoutStopSec=.*$', unit, re.M)) != 1:
        raise ValueError('Unexpected original gyro unit timeouts')
    unit = re.sub(rb'^TimeoutStartSec=.*$', b'TimeoutStartSec=40', unit, flags=re.M)
    unit = re.sub(rb'^TimeoutStopSec=.*$', b'TimeoutStopSec=20', unit, flags=re.M)
    files = {
        prefix + 'oxp3_bmi260.ko': (original['000.bin'], 0o644),
        prefix + 'inputplumber': (original['001.bin'], 0o755),
        prefix + 'binding.py': (helper, 0o644),
        prefix + 'launch.sh': (original['004.bin'], 0o755),
        'etc/systemd/system/oxp3-gyro.service': (unit, 0o644),
        'etc/systemd/system/inputplumber.service.d/oxp3-gyro-v1.conf': (original['006.bin'], 0o644),
    }
    for filename, action in (('load.sh', 'start'), ('unload.sh', 'stop')):
        files[prefix + filename] = (('#!/bin/sh\nexec /usr/bin/python3 -I -B /var/lib/oxp3-gyro-v1/binding.py ' + action + '\n').encode(), 0o755)
    profiles = {model: {target: (original[name], 0o644) for name, target in PROFILE_TARGETS.items()}
                for model in ('oxp3', 'x2-mini')}
    return files, profiles, provenance


def write_output(destination, files, profiles, provenance):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    for group, entries in [('common', files), *[('profiles/' + k, v) for k, v in profiles.items()]]:
        for target, (data, mode) in sorted(entries.items()):
            output = destination / group / target
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            records.append(dict(group=group, target=target, mode=mode, size=len(data), sha256=digest(data)))
    provenance=dict(provenance); source_commit=provenance.pop('source_commit',COMMIT)
    proof = dict(kind='BMI260RuntimePreparation', source_commit=source_commit, **provenance,
                 archive_sha256=ARCHIVE_SHA, official_release=False, deployable=False,
                 x2_orientation='inherited original OXP3 profile; no independent measurement', files=records)
    (destination / 'preparation.json').write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8', newline='\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='new empty output path on the preparation volume')
    args = parser.parse_args()
    files, profiles, provenance = prepare(args.stage, args.archive)
    write_output(args.output, files, profiles, provenance)


if __name__ == '__main__':
    main()
