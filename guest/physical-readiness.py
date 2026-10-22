"""Inspect actual source contents before the Windows helper opens a target for writes."""
import json
import subprocess
from pathlib import Path
from source_profile import inspect_source

def run(*args):return subprocess.check_output(args,text=True).strip()
if Path('/sys/class/dmi/id/sys_vendor').read_text().strip()!='QEMU':raise RuntimeError('Managed VM required')
if Path('/sys/class/block/vdc/ro').read_text().strip()!='1':raise RuntimeError('Source must be read-only')
table=json.loads(run('sfdisk','--json','/dev/vdc'))['partitiontable']
nodes={p.get('name'):p['node'] for p in table['partitions']}
root=Path('/mnt/readiness');root.mkdir(exist_ok=True)
profiles={}
for slot in ('A','B'):
    run('mount','-t','btrfs','-o','ro,rescue=nologreplay',nodes['rootfs-'+slot],str(root))
    try:
        release=inspect_source(root)
        profiles[slot]=release
        if run('btrfs','property','get','-ts',str(root),'ro')!='ro=true':raise RuntimeError('Root is not protected')
        print('OXP_PHYSICAL_SOURCE='+slot+':'+release['version']+':'+release['build'],flush=True)
    finally:run('umount',str(root))
print('BOILER_SOURCE_JSON='+json.dumps(dict(profiles['A'],slots=profiles),sort_keys=True),flush=True)
run('mount','-t','vfat','-o','ro',nodes['esp'],str(root))
try:
    if not (root/'EFI/BOOT/BOOTX64.EFI').is_file():raise RuntimeError('Missing removable UEFI loader')
finally:run('umount',str(root))
selection = Path('/mnt/payload/selection.json')
if selection.exists():
    request = json.loads(selection.read_text())
    if request != {'home_copy': True}:
        raise RuntimeError('Unknown physical readiness selection')
    from ext4_copy_plan import canonical_json, generate_managed_home
    print('OXP_HOME_COPY_PLAN=' + canonical_json(generate_managed_home()), flush=True)
