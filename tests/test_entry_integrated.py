import hashlib,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance import desktop_hdr_firstboot as first,desktop_hdr_panel as panel,desktop_hdr_resources as desktop
from maintenance.resource_runtime import ResourcePool,ResourceSession
from maintenance import core_retention
from maintenance.engine import Engine
ID=desktop.IDENTITY
class Integrated(unittest.TestCase):
 def test_installed_owned_core_retained_two_slots_before_binding(self):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp);pool=base/'pool';pool.mkdir();home=base/'home';home.mkdir();slots={};(pool/'body').write_bytes(b'abc');sha=hashlib.sha256(b'abc').hexdigest()
   raw=bytearray(128);raw[:8]=bytes.fromhex('00ffffffffffff00');raw[8:12]=bytes.fromhex('0443d204');raw[127]=-sum(raw)%256;raw=bytes(raw)
   observation=dict(sysfs_connector='card3-eDP-2',connector='eDP-2',edid=raw.hex(),edid_sha256=hashlib.sha256(raw).hexdigest(),panel=panel.decode_edid(raw))
   binding=panel.make_binding('x2-mini',observation,generation='a'*32,account=dict(name='deck',uid=1234,gid=1235,home='/home/deck'),bundle_sha256='b'*64)
   for slot in ('A','B'):
    root=base/slot;root.mkdir();(root/'etc').mkdir();(root/'home').mkdir();(root/'var').mkdir();(root/'etc/passwd').write_text('deck:x:1234:1235::/home/deck:/bin/sh\n');(root/'etc/group').write_text('deck:x:1235:\n')
    for name,version in desktop.PACKAGES.items():
     db=root/'usr/lib/holo/pacmandb/local'/(name+'-'+version);db.mkdir(parents=True);(db/'desc').write_text('%NAME%\n'+name+'\n\n%VERSION%\n'+version+'\n')
    manifest=dict(schema_version=1,kind='ComponentManifest',id='core:maintenance',version='1.0.0',compatibility={},requires=[],conflicts=[],files=[dict(source='body',target='usr/local/core',size=3,sha256=sha,mode=420)])
    Engine(root).apply(manifest,pool);slots[slot]=dict(root=root,environment={},hardware={},opt_offload=False)
   resource=dict(id=ID,version='1.0.0',dependencies=[],compatibility={},hardware_requires={},hdr_selected_profile='x2-mini',desktop_bundle_sha256='b'*64,payload={'files':[dict(path='body',size=3,sha256=sha)]},install=dict(kind='files-v1',files=[dict(source='body',target='opt/oxp3-desktop-gamma22/6.7.3-1.6/share/boiler-desktop-hdr/panel-binding.json',scope='root-slots',mode=420),dict(source='body',target='home/deck/current',scope='shared-home',mode=420)]),verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'})
   session=ResourceSession(ResourcePool(pool,{'resources':[resource]},local_test=True),slots,home,offline_image=True,planned_core=True)
   for engine in session.engines.values():engine.home_root=home;core_retention.seed_root(engine)
   with patch.object(core_retention,'guard_apply'):
    receipt=session.install([{'resource':ID}],[ID])
   desktop.check_restore(session,receipt);self.assertTrue(all(row['ok'] for row in session.verify([ID])))
   mounts=SimpleNamespace(home=home,roots={k:v['root'] for k,v in slots.items()});intent={'selection':{'components':[ID]},'resource_receipt':receipt}
   with patch.object(first.lifecycle,'_session',return_value=(session,[])),patch('maintenance.resource_reinstall.engines',side_effect=RuntimeError('verified-owned')):
    with self.assertRaisesRegex(RuntimeError,'verified-owned'):first.prepare(intent,mounts,session.pool,[],binding)
if __name__=='__main__':unittest.main()
