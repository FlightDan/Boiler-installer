"""Real marked-fixture CLI denial for unsupported mutable configuration plans."""
import hashlib,json,os,subprocess,sys,tempfile,unittest,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from maintenance import resource_cli as api

def snapshot(root):
 return {p.relative_to(root).as_posix():('link',os.readlink(p)) if p.is_symlink() else ('file',hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mode&0o777,p.stat().st_uid,p.stat().st_gid) if p.is_file() else ('dir',) for p in root.rglob('*')}

class CliFixedRestoreBlock(unittest.TestCase):
 def fixed_case(self,fixture_class,selected,target):
  fixture_class.setUpClass()
  try:
   fixture=fixture_class()
   with tempfile.TemporaryDirectory() as temporary:
    work=Path(temporary);token=uuid.uuid4().hex;base=work/(api.FIXTURE_PREFIX+token);base.mkdir(mode=0o700)
    slots,home=fixture.fixture(base)
    for directory in (base,*(v['root'] for v in slots.values()),home):
     directory.chmod(0o700);mark=directory/'.boiler-resource-fixture.json';mark.write_bytes(api._json(dict(kind=api.FIXTURE_KIND,id=token)));mark.chmod(0o600)
    lease=base/'operation.lock';lease.write_bytes(b'0');lease.chmod(0o600)
    receipt=fixture.install(slots,home);config=slots['A']['root']/target;config.write_bytes(b'user-edited-config')
    before=snapshot(base);catalog_sha=hashlib.sha256((fixture.pool.root/'catalog.toon').read_bytes()).hexdigest()
    code="import sys,runpy;sys.path[:0]=[sys.argv.pop(1)];runpy.run_module('maintenance',run_name='__main__')"
    common=['--root',str(slots['A']['root']),'resources']
    options=['--pool',str(fixture.pool.root),'--catalog-sha256',catalog_sha,'--model','device:oxp3','--fixture',str(base),'--trust-local-test']
    for identity in selected:options+=['--component',identity]
    def command(action,extra=()):
     run=subprocess.run([sys.executable,'-B','-c',code,str(ROOT),*common,action,*options,*extra],capture_output=True,text=True)
     try:value=json.loads(run.stdout)
     except Exception:self.fail('CLI did not return structured JSON: '+run.stdout+run.stderr)
     return run.returncode,value
    # Inspection remains readonly and available, even for edited mutable data.
    status,value=command('verify');self.assertEqual(status,0,value);self.assertEqual(snapshot(base),before)
    output=work/'new-plan.json';status,value=command('plan-restore',['--group',receipt['id'],'--scope','A','--plan-output',str(output)])
    self.assertEqual(status,2,value);self.assertIn('fixed mutable-configuration plan renderer',value['error']);self.assertFalse(output.exists());self.assertEqual(snapshot(base),before)
    # A saved synthetic plan with a matching digest cannot bypass the guard.
    prior=dict(schema_version=1,kind='ResourceMaintenanceRestorePlan',selected=selected,group_id=receipt['id'],scope='A',historical_mutable_action='remove')
    raw=api._json(prior);saved=work/'historical-plan.json';saved.write_bytes(raw);saved.chmod(0o600)
    status,value=command('restore',['--group',receipt['id'],'--scope','A','--plan',str(saved),'--reviewed-sha256',api.plan_digest(raw),'--confirm-fixture-restore'])
    self.assertEqual(status,2,value);self.assertIn('fixed mutable-configuration plan renderer',value['error']);self.assertEqual(snapshot(base),before)
    self.assertEqual(config.read_bytes(),b'user-edited-config')
  finally:fixture_class.tearDownClass()
 def test_input_plan_and_execution_refuse_without_writes_readonly_verify_survives(self):
  try: import test_input_resource as inputs
  except ModuleNotFoundError: self.skipTest('Input resource candidate is not yet merged')
  from protocol.input_contract import INPUT,GYRO,CALIBRATION
  self.fixed_case(inputs.InputResourceTests,[INPUT,GYRO],CALIBRATION)
 def test_cpu_plan_and_execution_refuse_without_writes_readonly_verify_survives(self):
  import test_cpu_resource as cpu
  from protocol.cpu_contract import IDENTITY,CONFIG
  self.fixed_case(cpu.CpuResourceTests,[IDENTITY],CONFIG)
if __name__=='__main__':unittest.main()
