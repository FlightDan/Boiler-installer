import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path

@unittest.skipUnless(os.name=='posix','HID helper requires POSIX')
class HidInterruptTests(unittest.TestCase):
 def test_exit_at_owned_start_and_restore_boundaries(self):
  # Real process termination; all hardware and service actions are fixture mocks.
  script='''
import json,os,sys
from pathlib import Path
from contextlib import nullcontext
sys.path.insert(0,sys.argv[1])
from maintenance import input_hid_binding as h,input_ownership as o
root=Path(sys.argv[2]);operation=sys.argv[3];stop_at=sys.argv[4];h.STATE=root/'run';h.BUS=root/'bus'
hardware=root/'hardware.json';record=h.STATE/'state.json';value=json.loads(hardware.read_text())
expected=dict(kind='BoilerHidBinding',schema_version=1,boot='11111111-1111-1111-1111-111111111111',kernel=o.KERNEL,device='0003:1A86:FE00.0001',root_partuuid='a',root_uuid='b',generation='1'*32,module_sha256='2'*64)
def persist(event):
 hardware.write_text(json.dumps(value))
 if event==stop_at:os._exit(42)
def state():return json.loads(record.read_text()) if record.exists() else None
def save(row):record.write_text(json.dumps(row));persist('save_'+row['phase'])
def command(*args):
 value['loaded']=args[0].endswith('insmod');persist('insmod' if value['loaded'] else 'unload')
def bind(device,name):value['driver']=name;persist('bind_'+name)
def unbind(device,name):value['driver']=None;persist('unbind_'+name)
old_exists=Path.exists
def exists(p):return value['loaded'] if str(p)=='/sys/module/hid_oxp' else old_exists(p)
old_unlink=Path.unlink
def unlink(p,*a,**kw):
 result=old_unlink(p,*a,**kw)
 if p==record:persist('clear')
 return result
Path.exists=exists;Path.unlink=unlink
h.target=lambda:expected['device'];h.driver=lambda device:value['driver'];h.state=state;h.save=save;h.command=command;h.bind=bind;h.unbind=unbind;h.locked=lambda:nullcontext();o.context=lambda *a,**kw:expected
getattr(h,operation)()
'''
  sdk=str(Path(__file__).resolve().parents[1])
  expected=dict(kind='BoilerHidBinding',schema_version=1,boot='11111111-1111-1111-1111-111111111111',kernel='7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9',device='0003:1A86:FE00.0001',root_partuuid='a',root_uuid='b',generation='1'*32,module_sha256='2'*64)
  for operation,event in [('start','save_prepared'),('start','insmod'),('start','unbind_hid-generic'),('start','bind_hid-oxp'),('start','save_active'),('stop','save_restoring'),('stop','unbind_hid-oxp'),('stop','bind_hid-generic'),('stop','unload'),('stop','clear')]:
   with self.subTest(operation=operation,event=event),tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);(root/'run').mkdir();(root/'bus/devices').mkdir(parents=True);helper=root/'helper.py';helper.write_text(script)
    (root/'hardware.json').write_text(json.dumps(dict(driver='hid-generic' if operation=='start' else 'hid-oxp',loaded=operation=='stop')))
    record=root/'run/state.json'
    if operation=='stop':record.write_text(json.dumps(dict(expected,previous='hid-generic',phase='active',loaded_before=False)))
    args=[sys.executable,'-B',str(helper),sdk,str(root),operation]
    self.assertEqual(subprocess.run(args+[event]).returncode,42)
    self.assertEqual(subprocess.run(args+['no-exit']).returncode,0)
    hardware=json.loads((root/'hardware.json').read_text())
    if operation=='start':
     state=json.loads(record.read_text());self.assertEqual((hardware['driver'],state['phase'],state['previous'],state['generation']),('hid-oxp','active','hid-generic','1'*32))
    else:self.assertEqual((hardware['driver'],hardware['loaded'],record.exists()),('hid-generic',False,False))

if __name__=='__main__':unittest.main()
