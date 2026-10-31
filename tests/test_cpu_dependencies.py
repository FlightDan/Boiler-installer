import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import json,tempfile,unittest
from pathlib import Path
from unittest import mock
from maintenance import cpu_sidecar,resource_activation

class System:
 def __init__(self,root):self.output=json.dumps({'pl1_values':[15,15],'pl2_values':[30,30]});self.calls=[]
 def service(self,unit):return 'active'
 def invocation(self,unit):return 'a'*32
 def run(self,*args,**kwargs):self.calls.append(args);return (0,self.output)
class DependencyTests(unittest.TestCase):
 def test_fixed_rapl_domains_and_dbus_are_read_only(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp)
   for name in ('intel-rapl:0','intel-rapl-mmio:0'):
    p=root/'sys/class/powercap'/name;p.mkdir(parents=True)
    for n,v in {'name':'package-0','enabled':'1','constraint_0_name':'long_term','constraint_1_name':'short_term',
      'constraint_0_power_limit_uw':'15000000','constraint_1_power_limit_uw':'30000000'}.items():(p/n).write_text(v)
   loader=root/'var/lib/boiler/decky/runtime/services/PluginLoader';loader.parent.mkdir(parents=True);loader.write_text('fixture')
   unit=root/'etc/systemd/system/plugin_loader.service';unit.parent.mkdir(parents=True);unit.write_text('fixture')
   system=System(root);before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
   with mock.patch.object(cpu_sidecar,'ROOT',root),mock.patch.object(resource_activation,'LiveSystem',return_value=system):
    cpu_sidecar._dependencies()
    self.assertEqual(before,{str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()})
    self.assertEqual(len(system.calls),1);self.assertIn('GetLimits',system.calls[0][-1])
    system.output=json.dumps({'pl1_values':[15,14],'pl2_values':[30,30]})
    with self.assertRaises(cpu_sidecar.SidecarError):cpu_sidecar._dependencies()

if __name__=='__main__':unittest.main()
