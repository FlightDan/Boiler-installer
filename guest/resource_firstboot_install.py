"""Owned prepared-image deployment with mandatory, fixed first-boot activation.

The existing host binding authenticates this local test session, exact payload,
preparation bytes and image. Hardware acceptance is never inferred from QEMU.
"""
from pathlib import Path
import copy
import hashlib
import json
import subprocess
import tempfile
import re

from maintenance.engine import Engine, _json_write
from maintenance.resource_runtime import ResourceSession, ResourceError, resolve
from maintenance.core_retention import seed_root
from maintenance.resource_firstboot import INTENT, KIND
from resource_coordinator import checked_parts, PreparedMounts, selection
from resource_local_session import local_authorizer
from resource_results import retained_core, require_clean, preflight_metadata, documents, publish, verify_records, receipt_readonly

BOOTSTRAP = 'core:resource-firstboot'
SERVICE = '''[Unit]
Description=Boiler resource hardware verification and activation
After=local-fs.target
Before=display-manager.service
ConditionPathExists=/var/lib/boiler/resource-firstboot/intent.json
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-firstboot.py run
TimeoutStartSec=300
[Install]
WantedBy=multi-user.target
'''
TIMER = '''[Unit]
Description=Boiler resource activation follow-up
[Timer]
OnBootSec=120
OnUnitActiveSec=120
Unit=boiler-resource-firstboot.service
[Install]
WantedBy=timers.target
'''
TARGET = '[Unit]\nWants=boiler-resource-firstboot.service boiler-resource-firstboot.timer\n'
LAUNCHER = '''import sys
sys.path.insert(0, '/usr/local/lib/boiler')
from maintenance.resource_firstboot import main
main()
'''
NOTICE_LAUNCHER = '''import sys
sys.path.insert(0, '/usr/local/lib/boiler')
from maintenance.firstboot_notice import main
raise SystemExit(main())
'''
NOTICE_SERVICE = '''[Unit]
Description=Boiler device support failure notice
After=graphical-session.target
PartOf=graphical-session.target
ConditionUser=!root
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-notice.py
TimeoutStartSec=0
'''
NOTICE_TIMER = '''[Unit]
Description=Check Boiler device support after desktop login
PartOf=graphical-session.target
[Timer]
OnBootSec=60
OnUnitActiveSec=120
Unit=boiler-resource-notice.service
[Install]
WantedBy=graphical-session.target
'''


def _bytes(value): return (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()


def bootstrap_manifest(temporary, intent, *, cpu_live=False, ec_profile=False, fan_live=False):
    files={INTENT:_bytes(intent),
        'etc/systemd/system/boiler-resource-firstboot.service':SERVICE.encode(),
        'etc/systemd/system/boiler-resource-firstboot.timer':TIMER.encode(),
        'etc/systemd/system/multi-user.target.d/90-boiler-resources.conf':TARGET.encode(),
        'usr/local/lib/boiler/boiler-resource-firstboot.py':LAUNCHER.encode(),
        'usr/local/lib/boiler/boiler-resource-notice.py':NOTICE_LAUNCHER.encode(),
        'etc/systemd/user/boiler-resource-notice.service':NOTICE_SERVICE.encode(),
        'etc/systemd/user/boiler-resource-notice.timer':NOTICE_TIMER.encode(),
        'etc/systemd/user/graphical-session.target.d/90-boiler-resource-notice.conf':b'[Unit]\nWants=boiler-resource-notice.timer\n'}
    # Conditions are present before first boot, so existing/default target
    # dependencies cannot start a newly deployed service before its provider.
    from maintenance.resource_activation import SYSTEM_SERVICES, USER_SERVICES, PLUGIN_IDS
    selected=intent['selection']['components']
    for identity in selected:
        gate='/var/lib/boiler-resource-approved/'+identity.split(':',1)[1]+'.json'
        units=[('etc/systemd/system',u) for u in SYSTEM_SERVICES.get(identity,())]
        units += [('etc/systemd/user',u) for u in USER_SERVICES.get(identity,())]
        if identity=='resource:oxp3-speaker-route':units.append(('etc/systemd/system','oxp3-speaker-resume.service'))
        for directory,unit in units:
            files[directory+'/'+unit+'.d/90-boiler-resource-gate.conf']=('[Unit]\nConditionPathExists='+gate+'\n').encode()
    if cpu_live:
        if 'resource:intel-g3e-public-cpu-controls' not in selected:
            raise ResourceError('CPU sidecar requires the selected live contract')
        files['etc/systemd/system/oxp3-cpu.service.d/90-boiler-cpu-live.conf'] = (
            '[Service]\nTimeoutStartSec=120\nTimeoutStopSec=120\n'
            'ExecStartPre=/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-firstboot.py cpu-prestart\n'
            'ExecStartPost=/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-firstboot.py cpu-poststart\n'
            'ExecStopPost=/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-firstboot.py cpu-poststop\n').encode()
    if ec_profile:
        if 'resource:onexplayer-ec-fan-driver' not in selected or intent.get('ec_board_profile')!='oxp3-g1i-v1':
            raise ResourceError('EC loader requires the selected reviewed profile')
        files['etc/systemd/system/oxp3-fan-module.service.d/90-boiler-ec-profile.conf'] = (
            '[Service]\nExecStart=\n'
            'ExecStart=/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-firstboot.py ec-profile-load\n').encode()
        files['etc/modprobe.d/90-boiler-oxpec-blacklist.conf']=b'blacklist oxpec\n'
    if fan_live:
        if 'resource:onexplayer-fan' not in selected or not ec_profile:
            raise ResourceError('Fan guard requires selected fan and reviewed EC profile')
        files['etc/systemd/system/oxp3-fan.service.d/90-boiler-fan-live.conf'] = (
            '[Service]\nReadWritePaths=/var/lib/oxp3-fan\nTimeoutStartSec=120\nTimeoutStopSec=120\n'
            'ExecStartPre=/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-firstboot.py fan-prestart\n'
            'ExecStartPost=/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-firstboot.py fan-poststart\n'
            'ExecStop=/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-firstboot.py fan-prestop\n'
            'ExecStopPost=\n'
            'ExecStopPost=/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-firstboot.py fan-stop-auto\n'
            'ExecStopPost=/usr/bin/python3 -I /usr/local/lib/boiler/boiler-resource-firstboot.py fan-poststop\n').encode()
    if set(selected)&set(PLUGIN_IDS):
        files['etc/systemd/system/plugin_loader.service.d/90-boiler-resource-gate.conf']=b'[Unit]\nAfter=boiler-resource-firstboot.service\nConditionPathExists=/var/lib/boiler-resource-approved/decky.json\n'
    rows=[]
    for index,(target,data) in enumerate(files.items()):
        name=str(index)+'.bin';(temporary/name).write_bytes(data)
        rows.append(dict(source=name,target=target,size=len(data),sha256=hashlib.sha256(data).hexdigest(),mode=0o600 if target==INTENT else 0o644))
    return dict(schema_version=1,kind='ComponentManifest',id=BOOTSTRAP,version='1.0.0',
                files=rows,requires=['core:maintenance'],conflicts=[],compatibility={})


def _generic(options):
    from integration import validate_options
    generic={k:copy.deepcopy(v) for k,v in options.items() if not k.startswith('resource_')}
    if generic.get('device_package') or generic.get('device_components'):
        raise ResourceError('Resource deployment cannot run a legacy model package')
    if 'core:maintenance' not in generic.get('plugins',[]):raise ResourceError('Retained maintenance is required')
    validate_options(generic)
    return generic


def _scheduled(rows,pool,identities,receipt):
    for row in rows.values():
        for identity in identities:
            resource=pool.resource(identity)
            raw=json.dumps(resource['payload']['files'],sort_keys=True,separators=(',',':')).encode()
            handler=resource['install']['kind']
            row['components'][identity]=dict(status='firstboot_scheduled',runtime='pending_firstboot',activation='pending-firstboot',
                resource_receipt=receipt['id'],handler=handler,bundle_sha256=hashlib.sha256(raw).hexdigest())
            row['install_plan']['components'].append(dict(schema_version=1,kind='ComponentManifest',id=identity,
                version=resource['version'],files=[],requires=['core:maintenance',*resource['dependencies']],conflicts=[],
                compatibility=resource['compatibility'],firstboot_handler=handler,scheduled_payload_sha256=hashlib.sha256(raw).hexdigest()))


def _merge_generic(row,generic):
    extras={k:v for k,v in generic['components'].items() if not k.startswith('resource:') and k!='core:maintenance'}
    row['components'].update(copy.deepcopy(extras))
    row['install_plan']['components'].extend(copy.deepcopy(c) for c in generic['install_plan']['components'] if c['id'] in extras)


def _password_hash(options):
    if not options.get('set_deck_password'):return None
    import os,stat,re
    path=Path('/tmp/oxp-deck-initial-password')
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=0 or info.st_mode&0o077:
            raise ResourceError('Initial password did not arrive through the secure channel')
        password=bytearray(stream.read(256))
    try:
        if not password or len(password)>255 or any(v in password for v in (0,10,13)):raise ResourceError('Invalid initial password')
        result=subprocess.run(['openssl','passwd','-6','-stdin'],input=bytes(password),check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20).stdout.strip().decode('ascii')
        if not re.fullmatch(r'\$6\$(?:rounds=\d+\$)?[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{20,100}',result):raise ResourceError('Initial password hash failed')
        return result
    finally:
        for i in range(len(password)):password[i]=0
        path.unlink(missing_ok=True)

def execute(payload,options,*,readonly):
    payload=Path(payload);reviewed=selection(options);generic=_generic(options)
    from maintenance.hdr_resources import bind,check_legacy
    with local_authorizer(readonly=readonly)(payload,reviewed,'/dev/vdc') as authorization:
        if authorization.binding.get('execution')!='firstboot-install-v1' or authorization.hardware:
            raise ResourceError('First-boot deployment requires a purpose-bound offline image session without physical facts')
        if Path('/sys/class/dmi/id/sys_vendor').read_text().strip()!='QEMU':raise ResourceError('Managed preparation VM required')
        if Path('/sys/class/block/vdc/ro').read_text().strip()!=('1' if readonly else '0'):raise ResourceError('Prepared image protection differs')
        table=json.loads(subprocess.check_output(['sfdisk','--json','/dev/vdc']))['partitiontable']
        parts=checked_parts(table);authorization.revalidate('/dev/vdc',copy.deepcopy(reviewed),copy.deepcopy(table))
        models=[m for m in authorization.pool.catalog['models'] if m['id']==reviewed['model_id']]
        if len(models)!=1:raise ResourceError('Resource model reference is ambiguous')
        pool,references=bind(authorization.pool,models[0]['resources'],reviewed['components'])
        identities=[r['id'] for r in resolve(pool,references,reviewed['components'])]
        if set(identities)!=set(reviewed['components']):raise ResourceError('Explicit dependency closure changed')
        ec_selected=('resource:onexplayer-ec-fan-driver' in identities and
            pool.resource('resource:onexplayer-ec-fan-driver').get('ec_profile_contract') is not None)
        if ec_selected:
            from protocol.ec_profile_contract import validate_contract, PROFILE
            validate_contract(pool.resource('resource:onexplayer-ec-fan-driver'))
            if models[0].get('parameters',{}).get('ec_board_profile')!=PROFILE:
                raise ResourceError('Reviewed model has no matching fixed EC profile')
        scheduled=[i for i in identities if pool.resource(i)['install']['kind'] in ('boiler-input-runtime-v1','boiler-kde-keyboard-v1')]
        offline_selected=[i for i in identities if i not in scheduled]
        needs_efi=any(pool.resource(i)['install']['kind']=='boiler-boot-settings-v1' or any(r['target']=='efi' or r['target'].startswith('efi/') for r in pool.resource(i)['install']['files']) for i in offline_selected)
        generic_ids=set(generic.get('plugins',[])+generic.get('repairs',[]))
        from protocol.resource_selection import installer_dependencies
        for resource in resolve(pool,references,reviewed['components']):
            missing=installer_dependencies(resource)-generic_ids
            if missing:raise ResourceError('Missing selected installer dependency: '+','.join(sorted(missing)))
        core=retained_core(payload);sources={};templates={};generic_results={}
        from maintenance.resource_staging import _fixed_core
        from protocol.resource_pool import inventory
        _fixed_core(core,inventory(pool.root))
        def session(mounts,*,read_only,planned=False):
            from integration import release,install_plan
            slots={}
            for slot,root in mounts.roots.items():
                source=release(root);sources[slot]=source;templates[slot]=install_plan(generic,source,slot)
                templates[slot]['maintenance']['version']=core['version']
                slots[slot]=dict(root=root,environment=dict(architecture='x86_64',os_id=source['ID'],os_version=source['VERSION_ID'],kernels=source['KERNELS']),
                    hardware={'target_disk':copy.deepcopy(authorization.binding.get('target_disk'))},opt_offload=mounts.opt_offload[slot])
            binding=None
            if ('resource:intel-g3e-public-cpu-controls' in identities and
                pool.resource('resource:intel-g3e-public-cpu-controls')['install']['kind']=='intel-g3e-public-cpu-live-v1'):
                from maintenance.cpu_live_resources import offline_binding
                binding=offline_binding(authorization,mounts,reviewed)
            return ResourceSession(pool,slots,mounts.home,read_only=read_only,planned_core=planned,offline_image=True,cpu_live_binding=binding)
        with PreparedMounts(parts,readonly=True,base='/run/boiler-resource-target',efi=needs_efi) as mounts:
            if 'resource:common-kde-keyboard' in scheduled:
                from maintenance.keyboard_runtime.service import Keyboard
                for root in mounts.roots.values():
                    Keyboard(root)._preflight(apply=True)
            value=session(mounts,read_only=True,planned=not readonly)
            require_clean(value);preflight_metadata(mounts.roots);check_legacy(mounts.home,reviewed['components'])
            ec_early={}
            if ec_selected:
                from maintenance.ec_initramfs import inspect as inspect_initramfs
                from maintenance.adoption_blobs import observe
                for slot,root in mounts.roots.items():
                    ec_early[slot]=inspect_initramfs(root)
                    engine=Engine(root,read_only=True)
                    if not readonly and observe(engine,engine._target('etc/modprobe.d/90-boiler-oxpec-blacklist.conf'))['exists']:
                        raise ResourceError('Existing EC blacklist path must be preserved and reviewed')
            prepared=value.prepare(references,offline_selected)
            if readonly:
                if any(not p['ok'] for p in value.verify(offline_selected)):raise ResourceError('Readonly resource verification failed')
                for slot,root in mounts.roots.items():
                    from maintenance.resource_firstboot import checked_intent
                    intent=checked_intent(root)
                    expected=next(p['uuid'] for p in table['partitions'] if p['name']=='rootfs-'+slot)
                    if intent['selection']!=reviewed or intent['root_partuuid'].lower()!=expected.lower():raise ResourceError('First-boot intent target/selection changed')
                    if not Engine(root,read_only=True).verify_readonly(BOOTSTRAP)['ok']:raise ResourceError('First-boot runtime/intent changed')
                receipt=receipt_readonly(value,mounts.roots,prepared)
                rows=documents(options,sources,core,prepared,receipt,templates,session=value)
                _scheduled(rows,pool,scheduled,receipt)
                # Use exact stored firstboot metadata; only committed file/selection
                # evidence is recomputed, never a current hardware success claim.
                for slot,root in mounts.roots.items():
                    recorded=json.loads((root/'home/.boiler/integration'/slot/'result.json').read_bytes())
                    for identity,entry in recorded['components'].items():
                        if identity not in identities and identity!='core:maintenance':
                            if identity=='core:decky':
                                from maintenance.plugins import PluginMaintenance
                                proof=PluginMaintenance(root).verify('decky')
                            else:proof=Engine(root,read_only=True).verify_readonly(identity)
                            if not proof['ok']:raise ResourceError('Generic component files changed: '+identity)
                    from system_presets import verify as verify_presets
                    verify_presets(root,generic,Engine(root,read_only=True))
                    if generic.get('graphics_profile'):
                        from graphics import verify_inventory
                        from graphics_boot import verify as verify_graphics_boot
                        graphics_result=recorded['components'][generic['graphics_profile']]
                        verify_inventory(root,graphics_result);verify_graphics_boot(root,graphics_result['early_boot_proof'])
                    _merge_generic(rows[slot],recorded)
                    rows[slot]['firstboot']=recorded['firstboot'];rows[slot]['install_plan']['firstboot']=recorded['install_plan']['firstboot']
                    for identity in identities:rows[slot]['components'][identity]['activation']='pending-firstboot'
                verify_records(mounts.roots,rows)
                return dict(status='files-verified-readonly',runtime='not_validated',slot_results=rows)
        current=json.loads(subprocess.check_output(['sfdisk','--json','/dev/vdc']))['partitiontable']
        if current!=table:raise ResourceError('Prepared image topology changed before resource deployment')
        authorization.revalidate('/dev/vdc',copy.deepcopy(reviewed),copy.deepcopy(current))
        with PreparedMounts(parts,readonly=False,base='/run/boiler-resource-target',efi=needs_efi) as mounts:
            if ec_selected:
                for slot,root in mounts.roots.items():
                    if inspect_initramfs(root)!=ec_early[slot]:raise ResourceError('Target initramfs changed after preflight')
            from integration import stage
            password_hash=_password_hash(generic)
            for slot,root in mounts.roots.items():
                seed_root(Engine(root))
                generic_results[slot]=stage(root,payload,generic,slot,password_hash,target_disk=options.get('_target_disk'))
            value=session(mounts,read_only=False)
            prepared=value.prepare(references,offline_selected)
            receipt=value.install(references,offline_selected)
            if any(not p['ok'] for p in value.verify(offline_selected)):raise ResourceError('Committed resource files failed verification')
            if ec_selected:
                # Other selected fixed components may legitimately refresh the
                # image; bind proof of the final bytes before first-boot setup.
                for slot,root in mounts.roots.items():ec_early[slot]=inspect_initramfs(root)
            rows=documents(options,sources,core,prepared,receipt,templates,session=value)
            _scheduled(rows,pool,scheduled,receipt)
            for slot in ('A','B'):
                _merge_generic(rows[slot],generic_results[slot])
            generation=receipt['id']
            for slot,root in mounts.roots.items():
                root_partuuid=next(p['uuid'] for p in table['partitions'] if p['name']=='rootfs-'+slot)
                intent=dict(schema_version=1,kind=KIND,mode='firstboot-install-v1',generation=generation,
                    slot=slot,root_partuuid=root_partuuid,selection=copy.deepcopy(reviewed),resource_receipt=copy.deepcopy(receipt),
                    target_roles={role:dict(partuuid=next(p['uuid'] for p in table['partitions'] if p['name']==role),
                        filesystem=authorization.filesystems[role][0],uuid=authorization.filesystems[role][1]) for role in ('rootfs-A','rootfs-B','var-A','var-B','home','efi-A','efi-B')},mount_efi=needs_efi)
                if ec_selected:
                    intent['ec_board_profile']=models[0]['parameters']['ec_board_profile']
                    intent['ec_initramfs']=copy.deepcopy(ec_early[slot])
                    from maintenance.adoption_blobs import observe
                    engine=Engine(root,read_only=True)
                    if observe(engine,engine._target('etc/modprobe.d/90-boiler-oxpec-blacklist.conf'))['exists']:
                        raise ResourceError('Existing EC blacklist path must be preserved and reviewed')
                for bound_slot,bound_root in mounts.roots.items():
                    output=subprocess.check_output(['btrfs','subvolume','show',str(bound_root)],text=True)
                    values=re.findall(r'^\s*UUID:\s*([0-9a-f-]{36})\s*$',output,re.MULTILINE)
                    if len(values)!=1:raise ResourceError('Prepared Btrfs subvolume identity is ambiguous')
                    intent['target_roles']['rootfs-'+bound_slot]['subvolume_uuid']=values[0]
                with tempfile.TemporaryDirectory(prefix='boiler-firstboot-') as directory:
                    temporary=Path(directory);manifest=bootstrap_manifest(temporary,intent,cpu_live=('resource:intel-g3e-public-cpu-controls' in identities and pool.resource('resource:intel-g3e-public-cpu-controls')['install']['kind']=='intel-g3e-public-cpu-live-v1'),ec_profile=ec_selected,fan_live=ec_selected and 'resource:onexplayer-fan' in identities)
                    operation=Engine(root).apply(manifest,temporary)
                if not Engine(root,read_only=True).verify_readonly(BOOTSTRAP)['ok']:raise ResourceError('First-boot installation verification failed')
                metadata=dict(status='scheduled',runtime='not_validated',generation=generation,root_partuuid=root_partuuid,
                    bootstrap=operation,manifest=manifest)
                rows[slot]['firstboot']=metadata;rows[slot]['install_plan']['firstboot']=metadata
                for identity in identities:rows[slot]['components'][identity]['activation']='pending-firstboot'
            publish(mounts.roots,rows);verify_records(mounts.roots,rows)
            return dict(status='files-installed',runtime='not_validated',slot_results=rows)

