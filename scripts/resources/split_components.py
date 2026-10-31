"""Build independent data-only resources from the public, immutable source lock.

No cached model manifest/tree or old integration runtime is an input. An optional
individual xevma module is explicitly supplied and checked against module-lock.
The output is unsigned; production authentication remains the host's job.
"""
from __future__ import annotations
import argparse
import ast
import configparser
import difflib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))

COMMIT = '72f864e0498c10366493c74608c5ffed62cc99ca'
COMMON = 'common/scripts/'
OXP = 'brands/onexplayer/oxp3/scripts/'
KERNEL = '7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
GPL_LICENSE_SHA256 = '0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0'
GUARD_SHA256 = 'a5c4b028df4ff3e6a36861950f8270f6aee90e0e51f89ec74402f7ec84c5d59d'


def sha(data): return hashlib.sha256(data).hexdigest()
def jb(value): return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()


def path(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or any(p in ('', '.', '..') for p in name.split('/')):
        raise ValueError('Unsafe source path')
    root = Path(root).absolute()
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink() or getattr(ancestor, 'is_junction', lambda: False)():
            raise ValueError('Linked source root')
    target = root
    for part in name.split('/'):
        target /= part
        if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
            raise ValueError('Linked source member')
    return target


class Sources:
    def __init__(self, root, xevma_module=None):
        self.root = Path(root)
        self.lock_raw = path(root, 'source-lock.json').read_bytes()
        self.lock = json.loads(self.lock_raw)
        if self.lock['source'] != dict(kind='github', repository='FlightDan/OneXPlayer3-SteamOS', commit=COMMIT):
            raise ValueError('Unexpected public source lock')
        rows = self.lock['files']
        self.rows = {row['path']: row for row in rows}
        if len(rows) != len(self.rows): raise ValueError('Duplicate source inventory')
        self.used = {}
        self.current_used = {}
        self.current_lock = None
        self.upstream_used = {}
        self.upstream_lock = None
        self.candidate_used = {}
        self.xevma_module = Path(xevma_module) if xevma_module is not None else None

    def read(self, name):
        if name=='candidate/xevmafix.ko':
            if self.xevma_module is None:raise ValueError('Explicit locked xevma module input is required')
            lock_name='brands/onexplayer/oxp3/current/xevma-20261029/module-lock.json'
            build_lock=json.loads(self.read(lock_name));expected=build_lock['module']
            data=self.xevma_module.read_bytes()
            if len(data)!=expected['size'] or sha(data)!=expected['sha256']:raise ValueError('Explicit xevma module differs from build lock')
            if (not data.startswith(b'\x7fELF\x02') or int.from_bytes(data[18:20],'little')!=62 or
                ('vermagic='+expected['vermagic']).encode() not in data):raise ValueError('Xevma ELF/vermagic differs from reviewed module')
            self.candidate_used[name]=dict(path=name,size=len(data),sha256=sha(data),origin='explicit-prebuilt-module-input-verified-by-upstream-module-lock',
                source_lock=lock_name,source_lock_sha256=self.upstream_rows[lock_name]['sha256'])
            return data
        if name=='candidate/charge_guard.py':
            data=(Path(__file__).resolve().parent/'charge_guard.py').read_bytes()
            if sha(data)!=GUARD_SHA256:raise ValueError('Changed fixed host guard input')
            self.candidate_used[name]=dict(path=name,size=len(data),sha256=sha(data),origin='new-fixed-host-compatibility-guard')
            return data
        if name.startswith(('brands/onexplayer/oxp3/current/upstream-20261029/','brands/onexplayer/oxp3/current/xevma-20261029/')):
            if self.upstream_lock is None:
                self.upstream_raw = path(self.root, 'upstream-source-lock.json').read_bytes()
                self.upstream_lock = json.loads(self.upstream_raw)
                expected=dict(repository='HHHHanasak1/onexplayer3-steamos-setup',tag='20261029',
                              commit='e72e814da1944da3792076689995b6fe961f5768',license='MIT; xevma module declares GPL')
                if self.upstream_lock.get('kind')!='BoilerUpstreamRepairSources' or self.upstream_lock.get('upstream')!=expected:
                    raise ValueError('Unexpected immutable upstream source lock')
                self.upstream_rows={r['path']:r for r in self.upstream_lock['files']}
            row=self.upstream_rows[name];data=path(self.root,name).read_bytes()
            if len(data)!=row['size'] or sha(data)!=row['sha256']:raise ValueError('Upstream source differs from lock: '+name)
            self.upstream_used[name]=row
            return data
        if name.startswith(('brands/onexplayer/common/battery/','brands/onexplayer/common/microphone/')):
            if self.current_lock is None:
                self.current_raw = path(self.root, 'current-source-lock.json').read_bytes()
                self.current_lock = json.loads(self.current_raw)
                if (self.current_lock.get('kind') != 'BoilerCurrentAdaptationSources' or
                    self.current_lock.get('cpu_baseline') != COMMIT or self.current_lock.get('battery_version') != '0.2.2'):
                    raise ValueError('Unexpected approved battery source lock')
                self.current_rows = {r['path']:r for r in self.current_lock['files']}
            row = self.current_rows[name]
            if row['origin'] not in ('approved-battery-0.2.2-source', 'byte-exact-battery-generated-template','approved-native-microphone-source'):
                raise ValueError('Unapproved current source origin')
            data = path(self.root, name).read_bytes()
            if len(data) != row['size'] or sha(data) != row['sha256']:
                raise ValueError('Approved source differs from lock: '+name)
            self.current_used[name] = row
            return data
        row = self.rows[name]
        data = path(self.root, name).read_bytes()
        if len(data) != row['size'] or sha(data) != row['sha256']:
            raise ValueError('Public source differs from lock: '+name)
        self.used[name] = row
        return data

    def functions(self, name, names):
        # Select only reviewed pure producers, never import legacy integration.
        raw = self.read(name)
        tree = ast.parse(raw)
        selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        if {n.name for n in selected} != set(names): raise ValueError('Missing reviewed source function')
        namespace = {'Path': Path, 're': __import__('re')}
        exec(compile(ast.Module(body=selected, type_ignores=[]), name, 'exec'), namespace)
        return namespace


def speaker_model_advisory(raw):
    old="    if not supported():\n        return 0\n"
    new="    if not supported():\n        print('Speaker route: model name is advisory; checking actual ALC245/audio interfaces.', flush=True)\n"
    text=raw.decode()
    if text.count(old)!=1:raise ValueError('Changed speaker model gate anchor')
    return text.replace(old,new).encode()


def static_maps(source):
    """Explicit address maps, matching the pinned independent file producers."""
    maps = {}
    def rows(component, prefix, names, modes=None):
        maps[component] = [(COMMON+prefix+'/'+name, dest, (modes or {}).get(name, 0o644)) for name, dest in names]
    rows('slot-switch', 'slot-switch', [
        ('boot.py', 'home/.oxp3-slot-switch/boot.py'),
        ('desktop.py', 'home/.oxp3-slot-switch/desktop.py'),
        ('oxp3-switch-slot', 'home/.oxp3-slot-switch/oxp3-switch-slot'),
        ('49-oxp3-slot-switch.rules', 'etc/polkit-1/rules.d/49-oxp3-slot-switch.rules'),
        ('oxp3-slot-switch.desktop', 'home/deck/Desktop/oxp3-slot-switch.desktop'),
        ('oxp3-slot-switch.desktop', 'home/deck/.local/share/applications/oxp3-slot-switch.desktop')],
        {'oxp3-switch-slot': 0o755, 'oxp3-slot-switch.desktop': 0o755})
    rows('partition-mount', 'partition-mount', [
        ('mounts.py', 'home/.oxp3-partition-mount/mounts.py'),
        ('desktop.py', 'home/.oxp3-partition-mount/desktop.py'),
        ('oxp3-partition-mount', 'home/.oxp3-partition-mount/oxp3-partition-mount'),
        ('oxp3-partition-mount.desktop', 'home/deck/Desktop/oxp3-partition-mount.desktop'),
        ('oxp3-partition-mount.desktop', 'home/deck/.local/share/applications/oxp3-partition-mount.desktop')],
        {'oxp3-partition-mount': 0o755, 'oxp3-partition-mount.desktop': 0o755})
    rows('windows-reboot', 'windows-reboot', [
        *[(n, 'var/lib/oxp3-installer/decky/runtime/plugins/oxp3-restart-windows/'+n) for n in ('main.py', 'plugin.json', 'package.json', 'dist/index.js')],
        *[(n, 'usr/local/lib/oxp3-windows-reboot/'+n) for n in ('boot.py', 'desktop.py')],
        ('oxp3-reboot-windows', 'usr/local/bin/oxp3-reboot-windows'),
        ('org.oxp3.restart-windows.policy', 'usr/share/polkit-1/actions/org.oxp3.restart-windows.policy'),
        ('49-oxp3-restart-windows.rules', 'etc/polkit-1/rules.d/49-oxp3-restart-windows.rules'),
        ('oxp3-reboot-windows.desktop', 'home/deck/Desktop/oxp3-reboot-windows.desktop'),
        ('oxp3-reboot-windows.desktop', 'home/deck/.local/share/applications/oxp3-reboot-windows.desktop')],
        {'oxp3-reboot-windows': 0o755, 'oxp3-reboot-windows.desktop': 0o755})
    maps['oxp3-light'] = [(OXP+'light/'+n, 'var/lib/oxp3-installer/decky/runtime/plugins/oxp3-light/'+n, 0o644)
                         for n in ('main.py', 'light_control.py', 'dist/index.js', 'plugin.json', 'package.json', 'LICENSE', 'LICENSE-HHD', 'LICENSE-HueSync', 'THIRD_PARTY_NOTICES.md')]
    # Use the reviewed public constant rather than duplicate a policy byte string.
    raw = source.read(OXP+'serial_console.py')
    tree = ast.parse(raw)
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)}
    maps['serial-console-policy'] = [(OXP+'serial_console.py', constants['DROPIN'], 0o644, constants['DATA'])]
    battery='brands/onexplayer/common/battery/'
    maps['battery-common'] = [(battery+n,target,0o644) for n,target in (
        ('sleep_control.py','var/lib/oxp3-battery/sleep_control.py'),
        ('sleep-router.conf','etc/systemd/system/systemd-suspend.service.d/90-oxp3-battery.conf'),
        ('battery-plugin-main.py','var/lib/oxp3-installer/decky/runtime/plugins/OXP3-battery/main.py'),
        ('battery-plugin-index.js','var/lib/oxp3-installer/decky/runtime/plugins/OXP3-battery/dist/index.js'),
        ('templates/plugin.json','var/lib/oxp3-installer/decky/runtime/plugins/OXP3-battery/plugin.json'),
        ('templates/package.json','var/lib/oxp3-installer/decky/runtime/plugins/OXP3-battery/package.json'))]
    maps['battery-charge-backend'] = [(battery+n,target,0o644) for n,target in (
        ('battery_control.py','var/lib/oxp3-battery/battery_control.py'),
        ('battery_provider.py','var/lib/oxp3-battery/battery_provider.py'),
        ('templates/oxp3-battery.service','etc/systemd/system/oxp3-battery.service'),
        ('templates/battery-dbus.conf','etc/dbus-1/system.d/org.onexplayer.SteamOSManager.Battery.conf'),
        ('templates/oxp3-battery.toml','etc/steamos-manager/remotes.d/oxp3-battery.toml'))]
    old_guard=b'ExecCondition=/usr/bin/python3 -I -B /usr/local/lib/oxp3-integration/integration_runtime.py guard'
    new_guard=b'ExecCondition=/usr/bin/python3 -I -B /usr/local/lib/boiler-resource-guard/charge_guard.py'
    unit_name=battery+'templates/oxp3-battery.service';original=source.read(unit_name)
    if original.count(old_guard)!=1:raise ValueError('Changed legacy charge unit guard anchor')
    maps['battery-charge-backend']=[(*row,original.replace(old_guard,new_guard),'replace-legacy-guard-v1') if row[0]==unit_name else row
                                   for row in maps['battery-charge-backend']]
    maps['charge-compatibility-guard']=[('candidate/charge_guard.py','usr/local/lib/boiler-resource-guard/charge_guard.py',0o644)]
    # Existing immutable install definitions supply explicit file addresses;
    # source bytes are read directly from their source collection, never cache.
    microphone='brands/onexplayer/common/microphone/'
    definition=json.loads(source.read(microphone+'install.json'))
    if definition.get('schemaVersion')!=1 or len(definition['files'])!=3:raise ValueError('Unexpected microphone install definition')
    maps['microphone']=[(microphone+r['source'],r['target'].removeprefix('/'),int(r['mode'],8)) for r in definition['files']]
    speaker='brands/onexplayer/oxp3/current/upstream-20261029/'
    definition=json.loads(source.read(speaker+'install.json'))
    if definition.get('id')!='oxp3:speaker-route' or len(definition['files'])!=6:raise ValueError('Unexpected speaker install definition')
    maps['speaker-route']=[(speaker+r['source'],r['target'],r['mode']) for r in definition['files']]
    original=source.read(speaker+'speaker_route.py')
    maps['speaker-route']=[(*row,speaker_model_advisory(original),'speaker-model-advisory-v1') if row[0]==speaker+'speaker_route.py' else row
                           for row in maps['speaker-route']]
    if source.xevma_module is not None:
        from hardware_name_patches import xevma_model_advisory
        base='brands/onexplayer/oxp3/current/xevma-20261029/'
        definition=json.loads(source.read(base+'install.json'))
        if definition.get('id')!='oxp3:xevmafix' or len(definition['files'])!=10:raise ValueError('Unexpected locked xevma install definition')
        maps['xevmafix']=[]
        for row in definition['files']:
            original='candidate/xevmafix.ko' if row['source']=='xevmafix.ko' else base+row['source']
            item=(original,row['target'],row['mode'])
            if row['source']=='load.py':item=(*item,xevma_model_advisory(source.read(original)),'xevma-model-advisory-and-real-xe-v1')
            maps['xevmafix'].append(item)
    return maps


TOOL_FACTS = {
    'slot-switch': {'slot_switch_tools': [True]},
    'partition-mount': {'partition_mount_tools': [True]},
    'windows-reboot': {'windows_restart_tools': [True], 'decky_plugin_runtime': [True]},
    'oxp3-light': {'onexplayer_light_interface': ['public-hid-or-native-led-v1'], 'decky_plugin_runtime': [True]},
    'serial-console-policy': {'serial_console_ttys0_unusable': [True]},
    'battery-common': {'systemd_sleep_router': [True], 'decky_plugin_runtime': [True]},
    'battery-charge-backend': {'ec_charge_control_attributes': [True], 'fan_driver_ready': [True],
                               'steamos_manager_package': ['26.4.1-2'], 'decky_plugin_runtime': [True]},
    'microphone': {'alsa_capture_switch': [True], 'audio_session_tools': [True]},
    'speaker-route': {'speaker_route_layout': ['onexplayer-public-20261029'], 'audio_session_tools': [True]},
    'charge-compatibility-guard': {'python3_runtime': [True]},
    'xevmafix': {'display_driver': ['xe'], 'kernel': [KERNEL]},
}

FACT_DEFINITIONS = {
    'slot_switch_tools': ['python3', 'pkexec', 'kdialog', 'steamos-bootconf', 'systemctl', 'busctl'],
    'partition_mount_tools': ['python3', 'pkexec', 'kdialog', 'lsblk', 'findmnt', 'systemctl', 'systemd-escape'],
    'windows_restart_tools': ['python3', 'efibootmgr', 'systemctl', 'pkexec', 'kdialog'],
    'decky_plugin_runtime': 'Caller verifies root-owned Decky Loader scans /var/lib/oxp3-installer/decky/runtime/plugins; name/model choice is insufficient.',
    'onexplayer_light_interface': 'Caller probes complete native LED interface, or exact public SteamOS/kernel/HID transport. Resource runtime performs its own live guard.',
    'serial_console_ttys0_unusable': 'Explicit target policy; must not be inferred merely from the selected model name.',
    'systemd_sleep_router': 'Caller verifies systemd-suspend supports the reviewed wrapper and its absolute Python script path. No sleep/reboot may be run during source audit.',
    'ec_charge_control_attributes': 'Caller reads real charge_control_end_threshold and charge_behaviour interfaces; a selected product name grants no charge-control capability.',
    'fan_driver_ready': 'Exact compatible oxpec fan/charge module must be independently verified. Presence of a model record is insufficient.',
    'steamos_manager_package': 'Caller verifies target-image pacman package identity and version. The service is not activated by the file engine.',
    'alsa_capture_switch': 'Caller verifies a real ALSA capture switch; selecting a model does not assert it exists.',
    'audio_session_tools': 'Caller verifies Python3, pactl, amixer, systemctl, journalctl, true and applicable runuser/user-session routing plus matching user PipeWire-Pulse/WirePlumber services. Offline byte placement does not prove audio.',
    'speaker_route_layout': 'Caller probes the reviewed ALC245/route layout; neither matching a product name nor a model record grants this fact.',
    'python3_runtime': 'Caller verifies the target has its trusted Python3 runtime. The fixed guard checks OS/build/kernel/Manager/real charge interfaces at service start.',
    'display_driver': 'Caller probes real GPU driver; the fixed xevma loader additionally verifies Intel0x8086 plus DRIVER=xe in sysfs, exact OS/build/kernel and locked module bytes.',
    'kernel': 'Caller supplies the reviewed image/live kernel identity; a model record cannot grant kernel compatibility.',
}

RESOURCE_FOLDERS = {
    'slot-switch': 'common/boot/slot-switch',
    'partition-mount': 'common/storage/partition-mount',
    'windows-reboot': 'common/boot/windows-reboot',
    'oxp3-light': 'devices/EC/onexplayer/light',
    'serial-console-policy': 'common/system/serial-console-policy',
    'battery-common': 'devices/EC/onexplayer/battery/common',
    'battery-charge-backend': 'devices/EC/onexplayer/battery/charge-backend',
    'microphone': 'devices/Audio/onexplayer/microphone',
    'speaker-route': 'devices/Audio/onexplayer/speaker-route',
    'charge-compatibility-guard': 'common/system/charge-compatibility-guard',
    'xevmafix': 'devices/GPU/Intel/xe/xevmafix-724',
}


def build(project, output, xevma_module=None):
    project, output = Path(project), Path(output)
    if output.exists(): raise ValueError('Use a new output directory')
    source = Sources(project/'adaptations',xevma_module=xevma_module)
    # Use the existing pinned TOON parser, not a private or permissive decoder.
    spec = importlib.util.spec_from_file_location('component_split_toon', project/'adaptations/scripts/toon_catalog.py')
    tc = importlib.util.module_from_spec(spec); spec.loader.exec_module(tc)
    toon = tc.decoder(project/'adaptations')
    maps = static_maps(source)
    options = json.loads(source.read(OXP+'options.json'))
    output.mkdir(parents=True)
    def write(name, data):
        target = path(output, name); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    def document(name, value): write(name, (toon.dumps(value)+'\n').encode())
    gpl = (project/'adaptations/LICENSE').read_bytes()
    if sha(gpl) != GPL_LICENSE_SHA256: raise ValueError('Changed GPL license input')
    write('licenses/project-AGPL-3.0', gpl)
    write('licenses/upstream-MIT', source.read(COMMON+'licenses/upstream-fixes-MIT.txt'))
    resources = []
    for identity, mapping in maps.items():
        slug = ('intel-xevmafix-724' if identity=='xevmafix' else 'onexplayer-microphone' if identity=='microphone' else 'oxp3-speaker-route' if identity=='speaker-route' else
                'onexplayer-'+identity if identity.startswith('battery-') else
                'common-'+identity if identity != 'oxp3-light' else 'onexplayer-light')
        resource_id = 'resource:'+slug
        folder = RESOURCE_FOLDERS[identity]
        payload, install, origins = [], [], []
        # Identical source bytes have one storage member even when installed twice.
        stored = {}
        for row in mapping:
            original, target, mode = row[:3]
            raw = source.read(original)
            data = row[3] if len(row) >= 4 else raw
            key = (original, sha(data))
            if key not in stored:
                member = f'{folder}/files/{len(stored):03d}-'+target.rsplit('/', 1)[-1]
                write(member, data); stored[key] = member
                payload.append(dict(path=member, size=len(data), sha256=sha(data)))
                origins.append(dict(payload=member, source_path=original, source_sha256=sha(raw),
                                    source_lock='provenance/candidate-source-lock.json' if original in source.candidate_used else
                                        'provenance/current-source-lock.json' if original in source.current_used else
                                        'provenance/upstream-source-lock.json' if original in source.upstream_used else 'provenance/source-lock.json',
                                    transform=row[4] if len(row)>=5 else 'literal-constant-DATA' if len(row)==4 else 'identity'))
            install.append(dict(source=stored[key], target=target, mode=mode,
                                scope='shared-home' if target.startswith('home/') else 'root-slots'))
        value = dict(schema_version=1, kind='BoilerResource', id=resource_id, version='0.1.0+source-split.20261031',
            status='public-source-static-candidate', dependencies=[], hardware_requires=TOOL_FACTS[identity],
            compatibility=dict(architectures=['x86_64'], os_ids=['steamos'], os_versions=['3.10']),
            payload=dict(files=payload), install=dict(kind='files-v1', files=install),
            verify=dict(kind='sha256-v1'), restore=dict(kind='transaction-backup-v1'),
            license_files=['licenses/project-AGPL-3.0', 'licenses/upstream-MIT'],
            provenance=dict(source_lock='provenance/source-lock.json', public_commit=COMMIT, files=origins),
            acceptance=dict(source_hashes=True, build_chain_reproduced=True, file_lifecycle_tested=False, hardware_verified=False),
            limitations=['Offline file placement only. Live commands, runtime state and firmware effects require separate acceptance.',
                'Positive trusted probe facts are mandatory; selected model names are advisory.'])
        if identity in ('windows-reboot', 'oxp3-light'):
            value['external_component_dependencies'] = ['decky']
        if identity == 'oxp3-light':
            value['license_files'] += [r['path'] for r in payload if '/files/' in r['path'] and r['path'].rsplit('-',1)[-1] in ('LICENSE','HHD','HueSync')]
            value['license_expression'] = 'BSD-3-Clause AND LGPL-2.1-or-later'
        if identity.startswith('battery-'):
            value['version'] = '0.2.2+source-split.20261031'
            value['status'] = 'approved-source-static-candidate-activation-pending'
            value['provenance'].pop('public_commit')
            value['provenance']['source_lock'] = 'provenance/current-source-lock.json'
            value['provenance']['public_repository_commit_verified'] = False
            value['external_component_dependencies'] = ['decky', 'fan-driver', 'manager']
            value['acceptance']['service_activation_verified'] = False
            value['limitations'] += ['Charging default does not change policy; raw firmware charge-behaviour restoration is not accepted.',
                'Complete source is approved/byte-locked locally; this builder does not assert an immutable public repository commit for current battery inputs.',
                'Both model records reference the same full charge backend; only actual interface probes establish compatibility.',
                'Replace the earlier x2-battery-sleep resource when merging; its old targets must not remain concurrently managed.']
            if identity == 'battery-charge-backend':
                value['version']='0.2.2+source-split.20261031.guard1'
                value['dependencies'] = ['resource:onexplayer-battery-common','resource:common-charge-compatibility-guard']
                value['compatibility']['kernels'] = [KERNEL]
        if identity=='charge-compatibility-guard':
            value['provenance'].pop('public_commit')
            value['provenance']['source_lock']='provenance/candidate-source-lock.json'
            value['provenance']['source_status']='new-fixed-host-implementation'
        if identity=='microphone':
            value['provenance'].pop('public_commit')
            value['provenance']['source_lock']='provenance/current-source-lock.json'
            value['provenance']['public_repository_commit_verified']=False
        if identity=='speaker-route':
            value['version']='2026.10.29+model-advisory.1'
            value['provenance']['public_commit']='e72e814da1944da3792076689995b6fe961f5768'
            value['provenance']['repository']='HHHHanasak1/onexplayer3-steamos-setup'
            value['provenance']['source_lock']='provenance/upstream-source-lock.json'
            value['license_files'] += [r['path'] for r in payload if r['path'].endswith('-LICENSE')]
            original=source.read('brands/onexplayer/oxp3/current/upstream-20261029/speaker_route.py')
            patched=speaker_model_advisory(original)
            diff=''.join(difflib.unified_diff(original.decode().splitlines(keepends=True),patched.decode().splitlines(keepends=True),
                fromfile='upstream/speaker_route.py',tofile='resource/speaker_route.py'))
            patch_path='provenance/patches/speaker-model-advisory.patch';write(patch_path,diff.encode())
            value['provenance']['patches']=[patch_path]
            payload_path=next(r['source'] for r in install if r['target']=='usr/local/lib/oxp3-speaker-route/speaker_route.py')
            value['provenance']['transforms']=[dict(kind='speaker-model-advisory-v1',
                source_path='brands/onexplayer/oxp3/current/upstream-20261029/speaker_route.py',source_sha256=sha(original),
                patch=patch_path,patch_sha256=sha(diff.encode()),output=payload_path,output_sha256=sha(patched))]
        if identity=='xevmafix':
            from hardware_name_patches import xevma_model_advisory
            base='brands/onexplayer/oxp3/current/xevma-20261029/'
            original=source.read(base+'load.py');patched=xevma_model_advisory(original)
            diff=''.join(difflib.unified_diff(original.decode().splitlines(keepends=True),patched.decode().splitlines(keepends=True),
                fromfile='upstream/xevma-load.py',tofile='resource/xevma-load.py'))
            patch_path='provenance/patches/xevma-model-advisory.patch';write(patch_path,diff.encode())
            payload_path=next(r['source'] for r in install if r['target'].endswith('/xevmafix/load.py'))
            value['version']='2026.10.29+kernel7.2.4.model-advisory.1'
            value['compatibility']['kernels']=[KERNEL]
            value['provenance'].update(source_lock='provenance/upstream-source-lock.json',public_commit='e72e814da1944da3792076689995b6fe961f5768',
                repository='HHHHanasak1/onexplayer3-steamos-setup',module_input_lock='provenance/candidate-source-lock.json',
                transforms=[dict(kind='xevma-model-advisory-and-real-xe-v1',source_path=base+'load.py',source_sha256=sha(original),
                    patch=patch_path,patch_sha256=sha(diff.encode()),output=payload_path,output_sha256=sha(patched))])
            value['license_files'] += [r['path'] for r in payload if r['path'].endswith('-GPL-2.0')]
            value['acceptance']['build_chain_reproduced']=False
            value['acceptance']['explicit_prebuilt_module_verified']=True
            value['limitations'] += ['The exact module is an explicit input checked against the immutable module-lock; this resource build does not recompile the kernel module.',
                'No exit handler: an active module requires disabling activation and reboot; file restore does not unload it.',
                'Both product names are advisory; Intel xe binding, exact OS/build/kernel and module SHA remain required.']
        member = f'{folder}/resource.toon'; document(member, value)
        resources.append(dict(id=resource_id, version=value['version'], path=member, sha256=sha(path(output, member).read_bytes())))
    write('provenance/source-lock.json', jb(dict(schema_version=1, kind='ComponentSplitPublicSourceInventory',
        source=source.lock['source'], upstream_lock_sha256=sha(source.lock_raw), files=sorted(source.used.values(), key=lambda r:r['path']))))
    write('provenance/probe-contract.json', jb(FACT_DEFINITIONS))
    if source.current_used:
        write('provenance/current-source-lock.json', jb(dict(schema_version=1, kind='ApprovedCurrentSourceInventory',
            upstream_lock_sha256=sha(source.current_raw), battery_version='0.2.2',
            source_status='approved-local-byte-lock-public-commit-not-asserted',
            files=sorted(source.current_used.values(),key=lambda r:r['path']))))
    if source.upstream_used:
        write('provenance/upstream-source-lock.json',jb(dict(schema_version=1,kind='PinnedUpstreamAudioSourceInventory',
            upstream=source.upstream_lock['upstream'],upstream_lock_sha256=sha(source.upstream_raw),
            files=sorted(source.upstream_used.values(),key=lambda r:r['path']))))
    if source.candidate_used:
        write('provenance/candidate-source-lock.json',jb(dict(schema_version=1,kind='CandidateExplicitSourceInventory',
            files=sorted(source.candidate_used.values(),key=lambda r:r['path']))))
    models = []
    for model in ('oxp3', 'x2-mini'):
        # Same shared resources, no per-model copied scripts.
        value = dict(schema_version=1, kind='BoilerResourceModel', id='device:'+model,
            name='ONEXPLAYER 3' if model=='oxp3' else 'ONEXPLAYER X2 Mini',
            hardware_matching=dict(mode='advisory-only'),
            resources=[dict(resource=r['id'], enabled=False, parameters={}) for r in resources],
            complete=False, official_release=False)
        member = 'brands/onexplayer/models/'+model+'.toon'; document(member, value)
        models.append(dict(id=value['id'], path=member, sha256=sha(path(output, member).read_bytes())))
    files = [dict(path=p.relative_to(output).as_posix(), size=p.stat().st_size, sha256=sha(p.read_bytes())) for p in sorted(output.rglob('*')) if p.is_file()]
    catalog = dict(schema_version=1, kind='BoilerResourceCatalog', trust='unsigned-development', official_release=False,
                   resources=resources, models=models, files=files)
    document('catalog.toon', catalog); write('catalog.json', jb(catalog))
    return catalog


def render_target(project, component, root, *, target_disk=None):
    """Read-only fixed renderers. Returned files need a bound host transaction.

    Never execute a model-supplied Python function, package hook or shell command.
    These handlers do not install files, activate services or claim acceptance.
    """
    source = Sources(Path(project)/'adaptations')
    root = Path(root)
    if component == 'keyboard':
        if not path(root, 'usr/share/applications/org.kde.plasma.keyboard.desktop').is_file():
            raise ValueError('Plasma Keyboard dependency is missing')
        relative = 'home/deck/.config/kwinrc'
        previous = path(root, relative)
        functions = source.functions(OXP+'integration.py', ['config_data'])
        data = functions['config_data'](previous.read_text() if previous.exists() else '', 'Wayland',
            {'InputMethod': '/usr/share/applications/org.kde.plasma.keyboard.desktop', 'VirtualKeyboardMode': '1'})
        return {relative: dict(data=data, user=True, mode=0o644)}
    if component == 'multifunction':
        source.read(OXP+'integration.py')
        relative = 'etc/inputplumber/capability_maps.d/oxp3-installer.yaml'
        existing = path(root, relative)
        if not existing.exists(): existing = path(root, 'usr/share/inputplumber/capability_maps/oxp3-installer.yaml')
        text = existing.read_text()
        start = text.index('  - name: Keyboard key (Ctrl+Meta+O)')
        end = text.index('  - name: Orange Button', start)
        old = '    target_event:\n      gamepad:\n        button: Keyboard'
        new = old.replace('Keyboard', 'RightPaddle2'); block = text[start:end]
        if block.count(old)+block.count(new) != 1: raise ValueError('Unsupported multifunction mapping')
        return {relative: dict(data=(text[:start]+block.replace(old,new)+text[end:]).encode(), mode=0o644)}
    if component == 'gm7-resume':
        namespace = source.functions(OXP+'boot_repairs.py', ['affected_gm7', 'gm7_files'])
        namespace.update(GRUB_DROPIN='etc/default/grub.d/oxp3-nvme.cfg', GRUB_CONFIG='efi/EFI/steamos/grub.cfg')
        return namespace['gm7_files'](root, target_disk)
    if component == 'split-lock':
        # This remains read-only; no historical report deletion is exposed.
        data = source.read(COMMON+'split_lock.py'); tree = ast.parse(data)
        constants = {n.targets[0].id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant)}
        namespace = source.functions(COMMON+'split_lock.py', ['patch_grub'])
        relative = constants['GRUB']; before = path(root, relative).read_text()
        dropin = path(root, constants['DROPIN'])
        if dropin.exists() and dropin.read_bytes()!=constants['DROPIN_DATA']: raise ValueError('Conflicting split-lock drop-in')
        return {constants['DROPIN']:dict(data=constants['DROPIN_DATA']), relative:dict(data=namespace['patch_grub'](before).encode()),
                'usr/share/licenses/oxp3-split-lock/UPSTREAM_LICENSE':dict(data=source.read(COMMON+'split-lock/UPSTREAM_LICENSE'))}
    raise ValueError('Component has no isolated target renderer: '+component)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--xevma-module',help='Explicit individual module input; size/SHA/ELF/vermagic must match the locked upstream build record')
    args = parser.parse_args()
    result = build(args.project, args.output, xevma_module=args.xevma_module)
    print(json.dumps(dict(resources=len(result['resources']), models=len(result['models']), files=len(result['files']))))
