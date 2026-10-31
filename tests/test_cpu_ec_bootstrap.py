import tempfile,unittest
from pathlib import Path
from resource_firstboot_install import bootstrap_manifest

class BootstrapTests(unittest.TestCase):
 def test_fixed_cpu_ec_hooks_and_old_contract_unchanged(self):
  intent={'selection':{'components':['resource:intel-g3e-public-cpu-controls','resource:onexplayer-ec-fan-driver']},'ec_board_profile':'oxp3-g1i-v1'}
  with tempfile.TemporaryDirectory() as d:
   manifest=bootstrap_manifest(Path(d),intent,cpu_live=True,ec_profile=True)
   rows={r['target']:r for r in manifest['files']}
   cpu='etc/systemd/system/oxp3-cpu.service.d/90-boiler-cpu-live.conf'
   ec='etc/systemd/system/oxp3-fan-module.service.d/90-boiler-ec-profile.conf'
   blacklist='etc/modprobe.d/90-boiler-oxpec-blacklist.conf'
   self.assertTrue({cpu,ec,blacklist}<=rows.keys())
   raw=(Path(d)/rows[cpu]['source']).read_text()
   self.assertIn('cpu-prestart',raw);self.assertIn('cpu-poststart',raw);self.assertIn('cpu-poststop',raw)
  with tempfile.TemporaryDirectory() as d:
   old=bootstrap_manifest(Path(d),intent)
   self.assertFalse(any('cpu-live.conf' in r['target'] or 'ec-profile.conf' in r['target'] for r in old['files']))
 def test_ec_profile_requires_selected_resource(self):
  with tempfile.TemporaryDirectory() as d,self.assertRaises(Exception):
   bootstrap_manifest(Path(d),{'selection':{'components':[]},'ec_board_profile':'oxp3-g1i-v1'},ec_profile=True)

if __name__=='__main__':unittest.main()
