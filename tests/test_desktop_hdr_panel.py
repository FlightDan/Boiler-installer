import copy
import hashlib
import tempfile
import unittest
import sys
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance import desktop_hdr_panel as panel

def edid(identity=b'\x04\x43\xd2\x04'):
    raw = bytearray(128)
    raw[:8] = bytes.fromhex('00ffffffffffff00')
    raw[8:12] = identity
    raw[127] = -sum(raw) % 256
    return bytes(raw)

class BindingTests(unittest.TestCase):
    def test_profile_and_complete_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'card7-eDP-1'; p.mkdir()
            (p/'status').write_text('connected\n'); (p/'edid').write_bytes(edid())
            obs = panel.probe_internal_edp(Path(tmp))
            account = dict(name='deck', uid=1001, gid=1002, home='/home/deck')
            b = panel.make_binding('x2-mini', obs, generation='a'*32, account=account, bundle_sha256='b'*64)
            self.assertEqual((b['connector'], b['full_frame'], b['peak']), ('eDP-1', 475, 700))
            self.assertEqual(panel.validate_binding(b), b)
            self.assertIn(b'"profile":"x2-mini"', panel.render_runtime_binding(b))
            with self.assertRaises(ValueError): panel.make_binding('oxp3', obs, generation='a'*32, account=account, bundle_sha256='b'*64)
            corrupt = copy.deepcopy(b); corrupt['edid_sha256'] = '0'*64
            with self.assertRaises(ValueError): panel.validate_binding(corrupt)
            (p/'edid').write_bytes(edid()[:-1])
            with self.assertRaises(ValueError): panel.probe_internal_edp(Path(tmp))

    def test_original_oxp3_and_extra_fields(self):
        raw = edid(bytes.fromhex('4c830143'))
        obs = dict(sysfs_connector='card0-eDP-2', connector='eDP-2', edid=raw.hex(),
                   edid_sha256=hashlib.sha256(raw).hexdigest(), panel=panel.decode_edid(raw))
        b = panel.make_binding('oxp3', obs, generation='a'*32,
            account=dict(name='deck',uid=1001,gid=1002,home='/home/deck'), bundle_sha256='b'*64)
        self.assertEqual((b['full_frame'], b['peak']), (472, 1100))
        b['extra'] = True
        with self.assertRaises(ValueError): panel.validate_binding(b)

    def test_dispatch_uses_private_state_before_shared_marker(self):
        from maintenance import resource_firstboot as firstboot, resource_lifecycle, hdr_firstboot, desktop_hdr_firstboot, retry_documents
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); home=root/'home'; a=root/'a'; b=root/'b'
            for path in (home,a,b): path.mkdir()
            state=a/firstboot.STATE; state.parent.mkdir(parents=True); state.write_text('pending')
            class Mounts:
                def __init__(self, *args, **kwargs): self.home=home; self.roots={'A':a,'B':b}
                def __enter__(self): return self
                def __exit__(self,*args): pass
            calls=[]
            def read(path): return {'maintenance_retry': {'kind':'BoilerResourceRetryAction','status':'reinstalling','mode':'initial-desktop-hdr-binding-v1'}}
            def game(root,intent): calls.append('game'); return intent
            def desktop(root,intent): calls.append('desktop'); return intent
            with patch.object(resource_lifecycle,'TargetMounts',Mounts), patch.object(firstboot,'read_private',read), patch.object(retry_documents,'hydrate',lambda x,p:x), patch.object(hdr_firstboot,'ensure',game), patch.object(desktop_hdr_firstboot,'ensure',desktop):
                firstboot._ensure_hdr_bindings(Path('/'), {'selection':{'components':[]}})
            self.assertEqual(calls, ['desktop','game','desktop'])

    def test_dispatch_rejects_unrelated_pending_work(self):
        from maintenance import resource_firstboot as firstboot, resource_lifecycle, retry_documents
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); home=root/'home'; a=root/'a'; b=root/'b'
            for path in (home,a,b): path.mkdir()
            state=a/firstboot.STATE; state.parent.mkdir(parents=True); state.write_text('pending')
            class Mounts:
                def __init__(self, *args, **kwargs): self.home=home; self.roots={'A':a,'B':b}
                def __enter__(self): return self
                def __exit__(self,*args): pass
            def read(path): return {'maintenance_retry': {'kind':'BoilerResourceRetryAction','status':'reinstalling','mode':'unrelated'}}
            with patch.object(resource_lifecycle,'TargetMounts',Mounts), patch.object(firstboot,'read_private',read), patch.object(retry_documents,'hydrate',lambda x,p:x):
                with self.assertRaises(firstboot.ResourceError): firstboot._ensure_hdr_bindings(Path('/'), {'selection':{'components':[]}})

    def test_shared_opt_rejects_caller_supplied_booleans(self):
        from maintenance.resource_runtime import ResourceSession,ResourceError
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);home=root/'home';backing=home/'.steamos/offload/opt';backing.mkdir(parents=True)
            slots={}
            for name in ('A','B'):
                path=root/name;(path/'opt').mkdir(parents=True);slots[name]={'root':path,'opt_offload':True}
            session=ResourceSession.__new__(ResourceSession);session.home=home;session.slots=slots;session.shared_opt=True
            with self.assertRaises(ResourceError):session._desktop_opt_proof()
            with patch('maintenance.resource_runtime.os.path.samestat',return_value=True):session._desktop_opt_proof()
            session.shared_opt=False
            with patch('maintenance.resource_runtime.os.path.samestat',return_value=True):
                with self.assertRaises(ResourceError):session._desktop_opt_proof()

    def test_both_target_package_databases_are_required(self):
        from maintenance.desktop_hdr_resources import preflight,PACKAGES
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);slots={}
            for slot in ('A','B'):
                root=base/slot;slots[slot]={'root':root}
                for name,version in PACKAGES.items():
                    directory=root/'usr/lib/holo/pacmandb/local'/(name+'-'+version);directory.mkdir(parents=True)
                    (directory/'desc').write_text('%NAME%\n'+name+'\n\n%VERSION%\n'+version+'\n')
            preflight(None,[panel.IDENTITY],slots)
            version=PACKAGES['kwin'];(base/'B/usr/lib/holo/pacmandb/local'/('kwin-'+version)/'desc').write_text('%NAME%\nkwin\n\n%VERSION%\nwrong\n')
            with self.assertRaisesRegex(Exception,'target B package ABI mismatch'):preflight(None,[panel.IDENTITY],slots)

if __name__ == '__main__': unittest.main()
