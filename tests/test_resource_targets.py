import contextlib,copy,json,stat,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance import resource_targets as targets
from maintenance.resource_runtime import ResourceError

class TargetBindingTests(unittest.TestCase):
 def fixture(self):
  roles={};rows=[]
  for number,(role,kind) in enumerate(targets.ROLES.items(),1):
   part=f'{number:08x}-1234-1234-1234-123456789abc';fs=f'{number+20:04X}-ABCD' if kind=='vfat' else f'{number+20:08x}-1234-1234-1234-123456789abc'
   roles[role]=dict(partuuid=part,uuid=fs,filesystem=kind)
   rows.append(dict(path='/dev/test'+str(number),type='part',pkname='/dev/testdisk',partuuid=part,uuid=fs,fstype=kind))
  rows.append(dict(path='/dev/testdisk',type='disk',tran='nvme',model='YMTC GM7'))
  intent=dict(slot='A',root_partuuid=roles['rootfs-A']['partuuid'],target_roles=roles)
  return intent,rows
 def execute(self,intent,rows):
  def command(*args):
   if args[0]=='lsblk':return json.dumps({'blockdevices':rows})
   target=args[-1]
   return intent['root_partuuid'] if target=='/' else intent['target_roles']['var-A' if target=='/var' else 'home']['uuid']
  def device(node):return SimpleNamespace(st_mode=stat.S_IFBLK,st_rdev=int(node.removeprefix('/dev/test')))
  with patch.object(targets,'command',side_effect=command),patch.object(targets.os,'stat',side_effect=device):return targets.probe(intent)
 def test_fat_short_filesystem_ids_remain_bound_to_standard_partition_guids(self):
  intent,rows=self.fixture();result=self.execute(intent,rows)
  self.assertEqual(result['efi-A']['filesystem'],'vfat');self.assertEqual(result['efi-A']['uuid'],intent['target_roles']['efi-A']['uuid'].lower())
 def test_duplicate_cloned_partition_is_refused(self):
  intent,rows=self.fixture();rows.append(copy.deepcopy(rows[0]))
  with self.assertRaisesRegex(ResourceError,'duplicated'):self.execute(intent,rows)
 def test_roles_on_different_disks_are_refused(self):
  intent,rows=self.fixture();rows[-2]['pkname']='/dev/other'
  with self.assertRaisesRegex(ResourceError,'one actual disk'):self.execute(intent,rows)
 def test_changed_live_home_binding_is_refused(self):
  intent,rows=self.fixture();rows[-3]['uuid']='ffffffff-1234-1234-1234-123456789abc'
  with self.assertRaises(ResourceError):self.execute(intent,rows)

class VfatMountMaskTests(unittest.TestCase):
 def test_existing_incompatible_mount_is_refused(self):
  result=SimpleNamespace(returncode=0,stdout='rw,relatime,fmask=0022,dmask=0022\n')
  with patch.object(targets.subprocess,'run',return_value=result):
   with self.assertRaisesRegex(ResourceError,'incompatible'):targets._check_vfat_mounts('/dev/efi')
 def test_all_existing_mounts_must_match(self):
  result=SimpleNamespace(returncode=0,stdout='rw,fmask=0133,dmask=0022\nro,fmask=0133,dmask=0022\n')
  with patch.object(targets.subprocess,'run',return_value=result):targets._check_vfat_mounts('/dev/efi')
 def test_probed_disk_facts_come_from_bound_disk(self):
  fixture=TargetBindingTests();intent,rows=fixture.fixture();result=fixture.execute(intent,rows)
  self.assertEqual(result['rootfs-A']['target_disk'],{'bus':'NVMe','model':'YMTC GM7'})

class ExactMountpointTests(unittest.TestCase):
 def test_same_filesystem_bind_mount_is_refused_before_mount(self):
  target=object.__new__(targets.TargetMounts);target.mounts=[]
  with patch.object(targets,'_directory',return_value=contextlib.nullcontext()), \
       patch.object(Path,'is_mount',return_value=False), \
       patch.object(targets.subprocess,'run',return_value=SimpleNamespace(returncode=0)), \
       patch.object(targets,'run') as write:
   with self.assertRaisesRegex(ResourceError,'already in use'):target.mount('/source',Path('/owned/A'),'--bind')
   write.assert_not_called()
  self.assertEqual(target.mounts,[])
 def test_mount_probe_error_refuses_without_mutation(self):
  target=object.__new__(targets.TargetMounts);target.mounts=[]
  with patch.object(targets,'_directory',return_value=contextlib.nullcontext()), \
       patch.object(targets.subprocess,'run',return_value=SimpleNamespace(returncode=2)), \
       patch.object(targets,'run') as write:
   with self.assertRaisesRegex(ResourceError,'Cannot inspect'):target.mount('/source',Path('/owned/A'),'--bind')
   write.assert_not_called()

class SharedHomeOnlyMountTests(unittest.TestCase):
 def test_ro_roots_with_writable_home_and_var_without_unprotect(self):
  target=object.__new__(targets.TargetMounts)
  target.intent={'slot':'A','generation':'a'*32,'mount_efi':True,'target_roles':{
   'rootfs-A':{'subvolume_uuid':'11111111-1111-1111-1111-111111111111'},
   'rootfs-B':{'subvolume_uuid':'11111111-1111-1111-1111-111111111111'}}}
  target.readonly=False;target.home_only=True;target.recover_window=False;target.unprotected=[]
  target.bound={name:{'node':'/dev/'+name} for name in ('rootfs-A','rootfs-B','var-A','var-B')}
  target.roots={'A':Path('/fake/A'),'B':Path('/fake/B')};target.home=Path('/fake/home');target.opt_offload={}
  calls=[]
  def command(*args):
   if args[:3]==('btrfs','subvolume','show'):return 'UUID: 11111111-1111-1111-1111-111111111111'
   if args[:3]==('btrfs','property','get'):return 'ro=true'
   raise AssertionError(args)
  def view(root,slot):target.opt_offload[slot]=False
  with patch.object(target,'revalidate'),patch.object(target,'pending_window',return_value=None), \
       patch.object(target,'_inactive_clean'),patch.object(target,'mount',side_effect=lambda *a:calls.append(a)), \
       patch.object(target,'bind',side_effect=lambda *a:calls.append(('bind',*a))),patch.object(target,'_view',side_effect=view), \
       patch.object(targets,'command',side_effect=command),patch.object(targets,'run',side_effect=AssertionError('must not unprotect root')), \
       patch.object(targets.subprocess,'run',return_value=SimpleNamespace(returncode=1)):
   target.open()
  root_mounts=[x for x in calls if x[0] in ('/dev/rootfs-A','/dev/rootfs-B')]
  self.assertEqual(len(root_mounts),2)
  self.assertTrue(all('ro,rescue=nologreplay' in x for x in root_mounts))
  self.assertFalse(any('efi' in str(x) for x in calls))
  self.assertEqual(target.unprotected,[])

if __name__=='__main__':unittest.main()
