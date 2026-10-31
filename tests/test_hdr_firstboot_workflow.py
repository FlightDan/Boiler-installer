"""Real A/B Engine journals; panel/device and mount leases are synthetic."""
import copy,hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_resource_retry_resume as fixture
from maintenance import hdr_firstboot as hdr,resource_firstboot as firstboot,resource_lifecycle as lifecycle,retry_documents
from maintenance.engine import Engine,_json_write
from maintenance.hdr_live_panel import KIND,decode_edid,validate_binding

def binding():
    raw=bytearray(128);raw[:8]=b'\x00\xff\xff\xff\xff\xff\xff\x00'
    raw[8:10]=((1<<10)|(2<<5)|3).to_bytes(2,'big');raw[10:12]=(1234).to_bytes(2,'little')
    raw[127]=(-sum(raw))%256;raw=bytes(raw)
    return validate_binding(dict(kind=KIND,profile='x2-mini',connector='card0-eDP-1',edid=raw.hex(),
        edid_sha256=hashlib.sha256(raw).hexdigest(),panel=decode_edid(raw),full_frame=475,peak=700,timing='edid-native'))

def setup(base):
    with patch.object(fixture,'ID',hdr.ID):
        session,mounts,get_session=fixture.lifecycle_fixture(base,True)
    session.pool.catalog['resources'][0]['hdr_selected_profile']='x2-mini'
    for root in mounts.roots.values():
        payload=base/('core-'+root.name);payload.mkdir();(payload/'body').write_bytes(b'retained core\n')
        manifest=dict(schema_version=1,kind='ComponentManifest',id='core:maintenance',version='1.0.0',compatibility={},requires=[],conflicts=[],
            files=[dict(source='body',target='usr/local/lib/boiler/test-runtime',size=14,sha256=hashlib.sha256(b'retained core\n').hexdigest(),mode=420)])
        Engine(root).apply(manifest,payload)
    intent=firstboot.checked_intent(mounts.roots['A'])
    with patch.object(lifecycle,'_session',get_session):action=hdr.prepare(intent,mounts,session.pool,[],binding())
    return session,mounts,get_session,intent,action

class WorkflowTests(unittest.TestCase):
    def test_resume_binding_across_every_publication_boundary(self):
        for point in ('installing','bootstrap-applying','bootstrap-A-committed','slot-A-state','closing-A-state','closing-shared'):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);session,mounts,get_session,intent,action=setup(base)
                _json_write(base/'retry-action.json',action)
                result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'child',str(base),point],capture_output=True,text=True)
                self.assertEqual(result.returncode,92,result.stderr)
                with patch.object(lifecycle,'_session',get_session):
                    pending=hdr._pending(mounts)
                    # The test's current /var callback has its own owner path.
                    live=firstboot.read_private(base/'retry-action.json')
                    live=retry_documents.hydrate(live,base/'retry-action.json')
                    candidates=[v for v in (pending,live) if v]
                    action=max(candidates,key=lambda v:hdr.PHASES.index(v['phase']))
                    done=lifecycle._continue_retry(intent,action,mounts,lambda value:_json_write(base/'retry-action.json',value))
                self.assertEqual(done['status'],'closed')
                for root in mounts.roots.values():
                    published=firstboot.checked_intent(root)
                    self.assertEqual(published['hdr_panel_binding'],binding())
                    self.assertEqual(published['resource_receipts'][hdr.ID]['id'],action['group'])
                    self.assertTrue(Engine(root,read_only=True).verify_readonly('core:resource-firstboot')['ok'])
                self.assertIsNone(hdr._pending(mounts))

    def test_mixed_binding_copy_refuses_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);session,mounts,get_session,intent,action=setup(base)
            marker=mounts.home/'.boiler/resource-maintenance/lifecycle-action.json'
            _json_write(marker,retry_documents.compact(action,marker))
            bad=copy.deepcopy(action);bad['hdr_panel_binding']['peak']=1100
            state=firstboot.read_private(mounts.roots['A']/firstboot.STATE)
            state['maintenance_retry']=retry_documents.compact(bad,mounts.roots['A']/firstboot.STATE)
            _json_write(mounts.roots['A']/firstboot.STATE,state)
            before={str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(Exception,'copies differ'):hdr._pending(mounts)
            self.assertEqual(before,{str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()})

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='child':
        with patch.object(fixture,'ID',hdr.ID):fixture.lifecycle_child(Path(sys.argv[2]),sys.argv[3])
    else:unittest.main()
