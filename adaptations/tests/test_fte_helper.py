"""Exercise service gating on synthetic sysfs, never physical hardware."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FTE=ROOT/'brands/onexplayer/x2-mini/current/fingerprint/fte3600'

@unittest.skipUnless(os.name == 'posix', 'requires POSIX shell and symlinks')
class HelperTests(unittest.TestCase):
    def test_noop_corruption_and_matching_uio(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); sys=root/'sys'; bins=root/'bin'; bins.mkdir()
            def put(path, content):
                path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content)
            def command(name, content):
                path=bins/name; path.write_text('#!/bin/sh\n'+content+'\n'); path.chmod(0o755)
            command('uname',"echo '7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'")
            command('sha256sum',"echo '7e608fe13335f8b6781f84af090a7cd829e29531f0b64d032447bf8006efbe22  file'")
            command('sleep','exit 0'); command('modprobe','exit 0'); command('insmod','exit 0')
            put(sys/'class/dmi/id/sys_vendor','ONE-NETBOOK\n')
            put(sys/'class/dmi/id/product_name','ONEXPLAYER X2Mini\n')
            put(sys/'module/spidev/parameters/bufsiz','65536\n')
            sensor=sys/'devices/spi0.0'; put(sensor/'modalias','acpi:FTE3600:00\n')
            driver=sys/'drivers/spidev'; driver.mkdir(parents=True); (sensor/'driver').symlink_to(driver)
            bus=sys/'bus/spi/devices'; bus.mkdir(parents=True); (bus/'spi0.0').symlink_to(sensor)
            glue=sensor/'fte3600-glue.0'; put(glue/'fte3600_glue_abi','2\n'); put(glue/'fte3600_status','ready\n')
            node=sys/'class/uio/uio0'; put(node/'name','fte3600-irq\n'); put(node/'version','2\n'); (node/'device').symlink_to(glue)
            module=root/'fte3600.ko'; module.write_bytes(b'x'*647200)
            code=(FTE/'load.sh').read_text().replace('/sys/',str(sys)+'/').replace('/var/lib/oxp3-installer/fingerprint/fte3600/fte3600.ko',str(module)).replace('/sbin/modprobe',str(bins/'modprobe')).replace('/sbin/insmod',str(bins/'insmod'))
            helper=root/'load.sh'; helper.write_text(code)
            def run(): return subprocess.run(['/bin/sh',str(helper)],env={**os.environ,'PATH':str(bins)+':'+os.environ['PATH']},capture_output=True,text=True)
            self.assertEqual(run().returncode,0)
            put(glue/'fte3600_status','suspended\n'); result=run(); self.assertNotEqual(result.returncode,0); self.assertIn('did not become ready',result.stderr)
            module.write_bytes(b'broken'); result=run(); self.assertNotEqual(result.returncode,0); self.assertIn('missing or corrupted',result.stderr)
            put(sensor/'modalias','acpi:BF63112:00\n'); self.assertEqual(run().returncode,0)

    def test_fprintd_requires_ordered_oneshot(self):
        text=(FTE/'50-boiler-fte3600.conf').read_text()
        self.assertIn('Requires=boiler-fte3600.service',text)
        self.assertIn('After=boiler-fte3600.service',text)

if __name__=='__main__': unittest.main()
