"""Verify a canonical common stage against the current source snapshot and fixed inputs."""
import hashlib,json
from pathlib import Path
from protocol.resource_pool import local,json_document,decoder
from . import model_source

PREFIXES=('common/gpu/intel/xe/xevmafix-724/','common/network/intel/be201/',
          'common/audio/realtek/alc245/speaker-route/','common/sensors/bosch/bmi260/gyro/',
          'common/ssd/acer/gm7/')
RECORDS=('common-device-source-index.json','brands/onexplayer/oxp3/common-device-sources.toon',
         'brands/onexplayer/x2-mini/common-device-sources.toon',
         'records/migrations/common-device-resource-ownership-20261103.json',
         'records/migrations/common-bmi260-inputplumber-20261103.json')
GYRO={
 'payload-inputs/bmi260/gyro/000.bin':(426384,'94db735bde8e6d310b5056fb8faf6fac58d06fa54cf3a4b908273384f22b2e52'),
 'payload-inputs/bmi260/gyro/001.bin':(10727640,'1107d95c34863c7865cac64183673135098d77265a32763c22fc7efbc1893aa3'),
 'payload-inputs/bmi260/sources/gyro/inputplumber-0.78.0-oxp3-gyro.patch':(31057,'e1fd31a17aaacb4bb35b1158dae4471f0ea74f21861ef581ac9985bba9d1bc2d'),
 'payload-inputs/bmi260/sources/gyro/InputPlumber-LICENSE':(35151,'7fe14716ed31cc71fc3bf9af293c368d464a853a70ea86e9995388f6b9e929e5')}

def model_records(names):
    return {*RECORDS, *(model_source.onexplayer_source(names, model) for model in ('oxp3','x2-mini'))}


def sha(raw): return hashlib.sha256(raw).hexdigest()

def verify(stage, project):
    stage=Path(stage);project=Path(project);model_source.sources(project)
    raw_lock=local(project,model_source.SNAPSHOT).read_bytes(); lock=json_document(raw_lock)
    proof_raw=local(stage,'preparation.json').read_bytes(); proof=json_document(proof_raw)
    if (proof.get('kind')!='CanonicalCommonSnapshotPreparation' or proof.get('source_repository')!='FlightDan/Boiler-adaptations'
            or proof.get('source_commit')!=lock['commit'] or proof.get('snapshot_sha256')!=sha(raw_lock)
            or proof.get('official_release') is not False or proof.get('deployable') is not False):
        raise ValueError('Canonical stage snapshot binding differs')
    project_lock_name='adaptations/resource-build-inputs.json'
    project_lock_raw=local(project,project_lock_name).read_bytes()
    if proof.get('project_input_lock')!=project_lock_name or proof.get('project_input_lock_sha256')!=sha(project_lock_raw):
        raise ValueError('Canonical stage project input lock binding differs')
    if proof.get('gyro_public_input')!={'size':555684389,'sha256':'d8bee8bad14f4cd136c814ebcaa3299fa59202b6cdc6d26ec9c52af0e055f4d4'}:
        raise ValueError('Canonical stage public gyro archive identity differs')
    source={row['path'].removeprefix('adaptations/'):row for row in lock['files']}
    names={n for n in source if n.startswith(PREFIXES)}|model_records(source)
    if not names<=set(source): raise ValueError('Canonical stage source closure is incomplete')
    rows=proof.get('files'); content={}
    if not isinstance(rows,list):raise ValueError('Missing canonical stage inventory')
    for row in rows:
        name=row['path']
        if name in content:raise ValueError('Duplicate stage member')
        raw=local(stage,name).read_bytes()
        if len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Canonical stage member differs: '+name)
        content[name]=raw
    actual={p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}
    if actual != set(content)|{'preparation.json'}:raise ValueError('Unexpected canonical stage members')
    for name in names:
        raw=content.get('source/'+name); row=source[name]
        if raw is None or len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Canonical stage source differs: '+name)
    if {name.removeprefix('source/') for name in content if name.startswith('source/')} != names:
        raise ValueError('Canonical stage source set differs')
    for name,(size,digest) in GYRO.items():
        raw=content.get(name)
        if raw is None or len(raw)!=size or sha(raw)!=digest:raise ValueError('Canonical gyro input differs: '+name)
    expected={'payload-inputs/xevma/load.py':'common/gpu/intel/xe/xevmafix-724/final/load.py',
              'payload-inputs/alc245/speaker_route.py':'common/audio/realtek/alc245/speaker-route/final/speaker_route.py'}
    for out,name in expected.items():
        if content.get(out)!=content['source/'+name]:raise ValueError('Canonical transformed source differs: '+name)
    from . import build_wifi_pool
    recipe=json_document(local(project,build_wifi_pool.SOURCE).read_bytes())
    for row in recipe['files']:
        if '/inputs/' not in row['path']: continue
        name=Path(row['path']).name[4:];raw=content.get('payload-inputs/be201/'+name)
        if raw is None or raw!=build_wifi_pool.resolve(project,row['path']):raise ValueError('Canonical firmware input differs: '+name)
    record=decoder(project/'adaptations').loads(content['source/common/network/intel/be201/source.toon'].decode('utf-8'),strict=True)
    if record.get('id')!='resource:intel-be201-firmware':raise ValueError('Canonical firmware source identity differs')
    declared=[row for row in record.get('files',[]) if row.get('path','').startswith('inputs/')]
    if len(declared)!=9 or len({row['path'] for row in declared})!=9:raise ValueError('Canonical firmware source set differs')
    for row in declared:
        raw=content.get('payload-inputs/be201/'+Path(row['path']).name)
        if raw is None or len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Firmware differs from canonical source declaration')
    project_lock=json_document(local(project,'adaptations/resource-build-inputs.json').read_bytes())
    module=local(project,project_lock['xevma_module']).read_bytes()
    if content.get('payload-inputs/xevma/xevmafix.ko')!=module:raise ValueError('Canonical xe module differs')
    expected_payload=set(GYRO)|set(expected)|{'payload-inputs/xevma/xevmafix.ko'}|{'payload-inputs/be201/'+Path(row['path']).name[4:] for row in recipe['files'] if '/inputs/' in row['path']}
    if {name for name in content if not name.startswith('source/')}!=expected_payload:raise ValueError('Canonical payload input set differs')
    return content,dict(source_proof_sha256=sha(proof_raw),project_input_lock='adaptations/resource-build-inputs.json',
        project_input_lock_sha256=sha(local(project,'adaptations/resource-build-inputs.json').read_bytes()),source_semantic_sha256=sha(raw_lock),source_commit=lock['commit'])