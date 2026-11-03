"""Apply choices to the wizard's independent prepared raw image, never a host disk."""
import json
from pathlib import Path
import re
import os
import stat
import subprocess
import sys
sys.path.insert(0,'/mnt/payload/components')
sys.path.insert(0,'/mnt/payload')
from integration import stage, validate_options, authenticate_model_efi, complete_ssd, save_result, device_plan, release, selected_ssd_provider_owner, SSD_REPAIR
from system_presets import selected as presets_selected
from offload_mounts import mount_opt, mount_etc
if Path('/sys/class/dmi/id/sys_vendor').read_text().strip()!='QEMU': raise RuntimeError('Managed VM required')
if Path('/sys/class/block/vdc/ro').read_text().strip()!='0': raise RuntimeError('Prepared image must be writable')
table=json.loads(subprocess.check_output(['sfdisk','--json','/dev/vdc']))['partitiontable']
parts={p.get('name'):p['node'] for p in table['partitions']}
if len(parts) != len(table['partitions']): raise RuntimeError('Duplicate prepared partition labels')
if not {'rootfs-A','rootfs-B','var-A','var-B','home'}.issubset(parts): raise RuntimeError('Not a prepared A/B image')
options=json.loads(Path('/mnt/payload/selection.json').read_text())
validate_options(options)
if options.get('install_mode')=='imported':
    print('OXP_INTEGRATION_PRESERVED=imported-image',flush=True)
    raise SystemExit(0)
if not any(options.get(key) for key in ('repairs','plugins','wireless','device_components','graphics_profile')) and not presets_selected(options):
    probe=Path('/mnt/boiler-plan-source'); probe.mkdir(exist_ok=True)
    for slot in ('A','B'):
        subprocess.run(['mount','-t','btrfs','-o','ro,rescue=nologreplay',parts['rootfs-'+slot],str(probe)],check=True)
        try:
            result=stage(probe,'/mnt/payload',options,slot)
            print('BOILER_INSTALL_PLAN_JSON='+json.dumps(result['install_plan']),flush=True)
        finally: subprocess.run(['umount',str(probe)],check=True)
    print('OXP_INTEGRATION_PRESERVED=no-components-selected',flush=True)
    raise SystemExit(0)
Path('/run/boiler-guest-context').write_text('managed-qemu-integration\n')
target_disk=options.pop('_target_disk',None)
ssd_context=options.pop('_ssd_binding',None)
ssd_identity=options.pop('_ssd_component',None)
ssd_plans={}
ssd_provider_owner=None
model_plans={}
ssd_previews={}
model_source=None
model_selection=None
model_context=ssd_context if ssd_identity is not None else {'kind':'nonphysical'}
model_needed='oxp3:split-lock' in (options.get('device_legacy_options') or {}).get('repairs', [])
if ssd_identity is not None:
    if not isinstance(ssd_identity,str) or ssd_identity not in options.get('repairs', []) + (options.get('device_components') or []):
        raise RuntimeError('SSD component is not explicitly selected')
    if not isinstance(ssd_context,dict) or ssd_context.get('kind') != 'physical':
        raise RuntimeError('SSD repair requires reviewed physical destination context')
elif ssd_context is not None or 'ssd:gm7-boot-repair' in options.get('repairs', []):
    raise RuntimeError('Unexpected SSD repair context')
if ssd_identity is not None or model_needed:
    if not {'efi-A','efi-B'}.issubset(parts): raise RuntimeError('Both prepared EFI partitions are required')
    from maintenance.ssd_boot import installer_plans
    roots=[Path('/mnt/boiler-ssd-preflight-A'),Path('/mnt/boiler-ssd-preflight-B')]
    mounts=[]
    def preflight_mount(node,path,*flags):
        path.mkdir(parents=True,exist_ok=True)
        subprocess.run(['mount',*flags,node,str(path)],check=True); mounts.append(path)
    try:
        for slot,root in zip(('A','B'),roots):
            preflight_mount(parts['rootfs-'+slot],root,'-t','btrfs','-o','ro,rescue=nologreplay')
            if subprocess.check_output(['btrfs','property','get','-ts',str(root),'ro'],text=True).strip()!='ro=true':
                raise RuntimeError('Prepared root protection missing during SSD preflight')
            preflight_mount(parts['var-'+slot],root/'var','-t','ext4','-o','ro,noload')
            preflight_mount(parts['home'],root/'home','-t','ext4','-o','ro,noload')
            mount_etc(root,mounts,readonly=True)
            preflight_mount(parts['efi-'+slot],root/'efi','-t','vfat','-o','ro')
        if ssd_identity is not None and ssd_identity in (options.get('device_components') or []):
            authenticated=[]
            for candidate in roots:
                _,parts_selected,_,descriptor=device_plan(Path('/mnt/payload'),options,release(candidate))
                authenticated.append(selected_ssd_provider_owner(ssd_identity,parts_selected,descriptor))
            if authenticated[0] != authenticated[1]:
                raise RuntimeError('SSD provider identity differs between slots')
            ssd_provider_owner=authenticated[0]
        if model_needed:
            from maintenance.model_efi import model_efi_plan
            from maintenance.ssd_boot import installer_preflight_model_ssd
            authenticated=[authenticate_model_efi(root,'/mnt/payload',options) for root in roots]
            if authenticated[0] != authenticated[1]: raise RuntimeError('Model source differs between slots')
            model_source=authenticated[0]['source']; model_selection=authenticated[0]['selection']
            plans=[model_efi_plan(root,model_context,model_source,model_selection) for root in roots]
            model_plans=dict(zip(('A','B'),plans))
            if ssd_identity is not None:
                previews=[installer_preflight_model_ssd(root,model_context,plan,provider_owner=ssd_provider_owner) for root,plan in zip(roots,plans)]
                ssd_previews=dict(zip(('A','B'),previews))
        else:
            plans=installer_plans(roots[0],roots[1],ssd_context,provider_owner=ssd_provider_owner)
            ssd_plans=dict(zip(('A','B'),plans))
    finally:
        original_error=sys.exc_info()[1]
        failures=[]
        for path in reversed(mounts):
            try: subprocess.run(['umount',str(path)],check=True)
            except Exception as error: failures.append(str(path)+': '+str(error))
        if failures: raise RuntimeError('SSD preflight cleanup incomplete; preserve image: '+'; '.join(failures)) from original_error
password_file=Path('/tmp/oxp-deck-initial-password')
password_hash=None
if options.get('set_deck_password',False):
    descriptor = os.open(password_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or info.st_mode & 0o077:
            raise RuntimeError('The selected deck password was not received securely')
        password=bytearray(stream.read(256))
    try:
        if not password or len(password)>255 or any(value in password for value in (0,10,13)):
            raise RuntimeError('Invalid deck password input')
        hashed=subprocess.run(['openssl','passwd','-6','-stdin'],input=bytes(password),check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20).stdout.strip()
        password_hash=hashed.decode('ascii')
        if not re.fullmatch(r'\$6\$(?:rounds=\d+\$)?[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{20,100}',password_hash):
            raise RuntimeError('OpenSSL did not produce a SHA-512 crypt password hash')
    finally:
        for index in range(len(password)): password[index]=0
        password_file.unlink(missing_ok=True)
else:
    password_file.unlink(missing_ok=True)
if model_plans:
    from maintenance.model_efi import model_efi_authorize_pair
    from maintenance.ssd_boot import installer_model_ssd_plan
    mounts=[]; protected=[]; results={}
    def combined_mount(node,path,*flags):
        path.mkdir(parents=True,exist_ok=True)
        subprocess.run(['mount',*flags,node,str(path)],check=True); mounts.append(path)
    try:
        for slot,root in zip(('A','B'),roots):
            combined_mount(parts['rootfs-'+slot],root,'-t','btrfs')
            if subprocess.check_output(['btrfs','property','get','-ts',str(root),'ro'],text=True).strip()!='ro=true':
                raise RuntimeError('Prepared root is not protected')
            subprocess.run(['btrfs','property','set','-ts',str(root),'ro','false'],check=True); protected.append(root)
            combined_mount(parts['var-'+slot],root/'var'); combined_mount(parts['home'],root/'home')
            mount_etc(root,mounts); mount_opt(root,mounts)
            combined_mount(parts['efi-'+slot],root/'efi','-t','vfat')
        model_efi_authorize_pair(roots[0],roots[1],model_context,model_source,model_selection,
            [model_plans['A'],model_plans['B']],
            [ssd_previews['A'],ssd_previews['B']] if ssd_identity is not None else None)
        for slot,root in zip(('A','B'),roots):
            review_path='/run/boiler-oxp3-efi-'+slot+'.json'
            context=dict(source=model_source,selection=model_selection,host_context=model_context,
                         plan=model_plans[slot],sibling_root=str(roots[0]) if slot=='B' else None)
            descriptor=os.open(review_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(descriptor,'w') as stream:
                json.dump(context,stream); stream.flush(); os.fsync(stream.fileno())
            Path('/run/boiler-install-slot').write_text(slot+'\n')
            results[slot]=stage(root,'/mnt/payload',options,slot,password_hash,target_disk=target_disk,
                ssd_context=ssd_context,ssd_plan=ssd_previews.get(slot),ssd_identity=ssd_identity,
                defer_ssd=ssd_identity is not None,model_review_path=review_path)
        if ssd_identity is not None:
            for slot,root in zip(('A','B'),roots):
                plan=installer_model_ssd_plan(root,ssd_context,ssd_previews[slot],provider_owner=ssd_provider_owner)
                complete_ssd(root,results[slot],ssd_context,plan,ssd_identity,model_preview=ssd_previews[slot])
                results[slot]['status']='files_installed'; save_result(root,slot,results[slot])
        for slot in ('A','B'):
            result=results[slot]
            print('OXP_INTEGRATION_RESULT='+json.dumps(result),flush=True)
            print('BOILER_INSTALL_PLAN_JSON='+json.dumps(result['install_plan']),flush=True)
            for name,value in result['components'].items():
                print('OXP_COMPONENT_RESULT='+json.dumps(dict(slot=slot,id=name,**value)),flush=True)
    finally:
        original_error=sys.exc_info()[1]; failures=[]
        for root in reversed(protected):
            try: subprocess.run(['btrfs','property','set','-ts',str(root),'ro','true'],check=True)
            except Exception as error: failures.append('root protection '+str(root)+': '+str(error))
        for path in reversed(mounts):
            try: subprocess.run(['umount',str(path)],check=True)
            except Exception as error: failures.append('unmount '+str(path)+': '+str(error))
        if failures: raise RuntimeError('Combined EFI cleanup incomplete; preserve image: '+'; '.join(failures)) from original_error
    raise SystemExit(0)
root=Path('/mnt/integration-root'); root.mkdir(exist_ok=True)
for slot in ('A','B'):
    mounts=[]
    def mount(node,path,*flags):
        path.mkdir(parents=True,exist_ok=True); subprocess.run(['mount',*flags,node,str(path)],check=True); mounts.append(path)
    try:
        mount(parts['rootfs-'+slot],root,'-t','btrfs')
        was=subprocess.check_output(['btrfs','property','get','-ts',str(root),'ro'],text=True).strip()
        if was!='ro=true': raise RuntimeError('Prepared root is not protected')
        subprocess.run(['btrfs','property','set','-ts',str(root),'ro','false'],check=True)
        mount(parts['var-'+slot],root/'var'); mount(parts['home'],root/'home')
        mount_etc(root,mounts)
        mount_opt(root,mounts)
        # Device package hooks receive the actual slot EFI mount explicitly.
        if options.get('device_package') or ssd_plans: mount(parts['efi-'+slot],root/'efi','-t','vfat')
        Path('/run/boiler-install-slot').write_text(slot+'\n')
        result=stage(root,'/mnt/payload',options,slot,password_hash,target_disk=target_disk,
                     ssd_context=ssd_context,ssd_plan=ssd_plans.get(slot),ssd_identity=ssd_identity)
        print('OXP_INTEGRATION_RESULT='+json.dumps(result),flush=True)
        print('BOILER_INSTALL_PLAN_JSON='+json.dumps(result['install_plan']),flush=True)
        for name,value in result['components'].items():
            print('OXP_COMPONENT_RESULT='+json.dumps(dict(slot=slot,id=name,**value)),flush=True)
            if value.get('coverage')=='partial':
                print('OXP_COMPONENT_WARNING='+json.dumps(dict(slot=slot,id=name,
                    reason='Only available declared firmware was resolved; uncovered hardware variants are not supported by this payload',
                    unavailable_requirements=value['unavailable_requirements'],
                    unavailable_companions=value.get('unavailable_companions',[]),runtime='not_validated')),flush=True)
    finally:
        original_error = sys.exc_info()[1]
        cleanup_errors = []
        if root in mounts:
            try:
                subprocess.run(['btrfs','property','set','-ts',str(root),'ro','true'],check=True)
            except Exception as error:
                cleanup_errors.append('root protection: ' + str(error))
        for path in reversed(mounts):
            try:
                subprocess.run(['umount',str(path)],check=True)
            except Exception as error:
                cleanup_errors.append('unmount ' + str(path) + ': ' + str(error))
        if cleanup_errors:
            raise RuntimeError('Prepared image cleanup incomplete; preserve image: ' + '; '.join(cleanup_errors)) from original_error
