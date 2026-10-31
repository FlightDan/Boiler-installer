import hashlib,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance.engine import Engine,MaintenanceError

class BootRenderBindingTests(unittest.TestCase):
 def test_render_input_is_checked_under_transaction_lock_before_target_write(self):
  for changed in (False,True):
   with self.subTest(changed=changed),tempfile.TemporaryDirectory() as directory:
    base=Path(directory);root=base/'root';source=base/'source';source.mkdir();root.mkdir()
    target=root/'efi/EFI/steamos/grub.cfg';target.parent.mkdir(parents=True);before=b'original grub\n';target.write_bytes(before)
    data=b'fixed boot configuration\n';(source/'fixed').write_bytes(data)
    manifest=dict(schema_version=1,kind='ComponentManifest',id='resource:common-boot-fixes',version='1.0.0',compatibility={},requires=[],conflicts=[],
     renderer_input_sha256=hashlib.sha256(before).hexdigest(),files=[dict(source='fixed',target='efi/EFI/steamos/grub.cfg',mode=420,size=len(data),sha256=hashlib.sha256(data).hexdigest())])
    if changed:target.write_bytes(b'edited by another tool\n')
    engine=Engine(root)
    if changed:
     snapshot=target.read_bytes()
     with self.assertRaisesRegex(MaintenanceError,'render input changed'):engine.apply(manifest,source)
     self.assertEqual(target.read_bytes(),snapshot);self.assertFalse(engine._state()['components'])
     self.assertFalse(list(engine.journals.glob('*.json')))
    else:
     receipt=engine.apply(manifest,source);self.assertEqual(receipt['status'],'committed');self.assertEqual(target.read_bytes(),data)

if __name__=='__main__':unittest.main()
