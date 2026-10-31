import os as _test_os, unittest as _test_unittest
if _test_os.name != 'posix':
    raise _test_unittest.SkipTest('Linux kernel interfaces and POSIX ownership/locking require the native Linux suite')
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from maintenance import cpu_sidecar_store as store
from test_cpu_sidecar_ledger import values,GEN,BOOT,INV,TX

class StoreTests(unittest.TestCase):
    def test_completed_history_archives_without_losing_first_before(self):
        from maintenance import cpu_sidecar_ledger as ledger
        original=ledger.MAX_ATTEMPTS;ledger.MAX_ATTEMPTS=2
        try:
            with tempfile.TemporaryDirectory() as tmp:
                before=values(4000000,'balance_power');target=values(3000000,'performance')
                for invocation in ('1'*32,'2'*32):
                    store._transition(tmp,'prepare',generation=GEN,boot=BOOT,invocation=invocation,transaction=TX,
                        config_sha256='c'*64,expected_config_sha256='c'*64,before=before,desired=target,expected_after=target)
                    store._transition(tmp,'complete',generation=GEN,boot=BOOT,invocation=invocation,transaction=TX,
                        after=target,config_sha256='c'*64)
                    store._transition(tmp,'stopped',generation=GEN,boot=BOOT,invocation=invocation,transaction=TX,after=before)
                state=store._transition(tmp,'prepare',generation=GEN,boot=BOOT,invocation='3'*32,transaction=TX,
                    config_sha256='c'*64,expected_config_sha256='c'*64,before=before,desired=target,expected_after=target)
                self.assertEqual(state['archive_count'],2);self.assertEqual(state['boots'][BOOT],before)
                self.assertTrue((Path(tmp)/store.DIRECTORY/('archive-'+state['archive_head']+'.json')).is_file())
                history=store._snapshot_with_history(tmp)
                self.assertEqual([a['invocation'] for a in history['attempts']],['1'*32,'2'*32,'3'*32])
        finally:ledger.MAX_ATTEMPTS=original
    def test_durable_prestart_survives_process_exit_and_readonly_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);args={'generation':GEN,'boot':BOOT,'invocation':INV,
                'transaction':TX,'config_sha256':'c'*64,'expected_config_sha256':'c'*64,'before':values(4000000,'balance_power'),
                'desired':values(3000000,'performance'),'expected_after':values(3000000,'performance')}
            script="from maintenance.cpu_sidecar_store import _transition; import json,os,sys; _transition(sys.argv[1],'prepare',**json.loads(sys.argv[2])); os._exit(73)"
            p=subprocess.run([sys.executable,'-B','-c',script,str(root),json.dumps(args)],check=False)
            self.assertEqual(p.returncode,73)
            ledger=root/store.DIRECTORY/'ledger.json'; before=ledger.read_bytes(); stat_before=ledger.stat()
            current=dict(args['before']);name=next(iter(current));current[name]=args['desired'][name]
            result=store._inspect(root,'reconcile',generation=GEN,boot=BOOT,invocation=INV,transaction=TX,current=current)
            self.assertIn(name,result['safe_to_restore'])
            self.assertEqual(ledger.read_bytes(),before)
            self.assertEqual(ledger.stat().st_mtime_ns,stat_before.st_mtime_ns)
            store._transition(root,'complete',generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=args['desired'],config_sha256='c'*64)
            store._transition(root,'stopped',generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=args['before'])
            self.assertEqual(json.loads(ledger.read_text())['attempts'][-1]['phase'],'stopped')

class HandoverTests(unittest.TestCase):
    def test_absent_ledger_in_existing_private_store(self):
        with tempfile.TemporaryDirectory() as root:
            with store._locked(root,create=True):pass
            with self.assertRaises(FileNotFoundError):store._snapshot(root)
            path=Path(root)/store.DIRECTORY/'ledger.json';path.write_text('{');path.chmod(0o600)
            with self.assertRaises(ValueError):store._snapshot(root)
            path.unlink();path.symlink_to('missing')
            with self.assertRaises(OSError):store._snapshot(root)
            path.unlink();path.write_text('{}');path.chmod(0o600)
            (path.parent/'lock').unlink()
            with self.assertRaises(store.ledger.LedgerError):store._snapshot(root)

    def test_handover_preserves_history_and_new_baseline(self):
        with tempfile.TemporaryDirectory() as root:
            before=values(4000000,'balance_power');target=values(3000000,'performance')
            args=dict(generation=GEN,boot=BOOT,invocation=INV,transaction=TX,config_sha256='c'*64,
                      expected_config_sha256='c'*64,before=before,desired=target,expected_after=target)
            store._transition(root,'prepare',**args)
            store._transition(root,'complete',generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=target,config_sha256='c'*64)
            store._transition(root,'stopped',generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=before)
            new='d'*32;checked=[]
            store._handover(root,generation=GEN,transaction=new,engine_identity='fixture',validate=lambda old,last:checked.append((old,last)))
            self.assertEqual(checked[0][1]['phase'],'stopped')
            fresh=store._snapshot(root);self.assertEqual(fresh['attempts'],[])
            archive=Path(root)/store.DIRECTORY/('handover-'+fresh['handover_head']+'.json')
            self.assertEqual(json.loads(archive.read_bytes())['ledger']['attempts'][-1]['phase'],'stopped')
            actual=values(3500000,'power');args.update(transaction=new,invocation='e'*32,before=actual)
            updated=store._transition(root,'prepare',**args)
            self.assertEqual(updated['boots'][BOOT],actual)
            self.assertEqual(store._snapshot_with_history(root)['attempts'][0]['before'],actual)
            archive.write_text('{}');archive.chmod(0o600)
            with self.assertRaises(store.ledger.LedgerError):store._snapshot(root)

    def test_process_exit_at_handover_and_prepare_publication(self):
        before=values(4000000,'balance_power');target=values(3000000,'performance')
        for failpoint in ('archive','ledger','prepare'):
            with self.subTest(failpoint=failpoint),tempfile.TemporaryDirectory() as root:
                args=dict(generation=GEN,boot=BOOT,invocation=INV,transaction=TX,config_sha256='c'*64,
                          expected_config_sha256='c'*64,before=before,desired=target,expected_after=target)
                store._transition(root,'prepare',**args)
                store._transition(root,'complete',generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=target,config_sha256='c'*64)
                store._transition(root,'stopped',generation=GEN,boot=BOOT,invocation=INV,transaction=TX,after=before)
                new='d'*32;args.update(transaction=new,invocation='e'*32)
                script="""import json,os,sys
from maintenance import cpu_sidecar_store as s
root,point,args=sys.argv[1],sys.argv[2],json.loads(sys.argv[3]);write=s._json_write
def crash(path,value):
 write(path,value)
 if (point=='archive' and path.name.startswith('handover-')) or (point=='ledger' and path.name=='ledger.json'):
  os._exit(73)
s._json_write=crash
s._handover(root,generation=args['generation'],transaction=args['transaction'],engine_identity='fixture',validate=lambda *_:None)
if point=='prepare':
 s._transition(root,'prepare',**args);os._exit(73)
"""
                process=subprocess.run([sys.executable,'-B','-c',script,root,failpoint,json.dumps(args)])
                self.assertEqual(process.returncode,73)
                store._handover(root,generation=GEN,transaction=new,engine_identity='fixture',validate=lambda *_:None)
                state=store._snapshot(root)
                if failpoint!='prepare':state=store._transition(root,'prepare',**args)
                self.assertEqual(len(state['attempts']),1)
                self.assertEqual(state['attempts'][0]['phase'],'prepared')
                archive=Path(root)/store.DIRECTORY/('handover-'+state['handover_head']+'.json')
                self.assertEqual(json.loads(archive.read_text())['ledger']['transaction'],TX)

    def test_compacted_history_and_multiple_handovers(self):
        limit=store.ledger.MAX_ATTEMPTS;store.ledger.MAX_ATTEMPTS=2
        try:
            with tempfile.TemporaryDirectory() as root:
                before=values(4000000,'balance_power');target=values(3000000,'performance')
                def cycle(tx,inv):
                    store._transition(root,'prepare',generation=GEN,boot=BOOT,invocation=inv,transaction=tx,
                        config_sha256='c'*64,expected_config_sha256='c'*64,before=before,desired=target,expected_after=target)
                    store._transition(root,'complete',generation=GEN,boot=BOOT,invocation=inv,transaction=tx,after=target,config_sha256='c'*64)
                    store._transition(root,'stopped',generation=GEN,boot=BOOT,invocation=inv,transaction=tx,after=before)
                for inv in ('1'*32,'2'*32,'3'*32):cycle(TX,inv)
                old=store._snapshot_with_history(root);self.assertEqual(len(old['attempts']),3);self.assertEqual(old['archive_count'],2)
                second='d'*32;third='e'*32
                store._handover(root,generation=GEN,transaction=second,engine_identity='fixture',validate=lambda *_:None)
                cycle(second,'4'*32)
                store._handover(root,generation=GEN,transaction=third,engine_identity='fixture',validate=lambda *_:None)
                state=store._snapshot_with_history(root);self.assertEqual(state['attempts'],[])
                directory=Path(root)/store.DIRECTORY
                latest=json.loads((directory/('handover-'+state['handover_head']+'.json')).read_text())
                previous=json.loads((directory/('handover-'+latest['previous']+'.json')).read_text())
                self.assertEqual(previous['ledger']['archive_head'],old['archive_head'])
                (directory/('archive-'+old['archive_head']+'.json')).unlink()
                with self.assertRaises(store.ledger.LedgerError):store._snapshot(root)
        finally:store.ledger.MAX_ATTEMPTS=limit

    def test_unfinished_or_foreign_generation_cannot_handover(self):
        with tempfile.TemporaryDirectory() as root:
            before=values(4000000,'balance_power');target=values(3000000,'performance')
            store._transition(root,'prepare',generation=GEN,boot=BOOT,invocation=INV,transaction=TX,
                config_sha256='c'*64,expected_config_sha256='c'*64,before=before,desired=target,expected_after=target)
            path=Path(root)/store.DIRECTORY/'ledger.json';original=path.read_bytes()
            for generation in (GEN,'f'*32):
                with self.assertRaises(store.ledger.LedgerError):
                    store._handover(root,generation=generation,transaction='d'*32,engine_identity='fixture',validate=lambda *_:None)
                self.assertEqual(path.read_bytes(),original)

if __name__=='__main__':unittest.main()

