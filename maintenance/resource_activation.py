"""Fixed live activation providers; never execute resource-defined commands."""
from pathlib import Path
import json,os,re,subprocess

class ActivationError(RuntimeError): pass

TOOLS={'systemctl','runuser','env','pacman','modinfo','modprobe','depmod','amixer','pactl','pw-dump','python3','busctl','kdialog','pkexec','steamos-bootconf','journalctl','true','mount','findmnt','lsblk'}
PLUGIN_IDS={'resource:onexplayer-fan','resource:common-windows-reboot','resource:onexplayer-light','resource:onexplayer-battery-common','resource:intel-g3e-public-cpu-controls'}
PLUGIN_IDS.add('resource:common-opti2xmx')
SYSTEM_SERVICES={'resource:onexplayer-battery-charge-backend':('oxp3-battery.service',),'resource:intel-g3e-public-cpu-controls':('oxp3-cpu.service',),'resource:intel-xevmafix-724':('boiler-oxp3-xevmafix.service',)}
SYSTEM_SERVICES.update({'resource:onexplayer-ec-fan-driver':('oxp3-fan-module.service',),'resource:onexplayer-fan':('oxp3-fan.service',),'resource:steamos-manager':('steamos-manager.service',),'resource:onexplayer-legacy-runtime':('oxp3-volkey-fix.service','oxp3-battery-clamp.service'),'resource:common-intel-rapl':('boiler-intel-rapl.service',)})
SYSTEM_SERVICES['resource:common-telemetry-overlay']=('boiler-overlay-read.service',)
USER_SERVICES={'resource:common-telemetry-overlay':('oxp3-overlay-telemetry.service',),'resource:onexplayer-microphone':('oxp-microphone.service',),'resource:oxp3-speaker-route':('oxp3-speaker-route.service',)}
STATIC_IDS={'resource:gamescope-environment','resource:intel-xe-dsb','resource:common-slot-switch','resource:common-partition-mount','resource:common-charge-compatibility-guard','resource:common-serial-console-policy','resource:onexplayer-battery-common','resource:gamma22-hdr-game','resource:intel-be201-firmware'}

class LiveSystem:
    def __init__(self,root=Path('/')):
        if Path(root)!=Path('/') or os.name!='posix' or os.geteuid()!=0:raise ActivationError('Live activation requires the real root-owned Linux system')
        self.root=Path('/')
    def run(self,name,*args,check=True):
        if name not in TOOLS:raise ActivationError('Unknown fixed activation tool')
        executable=next((Path(p)/name for p in ('/usr/bin','/usr/sbin','/bin','/sbin') if (Path(p)/name).is_file()),None)
        if executable is None:raise ActivationError('Required tool unavailable: '+name)
        result=subprocess.run([str(executable),*args],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120,env=dict(os.environ,LC_ALL='C',LANG='C'))
        if check and result.returncode:raise ActivationError(name+' failed: '+result.stderr.strip()[:1000])
        return result.returncode,result.stdout.strip()
    def available(self,name):return any((Path(p)/name).is_file() for p in ('/usr/bin','/usr/sbin','/bin','/sbin'))
    def user(self):
        rows=[x.split(':') for x in (self.root/'etc/passwd').read_text().splitlines() if x.startswith('deck:')]
        if len(rows)!=1 or rows[0][5]!='/home/deck' or int(rows[0][2])<=0:raise ActivationError('Expected unprivileged deck account')
        uid=int(rows[0][2]);return uid,(self.root/f'run/user/{uid}/bus').exists()
    def userctl(self,*args,check=True):
        uid,bus=self.user()
        if not bus:return None
        return self.run('runuser','-u','deck','--','/usr/bin/env',f'XDG_RUNTIME_DIR=/run/user/{uid}',f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus','/usr/bin/systemctl','--user',*args,check=check)
    def invocation(self,unit,user=False):
        result=self.userctl('show',unit,'--property=InvocationID','--value',check=False) if user else self.run('systemctl','show',unit,'--property=InvocationID','--value',check=False)
        return None if result is None or result[0] else result[1]
    def boot_id(self):
        value=(self.root/'proc/sys/kernel/random/boot_id').read_text().strip()
        if not re.fullmatch('[0-9a-f-]{36}',value):raise ActivationError('Invalid live boot identity')
        return value
    def service(self,unit,user=False):
        result=self.userctl('is-active',unit,check=False) if user else self.run('systemctl','is-active',unit,check=False)
        return None if result is None else result[1]


def _text(root,name):
    try:return (root/name).read_text().strip()
    except (OSError,UnicodeError):return None

def _package(root,name):
    databases=[root/'usr/lib/holo/pacmandb/local',root/'var/lib/pacman/local'];versions=[]
    for database in databases:
        if not database.is_dir():continue
        for desc in database.glob(name+'-*/desc'):
            lines=desc.read_text().splitlines()
            def value(key):
                indices=[i for i,x in enumerate(lines) if x=='%'+key+'%']
                return lines[indices[0]+1] if len(indices)==1 and indices[0]+1<len(lines) else None
            if value('NAME')==name:versions.append(value('VERSION'))
        break
    return versions[0] if len(versions)==1 else None

def collect_facts(root=Path('/'),*,system=None):
    system=system or LiveSystem(root);root=Path(root);facts={};evidence={}
    values={}
    for line in (_text(root,'etc/os-release') or '').splitlines():
        if '=' in line:
            key,value=line.split('=',1);values[key]=value.strip('"')
    facts['kernel']=os.uname().release if root==Path('/') else _text(root,'proc/sys/kernel/osrelease')
    facts['steamos_build']=values.get('BUILD_ID');facts['steamos_manager_package']=_package(root,'steamos-manager');facts['gamescope_version']=_package(root,'gamescope')
    def tools(*names):return all(system.available(n) for n in names)
    facts['slot_switch_tools']=tools('steamos-bootconf','busctl','systemctl','pkexec','kdialog','python3')
    facts['partition_mount_tools']=tools('lsblk','findmnt','mount','pkexec','kdialog','python3')
    facts['windows_restart_tools']=tools('busctl','systemctl','pkexec','kdialog','python3')
    facts['python3_runtime']=tools('python3');facts['systemd_sleep_router']=(root/'usr/lib/systemd/system/systemd-suspend.service').is_file()
    loader=root/'var/lib/boiler/decky/runtime/services/PluginLoader'
    facts['decky_plugin_runtime']=loader.is_file() and (root/'etc/systemd/system/plugin_loader.service').is_file()
    bat=root/'sys/class/power_supply/BAT0';threshold=_text(root,'sys/class/power_supply/BAT0/charge_control_end_threshold');behaviour=_text(root,'sys/class/power_supply/BAT0/charge_behaviour')
    facts['ec_charge_control_attributes']=bool(threshold and threshold.isdecimal() and 0<=int(threshold)<=100 and behaviour and re.search(r'\[(auto|inhibit-charge-awake|inhibit-charge)\]',behaviour))
    from maintenance.ec_profile import _acpi
    facts['acpi_ec_interface']=_acpi(root)
    facts['fan_driver_ready']=(root/'sys/module/oxpec').is_dir()
    try:
        from maintenance.ec_readonly_probe import _inspect as inspect_ec
        inspect_ec(root);facts['fan_hwmon_interface']='oxp-ec-pwm-v1'
    except (RuntimeError,OSError,ValueError):pass
    groups={};drivers=[]
    for p in (root/'sys/devices/system/cpu/cpufreq').glob('policy[0-9]*'):
        driver=_text(p,'scaling_driver');maximum=_text(p,'cpuinfo_max_freq');drivers.append(driver)
        if maximum and maximum.isdecimal():groups.setdefault(int(maximum),[]).append(p.name)
    facts['intel_pstate']=bool(drivers) and all(v=='intel_pstate' for v in drivers)
    facts['cpu_core_groups']=[len(groups[x]) for x in sorted(groups,reverse=True)]
    for key,unit in [('cpu','oxp3-cpu.service')]:
        facts[key+'_service_state']=system.service(unit)
        rc,state=system.run('systemctl','is-enabled',unit,check=False);facts[key+'_service_enablement']=state
    # Real kernel device bindings, never inferred from DMI or selected model.
    gpu=[];wifi=[]
    for p in (root/'sys/bus/pci/devices').glob('*'):
        driver=p/'driver'
        if not driver.exists():continue
        name=driver.resolve().name;classcode=_text(p,'class') or ''
        if classcode.startswith('0x03'):gpu.append(name)
        if classcode.startswith('0x0280'):wifi.append(name)
    if gpu and set(gpu)=={'xe'}:facts['display_driver']='xe'
    if 'iwlwifi' in wifi:facts['wifi_driver']='iwlwifi'
    # Firmware request names must come from an actual kernel device request/log,
    # not just modinfo's list of every device the module could support.
    rc,kernel_log=system.run('journalctl','-k','-b','--no-pager','-o','cat',check=False)
    requests=re.findall(r'(?:iwlwifi-sc-a0-fm-c0-[A-Za-z0-9.-]+\.ucode|ibt-00a0-0291[-A-Za-z0-9.]+)',kernel_log)
    if facts.get('wifi_driver')=='iwlwifi' and any(x.startswith('iwlwifi-sc-a0-fm-c0-') for x in requests):facts['wifi_firmware_prefix']='iwlwifi-sc-a0-fm-c0'
    if any(x.startswith('ibt-00a0-0291') for x in requests):facts['bluetooth_firmware_prefix']='ibt-00a0-0291'
    lednames={'oxp:rgb:joystick_rings','oxp:rgb:guide_button','oxp:rgb:rear_logo'}
    native=all((root/'sys/class/leds'/n/'brightness').is_file() for n in lednames)
    hid=False
    descriptor=bytes.fromhex('0600ff0901a101090215002600ff750895408106090215002600ff750895409106c0')
    for p in (root/'sys/bus/hid/devices').glob('*'):
        try:
            if ':1A86:FE00.' in p.name.upper() and (p/'report_descriptor').read_bytes()==descriptor:hid=True
        except OSError:pass
    if native or hid:facts['onexplayer_light_interface']='public-hid-or-native-led-v1'
    codecs=[];capture=False
    for p in (root/'proc/asound').glob('card[0-9]*'):
        for codec in p.glob('codec#*'):
            value=_text(codec.parent,codec.name)
            if value and 'Codec: Realtek ALC245' in value:codecs.append(p.name)
        if system.available('amixer'):
            rc,controls=system.run('amixer','-c',p.name[4:],'controls',check=False)
            capture=capture or (rc==0 and "name='Capture Switch'" in controls)
    facts['alsa_capture_switch']=capture
    if codecs:facts['speaker_route_layout']='onexplayer-public-20261029'
    facts['audio_session_tools']=tools('python3','pactl','amixer','systemctl','journalctl','runuser','pw-dump')
    rapl=True
    for directory in ('intel-rapl:0','intel-rapl-mmio:0'):
        base=root/'sys/class/powercap'/directory
        for name,wanted in (('name','package-0'),('enabled','1'),('constraint_0_name','long_term'),('constraint_1_name','short_term')):
            if _text(base,name)!=wanted:rapl=False
        for name in ('constraint_0_power_limit_uw','constraint_1_power_limit_uw'):
            value=_text(base,name)
            if not value or not value.isdecimal():rapl=False
    facts['rapl_interfaces']=rapl
    if system.available('python3'):
        rc,output=system.run('python3','-I','-B','-c','import dbus; from gi.repository import GLib',check=False)
        facts['python_dbus_runtime']=rc==0
    else:facts['python_dbus_runtime']=False
    panel_evidence=None
    drm=root/'sys/class/drm'
    if drm.is_dir():
        from .hdr_live_panel import probe_internal_edp
        try:
            binding=probe_internal_edp(drm)
        except (OSError,ValueError):
            pass
        else:
            panel=binding['panel']
            facts.update(gamescope_display_vendor=panel['vendor'],
                gamescope_display_product=panel['product'],hdr_panel_policy='edid-native')
            if panel['model'] is not None:facts['gamescope_display_model']=panel['model']
            panel_evidence=dict(connector=binding['connector'],edid_sha256=binding['edid_sha256'])
    facts['_probe']=dict(origin='live-linux' if root==Path('/') else 'fixture',environment=values,codec_cards=codecs,kernel_firmware_requests=requests,internal_panel=panel_evidence)
    return facts


def _services(identity):return [(False,u) for u in SYSTEM_SERVICES.get(identity,())]+[(True,u) for u in USER_SERVICES.get(identity,())]+([(True,'steamos-manager.service')] if identity=='resource:steamos-manager' else [])

def activate(identity,*,root=Path('/'),pool,facts,selection,record_before_write=None,approve=None,system=None):
    system=system or LiveSystem(root);root=Path(root)
    if identity not in set(selection):raise ActivationError('Activation is not in the reviewed selection')
    resource=pool.resource(identity)
    if identity not in STATIC_IDS|PLUGIN_IDS|set(SYSTEM_SERVICES)|set(USER_SERVICES):raise ActivationError('No fixed activation provider: '+identity)
    if identity in PLUGIN_IDS:
        if not facts.get('decky_plugin_runtime'):raise ActivationError('Decky runtime is unavailable')
        for row in resource['install']['files']:
            target=row['target']
            if '/decky/runtime/plugins/' in target and not target.startswith('var/lib/boiler/decky/runtime/plugins/'):
                raise ActivationError('Plugin targets do not match the real Boiler Decky runtime')
    before=[]
    for user,unit in _services(identity):
        state=system.service(unit,user=user)
        if state not in (None,'active','inactive','failed','unknown'):raise ActivationError('Unrecognized existing service state: '+unit)
        before.append(dict(user=user,unit=unit,before=state,started=False,start_pending=False,invocation=None))
    if before:
        if record_before_write is None:raise ActivationError('A durable write-ahead recorder is required before activation')
        recovery=dict(schema_version=1,identity=identity,boot_id=system.boot_id(),services=before)
        record_before_write(recovery)
        if approve is None:raise ActivationError('Fixed firstboot approval callback required')
        approve(identity)
        system.run('systemctl','daemon-reload')
        pending=[]
        for item in before:
            action='restart' if item['before']=='active' else 'start'
            if item['user']:
                if system.userctl('daemon-reload') is None:pending.append(item['unit']);continue
                item['start_pending']=True;record_before_write(recovery)
                system.userctl(action,item['unit'])
            else:
                item['start_pending']=True;record_before_write(recovery)
                system.run('systemctl',action,item['unit'])
            observed=system.service(item['unit'],user=item['user'])
            if observed!='active':
                if item['unit']!='oxp3-speaker-route.service':raise ActivationError('Service did not become active: '+item['unit'])
                result=system.userctl('show',item['unit'],'--property=Result','--property=ExecMainStatus',check=False)
                if result is None or result[0] or 'Result=success' not in result[1].splitlines() or 'ExecMainStatus=0' not in result[1].splitlines():raise ActivationError('Speaker routing service failed')
            invocation=system.invocation(item['unit'],user=item['user'])
            if not isinstance(invocation,str) or not re.fullmatch('[0-9a-f]{32}',invocation):raise ActivationError('Service invocation identity unavailable after activation')
            item.update(started=True,start_pending=False,invocation=invocation);record_before_write(recovery)
        return dict(status='configured' if pending else 'active',runtime='pending-user-session' if pending else 'validated',recovery=recovery,evidence=dict(pending_user_units=pending))
    if _services(identity):
        if record_before_write is None or approve is None:raise ActivationError('Durable recorder and approval callback required')
        recovery=dict(schema_version=1,identity=identity,boot_id=system.boot_id(),services=[]);record_before_write(recovery);approve(identity);system.run('systemctl','daemon-reload')
        return dict(status='active',runtime='validated',recovery=recovery,evidence=dict(kind='existing-active-services'))
    return dict(status='configured',runtime='validated',recovery=dict(schema_version=1,identity=identity,boot_id=system.boot_id(),services=[]),evidence=dict(kind='fixed-files-and-runtime-provider'))


def restore(identity,*,root=Path('/'),state,system=None):
    system=system or LiveSystem(root)
    if state.get('schema_version')!=1 or state.get('identity')!=identity:raise ActivationError('Activation recovery identity mismatch')
    allowed=set(_services(identity));rows=state.get('services')
    if not isinstance(rows,list) or any(not isinstance(r,dict) or set(r)!={'user','unit','before','started','start_pending','invocation'} or type(r['user']) is not bool or (r['user'],r['unit']) not in allowed or r['before'] not in (None,'active','inactive','failed','unknown') or type(r['started']) is not bool or type(r['start_pending']) is not bool or (r['started'] and (not isinstance(r['invocation'],str) or not re.fullmatch('[0-9a-f]{32}',r['invocation']))) for r in rows):raise ActivationError('Invalid fixed service recovery state')
    if any(r['start_pending'] for r in rows):raise ActivationError('Interrupted service start requires explicit reconciliation before restore')
    actions=[]
    for item in reversed(rows):
        if not item['started']:continue
        current=system.service(item['unit'],user=item['user'])
        if current in (None,'inactive','failed','unknown'):continue
        if current!='active' or system.invocation(item['unit'],user=item['user'])!=item['invocation']:raise ActivationError('Service changed outside the activation transaction')
        actions.append(item)
    for item in actions:
        if item['user']:system.userctl('stop',item['unit'])
        else:system.run('systemctl','stop',item['unit'])
    return dict(status='restored',runtime='reboot-required' if identity=='resource:intel-xevmafix-724' and rows else 'validated',reboot_required=bool(identity=='resource:intel-xevmafix-724' and rows),resume_services=[dict(user=r['user'],unit=r['unit']) for r in rows if r['before']=='active'])

def verify(identity,*,root=Path('/'),pool,facts,selection,state=None,system=None):
    """Readonly live checks; never starts services or creates recovery state."""
    system=system or LiveSystem(root)
    if identity not in set(selection):raise ActivationError('Verification is not in the reviewed selection')
    pool.resource(identity)
    if identity not in STATIC_IDS|PLUGIN_IDS|set(SYSTEM_SERVICES)|set(USER_SERVICES):raise ActivationError('No fixed verification provider')
    pending=[];checked=[]
    for user,unit in _services(identity):
        state=system.service(unit,user=user)
        if state is None and user:pending.append(unit);continue
        if state!='active':
            if unit!='oxp3-speaker-route.service':raise ActivationError('Selected resource service is not active: '+unit)
            result=system.userctl('show',unit,'--property=Result','--property=ExecMainStatus',check=False)
            if result is None or result[0] or 'Result=success' not in result[1].splitlines() or 'ExecMainStatus=0' not in result[1].splitlines():raise ActivationError('Speaker service has not completed successfully')
        checked.append(unit)
    if identity in PLUGIN_IDS and system.service('plugin_loader.service')!='active':raise ActivationError('Selected plugin Loader is not active')
    return dict(status='configured' if pending or not _services(identity) else 'active',runtime='pending-user-session' if pending else 'validated',evidence=dict(services=checked,pending_user_units=pending))


def resume_original(identity,*,root=Path('/'),state,record_before_write,system=None):
    """Called only after the same generation's Engine file restore succeeds."""
    system=system or LiveSystem(root)
    if state.get('identity')!=identity or state.get('schema_version')!=1:raise ActivationError('Original activation identity mismatch')
    allowed=set(_services(identity));rows=state.get('services',[])
    if any((r.get('user'),r.get('unit')) not in allowed for r in rows):raise ActivationError('Original service is outside the fixed provider')
    planned=[dict(user=r['user'],unit=r['unit']) for r in rows if r.get('before')=='active']
    if planned:
        if not callable(record_before_write):raise ActivationError('Original service restart requires a durable recorder')
        record_before_write(dict(state,resume_original=planned))
        system.run('systemctl','daemon-reload')
        resumed=[];pending=[]
        for item in planned:
            if item['user']:
                if system.userctl('daemon-reload') is None:pending.append(item['unit']);continue
                system.userctl('start',item['unit'])
            else:system.run('systemctl','start',item['unit'])
            resumed.append(item)
        return dict(status='restored',runtime='pending-user-session' if pending else 'validated',resumed_services=resumed,pending_user_units=pending)
    return dict(status='restored',runtime='validated',resumed_services=[],pending_user_units=[])
