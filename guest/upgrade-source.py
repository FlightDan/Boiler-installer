"""Compose a recovery disk from the fixed official root image, in an isolated VM."""
import json
import subprocess
from pathlib import Path
from source_profile import inspect_source

def run(*args): return subprocess.check_output(args,text=True).strip()
if Path('/sys/class/dmi/id/sys_vendor').read_text().strip()!='QEMU': raise RuntimeError('Managed VM required')
if Path('/sys/class/block/vdd/ro').read_text().strip()!='1': raise RuntimeError('Update must be read-only')
table=json.loads(run('sfdisk','--json','/dev/vdc'))['partitiontable']
if [p.get('name') for p in table['partitions']]!=['esp','efi-A','rootfs-A','var-A','home']: raise RuntimeError('Unexpected recovery layout')
part=table['partitions'][2]
if part['size']*512<int(run('blockdev','--getsize64','/dev/vdd')): raise RuntimeError('Root update does not fit')
root=Path('/mnt/update-root');root.mkdir(exist_ok=True)
run('mount','-t','btrfs','-o','ro,rescue=nologreplay','/dev/vdd',str(root))
try:
    release=inspect_source(root)
    print('OXP_UPDATE_SOURCE='+json.dumps(release),flush=True)
finally:run('umount',str(root))
run('dd','if=/dev/vdd','of='+part['node'],'bs=4M','conv=fsync')
run('mount','-t','btrfs','-o','ro,rescue=nologreplay',part['node'],str(root))
try:
    if inspect_source(root)!=release: raise RuntimeError('Written root metadata differs')
    print('OXP_UPDATE_ROOT='+release['version']+':'+release['build'],flush=True)
finally:run('umount',str(root))
