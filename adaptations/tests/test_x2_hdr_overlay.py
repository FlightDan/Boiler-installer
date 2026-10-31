"""HDR-only transformation tests against explicitly pinned public package bytes."""
import os
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BASE_INPUT = os.environ.get('BOILER_X2_HDR_BASE', '')
BASE = Path(BASE_INPUT).absolute() if BASE_INPUT else None
spec=importlib.util.spec_from_file_location('_boiler_x2_hdr_overlay_test_builder',HERE.parent/'scripts/build-x2-hdr-overlay.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec)
    missing=object();previous=sys.modules.get(name,missing)
    sys.modules[name]=value
    try:
        spec.loader.exec_module(value)
    finally:
        if previous is missing:sys.modules.pop(name,None)
        else:sys.modules[name]=previous
    return value

@unittest.skipUnless(BASE_INPUT, 'Explicit BOILER_X2_HDR_BASE package input is required')
class HdrOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='hdr-verified-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work=Path(cls.temp.name);cls.output=cls.work/'overlay'
        cls.lock=builder.build(BASE,cls.output)
        cls.data={entry['path']:(cls.output/entry['source']).read_bytes() for entry in cls.lock['files']}
        cls.old_manifest=json.loads((BASE/'manifest.json').read_bytes())
        cls.old={entry['path']:entry for entry in cls.old_manifest['files']}
    def legacy(self,path):return self.data['payload/legacy/'+path]
    def test_explicit_base_anchor(self):
        with tempfile.TemporaryDirectory(dir=self.work) as tmp:
            p=Path(tmp);(p/'manifest.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'accepted OXP3'):
                builder.build(p,p.parent/'bad-output')
            self.assertFalse((p.parent/'bad-output').exists())
    def test_output_cannot_pollute_base_or_overwrite(self):
        with self.assertRaises(ValueError):builder.build(BASE,self.output)
        with self.assertRaises(ValueError):builder.build(BASE,BASE/'new-hdr-output')
    def test_locked_overlay_has_exact_files_and_authentic_preimages(self):
        self.assertEqual(self.lock['role'],'hdr')
        expected={'overlay-lock.json'}
        for record in self.lock['files']:
            self.assertEqual(record['base_sha256'],self.old[record['path']]['sha256'])
            self.assertEqual(record['sha256'],builder.sha(self.data[record['path']]))
            self.assertEqual(record['size'],len(self.data[record['path']]))
            expected.add(record['source'])
        self.assertEqual(expected,{p.relative_to(self.output).as_posix() for p in self.output.rglob('*') if p.is_file()})
    def test_rebuild_is_byte_reproducible(self):
        second=self.work/'second';again=builder.build(BASE,second)
        self.assertEqual(self.lock,again)
        for file in self.output.rglob('*'):
            if file.is_file():self.assertEqual(file.read_bytes(),(second/file.relative_to(self.output)).read_bytes())
    def test_game_generation_uses_only_x2_luminance(self):
        folder=self.work/'game';folder.mkdir(exist_ok=True)
        for name in ('generate.py','upstream.lua'):(folder/name).write_bytes(self.legacy('components/hdr/'+name))
        generator=module('x2_game_generator',folder/'generate.py')
        lua=generator.generate()
        self.assertIn('max_content_light_level = 700,',lua)
        self.assertIn('max_frame_average_luminance = 475,',lua)
        self.assertNotIn('max_content_light_level = 1100,',lua)
        self.assertNotIn('max_frame_average_luminance = 472,',lua)
        for anchor in ('[60] = 1904','[144] = 56','eotf = gamescope.eotf.gamma22','== "AMS881KB01-0"'):
            self.assertIn(anchor,lua)
        with self.assertRaises(RuntimeError):generator.validate(lua.replace('max_content_light_level = 700,','max_content_light_level = 1100,'))
        with self.assertRaises(RuntimeError):generator.validate(lua.replace('max_frame_average_luminance = 475,','max_frame_average_luminance = 472,'))
    def test_maintenance_and_new_install_use_same_game_profile(self):
        tree=ast.parse(self.legacy('maintenance/app/oxp3_maintenance/components.py'))
        node=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='_hdr')
        namespace={'_read':lambda root,path:self.legacy('components/hdr/upstream.lua'),'_entry':lambda data,**kw:dict(data=data,**kw)}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'pinned-hdr-function','exec'),namespace)
        result=namespace['_hdr'](None)
        self.assertEqual(result['home/deck/.config/gamescope/scripts/98-oxp3-oled-hdr.lua']['data'],self.legacy('components/hdr/upstream.lua'))
    def test_profile_metadata_game_desktop_separate(self):
        doc=json.loads(self.legacy('components/hdr/profile.json'))
        self.assertEqual((doc['mapping']['full_frame'],doc['mapping']['peak']),(475,700))
        self.assertEqual(self.lock['mapping'],dict(game=dict(peak=700,full_frame=475),desktop=dict(peak=700,full_frame=475)))
        self.assertEqual(doc['desktop_hdr'],'No KDE HDR/WCG changes')
    def test_desktop_launch_forces_profile_with_abi_and_recovery_fallback(self):
        text=self.legacy('components/desktop-hdr/launch.sh').decode()
        self.assertIn('export KWIN_X2MINI_HDR_PROFILE=1',text)
        self.assertNotIn('if /usr/bin/python3 -I -B "$trial" --device-check; then',text)
        self.assertIn('"$trial" --recover-startup',text)
        self.assertIn('[ "$current" = "$expected" ]',text)
        self.assertIn('exec /usr/bin/kwin_wayland_wrapper --xwayland',text)
        self.assertEqual(self.legacy('integration/desktop-hdr/source/launch-stable.sh'),self.legacy('components/desktop-hdr/launch.sh'))
    def test_runtime_tar_only_changes_canonical_launcher(self):
        path='payload/legacy/integration/desktop-hdr/accepted-runtime.tar'
        def members(raw):
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                result={}
                for info in archive:
                    data=archive.extractfile(info).read() if info.isfile() else None
                    attrs=(info.type,info.mode,info.uid,info.gid,info.mtime,info.linkname,info.uname,info.gname)
                    result[info.name]=(attrs,data)
                return result
        before=members((BASE/path).read_bytes());after=members(self.data[path])
        self.assertEqual(set(before),set(after))
        changed=[name for name in before if before[name]!=after[name]]
        self.assertEqual(changed,['./bin/oxp3-kwin-launch'])
        self.assertEqual(after[changed[0]][1],self.legacy('components/desktop-hdr/launch.sh'))
    def test_canonical_user_device_helpers_stay_synchronized(self):
        expected=self.legacy('components/desktop-hdr/x2mini_device.py')
        self.assertEqual(self.legacy('components/x2mini_hdr_device.py'),expected)
        self.assertEqual(self.legacy('maintenance/app/oxp3_maintenance/x2mini_hdr_device.py'),expected)
    def test_machine_mismatch_is_advisory_for_launcher(self):
        path=self.work/'x2mini_device.py';path.write_bytes(self.legacy('components/desktop-hdr/x2mini_device.py'))
        device=module('overlay_x2mini_device',path)
        with patch.object(device,'is_x2mini',return_value=False),patch.object(device,'_home_info',return_value=(self.work,(1000,1000))),patch.object(device,'_trial_lock',return_value=contextlib.nullcontext()),patch.object(device,'assert_trial_available'),patch.object(device,'_install_launchers_unlocked',return_value=('app','desktop')) as install,contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(device.install_launchers(self.work,'trial.py'),('app','desktop'))
            install.assert_called_once()
        self.assertIn('continuing',printed.getvalue())
    def test_trial_machine_check_returns_available_with_warning(self):
        folder=self.work/'trial';folder.mkdir(exist_ok=True)
        (folder/'x2mini_device.py').write_bytes(self.legacy('components/desktop-hdr/x2mini_device.py'))
        (folder/'hdr_trial.py').write_bytes(self.legacy('components/desktop-hdr/hdr_trial.py'))
        with patch.dict(sys.modules,{'x2mini_device':module('x2mini_device',folder/'x2mini_device.py')}):
            trial=module('overlay_hdr_trial',folder/'hdr_trial.py')
        with patch.object(trial,'is_x2mini',return_value=False),patch.object(sys,'argv',['hdr_trial.py','--device-check']),contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(trial.main(),0)
        self.assertIn('remains available',printed.getvalue())
    def test_changed_python_files_compile(self):
        for name,data in self.data.items():
            if name.endswith('.py'):compile(data,name,'exec')
    def test_no_other_component_payload_changed(self):
        for name in self.data:
            self.assertNotRegex(name,r'(battery|opti2xmx|gyro|cpu|fan|waydroid|partition)')
        self.assertEqual(len(self.data),17)

if __name__=='__main__':unittest.main()
