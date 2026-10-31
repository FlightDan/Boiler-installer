"""Read-only partition authority using the retained public topology parser."""
import json
import os
import subprocess
from pathlib import Path
from .engine import MaintenanceError
from .adoption_blobs import _directory
from .keyboard_runtime.vendor import probe


def run(program, *arguments):
    # SteamOS uses /usr/bin; the existing installer guest's locked Alpine APKs
    # place these tools in /bin. Caller environment and package data do not
    # choose a command or PATH. Both programs only inspect mounted topology.
    if program not in ('findmnt','lsblk'):
        raise MaintenanceError('Unknown keyboard topology command')
    result=subprocess.run([program,*arguments],check=True,capture_output=True,text=True,
        stdin=subprocess.DEVNULL,timeout=30,
        env={'PATH':'/usr/bin:/usr/sbin:/bin:/sbin','LC_ALL':'C','LANG':'C.UTF-8'})
    return result.stdout.strip()


def binding(root, expected_label=None):
    root = Path(os.path.abspath(root))
    with _directory(root):
        pass
    mount = json.loads(run('findmnt','--json','--target',str(root),'--output',
        'TARGET,SOURCE,MAJ:MIN,FSTYPE,UUID'))['filesystems']
    if len(mount) != 1 or mount[0].get('target') != str(root):
        raise MaintenanceError('Keyboard requires an explicitly mounted target root')
    devices = json.loads(run('lsblk','--json','--tree','--paths','--output',
        'PATH,TYPE,MAJ:MIN,PARTUUID,PARTLABEL,TRAN,MODEL'))['blockdevices']
    verified = None
    if mount[0].get('fstype') == 'btrfs':
        verified = probe._single_btrfs_device(mount[0].get('uuid'), probe._canonical_block(mount[0].get('source')))
    topology = probe.identify_root(mount[0], devices, expected_label, verified_device=verified)
    if topology.get('label') not in ('A','B'):
        raise MaintenanceError('Keyboard requires an identified SteamOS A/B root partition')
    home = root/'home'
    with _directory(home):
        info = home.stat()
    home_mount = json.loads(run('findmnt','--json','--target',str(home),'--output',
        'TARGET,SOURCE,MAJ:MIN,FSTYPE,UUID'))['filesystems']
    if len(home_mount) != 1 or home_mount[0].get('target') != str(home) or not home_mount[0].get('uuid'):
        raise MaintenanceError('Keyboard requires a separately mounted shared home')
    if home_mount[0].get('maj:min') == mount[0].get('maj:min'):
        raise MaintenanceError('Shared home cannot be the root partition')
    return dict(slot=topology['id'], label=topology['label'], partuuid=topology['partuuid'],
        home=dict(device=info.st_dev,inode=info.st_ino,uuid=home_mount[0]['uuid'],
                  source=home_mount[0]['source'],major_minor=home_mount[0]['maj:min']))
