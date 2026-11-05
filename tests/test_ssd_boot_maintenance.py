import contextlib
import copy
import json
import io
import os
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from maintenance import ssd_boot as s
from maintenance.engine import MaintenanceError

CONFIG = b'linux /boot/vmlinuz-test quiet\n'
CONTEXT = dict(kind='physical', bus='NVMe', model='Predator SSD GM7 1TB', serial='serial1', disposition='match')


def topology():
    return [{'path':'/dev/vdc','type':'disk','maj:min':'252:32','tran':'virtio','model':'QEMU',
             'children':[{'path':'/dev/vdc1','type':'part','maj:min':'252:33','partuuid':'aaaaaaaa-1111', 'partlabel':'rootfs-A'},
                         {'path':'/dev/vdc2','type':'part','maj:min':'252:34','partuuid':'bbbbbbbb-2222', 'partlabel':'efi-A','parttype':s.ESP_TYPE}]}]


def get_binding(nodes, context=CONTEXT):
    mount = {'source':'/dev/vdc1','maj:min':'252:33','fstype':'ext4','uuid':'root-uuid'}
    home = {'source':'/dev/vdc3','maj:min':'252:35','fstype':'ext4','uuid':'home-uuid'}
    esp = {'source':'/dev/vdc2','maj:min':'252:34','fstype':'vfat','uuid':'esp-uuid'}
    with mock.patch.object(s, '_activate_efi'), mock.patch.object(s, '_directory', side_effect=lambda p: contextlib.nullcontext()), \
         mock.patch.object(s, '_mount', side_effect=[mount,home,esp]), \
         mock.patch.object(s, 'run', return_value=json.dumps({'blockdevices':nodes})):
        return s.binding('fixture', installer_context=context)


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


class SSDTests(unittest.TestCase):
    def test_btrfs_remount_binds_verified_partition_not_anonymous_mount(self):
        uuid = '11111111-2222-3333-4444-555555555555'
        partition = {'path':'/dev/vdc1','maj:min':'252:33'}
        home = {'source':'/dev/vdc3','maj:min':'252:35','fstype':'ext4','uuid':'home-uuid'}
        esp = {'source':'/dev/vdc2','maj:min':'252:34','fstype':'vfat','uuid':'esp-uuid'}
        with tempfile.TemporaryDirectory() as temporary:
            sysfs=Path(temporary);member=sysfs/uuid/'devices'/'vdc1';member.mkdir(parents=True)
            (member/'dev').write_text('252:33\n')
            devinfo=sysfs/uuid/'devinfo'/'1';devinfo.mkdir(parents=True);(devinfo/'missing').write_text('0\n')
            real_verify=s.probe._single_btrfs_device
            def verify(fs_uuid,device): return real_verify(fs_uuid,device,sysfs=sysfs)
            def bound(anonymous, *, fs_uuid=uuid, canonical=partition):
                root={'source':'/dev/vdc1','maj:min':anonymous,'fstype':'btrfs','uuid':fs_uuid}
                with mock.patch.object(s,'_activate_efi'),mock.patch.object(s,'_directory',side_effect=lambda p:contextlib.nullcontext()), \
                     mock.patch.object(s,'_mount',side_effect=[root,home,esp]),mock.patch.object(s,'run',return_value=json.dumps({'blockdevices':topology()})), \
                     mock.patch.object(s.probe,'_canonical_block',return_value=canonical),mock.patch.object(s.probe,'_single_btrfs_device',side_effect=verify):
                    return s.binding('fixture',installer_context=CONTEXT)
            first=bound('0:36');second=bound('0:35')
            self.assertEqual(first,second)
            self.assertEqual(first['root_device'],'252:33')
            (member/'dev').write_text('252:99\n')
            with self.assertRaises(s.probe.ProbeError): bound('0:36')
            (member/'dev').write_text('252:33\n')
            with self.assertRaises(s.probe.ProbeError): bound('0:36',fs_uuid='invalid')
            with self.assertRaises(s.probe.ProbeError): bound('0:36',canonical={'path':'/dev/vdc1','maj:min':'252:99'})

    def test_binding_uses_host_fact_and_partition_relationship(self):
        result = get_binding(topology())
        self.assertEqual(result['disposition'], 'match')
        self.assertEqual(result['root_partuuid'], 'aaaaaaaa-1111')
        self.assertEqual(result['esp_partuuid'], 'bbbbbbbb-2222')
        self.assertEqual(result['root_device'], '252:33')
        self.assertEqual(s._durable(result)['physical']['serial'], 'serial1')
        self.assertNotIn('QEMU', str(s._durable(result)))

    def test_binding_triggers_existing_automount_before_observation(self):
        state={'activated':False}
        def activate(root): state['activated']=True
        def mounted(path, **kwargs):
            name=Path(path).name
            if name == 'efi':
                return {'source':'/dev/vdc2','maj:min':'252:34','fstype':'vfat' if state['activated'] else 'autofs','uuid':'esp'}
            if name == 'home': return {'source':'/dev/vdc3','maj:min':'252:35','fstype':'ext4','uuid':'home-uuid'}
            return {'source':'/dev/vdc1','maj:min':'252:33','fstype':'ext4','uuid':'root'}
        with mock.patch.object(s,'_activate_efi',side_effect=activate),mock.patch.object(s,'_directory',side_effect=lambda p:contextlib.nullcontext()), \
             mock.patch.object(s,'_mount',side_effect=mounted),mock.patch.object(s,'run',return_value=json.dumps({'blockdevices':topology()})):
            self.assertEqual(s.binding('fixture',installer_context=CONTEXT)['disposition'],'match')
        self.assertTrue(state['activated'])
        def wrong(path, **kwargs):
            value=mounted(path)
            if Path(path).name == 'efi': value['fstype']='ext4'
            return value
        with mock.patch.object(s,'_activate_efi'),mock.patch.object(s,'_directory',side_effect=lambda p:contextlib.nullcontext()), \
             mock.patch.object(s,'_mount',side_effect=wrong),mock.patch.object(s,'run',return_value=json.dumps({'blockdevices':topology()})):
            with self.assertRaises(MaintenanceError): s.binding('fixture',installer_context=CONTEXT)

    def test_efi_automount_stack_requires_opened_vfat_device(self):
        path=Path('/efi')
        auto={'target':str(path),'fstype':'autofs','maj:min':'0:44'}
        fat={'target':str(path),'fstype':'vfat','maj:min':'259:9','uuid':'fixture'}
        def check(rows,device='259:9'):
            with mock.patch.object(s,'run',return_value=json.dumps({'filesystems':rows})):
                return s._mount(path,efi_device=device)
        self.assertEqual(check([auto,fat]),fat)
        self.assertEqual(check([fat]),fat)
        for rows,device in [([auto,fat],'259:8'),([auto,dict(fat,fstype='ext4')],'259:9'),
                            ([auto,fat,fat],'259:9'),([auto],'0:44')]:
            with self.subTest(rows=rows),self.assertRaises(MaintenanceError): check(rows,device)

    def test_wrong_efi_type_and_slot_are_blocked(self):
        for key, value in [('parttype', 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'), ('partlabel', 'efi-B')]:
            nodes = topology(); nodes[0]['children'][1][key] = value
            with self.subTest(key=key), self.assertRaises(MaintenanceError):
                get_binding(nodes)

    def test_wrong_parent_and_disposition_are_blocked(self):
        nodes = topology(); part = nodes[0]['children'].pop(); nodes.append({'path':'/dev/vdd','type':'disk','children':[part]})
        with self.assertRaises(MaintenanceError): get_binding(nodes)
        ctx = dict(CONTEXT, disposition='not_applicable')
        with self.assertRaises(MaintenanceError): get_binding(topology(), ctx)

    def test_original_is_exact_and_conflicting_owned_bytes_rejected(self):
        bound = get_binding(topology())
        original = dict(efi=s._encode(CONFIG),dropin=None)
        after = s._desired(original)
        record = dict(version=2,target=s._durable(bound),original=original,applied=after,status='installed')
        self.assertEqual(s._record(record,bound), (original,after))
        corrupt = copy.deepcopy(record); corrupt['applied']['efi'] = s._encode(s._decode(after['efi'])+b'changed')
        with self.assertRaises(MaintenanceError): s._record(corrupt,bound)
        preexisting = CONFIG.replace(b'quiet',b'quiet nvme.noacpi=1')
        self.assertEqual(s.render_grub_config(preexisting), preexisting)
        existing = dict(efi=s._encode(CONFIG),dropin=dict(data=s._encode(b'unrelated'),mode=0o644,uid=0,gid=0))
        with self.assertRaises(MaintenanceError): s._desired(existing)

    def test_interrupted_recovery_reverts_only_known_bytes(self):
        bound = get_binding(topology())
        original = dict(efi=s._encode(CONFIG),dropin=None); after = s._desired(original)
        record = dict(version=2,target=s._durable(bound),original=original,applied=after,status='installed')
        pending = dict(version=2,target=s._durable(bound),before=original,after=after,old=None,new=record)
        obj = object.__new__(s.SSD); obj.root='fixture'; obj.context=CONTEXT
        obj.store = mock.Mock(); obj.store.state = mock.MagicMock()
        mixed = dict(efi=original['efi'],dropin=after['dropin'])
        with mock.patch.object(s, '_replace') as write, mock.patch.object(obj, '_remove_pending'):
            result = obj._recover(bound,mixed,None,pending)
            self.assertTrue(result['changed']); write.assert_called_once_with('fixture',mixed,original,bound,CONTEXT)
        with self.assertRaises(MaintenanceError): obj._recover(bound,dict(mixed,efi=s._encode(b'external edit')),None,pending)

    def test_recovery_cannot_replace_first_original(self):
        bound = get_binding(topology())
        first = dict(efi=s._encode(CONFIG),dropin=None)
        original = dict(version=2,target=s._durable(bound),original=first,applied=s._desired(first),status='installed')
        second = dict(efi=s._encode(b'linux /boot/vmlinuz-other quiet\n'),dropin=None)
        new = dict(version=2,target=s._durable(bound),original=second,applied=s._desired(second),status='restored')
        pending = dict(version=2,target=s._durable(bound),before=original['applied'],after=new['original'],old=original,new=new)
        obj = object.__new__(s.SSD); obj.root='fixture'; obj.context=CONTEXT
        with self.assertRaises(MaintenanceError): obj._recover(bound,original['applied'],original,pending)

    def test_nonphysical_does_not_open_efi(self):
        obj = object.__new__(s.SSD); obj.root='fixture'; obj.context={'kind':'nonphysical'}
        obj.store=mock.Mock()
        with mock.patch.object(s, 'binding', return_value={'disposition':'not_applicable'}), \
             mock.patch.object(s, '_snapshot') as read:
            with self.assertRaises(MaintenanceError): obj.plan('apply')
            read.assert_not_called()

    def test_changed_owned_bytes_block_reapply_and_restore(self):
        bound = get_binding(topology()); original = dict(efi=s._encode(CONFIG),dropin=None)
        applied = s._desired(original)
        record = dict(version=2,target=s._durable(bound),original=original,applied=applied,status='installed')
        obj = object.__new__(s.SSD); obj.root='fixture'; obj.context=CONTEXT; obj.model_preflight=None
        obj.store=mock.Mock(); obj.store.state='missing-fixture-state'
        obj.store._state_read.side_effect=lambda name: None if name=='pending.json' else record
        changed = dict(applied,efi=s._encode(b'changed'))
        with mock.patch.object(s, 'binding', return_value=bound), mock.patch.object(s, 'Engine'), \
             mock.patch.object(s, '_legacy_conflicts'), mock.patch.object(s, '_snapshot',return_value=changed), \
             mock.patch.object(s.os.path, 'lexists',return_value=False):
            for action in ('apply','verify','restore'):
                with self.subTest(action=action), self.assertRaises(MaintenanceError): obj.plan(action)

    def test_replace_preflights_both_and_preserves_partial_intent(self):
        bound = get_binding(topology())
        before = dict(efi=s._encode(CONFIG), dropin=None); after = s._desired(before)
        current = copy.deepcopy(before)
        def dropin(root, old, new):
            self.assertEqual(current['dropin'], old)
            current['dropin'] = new
        def efi(root, old, new, observed):
            raise OSError('simulated interruption')
        with mock.patch.object(s, '_snapshot', side_effect=lambda root, target: copy.deepcopy(current)), \
             mock.patch.object(s, 'binding', return_value=bound), \
             mock.patch.object(s, '_replace_dropin', side_effect=dropin), \
             mock.patch.object(s, '_replace_efi', side_effect=efi):
            with self.assertRaises(OSError): s._replace('fixture',before,after,bound,CONTEXT)
        self.assertEqual(current, dict(efi=before['efi'],dropin=after['dropin']))
        record = dict(version=2,target=s._durable(bound),original=before,applied=after,status='installed')
        pending = dict(version=2,target=s._durable(bound),before=before,after=after,old=None,new=record)
        obj = object.__new__(s.SSD); obj.root='fixture'; obj.context=CONTEXT
        obj.store = mock.Mock(); obj.store.state = mock.MagicMock()
        with mock.patch.object(s, '_snapshot', side_effect=lambda root, target: copy.deepcopy(current)), \
             mock.patch.object(s, 'binding', return_value=bound), \
             mock.patch.object(s, '_replace_dropin', side_effect=dropin), \
             mock.patch.object(obj, '_remove_pending'):
            result = obj._recover(bound,current,None,pending)
        self.assertTrue(result['ok']); self.assertEqual(current,before)

    def test_existing_matching_dropin_is_preserved_exactly(self):
        dropin = dict(data=s._encode(s.DROPIN_DATA), mode=0o600, uid=0, gid=0)
        before = dict(efi=s._encode(CONFIG), dropin=dropin)
        self.assertEqual(s._desired(before)['dropin'],dropin)

    def test_old_legacy_dropin_is_blocked_without_replacement(self):
        legacy = (b'# Selected destination: Predator GM7; accepted suspend workaround.\n'
                  b'GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX} nvme.noacpi=1"\n')
        original = dict(efi=s._encode(CONFIG),dropin=dict(data=s._encode(legacy),mode=0o644,uid=0,gid=0))
        saved=copy.deepcopy(original)
        with self.assertRaises(MaintenanceError): s._desired(original)
        self.assertEqual(original,saved)
        self.assertIn(b'device validation pending',s.DROPIN_DATA)

    def test_cli_requires_explicit_plan_and_rejects_state_override(self):
        from maintenance.__main__ import main
        with mock.patch.object(s, 'SSD') as handler, mock.patch('builtins.print'):
            handler.return_value.plan.return_value = {'review_sha256':'abc'}
            self.assertEqual(main(['--root','fixture','ssd-boot','plan','apply']), 0)
            handler.return_value.plan.assert_called_once_with('apply')
            handler.return_value.execute.assert_not_called()
            self.assertEqual(main(['--root','fixture','--state-dir','elsewhere','ssd-boot','inspect']), 2)

    def test_ui_nonmatching_ssd_cannot_plan(self):
        from maintenance.model_ui import ModelUI
        dialog = mock.Mock(); backend = mock.Mock(); backend.ssd.return_value = {'available':False}
        ModelUI(dialog,backend).ssd_actions()
        backend.ssd.assert_called_once_with('inspect')
        dialog.menu.assert_not_called()

    def test_ui_requires_review_and_confirmation_before_execute(self):
        from maintenance.model_ui import ModelUI
        dialog=mock.Mock(); backend=mock.Mock(); dialog.menu.return_value='apply'
        dialog.details.return_value=True; dialog.confirm.return_value=False
        plan=dict(review_sha256='a'*64,target=dict(slot='A',root_partuuid='root',esp_partuuid='efi'))
        backend.ssd.side_effect=[{'available':True,'pending':False,'installed':False},plan]
        ModelUI(dialog,backend).ssd_actions()
        self.assertEqual(backend.ssd.call_args_list,[mock.call('inspect'),mock.call('plan','apply')])
        dialog.confirm.assert_called_once()

    def test_conflicting_generic_and_legacy_owners_are_casefolded_and_overlap_aware(self):
        paths = ('EFI/EFI/SteamOS/GRUB.CFG', 'efi/EFI/SteamOS',
                 'efi/efi/steamos/grub.cfg/child', 'ETC/DEFAULT/GRUB.D/OXP3-NVME.CFG',
                 'etc/default/grub.d', 'etc/default/grub.d/oxp3-nvme.cfg/child')
        engine = mock.Mock(); engine.state_dir = Path('fixture-state'); engine.journals.exists.return_value = False
        for path in paths:
            with self.subTest(store='generic',path=path):
                engine._state.return_value = {'owners':{path:{'component':'other'}}}
                with self.assertRaises(MaintenanceError): s._legacy_conflicts('fixture',engine)
            with self.subTest(store='legacy',path=path):
                engine._state.return_value = {'owners':{}}
                payload = json.dumps({'slots':{'A':{'current':{'paths':{path:{'component':'other'}}}}}}).encode()
                fake_file = contextlib.nullcontext((io.BytesIO(payload),mock.Mock(st_size=len(payload))))
                with mock.patch.object(s, '_directory',side_effect=_private_directory_fixture), \
                     mock.patch.object(s, '_file',return_value=fake_file), \
                     mock.patch.object(s.os.path,'lexists',side_effect=lambda p: Path(p).name in ('.oxp3-maintenance','index.json')):
                    with self.assertRaises(MaintenanceError): s._legacy_conflicts('fixture',engine)
        self.assertFalse(s._overlaps_owned({'efi/EFI/steamos/grub.cfg.backup':{}}))
        self.assertFalse(s._overlaps_owned({'etc/default/grub.d/other.cfg':{}}))

    def test_generic_barrier_blocks_installed_pending_and_uncertain_ownership(self):
        bound = get_binding(topology()); original = dict(efi=s._encode(CONFIG),dropin=None)
        record = dict(version=2,target=s._durable(bound),original=original,applied=s._desired(original),status='installed')
        engine = mock.Mock(); engine.home_root=Path('fixture/home')
        component = {'files':[{'target':'EFI/efi/SteamOS/GRUB.cfg'}]}
        payload = json.dumps(record).encode()
        def file_fixture(path, **kwargs):
            return contextlib.nullcontext((io.BytesIO(payload),mock.Mock(st_size=len(payload))))
        with mock.patch.object(s.os.path,'lexists',return_value=True), \
             mock.patch.object(s,'_directory',side_effect=_private_directory_fixture), \
             mock.patch.object(s,'_file',side_effect=file_fixture), \
             mock.patch.object(s.os,'listdir',return_value=['esp-bbbbbbbb-2222.json']):
            with self.assertRaises(MaintenanceError): s.reject_conflicting_component(engine,component)
            record['status']='restored'; payload=json.dumps(record).encode()
            s.reject_conflicting_component(engine,component)
            payload=b'{invalid'
            with self.assertRaises(MaintenanceError): s.reject_conflicting_component(engine,component)
        with mock.patch.object(s.os.path,'lexists',return_value=True), \
             mock.patch.object(s,'_directory',side_effect=_private_directory_fixture), \
             mock.patch.object(s.os,'listdir',return_value=['pending.json']):
            with self.assertRaises(MaintenanceError): s.reject_conflicting_component(engine,component)
        with mock.patch.object(s.os.path,'lexists',return_value=True), \
             mock.patch.object(s,'_directory',side_effect=_private_directory_fixture), \
             mock.patch.object(s.os,'listdir',return_value=['unrecognized.json']):
            with self.assertRaises(MaintenanceError): s.reject_conflicting_component(engine,component)
        # Unrelated targets do not require access to this independent ledger.
        with mock.patch.object(s.os.path,'lexists',side_effect=AssertionError('unexpected state lookup')):
            s.reject_conflicting_component(engine,{'files':[{'target':'etc/unrelated'}]})

    def test_unrelated_hook_does_not_claim_ssd_boot_targets(self):
        engine=mock.Mock(); engine.home_root=Path('fixture/home')
        component={'files':[{'target':'etc/unrelated'}],'hooks':{'post_install':'hook.sh'}}
        with mock.patch.object(s.os.path,'lexists',side_effect=AssertionError('unrelated hook must not inspect SSD state')):
            s.reject_conflicting_component(engine,component)

    def test_activation_reports_parameter_without_claiming_device_behavior(self):
        self.assertEqual(s._activation('fixture', CONTEXT), 'first_boot_required')
        for raw, expected in [('Y\n','active'), ('N\n','inactive'), ('garbage','unknown')]:
            with self.subTest(raw=raw), mock.patch('builtins.open', mock.mock_open(read_data=raw)):
                self.assertEqual(s._activation('/', None),expected)
        with mock.patch('builtins.open',side_effect=OSError('unavailable')):
            self.assertEqual(s._activation('/', None),'unknown')
        with mock.patch.object(s,'_activation',return_value='unknown'):
            result=s._result('/',None,'verify',False)
            self.assertEqual(result['status'],'verified'); self.assertIsNone(result['restart_required'])
        with mock.patch.object(s,'_activation',return_value='inactive'):
            self.assertTrue(s._result('/',None,'apply',False)['restart_required'])
            self.assertEqual(s._result('/',None,'apply',False)['status'],'already_configured')

    def test_reapply_of_owned_matching_bytes_does_not_replace_first_original(self):
        original = dict(efi=s._encode(CONFIG), dropin=None); applied=s._desired(original)
        record = dict(status='installed',original=original,applied=applied)
        body=dict(kind='ReviewedSsdBootPlan',version=2,action='apply',target={'disposition':'match'})
        review=dict(body,review_sha256=s.digest(body))
        obj=object.__new__(s.SSD); obj.root=Path('fixture'); obj.context=CONTEXT
        obj.store=mock.Mock(); obj.store.lock.return_value=contextlib.nullcontext()
        obj._inspect=mock.Mock(return_value=(review,applied,record,None))
        engine=mock.Mock(); engine._lock=mock.Mock(return_value=contextlib.nullcontext())
        lock_store=mock.Mock(); lock_store.lock.return_value=contextlib.nullcontext()
        with mock.patch.object(s.os,'name','posix'), mock.patch.object(s.os,'geteuid',return_value=0,create=True), \
             mock.patch.object(s,'Engine',return_value=engine), mock.patch.object(s,'Store',return_value=lock_store), \
             mock.patch.object(s.os.path,'lexists',return_value=False), mock.patch.object(s,'_replace') as replace:
            result=obj.execute(review,reviewed_sha256=review['review_sha256'])
        self.assertEqual(result['status'],'already_configured')
        self.assertEqual(result['activation'],'first_boot_required')
        obj.store._state_write.assert_not_called(); replace.assert_not_called()

    def test_existing_installer_and_adapter_state_block_ssd_without_handoff(self):
        engine=mock.Mock(); engine._state.return_value={'owners':{}}
        engine.state_dir=Path('fixture-state'); engine.journals.exists.return_value=False
        for name in ('integration','oxp3-adapter'):
            with self.subTest(name=name), mock.patch.object(s.os.path,'lexists',side_effect=lambda path: Path(path).name == name):
                with self.assertRaises(MaintenanceError): s._legacy_conflicts('fixture',engine)

    def test_model_ssd_preview_binds_original_and_fixed_model_render(self):
        from maintenance import model_efi as model
        bound=get_binding(topology()); expected=model._render(CONFIG)
        body=dict(kind='ReviewedModelEfiPlan',version=2,action='apply',target=bound,source={'package_id':'device:oxp3'},
                  current_sha256=__import__('hashlib').sha256(CONFIG).hexdigest(),expected_efi=s._encode(expected),
                  expected_efi_sha256=__import__('hashlib').sha256(expected).hexdigest())
        plan=dict(body,review_sha256=s.digest(body)); current=dict(efi=s._encode(CONFIG),dropin=None)
        with mock.patch.object(s,'binding',return_value=bound), mock.patch.object(s,'Engine'), \
             mock.patch.object(s,'_legacy_conflicts'), mock.patch.object(s,'_snapshot',return_value=current), \
             mock.patch.object(s.os.path,'lexists',return_value=False):
            preview=s.installer_preflight_model_ssd('fixture',CONTEXT,plan)
            self.assertEqual(preview['model_efi_sha256'],body['expected_efi_sha256'])
            self.assertEqual(preview['ssd_efi_sha256'],__import__('hashlib').sha256(s.render_grub_config(expected)).hexdigest())
            x2_body=dict(body,source={'package_id':'device:x2-mini'})
            x2_plan=dict(x2_body,review_sha256=s.digest(x2_body))
            x2_preview=s.installer_preflight_model_ssd('fixture',CONTEXT,x2_plan)
            self.assertEqual(x2_preview['source'],x2_body['source'])
            self.assertEqual(x2_preview['ssd_efi_sha256'],preview['ssd_efi_sha256'])
            changed=dict(body,expected_efi=s._encode(b'fake'))
            changed['review_sha256']=s.digest(changed)
            with self.assertRaises(MaintenanceError): s.installer_preflight_model_ssd('fixture',CONTEXT,changed)

    @unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'requires real Linux root-owned paths')
    def test_model_ssd_preflight_and_recovery_with_missing_grub_dropin_directory(self):
        from maintenance import model_efi as model
        bound = get_binding(topology())
        expected = model._render(CONFIG)
        body = dict(kind='ReviewedModelEfiPlan', version=2, action='apply', target=bound,
                    source={'package_id': 'device:oxp3'}, current_sha256=__import__('hashlib').sha256(CONFIG).hexdigest(),
                    expected_efi=s._encode(expected), expected_efi_sha256=__import__('hashlib').sha256(expected).hexdigest())
        plan = dict(body, review_sha256=s.digest(body))
        with tempfile.TemporaryDirectory(dir=os.environ.get('BOILER_SSD_TEST_TMP')) as directory:
            root = Path(directory)
            dropin = root / s.DROPIN
            dropin.parent.parent.mkdir(parents=True)
            with mock.patch.object(s, 'binding', return_value=bound), mock.patch.object(s, '_target', return_value=CONFIG), \
                 mock.patch.object(s, 'Engine'), mock.patch.object(s, '_legacy_conflicts'):
                preview = s.installer_preflight_model_ssd(root, CONTEXT, plan)
                self.assertEqual(preview['dropin_before_sha256'], s.digest(None))
                self.assertFalse(dropin.parent.exists())
                desired = s._desired(s._snapshot(root, bound))['dropin']
                s._replace_dropin(root, None, desired)
                self.assertEqual(s._dropin(root), desired)
                s._replace_dropin(root, desired, None)
                self.assertIsNone(s._dropin(root))
                self.assertEqual(list(dropin.parent.iterdir()), [])

    @unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'requires real Linux root-owned paths')
    def test_dropin_does_not_accept_missing_higher_parent_or_symlink_directory(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('BOILER_SSD_TEST_TMP')) as directory:
            root = Path(directory)
            dropin = root / s.DROPIN
            with self.assertRaises(FileNotFoundError):
                s._dropin(root)
            dropin.parent.parent.mkdir(parents=True)
            dropin.parent.symlink_to(root / 'missing')
            with self.assertRaises(OSError) as error:
                s._dropin(root)
            self.assertNotIsInstance(error.exception, FileNotFoundError)

    def test_installer_requires_both_distinct_slot_plans(self):
        a = {'target':dict(slot='A',disk='/dev/vdc',home_uuid='home',root_partuuid='a',esp_partuuid='x')}
        b = {'target':dict(slot='B',disk='/dev/vdc',home_uuid='home',root_partuuid='b',esp_partuuid='y')}
        with mock.patch.object(s, 'SSD') as factory:
            factory.return_value.plan.side_effect = [a,b]
            self.assertEqual(s.installer_plans('a','b',CONTEXT), [a,b])
            factory.return_value.plan.side_effect = [a,dict(target=dict(b['target'],esp_partuuid='x'))]
            with self.assertRaises(MaintenanceError): s.installer_plans('a','b',CONTEXT)


if __name__ == '__main__': unittest.main()
