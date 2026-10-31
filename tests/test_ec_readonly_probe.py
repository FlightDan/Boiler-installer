import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import tempfile, unittest
from pathlib import Path
from maintenance import ec_readonly_probe as probe

class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.put('sys/bus/acpi/devices/PNP0C09:00/status', '15')
        self.put('sys/bus/acpi/devices/PNP0C09:00/hid', 'PNP0C09')
        self.put('sys/module/oxpec/parameters/board_profile', probe.PROFILE)
        self.put('sys/class/hwmon/hwmon1/name', 'oxp_ec')
        self.put('sys/class/hwmon/hwmon1/fan1_input', '1800')
        self.put('sys/class/hwmon/hwmon1/pwm1', '100')
        self.put('sys/class/hwmon/hwmon1/pwm1_enable', '2')
        self.put('sys/class/power_supply/BAT0/charge_control_end_threshold', '80')
        self.put('sys/class/power_supply/BAT0/charge_behaviour', '[auto] inhibit-charge')
        target = self.root/'sys/bus/platform/drivers/oxp-platform';target.mkdir(parents=True)
        device = self.root/'sys/class/hwmon/hwmon1/device';device.mkdir()
        (device/'driver').symlink_to(target, target_is_directory=True)
    def tearDown(self): self.tmp.cleanup()
    def put(self, path, value):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value)
    def test_probe_is_read_only_and_unclaimed(self):
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=probe._inspect(self.root)
        self.assertEqual(result['ownership'],'unclaimed')
        self.assertEqual(result['values']['charge_limit'],80)
        self.assertEqual(before,{str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
    def test_foreign_and_invalid_interfaces_fail(self):
        for path,value in [('sys/bus/acpi/devices/PNP0C09:00/status','0'),
                           ('sys/bus/acpi/devices/PNP0C09:00/hid','BAD'),
                           ('sys/module/oxpec/parameters/board_profile','stock'),
                           ('sys/class/hwmon/hwmon1/pwm1','256'),
                           ('sys/class/power_supply/BAT0/charge_behaviour','auto')]:
            p=self.root/path;old=p.read_text();p.write_text(value)
            with self.subTest(path=path),self.assertRaises(probe.ECProbeError):probe._inspect(self.root)
            p.write_text(old)
        (self.root/'sys/module/oxpec/parameters/board_profile').unlink()
        with self.assertRaises(probe.ECProbeError):probe._inspect(self.root)

if __name__ == '__main__': unittest.main()


