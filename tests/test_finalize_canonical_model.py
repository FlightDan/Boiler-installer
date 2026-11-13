import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('canonical_model_finalizer',ROOT/'scripts/finalize-canonical-model.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class FinalizeCanonicalModelTests(unittest.TestCase):
    def test_declared_resource_closes_the_legacy_handler_and_other_defaults_remain(self):
        from protocol.model_options import validated_selection
        from protocol.manifest import ProtocolError
        def package():
            return dict(id='device:oxp3',selection_contract={'kind':'oxp3-legacy-options-v1','component':'oxp3:legacy-adaptation'},
                resources=[{'id':'resource:intel-be201-firmware','path':'common/network/intel/be201/resource.toon'}],
                legacy_capabilities=[
                dict(id='oxp3:input',category='repairs',dependencies=[],available=True,default=True),
                dict(id='oxp3:wifi',category='repairs',dependencies=[],available=True,default=False,reason={})])
        previous=dict(repairs=['oxp3:input'],plugins=[]);reviewed=dict(repairs=['oxp3:input'],plugins=[])
        value=package();choice=module.configure_wifi_selection(value,previous,reviewed)
        self.assertEqual(choice,previous);self.assertTrue(value['legacy_capabilities'][0]['default'])
        wifi=value['legacy_capabilities'][1];self.assertFalse(wifi['available']);self.assertFalse(wifi['default'])
        # Ownership, not policy: the reason names the resource, and no gate
        # evidence is recorded for a capability the table does not own.
        self.assertIn('resource',wifi['reason']['en'])
        self.assertNotIn('capability_gates',value)
        with self.assertRaisesRegex(ProtocolError,'Unavailable or unknown'):
            validated_selection(value,['oxp3:input','oxp3:wifi'],[],acknowledge_update_hold=True)
        self.assertEqual(validated_selection(value,['oxp3:input'],[],acknowledge_update_hold=True)['repairs'],['oxp3:input'])

    def test_empty_or_blank_placeholder_reason_is_replaced(self):
        # Two shapes reach this code: {} and {'en': '', 'zh': ''}. ``all()`` over
        # an empty mapping is True, so an emptiness check is required or the user
        # sees a capability with no explanation at all.
        for placeholder in ({}, {'en': '', 'zh': ''}, {'en': '  ', 'zh': ''}):
            with self.subTest(placeholder=placeholder):
                value={'id':'device:oxp3',
                    'resources':[{'id':'resource:intel-be201-firmware',
                                  'path':'common/network/intel/be201/resource.toon'}],
                    'legacy_capabilities':[
                    dict(id='oxp3:input',category='repairs',dependencies=[],available=True,default=True),
                    dict(id='oxp3:wifi',category='repairs',dependencies=[],available=True,
                         default=False,reason=dict(placeholder))]}
                module.configure_wifi_selection(value,{'repairs':['oxp3:input'],'plugins':[]},
                                                {'repairs':['oxp3:input'],'plugins':[]})
                reason=value['legacy_capabilities'][1]['reason']
                self.assertIn('resource',reason['en'])
                self.assertIn('BE201',reason['zh'])

    def test_reviewed_reason_is_preserved(self):
        reviewed={'en':'Reviewed wording.','zh':'已审阅的说明。'}
        value={'id':'device:oxp3',
            'resources':[{'id':'resource:intel-be201-firmware',
                          'path':'common/network/intel/be201/resource.toon'}],
            'legacy_capabilities':[
            dict(id='oxp3:input',category='repairs',dependencies=[],available=True,default=True),
            dict(id='oxp3:wifi',category='repairs',dependencies=[],available=True,
                 default=False,reason=dict(reviewed))]}
        module.configure_wifi_selection(value,{'repairs':['oxp3:input'],'plugins':[]},
                                        {'repairs':['oxp3:input'],'plugins':[]})
        self.assertEqual(value['legacy_capabilities'][1]['reason'],reviewed)

    def test_undeclared_resource_leaves_the_handler_alone(self):
        # Without the resource record there is no second owner, so nothing here
        # may silently disable the legacy handler. Ownership is derived from the
        # declaration, not asserted unconditionally.
        value={'id':'device:oxp3','legacy_capabilities':[
            dict(id='oxp3:wifi',category='repairs',dependencies=[],available=True,default=True)]}
        choice=module.configure_wifi_selection(value,{'repairs':['oxp3:wifi'],'plugins':[]},
                                               {'repairs':['oxp3:wifi'],'plugins':[]})
        self.assertEqual(choice,{'repairs':['oxp3:wifi'],'plugins':[]})
        self.assertIs(value['legacy_capabilities'][0]['available'],True)
        self.assertNotIn('capability_gates',value)

    def test_gate_application_is_the_only_path_and_has_no_canonical_switch(self):
        # The historical branch re-enabled BE201 and re-added it to the selection
        # whenever the preparation proof lacked a source commit. That fail-open
        # path must not come back in any form.
        import inspect
        signature = inspect.signature(module.configure_wifi_selection)
        self.assertEqual(list(signature.parameters), ['manifest', 'previous', 'reviewed'])
        self.assertNotIn('source_commit', inspect.getsource(module.configure_wifi_selection))

    def test_selection_that_includes_a_gated_capability_is_rejected(self):
        value={'id':'device:oxp3','legacy_capabilities':[
            dict(id='oxp3:input',category='repairs',dependencies=[],available=True,default=True),
            dict(id='oxp3:gm7-resume',category='repairs',dependencies=[],available=True,default=False)]}
        with self.assertRaisesRegex(ValueError,'keeps unavailable'):
            module.configure_wifi_selection(value,{'repairs':['oxp3:input','oxp3:gm7-resume'],'plugins':[]},
                                            {'repairs':['oxp3:input'],'plugins':[]})

    def test_reviewed_example_naming_a_resource_owned_capability_is_accepted(self):
        # The TOON asks for BE201 through the resource; the frozen example
        # selection still names the legacy identity. That is the request being
        # served, not an unavailable capability.
        value={'id':'device:oxp3',
            'resources':[{'id':'resource:intel-be201-firmware','path':'common/network/intel/be201/resource.toon'}],
            'legacy_capabilities':[
            dict(id='oxp3:input',category='repairs',dependencies=[],available=True,default=True),
            dict(id='oxp3:wifi',category='repairs',dependencies=[],available=True,default=False)]}
        choice=module.configure_wifi_selection(value,{'repairs':['oxp3:input'],'plugins':[]},
                                               {'repairs':['oxp3:input','oxp3:wifi'],'plugins':[]})
        self.assertEqual(choice,{'repairs':['oxp3:input'],'plugins':[]})

    def test_reviewed_example_naming_an_unrelated_unavailable_capability_is_rejected(self):
        value={'id':'device:oxp3','legacy_capabilities':[
            dict(id='oxp3:input',category='repairs',dependencies=[],available=True,default=True),
            dict(id='oxp3:gm7-resume',category='repairs',dependencies=[],available=False,default=False)]}
        with self.assertRaisesRegex(ValueError,'unavailable capability'):
            module.configure_wifi_selection(value,{'repairs':['oxp3:input'],'plugins':[]},
                                            {'repairs':['oxp3:input','oxp3:gm7-resume'],'plugins':[]})

    def test_legacy_version_targets_id_independent_of_component_order(self):
        value={'components':[{'id':'oxp3:microphone','version':'1.0.0'},
                             {'id':'oxp3:legacy-adaptation','version':'1.0.0'}]}
        module.version_legacy_bridge(value,'2.0.0')
        self.assertEqual(value['components'][0]['version'],'1.0.0')
        self.assertEqual(value['components'][1]['version'],'2.0.0')
        value['components'].append({'id':'oxp3:legacy-adaptation','version':'1.0.0'})
        with self.assertRaisesRegex(ValueError,'Exactly one legacy bridge'):
            module.version_legacy_bridge(value,'3.0.0')

    def test_replaced_component_versions_pass_real_update_planner(self):
        from maintenance import updates
        from maintenance.engine import Engine
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);package=root/'package';package.mkdir()
            old_versions={'oxp3:xevmafix':'2026.10.29+kernel7.2.4.2','oxp3:speaker-route':'2026.10.29'}
            components=[]
            for identity,old_version in old_versions.items():
                name=identity.replace(':','-')+'.bin'; raw=('new-'+identity).encode(); (package/name).write_bytes(raw)
                components.append(dict(schema_version=1,kind='ComponentManifest',id=identity,version=old_version,
                    requires=[],conflicts=[],files=[dict(source=name,target='usr/lib/test/'+name,size=len(raw),sha256=module.sha(raw),mode=420)]))
            manifest=dict(schema_version=1,kind='DevicePackage',id='device:oxp3',version='2.0.0',
                          source=dict(kind='github',repository='FlightDan/Boiler-installer',commit='a'*40),
                          components=components,files=[dict(path=p.name,size=p.stat().st_size,sha256=module.sha(p.read_bytes())) for p in package.iterdir()])
            state={'components':{identity:{'version':version,'transaction':'a'*32} for identity,version in old_versions.items()},'owners':{}}
            environment=dict(architecture='x86_64',os_id='steamos',os_version='3.10',kernels=['kernel-test'])
            current=dict(pending_transactions=[],pending_platform_recovery=None,environment=environment,
                         components={identity:{'verified':True} for identity in old_versions},target={},state_sha256='s',provenance_sha256='p')
            engine=mock.Mock(spec=Engine);engine._state.return_value=state;engine.retained_owned.return_value=[]
            engine._target.side_effect=lambda target: root/'unoccupied'/target
            source=dict(kind='github',repository='FlightDan/Boiler-installer',commit='a'*40)
            def plan():
                raw=module.json_bytes(manifest);(package/'manifest.json').write_bytes(raw)
                entry=dict(id='device:oxp3',version='2.0.0',sha256=module.sha(raw),size=len(raw),source=source)
                with mock.patch.object(updates,'inventory',return_value=current),mock.patch.object(updates,'Engine',return_value=engine), \
                     mock.patch.object(updates,'_provenance',return_value={'packages':{}}),mock.patch.object(updates,'_signed_snapshot',return_value=({'packages':[entry],'revocations':[]},{})), \
                     mock.patch.object(updates,'_check_registry'),mock.patch.object(updates,'_unchanged_component',return_value=False):
                    return updates.plan_update(root,root/'index',package,'device:oxp3','2.0.0',selected=list(old_versions))
            with self.assertRaisesRegex(updates.UpdateError,'Equal-precedence'):
                plan()
            module.version_replaced_components(*components)
            reviewed=plan()
            self.assertEqual({row['id'] for row in reviewed['actions']},set(old_versions))
            self.assertTrue(all(row['action']=='upgrade' for row in reviewed['actions']))
            self.assertTrue(all(row['to_version']=='2026.10.30' for row in reviewed['actions']))

    def test_gyro_metadata_records_actual_targets_and_x2_orientation_limit(self):
        value={'legacy_capabilities':[{'id':'oxp3:gm7-resume','available':False},
                                      {'id':'oxp3:gyro','notes':{'en':'Existing.','zh':'原说明。'}}]}
        module.update_capabilities(value,'device:x2-mini',['var/lib/oxp3-gyro-v1/binding.py'])
        gyro=value['legacy_capabilities'][1]
        self.assertEqual(gyro['targets'],['var/lib/oxp3-gyro-v1/binding.py'])
        self.assertIn('not been independently measured',gyro['notes']['en'])
        # GM7 ownership is now enforced by the shared table, not by a local check.
        from protocol.capability_gates import assert_gates
        value['legacy_capabilities'][0]['available']=True
        with self.assertRaisesRegex(ValueError,'still available: oxp3:gm7-resume'):
            assert_gates(value)

    def test_stage_snapshot_rejects_changed_bytes_and_uses_captured_values(self):
        with tempfile.TemporaryDirectory() as temp:
            stage=Path(temp); rows=[]
            for index in range(77):
                name='source/%03d.bin'%index; raw=('data-%d'%index).encode()
                path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
                rows.append(dict(path=name,size=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
            project=stage/'project';(project/'adaptations').mkdir(parents=True)
            input_lock=project/'adaptations/resource-build-inputs.json';input_lock.write_bytes(b'fixture lock')
            proof=dict(source_commit=module.bmi260.COMMIT,deployable=False,official_release=False,
                       project_input_lock='adaptations/resource-build-inputs.json',
                       project_input_lock_sha256=module.sha(b'fixture lock'),files=rows)
            semantic=dict(proof);semantic.pop('project_input_lock_sha256')
            digest=module.sha(json.dumps(semantic,sort_keys=True,separators=(',',':')).encode())
            (stage/'preparation.json').write_bytes(module.json_bytes(proof))
            # Keep the project input outside the staged inventory.
            project.rename(stage.parent/(stage.name+'-project'))
            project=stage.parent/(stage.name+'-project')
            self.addCleanup(lambda: __import__('shutil').rmtree(project))
            with mock.patch.object(module.bmi260,'SEMANTIC_SHA',digest), mock.patch.object(module,'ROOT',project):
                content,_=module.stage_snapshot(stage)
                (stage/'source/000.bin').write_bytes(b'changed after review')
                self.assertEqual(content['source/000.bin'],b'data-0')
                with self.assertRaisesRegex(ValueError,'stage member differs'):
                    module.stage_snapshot(stage)
                (stage/'source/000.bin').write_bytes(b'data-0')
                (project/'adaptations/resource-build-inputs.json').write_bytes(b'changed lock')
                with self.assertRaisesRegex(ValueError,'semantic proof differs'):
                    module.stage_snapshot(stage)

    def test_gyro_keeps_model_profile_and_existing_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); integration=root/'payload/legacy/integration'; (integration/'gyro').mkdir(parents=True)
            helper=root/'payload/legacy/maintenance/app/oxp3_maintenance/gyro_binding.py'
            helper.parent.mkdir(parents=True);helper.write_bytes(b'fixed binding')
            targets={'var/lib/oxp3-gyro-v1/load.sh':(b'new loader',0o755),
                     'var/lib/oxp3-gyro-v1/binding.py':(b'fixed binding',0o644)}
            profile={'etc/inputplumber/devices.d/profile.yaml':(b'upstream profile',0o644)}
            rows=[dict(target='var/lib/oxp3-gyro-v1/load.sh',source='gyro/000.bin',mode=0o755,user=False),
                  dict(target='etc/inputplumber/devices.d/profile.yaml',source='gyro/001.bin',mode=0o644,user=False)]
            (integration/'gyro/000.bin').write_bytes(b'old loader')
            (integration/'gyro/001.bin').write_bytes(b'model specific connector profile')
            (integration/'manifest.json').write_bytes(module.json_bytes({'gyro':{'files':rows}}))
            result=module.update_gyro(root,'device:x2-mini',targets,profile)
            self.assertEqual(set(result),set(targets)|set(profile))
            self.assertEqual((integration/'gyro/001.bin').read_bytes(),b'model specific connector profile')
            self.assertEqual((integration/'gyro/000.bin').read_bytes(),b'new loader')
            manifest=json.loads((integration/'manifest.json').read_bytes())
            self.assertEqual(len(manifest['gyro']['files']),3)
            self.assertEqual((integration/'gyro/canonical-000.bin').read_bytes(),b'fixed binding')

    def test_gyro_rejects_unknown_legacy_target_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); integration=root/'payload/legacy/integration'; integration.mkdir(parents=True)
            data={'gyro':{'files':[dict(target='etc/unknown',source='gyro/000.bin',mode=420)]}}
            path=integration/'manifest.json'; original=module.json_bytes(data);path.write_bytes(original)
            with self.assertRaisesRegex(ValueError,'Unexpected existing gyro'):
                module.update_gyro(root,'device:oxp3',{}, {})
            self.assertEqual(path.read_bytes(),original)


if __name__=='__main__': unittest.main()
