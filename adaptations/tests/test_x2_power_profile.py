import importlib.util, json, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('profile', ROOT/'scripts/x2_power_profile.py'); profile=importlib.util.module_from_spec(spec); spec.loader.exec_module(profile)

def regular(root, name):
    path=Path(root)/name
    if not path.is_file(): raise ValueError('Missing: '+name)
    return path

class ProfileTest(unittest.TestCase):
 def test_profile_preserves_shared_logical_paths_and_removes_provider(self):
    lock=json.loads((ROOT/'records/shared-20261030/inputs.json').read_bytes())['components']['battery']
    oxpsources=['battery_control.py','battery_provider.py','sleep_control.py','sleep-router.conf','templates/oxp3-battery.service','templates/battery-dbus.conf','templates/oxp3-battery.toml','battery-plugin-main.py','battery-plugin-index.js','templates/plugin.json','templates/package.json']
    with tempfile.TemporaryDirectory() as t:
      d=Path(t); prefix=d/'payload/legacy/integration'; (prefix/'charging').mkdir(parents=True)
      rows=[]
      for i,(pin,source) in enumerate(zip(lock['oxp3']['files'],oxpsources)):
        data=(ROOT/'brands/onexplayer/common/battery'/source).read_bytes()
        self.assertEqual(profile.digest(data),pin['sha256'])
        name=f'charging/{i:03d}.bin'; (prefix/name).write_bytes(data)
        rows.append(dict(target=pin['target'],source=name,mode=pin['mode'],user=pin['target'].startswith('home/deck/')))
      (prefix/'manifest.json').write_text(json.dumps({'charging':{'files':rows,'version':'0.2.2'}}))
      changed,removed=profile.finalize(d,ROOT,regular)
      result=json.loads((prefix/'manifest.json').read_bytes())['charging']['files']
      self.assertEqual([r['source'] for r in result],['charging/000.bin','charging/002.bin','charging/003.bin','charging/007.bin','charging/008.bin','charging/009.bin','charging/010.bin','charging/004.bin'])
      self.assertEqual(len(changed),9); self.assertEqual(len(removed),3)
      for pin,row in zip(lock['x2-mini']['files'],result):
        self.assertEqual(profile.digest((prefix/row['source']).read_bytes()),pin['sha256'])
      unit=(prefix/'charging/004.bin').read_text()
      self.assertEqual(unit, (ROOT/'brands/onexplayer/x2-mini/current/battery-compat.service').read_text())
      self.assertIn('ExecStart=/usr/bin/true',unit)
      self.assertNotIn('battery_provider',unit)
      self.assertNotIn('/sys/',unit)
      self.assertEqual(result[-1]['target'], 'etc/systemd/system/oxp3-battery.service')
      self.assertEqual(removed, {'payload/legacy/integration/charging/'+name for name in ('001.bin','005.bin','006.bin')})
      self.assertTrue(all(not (d/p).exists() for p in removed))
      backend=prefix/result[0]['source']; spec=importlib.util.spec_from_loader('battery', __import__('importlib').machinery.SourceFileLoader('battery',str(backend))); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
      module.ROOT=d/'power'; module.ROOT.mkdir(); bat=module.ROOT/'BAT0'; bat.mkdir(); (bat/'type').write_text('Battery'); (bat/'capacity').write_text('99')
      self.assertEqual(module.status()['capacity'],'99'); self.assertFalse(module.status()['charge_control_available'])
      with self.assertRaises(RuntimeError): module.set_value('charge_control_end_threshold',80)

if __name__=='__main__': unittest.main()
