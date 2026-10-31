import hashlib,os,subprocess,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance.resource_runtime import ResourcePool,ResourceSession
from maintenance import desktop_hdr_panel as panel

class InstallTests(unittest.TestCase):
    def test_two_slot_projection_install_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);base.chmod(0o755);pool=base/'pool';pool.mkdir();home=base/'home';home.mkdir();slots={}
            raw=bytearray(128);raw[:8]=bytes.fromhex('00ffffffffffff00');raw[8:12]=bytes.fromhex('0443d204');raw[127]=-sum(raw)%256;raw=bytes(raw)
            obs=dict(sysfs_connector='card3-eDP-2',connector='eDP-2',edid=raw.hex(),edid_sha256=hashlib.sha256(raw).hexdigest(),panel=panel.decode_edid(raw))
            binding=panel.make_binding('x2-mini',obs,generation='a'*32,account=dict(name='deck',uid=1234,gid=1235,home='/home/deck'),bundle_sha256='b'*64)
            data=panel.render_runtime_binding(binding);(pool/'binding').write_bytes(data)
            for slot in ('A','B'):
                root=base/slot;root.mkdir();(root/'etc').mkdir();(root/'home').mkdir();(root/'var').mkdir()
                (root/'etc/passwd').write_text('deck:x:1234:1235::/home/deck:/bin/sh\n');(root/'etc/group').write_text('deck:x:1235:\n')
                for name,version in {'kwin':'6.7.3-1.6','qt6-base':'6.11.1-1','qt6-declarative':'6.11.1-3'}.items():
                    package=root/'var/lib/pacman/local'/(name+'-'+version);package.mkdir(parents=True);(package/'desc').write_text('%NAME%\n'+name+'\n%VERSION%\n'+version+'\n')
                slots[slot]=dict(root=root,environment={},hardware={},opt_offload=False)
            target='opt/oxp3-desktop-gamma22/6.7.3-1.6/share/boiler-desktop-hdr/panel-binding.json'
            resource=dict(id=panel.IDENTITY,version='1.0.0',dependencies=[],compatibility={},hardware_requires={},
                hdr_selected_profile='x2-mini',desktop_bundle_sha256='b'*64,desktop_hdr_panel_binding=binding,
                payload={'files':[dict(path='binding',size=len(data),sha256=hashlib.sha256(data).hexdigest())]},
                install=dict(kind='files-v1',files=[dict(source='binding',target=target,scope='root-slots',mode=420)]),
                verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'})
            session=ResourceSession(ResourcePool(pool,{'resources':[resource]},local_test=True),slots,home,offline_image=True)
            receipt=session.install([{'resource':panel.IDENTITY}],[panel.IDENTITY])
            for slot in ('A','B'):
                output=slots[slot]['root']/target;self.assertEqual(output.read_bytes(),data)
                if os.name=='posix' and os.geteuid()==0:
                    self.assertEqual((output.stat().st_uid,output.stat().st_gid,output.stat().st_mode&0o777),(0,0,0o644))
                    def user():os.setgid(1235);os.setuid(1234)
                    child=subprocess.run([sys.executable,'-c','from pathlib import Path;import sys;Path(sys.argv[1]).read_bytes()',str(output)],preexec_fn=user)
                    self.assertEqual(child.returncode,0)
            for scope in ('A','B','shared-home'):session.restore(receipt,scope)
            self.assertFalse(any((slots[s]['root']/target).exists() for s in ('A','B')))

if __name__=='__main__':unittest.main()
