import json, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance import overlay_show, overlay_layout
class OverlayTests(unittest.TestCase):
    def test_cache_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'cache'
            valid={'boottime':100.0,'boot_id':'boot','display':{'domains':'12.0W / N/A','limits':'N/A / N/A','fan':'42 RPM'}}
            p.write_text(json.dumps(valid))
            self.assertEqual(overlay_show.read('domains',cache=p,boot_id='boot',now=102),'12.0W / N/A')
            for now,boot in ((104,'boot'),(99,'boot'),(102,'other')):
                self.assertEqual(overlay_show.read('domains',cache=p,boot_id=boot,now=now),'N/A')
            for contents in ('{','[]','{"boottime":null}',json.dumps(dict(valid,boottime=True))):
                p.write_text(contents)
                self.assertEqual(overlay_show.read('domains',cache=p,boot_id='boot',now=102),'N/A')
            self.assertEqual(overlay_show.read('invalid',cache=p,boot_id='boot',now=102),'N/A')
    def test_layout_preserves_bytes_and_rejects_ambiguous(self):
        original=b'# header\r\n[preset 0]\r\nfoo\r\n[preset 2]\r\nbar\r\n[preset 9]\r\nend'
        result=overlay_layout.render(original)
        self.assertIn(b'[preset 0]\r\nfoo\r\n[preset 2]\r\nbar\r\n',result)
        self.assertIn(b'[preset 9]\r\nend\r\n',result)
        self.assertEqual(result.count(b'# BEGIN BOILER OVERLAY TELEMETRY'),3)
        self.assertEqual(result.count(b'[preset 2]'),1)
        odd=overlay_layout.render(b'[preset 2]\nkeep\n[other]\nuntouched\n')
        self.assertLess(odd.index(b'# BEGIN'),odd.index(b'[other]'))
        self.assertEqual(overlay_layout.render(b'[preset  2]\ncustom\n').count(b'[preset 2]'),1)
        with self.assertRaises(ValueError):overlay_layout.render(result)
        with self.assertRaises(ValueError):overlay_layout.render(b'[preset 2]\n[preset 2]\n')
if __name__=='__main__':unittest.main()


