"""Canonical stage integrity fixture; no physical devices are accessed."""
import hashlib,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from resources import canonical_stage,model_source,build_wifi_pool

def digest(raw):return hashlib.sha256(raw).hexdigest()
class CanonicalStageTests(unittest.TestCase):
 def test_stage_source_and_locked_gyro_tampering_are_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);project=root/'project';stage=root/'stage'
   def write(base,name,raw):
    p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
   source={'common/fixture/source.toon':b'fixture-source','common/gpu/intel/xe/xevmafix-724/final/load.py':b'xe',
           'common/audio/realtek/alc245/speaker-route/final/speaker_route.py':b'speaker',
           'common/network/intel/be201/source.toon':b'firmware-record',
           'devices/onexplayer/oxp3.toon':b'oxp3-guide','devices/onexplayer/x2-mini.toon':b'x2-guide'}
   rows=[dict(path='adaptations/'+n,size=len(b),sha256=digest(b)) for n,b in source.items()]
   lock=json.dumps(dict(commit='a'*40,files=rows)).encode();write(project,model_source.SNAPSHOT,lock)
   write(project,'adaptations/resource-build-inputs.json',json.dumps(dict(xevma_module='module')).encode());write(project,'module',b'module')
   firmware=[dict(path='legacy/inputs/'+str(i).zfill(3)+'-fw'+str(i)) for i in range(9)]
   write(project,build_wifi_pool.SOURCE,json.dumps(dict(files=firmware)).encode())
   members={'source/'+name:raw for name,raw in source.items()};members.update({'payload-inputs/xevma/load.py':b'xe','payload-inputs/alc245/speaker_route.py':b'speaker','payload-inputs/xevma/xevmafix.ko':b'module','payload-inputs/bmi260/gyro/000.bin':b'gyro'})
   members.update({'payload-inputs/be201/fw'+str(i):('fw'+str(i)).encode() for i in range(9)})
   for name,raw in members.items():write(stage,name,raw)
   proof=dict(kind='CanonicalCommonSnapshotPreparation',source_repository='FlightDan/Boiler-adaptations',source_commit='a'*40,snapshot_sha256=digest(lock),project_input_lock='adaptations/resource-build-inputs.json',project_input_lock_sha256=digest((project/'adaptations/resource-build-inputs.json').read_bytes()),gyro_public_input=dict(size=555684389,sha256='d8bee8bad14f4cd136c814ebcaa3299fa59202b6cdc6d26ec9c52af0e055f4d4'),official_release=False,deployable=False,files=[dict(path=n,size=len(b),sha256=digest(b)) for n,b in members.items()]);write(stage,'preparation.json',json.dumps(proof).encode())
   declared=[dict(path='inputs/fw'+str(i),size=3,sha256=digest(('fw'+str(i)).encode())) for i in range(9)]
   fake_decoder=types.SimpleNamespace(loads=lambda *args,**kwargs:dict(id='resource:intel-be201-firmware',files=declared))
   with patch.object(model_source,'sources',return_value=[]),patch.object(canonical_stage,'decoder',return_value=fake_decoder),patch.object(build_wifi_pool,'resolve',side_effect=lambda project,name:Path(name).name[4:].encode()),patch.object(canonical_stage,'PREFIXES',('common/',)),patch.object(canonical_stage,'RECORDS',()),patch.object(canonical_stage,'GYRO',{'payload-inputs/bmi260/gyro/000.bin':(4,digest(b'gyro'))}):
    canonical_stage.verify(stage,project)
    for key,bad in (('project_input_lock','other.json'),('project_input_lock_sha256','0'*64),('gyro_public_input',{'size':1,'sha256':'0'*64})):
     original=proof[key];proof[key]=bad;write(stage,'preparation.json',json.dumps(proof).encode())
     with self.subTest(field=key),self.assertRaises(ValueError):canonical_stage.verify(stage,project)
     proof[key]=original;write(stage,'preparation.json',json.dumps(proof).encode())
    for name in ('source/common/fixture/source.toon','source/devices/onexplayer/x2-mini.toon','payload-inputs/bmi260/gyro/000.bin'):
     original=(stage/name).read_bytes();changed=original+b'!';(stage/name).write_bytes(changed)
     row=next(row for row in proof['files'] if row['path']==name);row.update(size=len(changed),sha256=digest(changed));write(stage,'preparation.json',json.dumps(proof).encode())
     with self.subTest(name=name),self.assertRaises(ValueError):canonical_stage.verify(stage,project)
     (stage/name).write_bytes(original);row.update(size=len(original),sha256=digest(original));write(stage,'preparation.json',json.dumps(proof).encode())
if __name__=='__main__':unittest.main()