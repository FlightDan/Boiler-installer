import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from maintenance import model_efi as m
from maintenance import ssd_boot as b
from maintenance.engine import MaintenanceError

ORIGINAL=b'linux /boot/vmlinuz-test quiet\n'
CONTEXT={'kind':'nonphysical'}
SELECTION={'schema_version':1,'slot':'guest','repairs':['oxp3:split-lock'],'plugins':[],
           'installer_language':'en-US','kde_language':'system','acknowledge_legacy_update_hold':True}
SOURCE={'package_id':'device:oxp3','manifest_sha256':'a'*64,'selection_sha256':b.digest(SELECTION)}
BOUND={'root_partuuid':'aaaaaaaa-1111','slot':'A','esp_partuuid':'bbbbbbbb-2222',
       'esp_type':b.ESP_TYPE,'home_uuid':'home-uuid','root_device':'1:1','esp_device':'1:2',
       'disk':'/dev/vdc','disposition':'not_applicable','physical':CONTEXT}
ABSENT={m.DROPIN:{'exists':False},m.LICENSE:{'exists':False}}


@contextlib.contextmanager
def _private_directory_fixture(*args, **kwargs):
    if os.name == 'nt':
        yield None
        return
    with tempfile.TemporaryDirectory(prefix='boiler-private-fixture-',dir=os.environ.get('BOILER_SSD_TEST_TMP')) as directory:
        descriptor=os.open(directory,os.O_RDONLY | os.O_DIRECTORY)
        try:
            yield descriptor
        finally:
            os.close(descriptor)


class ModelEfiTests(unittest.TestCase):
    def test_source_binds_exact_reviewed_selection(self):
        self.assertEqual(m._source(SOURCE,SELECTION),SOURCE)
        x2=dict(SOURCE,package_id='device:x2-mini')
        with self.assertRaises(MaintenanceError): m._source(x2,SELECTION)
        self.assertEqual(m._x2_source(x2,SELECTION),x2)
        with self.assertRaises(MaintenanceError): m._x2_source(SOURCE,SELECTION)
        with self.assertRaises(MaintenanceError):
            m._source(dict(SOURCE,package_id='device:unknown'),SELECTION)
        for selection in (dict(SELECTION,repairs=['oxp3:gm7-resume']),
                          dict(SELECTION,repairs=[]), dict(SELECTION,slot='A')):
            with self.subTest(selection=selection), self.assertRaises(MaintenanceError):
                m._source(SOURCE,selection)
        with self.assertRaises(MaintenanceError): m._source(dict(SOURCE,selection_sha256='b'*64),SELECTION)

    def test_fixed_model_lineage_rejects_changed_bytes_or_source(self):
        applied=m._render(ORIGINAL)
        self.assertIn(b'split_lock_detect=off',applied)
        record=dict(version=2,target=m._model_target(BOUND),source=SOURCE,
                    original=b._encode(ORIGINAL),applied=b._encode(applied),status='installed',
                    companions_original=ABSENT,journal_sha256='a'*64)
        self.assertEqual(m._record(record,BOUND),(ORIGINAL,applied))
        x2_record=dict(record,source=dict(SOURCE,package_id='device:x2-mini'))
        with self.assertRaises(MaintenanceError): m._record(x2_record,BOUND)
        self.assertEqual(m._x2_record(x2_record,BOUND),(ORIGINAL,applied))
        with self.assertRaises(MaintenanceError): m._x2_record(record,BOUND)
        bad=dict(record,applied=b._encode(applied+b'changed'))
        with self.assertRaises(MaintenanceError): m._record(bad,BOUND)
        bad=dict(record,target=dict(record['target'],esp_partuuid='cccccccc-3333'))
        with self.assertRaises(MaintenanceError): m._record(bad,BOUND)

    def test_interrupted_recovery_restores_exact_original(self):
        applied=m._render(ORIGINAL)
        record=dict(version=2,target=m._model_target(BOUND),source=SOURCE,
                    original=b._encode(ORIGINAL),applied=b._encode(applied),status='installed',
                    companions_original=ABSENT,journal_sha256='a'*64)
        pending=dict(version=2,target=m._model_target(BOUND),source=SOURCE,action='apply',
                     before=b._encode(ORIGINAL),after=b._encode(applied),old=None,companions_original=ABSENT)
        obj=object.__new__(m.ModelEFI); obj.root=Path('fixture'); obj.context=CONTEXT; obj.source=SOURCE
        obj.store=mock.Mock(); obj.store.state=mock.MagicMock()
        with mock.patch.object(b,'binding',return_value=BOUND), mock.patch.object(b,'_replace_efi') as replace, \
             mock.patch.object(m,'_ssd_restore_barrier'), mock.patch.object(m,'Engine'), \
             mock.patch.object(m,'_companion_journal',return_value=None), mock.patch.object(b,'_target',return_value=ORIGINAL), \
             mock.patch.object(obj,'_remove_pending'):
            result=obj._recover(BOUND,applied,None,pending)
        self.assertTrue(result['changed']); replace.assert_called_once_with(obj.root,applied,ORIGINAL,BOUND)
        with self.assertRaises(MaintenanceError): obj._recover(BOUND,b'changed',None,pending)

    def test_recovery_blocks_ssd_owner_before_model_restore(self):
        applied=m._render(ORIGINAL)
        record=dict(version=2,target=m._model_target(BOUND),source=SOURCE,
                    original=b._encode(ORIGINAL),applied=b._encode(applied),status='installed',
                    companions_original=ABSENT,journal_sha256='a'*64)
        pending=dict(version=2,target=m._model_target(BOUND),source=SOURCE,action='apply',
                     before=b._encode(ORIGINAL),after=b._encode(applied),old=None,companions_original=ABSENT)
        obj=object.__new__(m.ModelEFI); obj.root=Path('fixture'); obj.context=CONTEXT; obj.source=SOURCE
        obj.store=mock.Mock(); obj.store.state=mock.MagicMock()
        with mock.patch.object(m,'_ssd_restore_barrier',side_effect=MaintenanceError('SSD owns boot')), \
             mock.patch.object(b,'_replace_efi') as replace, mock.patch.object(m,'Engine'):
            with self.assertRaises(MaintenanceError): obj._recover(BOUND,applied,None,pending)
            replace.assert_not_called()

    def test_x2_interrupted_recovery_preserves_ssd_barrier(self):
        source=dict(SOURCE,package_id='device:x2-mini')
        applied=m._render(ORIGINAL)
        pending=dict(version=2,target=m._model_target(BOUND),source=source,action='apply',
                     before=b._encode(ORIGINAL),after=b._encode(applied),old=None,companions_original=ABSENT)
        obj=object.__new__(m.ModelEFI); obj.root=Path('fixture'); obj.context=CONTEXT; obj.source=source
        obj.store=mock.Mock(); obj.store.state=mock.MagicMock()
        with mock.patch.object(m,'_ssd_restore_barrier',side_effect=MaintenanceError('SSD owns boot')), \
             mock.patch.object(b,'_replace_efi') as replace, mock.patch.object(m,'Engine'):
            with self.assertRaises(MaintenanceError): obj._recover(BOUND,applied,None,pending)
            replace.assert_not_called()
        with mock.patch.object(m,'_ssd_restore_barrier'), mock.patch.object(b,'binding',return_value=BOUND), \
             mock.patch.object(b,'_replace_efi') as replace, \
             mock.patch.object(m,'Engine'), mock.patch.object(m,'_companion_journal',return_value=None), \
             mock.patch.object(b,'_target',return_value=ORIGINAL), mock.patch.object(obj,'_remove_pending'):
            self.assertTrue(obj._recover(BOUND,applied,None,pending)['changed'])
            replace.assert_called_once_with(obj.root,applied,ORIGINAL,BOUND)

    def test_companion_journal_proves_fixed_targets_and_exact_bytes(self):
        license_path=Path(__file__).resolve().parents[1]/'adaptations/common/scripts/licenses/upstream-fixes-MIT.txt'
        data={m.DROPIN:m.canonical.DROPIN_DATA,m.LICENSE:license_path.read_bytes()}
        snapshots={name:dict(exists=True,data=b._encode(content),mode=0o644,uid=0,gid=0) for name,content in data.items()}
        journal=dict(phase='installed',allowed=list(m.COMPANIONS),original=snapshots,
                     applied={name:value['data'] for name,value in snapshots.items()})
        def check(document):
            raw=json.dumps(document).encode()
            with mock.patch.object(m.os.path,'lexists',return_value=True), \
                 mock.patch.object(b,'_file',return_value=contextlib.nullcontext((io.BytesIO(raw),mock.Mock(st_size=len(raw))))), \
                 mock.patch.object(m,'_companions',return_value=snapshots), mock.patch.object(m,'_canonical',return_value=m.canonical):
                return m._companion_journal(Path('fixture'),'A',snapshots,phase='installed')
        self.assertEqual(len(check(journal)),64)
        with self.assertRaises(MaintenanceError): check(dict(journal,allowed=[m.DROPIN,b.TARGET]))
        with self.assertRaises(MaintenanceError): check(dict(journal,phase='installing'))
        changed=copy.deepcopy(journal); changed['applied'][m.DROPIN]=b._encode(b'wrong')
        with self.assertRaises(MaintenanceError): check(changed)

    def test_combined_recovery_requires_restored_companion_proof(self):
        applied=m._render(ORIGINAL)
        pending=dict(version=2,target=m._model_target(BOUND),source=SOURCE,action='apply',
                     before=b._encode(ORIGINAL),after=b._encode(applied),old=None,companions_original=ABSENT)
        obj=object.__new__(m.ModelEFI); obj.root=Path('fixture'); obj.context=CONTEXT; obj.source=SOURCE
        obj.store=mock.Mock()
        with mock.patch.object(m,'_ssd_restore_barrier'), mock.patch.object(m,'Engine'), \
             mock.patch.object(m,'_companion_journal',side_effect=MaintenanceError('not restored')), \
             mock.patch.object(b,'_replace_efi') as replace:
            with self.assertRaises(MaintenanceError): obj._recover(BOUND,applied,None,pending)
            replace.assert_not_called()

    def test_combined_apply_finalize_and_restore_keep_intent_until_companions(self):
        class MemoryStore:
            def __init__(self): self.state=Path('missing-state'); self.rows={}
            def lock(self): return contextlib.nullcontext()
            def _state_read(self,name): return copy.deepcopy(self.rows.get(name))
            def _state_write(self,name,value): self.rows[name]=copy.deepcopy(value)
            def _sync_state(self): pass
        obj=object.__new__(m.ModelEFI); obj.root=Path('fixture'); obj.context=CONTEXT; obj.source=SOURCE; obj.store=MemoryStore()
        obj._remove_pending=lambda: obj.store.rows.pop('model-pending.json')
        actual={'efi':ORIGINAL}
        def replace(root,before,after,bound):
            self.assertEqual(actual['efi'],before); actual['efi']=after
        with mock.patch.object(m.os,'name','posix'), mock.patch.object(m.os,'geteuid',return_value=0,create=True), \
             mock.patch.object(b,'binding',return_value=BOUND), mock.patch.object(b,'_target',side_effect=lambda *a:actual['efi']), \
             mock.patch.object(b,'_replace_efi',side_effect=replace), mock.patch.object(b,'_legacy_conflicts'), \
             mock.patch.object(m,'_ssd_restore_barrier'), mock.patch.object(m,'Engine'), \
             mock.patch.object(m.os.path,'lexists',return_value=False), mock.patch.object(m,'_companions',return_value=ABSENT), \
             mock.patch.object(m,'_preflight_companions',return_value=ABSENT), \
             mock.patch.object(m,'_companion_journal',return_value='a'*64), mock.patch.object(m,'_canonical',return_value=m.canonical), \
             mock.patch.object(m,'_make_proof',side_effect=lambda root,bound,record: {'applied_sha256':hashlib.sha256(b._decode(record['applied'])).hexdigest()}):
            plan=obj.plan('apply')
            self.assertEqual(b._decode(plan['expected_efi']),m._render(ORIGINAL))
            result=obj.execute(plan,reviewed_sha256=plan['review_sha256'])
            self.assertEqual(result['status'],'awaiting_companions')
            self.assertIn('model-pending.json',obj.store.rows)
            obj.finalize(); self.assertNotIn('model-pending.json',obj.store.rows)
            self.assertEqual(obj.proof()['applied_sha256'],hashlib.sha256(actual['efi']).hexdigest())
            plan=obj.plan('restore'); obj.execute(plan,reviewed_sha256=plan['review_sha256'])
            self.assertEqual(actual['efi'],ORIGINAL); self.assertIn('model-pending.json',obj.store.rows)
            obj.finalize(); self.assertNotIn('model-pending.json',obj.store.rows)
            self.assertEqual(obj.store.rows[obj._name(BOUND)]['status'],'restored')

    def test_pair_authorization_rechecks_both_model_only_slots(self):
        class MemoryStore:
            def __init__(self): self.state=Path('fixture/home/.boiler/boot-ownership-v1'); self.rows={}
            def lock(self): return contextlib.nullcontext()
            def _state_write(self,name,value): self.rows[name]=copy.deepcopy(value)
        first=mock.Mock(); second=mock.Mock(); first.root=Path('fixture/a'); second.root=Path('fixture/b')
        first.source=SOURCE; second.source=SOURCE; first.store=MemoryStore()
        a=dict(BOUND); c=dict(BOUND,slot='B',root_partuuid='cccccccc-3333',esp_partuuid='dddddddd-4444')
        plans=[dict(target=a,review_sha256='a'*64,expected_efi_sha256='b'*64),
               dict(target=c,review_sha256='c'*64,expected_efi_sha256='d'*64)]
        first.plan.return_value=plans[0]; second.plan.return_value=plans[1]
        with mock.patch.object(m.os,'name','posix'), mock.patch.object(m.os,'geteuid',return_value=0,create=True), \
             mock.patch.object(m,'ModelEFI',side_effect=[first,second]), mock.patch.object(m.os,'listdir',return_value=['lock']), \
             mock.patch.object(m.os,'stat',return_value=mock.Mock(st_dev=1)):
            pair=m.model_efi_authorize_pair('a','b',CONTEXT,SOURCE,SELECTION,plans)
        self.assertIsNone(pair['slots']['A']['ssd_preflight_sha256'])
        self.assertEqual(first.store.rows['pair.json'],pair)
        self.assertEqual(pair['slots']['B']['target']['esp_partuuid'],'dddddddd-4444')

    def test_proof_hashes_exact_private_journal_bytes(self):
        applied=m._render(ORIGINAL)
        record=dict(version=2,target=m._model_target(BOUND),source=SOURCE,original=b._encode(ORIGINAL),
                    applied=b._encode(applied),status='installed',companions_original=ABSENT,journal_sha256='a'*64)
        raw=json.dumps(record,indent=2).encode()
        with mock.patch.object(b,'_file',return_value=contextlib.nullcontext((io.BytesIO(raw),mock.Mock(st_size=len(raw))))), \
             mock.patch.object(m,'_canonical',return_value=m.canonical):
            proof=m._make_proof(Path('fixture'),BOUND,record)
        self.assertEqual(proof['record_file_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(proof['record_file_size'],len(raw))
        self.assertEqual(proof['applied_size'],len(applied))
        self.assertEqual(proof['record_path'],m.STATE+'/model-esp-bbbbbbbb-2222.json')

    def test_projection_requires_installed_exact_ssd_lineage(self):
        model_bytes=m._render(ORIGINAL)
        model_record=dict(version=2,target=m._model_target(BOUND),source=SOURCE,original=b._encode(ORIGINAL),
                          applied=b._encode(model_bytes),status='installed',companions_original=ABSENT,journal_sha256='a'*64)
        bound=dict(BOUND,disposition='match',physical={'kind':'physical','bus':'NVMe','model':'Predator SSD GM7 1TB','serial':'serial1','disposition':'match'})
        original=dict(efi=b._encode(model_bytes),dropin=None); applied=b._desired(original)
        handoff=dict(preflight={'test':'preview'},model_proof={'test':'proof'})
        record=dict(version=3,target=b._durable(bound),original=original,applied=applied,status='installed',handoff=handoff)
        store=mock.Mock(); store.state=Path('fixture/ssd-state')
        store._state_read.side_effect=lambda name: None if name=='pending.json' else record
        with mock.patch.object(m,'Store',return_value=store), mock.patch.object(m.os.path,'lexists',return_value=True), \
             mock.patch.object(b,'_directory',side_effect=_private_directory_fixture), \
             mock.patch.object(b,'_snapshot',return_value=applied), \
             mock.patch.object(m,'validate_model_handoff',return_value=(bound,{'record_sha256':b.digest(model_record)},model_bytes)):
            self.assertTrue(m._ssd_projection(Path('fixture'),CONTEXT,bound,model_record,b._decode(applied['efi'])))
            record['status']='restored'
            self.assertFalse(m._ssd_projection(Path('fixture'),CONTEXT,bound,model_record,b._decode(applied['efi'])))

    def test_unlisted_legacy_journals_block_handoff(self):
        def entry(name):
            obj=mock.Mock(); obj.name=name; obj.is_symlink.return_value=False
            obj.is_dir.return_value=True; obj.is_file.return_value=False
            return obj
        evidence={'home/.oxp3-installer/integration/A/split-lock/state.json'}
        with mock.patch.object(m.os.path,'lexists',side_effect=lambda path: Path(path).name in ('A','state.json')), \
             mock.patch.object(b,'_directory',side_effect=_private_directory_fixture), \
             mock.patch.object(m.os,'scandir',return_value=contextlib.nullcontext([entry('split-lock'),entry('gm7-resume')])):
            with self.assertRaises(MaintenanceError): m._check_journal_inventory('fixture','A',evidence)
        with mock.patch.object(m.os.path,'lexists',side_effect=lambda path: Path(path).name in ('A','state.json')), \
             mock.patch.object(b,'_directory',side_effect=_private_directory_fixture), \
             mock.patch.object(m.os,'scandir',return_value=contextlib.nullcontext([entry('split-lock')])):
            m._check_journal_inventory('fixture','A',evidence)

    def test_real_slot_inventory_requires_protected_null_disk_evidence(self):
        with tempfile.TemporaryDirectory(prefix='boiler-model-fixture-',dir=os.environ.get('BOILER_SSD_TEST_TMP')) as temporary:
            root=Path(temporary); slot=root/'home/.oxp3-installer/integration/A'
            (slot/'split-lock').mkdir(parents=True)
            (slot/'split-lock/state.json').write_bytes(b'{}')
            (slot/'request.json').write_bytes(b'{}')
            (slot/'result.json').write_bytes(b'{}')
            disk=slot/'target-disk.json'; disk.write_bytes(b'null\n'); disk.chmod(0o600)
            seen={'home/.oxp3-installer/integration/A/split-lock/state.json',
                  'home/.oxp3-installer/integration/A/target-disk.json'}
            m._check_journal_inventory(root,'A',seen)
            with self.assertRaises(MaintenanceError):
                m._check_journal_inventory(root,'A',seen-{'home/.oxp3-installer/integration/A/target-disk.json'})
            disk.write_bytes(b'{}\n')
            with self.assertRaises(MaintenanceError): m._check_journal_inventory(root,'A',seen)
            disk.write_bytes(b'null\n'); (slot/'extra.json').write_bytes(b'{}')
            with self.assertRaises(MaintenanceError): m._check_journal_inventory(root,'A',seen)

    def test_generic_guard_blocks_model_boot_targets_and_hooks(self):
        applied=m._render(ORIGINAL)
        record=dict(version=2,target=m._model_target(BOUND),source=SOURCE,original=b._encode(ORIGINAL),
                    applied=b._encode(applied),status='installed',companions_original=ABSENT,journal_sha256='a'*64)
        engine=mock.Mock(); engine.home_root=Path('fixture/home')
        payload=json.dumps(record).encode()
        def opened(path,**kwargs):
            return contextlib.nullcontext((io.BytesIO(payload),mock.Mock(st_size=len(payload))))
        targets=[{'files':[{'target':'EFI/efi/SteamOS'}]},
                 {'files':[{'target':'etc/default/grub.d/oxp3-splitlock.cfg/child'}]},
                 {'files':[{'target':'usr/share/licenses/oxp3-split-lock'}]}]
        with mock.patch.object(m.os.path,'lexists',return_value=True), \
             mock.patch.object(b,'_directory',side_effect=_private_directory_fixture), \
             mock.patch.object(b,'_file',side_effect=opened), mock.patch.object(m,'_canonical',return_value=m.canonical), \
             mock.patch.object(m.os,'listdir',return_value=['model-esp-bbbbbbbb-2222.json']):
            for component in targets:
                with self.subTest(component=component), self.assertRaises(MaintenanceError):
                    m.reject_conflicting_component(engine,component)
            record['status']='restored'; payload=json.dumps(record).encode()
            m.reject_conflicting_component(engine,targets[0])
            payload=b'{bad'
            with self.assertRaises(MaintenanceError): m.reject_conflicting_component(engine,targets[0])
        with mock.patch.object(m.os.path,'lexists',return_value=True), \
             mock.patch.object(b,'_directory',side_effect=_private_directory_fixture), \
             mock.patch.object(m.os,'listdir',return_value=['model-pending.json']):
            with self.assertRaises(MaintenanceError): m.reject_conflicting_component(engine,targets[0])
        with mock.patch.object(m.os.path,'lexists',side_effect=AssertionError('unrelated must not read model state')):
            m.reject_conflicting_component(engine,{'files':[{'target':'etc/unrelated'}]})
            m.reject_conflicting_component(engine,{'files':[{'target':'etc/unrelated'}],'hooks':{'post_install':'hook.sh'}})

    def test_ssd_restore_barrier_allows_only_proven_other_slot(self):
        physical={'bus':'nvme','model':'predator ssd gm7 1tb','serial':'serial1'}
        a=dict(BOUND,physical=physical,disposition='match')
        c=dict(a,slot='B',root_partuuid='cccccccc-3333',esp_partuuid='dddddddd-4444')
        da,dc=b._durable(a),b._durable(c)
        model_data=m._render(ORIGINAL)
        model_record=dict(version=2,target=m._model_target(c),source=SOURCE,original=b._encode(ORIGINAL),
                          applied=b._encode(model_data),status='installed',companions_original=ABSENT,journal_sha256='a'*64)
        preflight=dict(kind='ReviewedModelSsdPreflight',version=1,target=dc,source=SOURCE,
                       model_plan_sha256='a'*64,model_efi_sha256=hashlib.sha256(model_data).hexdigest())
        preflight['review_sha256']=b.digest(preflight)
        proof=dict(kind='ModelEfiProof',source=SOURCE,target=m._model_target(c),
                   applied_sha256=hashlib.sha256(model_data).hexdigest())
        proof['proof_sha256']=b.digest(proof)
        original=dict(efi=b._encode(model_data),dropin=None)
        other=dict(version=3,target=dc,original=original,applied=b._desired(original),status='installed',
                   handoff={'preflight':preflight,'model_proof':proof})
        slots={'A':dict(target=m._model_target(a),model_plan_sha256='a'*64,model_efi_sha256='b'*64,ssd_preflight_sha256='c'*64),
               'B':dict(target=m._model_target(c),model_plan_sha256='a'*64,model_efi_sha256=preflight['model_efi_sha256'],ssd_preflight_sha256=preflight['review_sha256'])}
        pair=dict(kind='FreshModelSsdPair',version=1,source=SOURCE,slots=slots);pair['pair_sha256']=b.digest(pair)
        rows={'pair.json':pair,'model-esp-'+dc['esp_partuuid']+'.json':model_record}
        payload={'esp-'+dc['esp_partuuid']+'.json':other}
        class MemoryStore:
            def __init__(self,*args): pass
            def _state_read(self,name): return rows.get(name)
        @contextlib.contextmanager
        def opened(path,**kwargs):
            raw=json.dumps(payload[Path(path).name]).encode(); yield io.BytesIO(raw),mock.Mock(st_size=len(raw))
        def check():
            with mock.patch.object(m,'Store',MemoryStore), mock.patch.object(m.os.path,'lexists',return_value=True), \
                 mock.patch.object(b,'_directory',side_effect=_private_directory_fixture), mock.patch.object(b,'_file',side_effect=opened), \
                 mock.patch.object(m.os,'listdir',side_effect=lambda path:list(payload)), mock.patch.object(m,'_make_proof',return_value=proof), \
                 mock.patch.object(m,'_canonical',return_value=m.canonical):
                m._ssd_restore_barrier(Path('fixture'),a,SOURCE)
        check()
        a=c
        with self.assertRaisesRegex(MaintenanceError,'current slot'): check()
        other['status']='restored'
        check()
        other['status']='installed'; a=dict(BOUND,physical=physical,disposition='match')
        before=copy.deepcopy(other)
        for field,value in [('home_uuid','different'),('root_partuuid',da['root_partuuid']),('physical',dict(physical,serial='different'))]:
            with self.subTest(field=field):
                other['target'][field]=value
                with self.assertRaises(MaintenanceError): check()
                other.clear();other.update(copy.deepcopy(before))
        payload['pending.json']={}
        with self.assertRaises(MaintenanceError): check()
        payload.pop('pending.json')
        self.assertEqual(other,before)

    def test_current_slot_applying_stage_is_bound_and_rejects_boot_owners(self):
        with tempfile.TemporaryDirectory(prefix='boiler-stage-fixture-',dir=os.environ.get('BOILER_SSD_TEST_TMP')) as temporary:
            root=Path(temporary); obj=object.__new__(m.ModelEFI); obj.root=root; obj.source=SOURCE; obj.selection=SELECTION
            a=m._model_target(BOUND); btarget=dict(a,slot='B',root_partuuid='cccccccc-3333',esp_partuuid='dddddddd-4444')
            slots={'A':dict(target=a,model_plan_sha256='a'*64,ssd_preflight_sha256=None,model_efi_sha256='b'*64),
                   'B':dict(target=btarget,model_plan_sha256='c'*64,ssd_preflight_sha256=None,model_efi_sha256='d'*64)}
            body=dict(kind='FreshModelSsdPair',version=1,source=SOURCE,slots=slots); pair=dict(body,pair_sha256=b.digest(body))
            adapter=root/'home/.boiler/oxp3-adapter'; adapter.mkdir(parents=True)
            record=dict(schema_version=1,phase='applying',selection=SELECTION,previous_runtime_link=None)
            options=dict(schema=1,installer_language='en-US',kde_language='system',repairs=['split-lock'],plugins=[])
            for label in ('A','B'):
                with self.subTest(slot=label):
                    (adapter/(label+'.json')).write_text(json.dumps(record),encoding='utf-8'); (adapter/(label+'.json')).chmod(0o600)
                    base=root/'home/.oxp3-installer/integration'/label; (base/'system-update-hold').mkdir(parents=True)
                    (base/'request.json').write_text(json.dumps(options),encoding='utf-8'); (base/'request.json').chmod(0o600)
                    (base/'target-disk.json').write_bytes(b'null\n'); (base/'target-disk.json').chmod(0o600)
                    journal=dict(phase='installed',allowed=['etc/example'],original={'etc/example':{'exists':False}},applied={'etc/example':'eA=='})
                    (base/'system-update-hold/state.json').write_text(json.dumps(journal),encoding='utf-8'); (base/'system-update-hold/state.json').chmod(0o600)
                    bound=dict(BOUND,**({'slot':'B','root_partuuid':'cccccccc-3333','esp_partuuid':'dddddddd-4444'} if label=='B' else {}))
                    obj._verify_fresh_current(bound,pair)
                    shared=root/'home/.oxp3-installer/integration/shared/decky-order'; shared.mkdir(parents=True,exist_ok=True)
                    shared_journal=dict(phase='installed',allowed=['home/deck/config'],original={'home/deck/config':{'exists':False}},applied={'home/deck/config':'eA=='})
                    (shared/'state.json').write_text(json.dumps(shared_journal),encoding='utf-8'); (shared/'state.json').chmod(0o600)
                    obj._verify_fresh_current(bound,pair)
                    shared_journal['allowed']=['EFI/EFI/SteamOS/grub.cfg']; shared_journal['original']={shared_journal['allowed'][0]:{'exists':False}}; shared_journal['applied']={shared_journal['allowed'][0]:'eA=='}
                    (shared/'state.json').write_text(json.dumps(shared_journal),encoding='utf-8')
                    with self.assertRaises(MaintenanceError): obj._verify_fresh_current(bound,pair)
                    shared_journal['allowed']=['home/deck/config']; shared_journal['original']={'home/deck/config':{'exists':False}}; shared_journal['applied']={'home/deck/config':'eA=='}
                    (shared/'state.json').write_text(json.dumps(shared_journal),encoding='utf-8')
                    journal['allowed']=['EFI/EFI/SteamOS/grub.cfg']; journal['original']={journal['allowed'][0]:{'exists':False}}; journal['applied']={journal['allowed'][0]:'eA=='}
                    (base/'system-update-hold/state.json').write_text(json.dumps(journal),encoding='utf-8')
                    with self.assertRaises(MaintenanceError): obj._verify_fresh_current(bound,pair)
                    journal['allowed']=['etc/example']; journal['original']={'etc/example':{'exists':False}}; journal['applied']={'etc/example':'eA=='}
                    (base/'system-update-hold/state.json').write_text(json.dumps(journal),encoding='utf-8')
                    (base/'split-lock').mkdir()
                    with self.assertRaises(MaintenanceError): obj._verify_fresh_current(bound,pair)

    def test_only_exact_running_parent_journal_is_accepted(self):
        with tempfile.TemporaryDirectory(prefix='boiler-parent-fixture-',dir=os.environ.get('BOILER_SSD_TEST_TMP')) as temporary:
            root=Path(temporary); state=root/'var/lib/boiler/maintenance'; journals=state/'transactions'; journals.mkdir(parents=True)
            identity='a'*32; component=dict(id='oxp3:legacy-adaptation',files=[],hooks={'apply':'scripts/apply.sh','verify':'scripts/verify.sh','restore':'scripts/restore.sh'})
            journal=dict(id=identity,root='mounted-target',status='hook_running',component=component)
            path=journals/(identity+'.json'); path.write_text(json.dumps(journal),encoding='utf-8'); path.chmod(0o600)
            engine=mock.Mock(); engine.identity='mounted-target'; engine.journals=journals; engine.state_dir=state
            engine._state.return_value={'owners':{}}
            obj=object.__new__(m.ModelEFI); obj.root=root
            real_file=b._file; marker=[b'A\n']
            def opened(path,**kwargs):
                if str(path).replace('\\','/').endswith('/run/boiler-install-slot'):
                    return contextlib.nullcontext((io.BytesIO(marker[0]),mock.Mock(st_size=len(marker[0]))))
                return real_file(path,**kwargs)
            with mock.patch.dict(os.environ,{'BOILER_TRANSACTION_ID':identity,'BOILER_TARGET_ROOT':str(root)}), mock.patch.object(b,'_file',side_effect=opened):
                check,seen=obj._verified_parent(engine,BOUND)
                b._legacy_noninstaller_conflicts(root,engine,allowed_parent=check); self.assertTrue(seen())
                other=dict(journal,id='b'*32); (journals/('b'*32+'.json')).write_text(json.dumps(other),encoding='utf-8')
                check,seen=obj._verified_parent(engine,BOUND)
                with self.assertRaises(MaintenanceError): b._legacy_noninstaller_conflicts(root,engine,allowed_parent=check)
                (journals/('b'*32+'.json')).unlink()
                marker[0]=b'B\n'
                with self.assertRaises(MaintenanceError): obj._verified_parent(engine,BOUND)
                marker[0]=b'A\n'
                journal['component']=dict(component,id='oxp3:other'); path.write_text(json.dumps(journal),encoding='utf-8')
                with self.assertRaises(MaintenanceError): obj._verified_parent(engine,BOUND)


if __name__ == '__main__': unittest.main()
