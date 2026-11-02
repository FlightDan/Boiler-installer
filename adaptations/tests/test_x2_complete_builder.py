"""Complete package assembly invariants; all targets are temporary files."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('boiler_x2_complete_builder', ROOT/'adaptations/scripts/build-x2-complete.py')
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)

def sha(data): return hashlib.sha256(data).hexdigest()

class CompleteAssembly(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='x2-complete-builder-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def overlay(self, role='bridge', target='adapter.py', payload=b'new bridge\n', old=b'old bridge\n'):
        folder=self.root/'overlay'; folder.mkdir()
        (folder/'payload.bin').write_bytes(payload)
        row=dict(path=target,source='payload.bin',size=len(payload),sha256=sha(payload),base_sha256=sha(old))
        lock=dict(schema_version=1,kind='BoilerX2CompleteOverlay',role=role,
                  base_manifest_sha256=builder.BASE_ANCHOR,files=[row])
        raw=builder.encoded(lock); (folder/'overlay-lock.json').write_bytes(raw)
        return folder,sha(raw),{target:dict(path=target,size=len(old),sha256=sha(old))}

    def test_overlay_requires_independent_pin_and_original_bytes(self):
        folder,pin,base=self.overlay()
        result=builder.overlay(folder,'bridge',pin,base)
        self.assertEqual({'adapter.py'},set(result))
        with self.assertRaisesRegex(builder.BuildError,'independent pin'):
            builder.overlay(folder,'bridge','0'*64,base)
        base['adapter.py']['sha256']='1'*64
        with self.assertRaisesRegex(builder.BuildError,'original bytes'):
            builder.overlay(folder,'bridge',pin,base)

    def test_overlay_changed_bytes_or_extra_dirty_file_rejected(self):
        folder,pin,base=self.overlay()
        (folder/'payload.bin').write_bytes(b'other bytes')
        with self.assertRaisesRegex(builder.BuildError,'Changed overlay'):
            builder.overlay(folder,'bridge',pin,base)
        (folder/'payload.bin').write_bytes(b'new bridge\n')
        (folder/'unreviewed-research.txt').write_bytes(b'no arbitrary source tree copies')
        with self.assertRaisesRegex(builder.BuildError,'Unlisted files'):
            builder.overlay(folder,'bridge',pin,base)

    def test_parent_traversal_rejected_even_with_rehashed_lock(self):
        folder,pin,base=self.overlay()
        lock=json.loads((folder/'overlay-lock.json').read_bytes())
        lock['files'][0]['source']='../outside.txt'
        raw=builder.encoded(lock); (folder/'overlay-lock.json').write_bytes(raw)
        with self.assertRaisesRegex(builder.BuildError,'Unsafe relative'):
            builder.overlay(folder,'bridge',sha(raw),base)

    def test_bridge_cannot_change_nonbridge_or_cpu(self):
        folder,pin,base=self.overlay(target='payload/legacy/components/controls/cpu/cpu_control.py')
        with self.assertRaisesRegex(builder.BuildError,'Protected'):
            builder.overlay(folder,'bridge',pin,base)

    def test_reserved_or_private_paths_rejected(self):
        for name in ('CON.txt','safe/../escape','safe/.local/secret','safe/trailing.','a\\b'):
            with self.subTest(name=name), self.assertRaises(builder.BuildError):
                builder.relative(name)

    def test_hdr_cannot_change_unrelated_payload(self):
        folder,pin,base=self.overlay(role='hdr',target='payload/legacy/components/input.py')
        with self.assertRaisesRegex(builder.BuildError,'non-HDR'):
            builder.overlay(folder,'hdr',pin,base)

    def test_wrong_overlay_role_and_base_rejected(self):
        folder,pin,base=self.overlay()
        with self.assertRaisesRegex(builder.BuildError,'Wrong overlay'):
            builder.overlay(folder,'hdr',pin,base)
        lock=json.loads((folder/'overlay-lock.json').read_bytes()); lock['base_manifest_sha256']='0'*64
        raw=builder.encoded(lock); (folder/'overlay-lock.json').write_bytes(raw)
        with self.assertRaisesRegex(builder.BuildError,'Wrong overlay'):
            builder.overlay(folder,'bridge',sha(raw),base)

    def test_all_four_components_and_twenty_eight_capabilities_required(self):
        components=[dict(id='oxp3:'+str(n)) for n in range(4)]
        capabilities=[dict(id='oxp3:option-'+str(n)) for n in range(28)]
        base=dict(components=components,legacy_capabilities=capabilities)
        model=dict(package=dict(inherited_components=[row['id'] for row in components],
                               inherited_legacy_capabilities=[row['id'] for row in capabilities]))
        self.assertEqual((4,28),tuple(map(len,builder.check_coverage(base,model))))
        model['package']['inherited_legacy_capabilities'].pop()
        with self.assertRaisesRegex(builder.BuildError,'omits'):
            builder.check_coverage(base,model)

    def test_output_cannot_alias_or_pollute_input(self):
        base=self.root/'base';base.mkdir()
        with self.assertRaisesRegex(builder.BuildError,'contaminate'):
            builder.output_path(base/'subdir'/'..'/'output',(base,))
        output=self.root/'fresh'
        self.assertEqual(output,builder.output_path(output,(base,)))
        output.mkdir()
        with self.assertRaisesRegex(builder.BuildError,'fresh'):
            builder.output_path(output,(base,))

    def test_final_copy_cannot_publish_changed_overlay_bytes(self):
        source=self.root/'source.py';source.write_bytes(b'changed after preflight')
        target=self.root/'output/source.py'
        with self.assertRaisesRegex(builder.BuildError,'changed while copying'):
            builder.copy_approved(source,target,dict(size=len(b'approved'),sha256=sha(b'approved')))

    def test_nested_manifests_are_real_inventory_members(self):
        root=self.root/'package';nested=root/'payload/maintenance';nested.mkdir(parents=True)
        (root/'manifest.json').write_bytes(b'{}')
        (nested/'manifest.json').write_bytes(b'{"nested":true}')
        rows=builder.inventory(root)
        self.assertEqual(['payload/maintenance/manifest.json'],[row['path'] for row in rows])

    def test_component_source_digests_regenerated_without_changing_targets(self):
        metadata=dict(components=[dict(id='oxp3:speaker-route',files=[
            dict(source='speaker.py',target='usr/lib/speaker.py',mode=0o644,size=1,sha256='old')])])
        rows=[dict(path='speaker.py',size=42,sha256='new')]
        builder.refresh_component_inventory(metadata,rows)
        self.assertEqual(dict(source='speaker.py',target='usr/lib/speaker.py',mode=0o644,size=42,sha256='new'),metadata['components'][0]['files'][0])

    def test_final_xevma_output_refreshes_component_inventory(self):
        original=(ROOT/'adaptations/brands/onexplayer/oxp3/current/xevma-20261029/load.py').read_bytes()
        spec=importlib.util.spec_from_file_location('historical_bridge',ROOT/'adaptations/scripts/build-complete-bridge.py')
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        name='upstream-repairs/xevmafix/load.py'
        staged=self.root/'package'/name;staged.parent.mkdir(parents=True)
        historical=bridge.transform(name,original);staged.write_bytes(historical)
        self.assertEqual(builder.finalize_xevma_loader(self.root/'package',ROOT,{name: {}}),name)
        final=staged.read_bytes()
        self.assertIn(b"subprocess.run(['modprobe', 'xe'], executable='/usr/bin/kmod', check=True)",final)
        self.assertIn(b'model differs from OXP3',final)
        metadata={'components':[{'id':'oxp3:xevmafix','files':[{'source':name,'target':'usr/lib/load.py','sha256':'old','size':0}]}]}
        builder.refresh_component_inventory(metadata,builder.inventory(self.root/'package'))
        self.assertEqual(metadata['components'][0]['files'][0]['sha256'],sha(final))
        with self.assertRaises(ValueError): builder.finalize_xevma_loader(self.root/'package',ROOT,{name: {}})

    def test_embedded_maintenance_manifest_regenerated_from_final_bytes(self):
        package=self.root/'package'
        app=package/'payload/legacy/maintenance/app/oxp3_maintenance';app.mkdir(parents=True)
        (app/'components.py').write_bytes(b'new reviewed HDR mapping')
        manifest=package/builder.GENERATED
        manifest.write_bytes(builder.encoded(dict(schema=1,publisher_key=True,files={'oxp3_maintenance/components.py':'old'})))
        self.assertTrue(builder.refresh_maintenance(package))
        self.assertEqual(sha(b'new reviewed HDR mapping'),json.loads(manifest.read_bytes())['files']['oxp3_maintenance/components.py'])
        self.assertFalse(builder.refresh_maintenance(package))

    def test_zip_bytes_are_deterministic_and_include_nested_manifest(self):
        package=self.root/'package';package.mkdir()
        (package/'manifest.json').write_bytes(b'{"schema_version":1}')
        (package/'data.bin').write_bytes(b'hello')
        rows=builder.inventory(package)
        a,b=self.root/'a.zip',self.root/'b.zip'
        builder.deterministic_zip(package,a,rows);builder.deterministic_zip(package,b,rows)
        self.assertEqual(a.read_bytes(),b.read_bytes())
        with zipfile.ZipFile(a) as archive:
            self.assertEqual({'manifest.json','data.bin'},set(archive.namelist()))

if __name__ == '__main__': unittest.main()
