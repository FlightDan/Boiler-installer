"""Apply choices to the wizard's independent prepared raw image, never a host disk."""
import json
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,'/mnt/payload/components')
from integration import stage, validate_options
from offload_mounts import mount_opt, mount_etc
if Path('/sys/class/dmi/id/sys_vendor').read_text().strip()!='QEMU': raise RuntimeError('Managed VM required')
if Path('/sys/class/block/vdc/ro').read_text().strip()!='0': raise RuntimeError('Prepared image must be writable')
table=json.loads(subprocess.check_output(['sfdisk','--json','/dev/vdc']))['partitiontable']
parts={p.get('name'):p['node'] for p in table['partitions']}
if not {'rootfs-A','rootfs-B','var-A','var-B','home'}.issubset(parts): raise RuntimeError('Not a prepared A/B image')
options=json.loads(Path('/mnt/payload/selection.json').read_text())
validate_options(options)
if options.get('install_mode')=='imported':
    print('OXP_INTEGRATION_PRESERVED=imported-image',flush=True)
    raise SystemExit(0)
if not any(options.get(key) for key in ('repairs','plugins','wireless','device_components')):
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
password_file=Path('/tmp/oxp-deck-initial-password')
password_hash=None
if options.get('set_deck_password',False):
    if not password_file.is_file(): raise RuntimeError('The selected deck password was not received securely')
    password=bytearray(password_file.read_bytes())
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
        if options.get('device_package'): mount(parts['efi-'+slot],root/'efi','-t','vfat')
        Path('/run/boiler-install-slot').write_text(slot+'\n')
        result=stage(root,'/mnt/payload',options,slot,password_hash,target_disk=target_disk)
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
        if root in mounts:
            subprocess.run(['btrfs','property','set','-ts',str(root),'ro','true'],check=True)
        for path in reversed(mounts): subprocess.run(['umount',str(path)],check=True)
