import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import gzip,tempfile,unittest
from pathlib import Path
from maintenance import ec_initramfs as check

def cpio(entries):
 out=bytearray()
 for name,data in [*entries,('TRAILER!!!',b'')]:
  encoded=name.encode()+b'\0';fields=[1,0o100644,0,0,1,0,len(data),0,0,0,0,len(encoded),0]
  out.extend(b'070701'+''.join(f'{x:08x}' for x in fields).encode()+encoded)
  out.extend(b'\0'*(-len(out)%4));out.extend(data);out.extend(b'\0'*(-len(out)%4))
 return bytes(out)

class InitramfsTests(unittest.TestCase):
 def inspect(self,raw):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/check.IMAGE;path.parent.mkdir();path.write_bytes(raw)
   return check.inspect(tmp)
 def test_plain_and_compressed_archives_and_stock_rejection(self):
  raw=cpio([('kernel/x86/microcode.bin',b'data')])+gzip.compress(cpio([('usr/lib/modules/example.ko.zst',b'module')]))
  self.assertFalse(self.inspect(raw)['stock_oxpec'])
  bad=cpio([('early',b'x')])+gzip.compress(cpio([('usr/lib/modules/oxpec.ko.zst',b'module')]))
  with self.assertRaises(check.InitramfsError):self.inspect(bad)
  with self.assertRaises(check.InitramfsError):self.inspect(cpio([('usr/lib/modules/oxpec.ko.lz4',b'module')]))
 def test_malformed_or_empty_rejected(self):
  for raw in (b'',b'070701broken',cpio([]),cpio([('oxpec.ko\0hidden',b'x')])):
   with self.assertRaises(check.InitramfsError):self.inspect(raw)

if __name__=='__main__':unittest.main()
