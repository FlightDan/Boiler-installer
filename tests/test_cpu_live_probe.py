import copy, tempfile, unittest
from pathlib import Path
from maintenance import cpu_live_probe as cpu

class CpuProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        base=self.root/'sys/devices/system/cpu/cpufreq';index=0
        for maximum,count in ((5000000,2),(4000000,8),(3000000,4)):
            for _ in range(count):
                p=base/('policy'+str(index));p.mkdir(parents=True);index+=1
                for name,value in {'cpuinfo_max_freq':maximum,'scaling_min_freq':1000000,
                    'scaling_max_freq':maximum,'scaling_driver':'intel_pstate',
                    'scaling_governor':'powersave','energy_performance_available_preferences':'performance balance_power',
                    'energy_performance_preference':'balance_power'}.items():
                    (p/name).write_text(str(value));(p/name).chmod(0o644)
        self.base=base
    def tearDown(self):self.tmp.cleanup()
    def test_disabled_readonly_and_enabled_targets(self):
        before={str(p):p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        self.assertEqual(cpu._scan(self.root,{'enabled':False})['desired'],{})
        config={'enabled':True,'epp':'balance_power','p_mhz':4000,'e_mhz':3000,'lp_mhz':2000}
        self.assertEqual(len(cpu._scan(self.root,config)['desired']),28)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.base.rglob('*') if p.is_file()})
    def test_reject_wrong_topology_driver_governor_and_ranges(self):
        cfg={'enabled':True,'epp':'balance_power','p_mhz':4000,'e_mhz':3000,'lp_mhz':2000}
        for name,value in [('scaling_driver','other'),('scaling_governor','performance'),
                           ('scaling_max_freq','0'),('energy_performance_preference','bad')]:
            p=self.base/'policy0'/name;before=p.read_text();p.write_text(value)
            with self.subTest(name=name),self.assertRaises(cpu.CpuProbeError):cpu._scan(self.root,cfg)
            p.write_text(before)
        (self.base/'policy0/cpuinfo_max_freq').write_text('6000000')
        with self.assertRaises(cpu.CpuProbeError):cpu._scan(self.root,cfg)

if __name__ == '__main__':unittest.main()
