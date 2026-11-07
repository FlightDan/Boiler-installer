"""Actual disposable-disk failure/retry adapter; not a production disk selector."""
import json
from pathlib import Path
from deployment import Deployment, InjectedFailure, run, table
from layout import GIB
from fixture_layout import validate, preserved_files, range_bytes

if Path('/sys/class/dmi/id/sys_vendor').read_text().strip() != 'QEMU': raise RuntimeError('Not QEMU')
if Path('/sys/block/vdd/size').read_text().strip() != '167772160': raise RuntimeError('Not 80 GiB fixture')
target = table('/dev/vdd')
profile_name = validate('/dev/vdd')
selection = (json.loads(Path('/tmp/session-deployment.json').read_text())
             if Path('/tmp/session-deployment.json').exists() else
             {'schema': 1, 'mode': 'virtual_install', 'source_table': table('/dev/vdc'),
              'target_table': target, 'range': {'start': range_bytes(profile_name)[0],
                                                 'size': range_bytes(profile_name)[1]}})

failed = False
try:
    Deployment(selection, fault='configure-A').apply()
except InjectedFailure:
    failed = True
    print('OXP_DEPLOY_TEST=injected-failure-observed', flush=True)

def mount(node, path, *args):
    path.mkdir(exist_ok=True, parents=True); run('mount', *args, node, str(path))

root = Path('/mnt/fixture-check-root')
home = Path('/mnt/fixture-check-home')
state_path = Path('/mnt/fixture-check-state')
nodes = Deployment(selection).nodes
if failed:
    mount(nodes['rootfs-A'], root, '-t', 'btrfs', '-o', 'ro,rescue=nologreplay')
    try:
        if run('btrfs', 'property', 'get', '-ts', str(root), 'ro', capture=True).strip() != 'ro=true':
            raise RuntimeError('Failure did not restore A root protection')
    finally: run('umount', str(root))
    mount(nodes['esp'], state_path, '-o', 'ro')
    try:
        state = json.loads((state_path / 'oxp3/install-state.json').read_text())
        if state['status'] != 'failed' or state['phase'] != 'configure-A' or len(state['copied']) != 2:
            raise RuntimeError('Failure phase was not persisted')
        before_roots = dict(state['copied']); before_home = state['formatted']['home']
    finally: run('umount', str(state_path))
    mount(nodes['home'], home)
    try: (home / 'retry-preserved.txt').write_text('Already formatted home must survive retry.\n')
    finally: run('umount', str(home))

Deployment(selection).apply()
mount(nodes['esp'], state_path, '-o', 'ro')
try:
    state = json.loads((state_path / 'oxp3/install-state.json').read_text())
    if state['status'] != 'complete' or state['configured'] != ['A', 'B']: raise RuntimeError('Incomplete deployment')
    if failed and (state['copied'] != before_roots or state['formatted']['home'] != before_home):
        raise RuntimeError('Retry recopied finished roots or reformatted home')
finally: run('umount', str(state_path))
if failed:
    mount(nodes['home'], home, '-o', 'ro')
    try:
        if (home / 'retry-preserved.txt').read_text() != 'Already formatted home must survive retry.\n':
            raise RuntimeError('Retry lost existing home content')
    finally: run('umount', str(home))
    print('OXP_DEPLOY_TEST=journal-retry-roots-and-home-preserved', flush=True)

windows = Path('/mnt/fixture-check-windows')
for part in preserved_files(profile_name):
    node = '/dev/vdd%d' % part['number']
    filesystem = 'vfat' if part['fs'] == 'vfat' else 'ntfs-3g'
    mount(node, windows, '-t', filesystem, '-o', 'ro')
    try:
        if (windows / part['sentinel']).read_text() != part['content']:
            raise RuntimeError('Protected Windows fixture file changed: ' + part['name'])
    finally: run('umount', str(windows))
if preserved_files(profile_name):
    print('OXP_DEPLOY_TEST=windows-efi-ntfs-files-and-metadata-preserved', flush=True)
else:
    print('OXP_DEPLOY_TEST=external-blank-fixture-no-preserved-partitions', flush=True)

# A media source must not turn the installed system into the recovery preview.
source = Path('/mnt/fixture-check-source')
mount('/dev/vdc3', source, '-t', 'btrfs', '-o', 'ro,rescue=nologreplay')
try:
    from media_layer import inspect, OWNED, SELECTION
    media = inspect(source)
    media_choice = json.loads((source / SELECTION).read_text()) if media else None
finally: run('umount', str(source))
if media:
    for slot in ('A', 'B'):
        mount(nodes['rootfs-' + slot], root, '-t', 'btrfs', '-o', 'ro,rescue=nologreplay')
        try:
            if any((root / name).exists() or (root / name).is_symlink() for name in OWNED):
                raise RuntimeError('Copied system still contains the recovery preview layer')
            if run('btrfs', 'property', 'get', '-ts', str(root), 'ro', capture=True).strip() != 'ro=true':
                raise RuntimeError('Installed root is not protected')
            if not (root / 'etc/os-release').is_file(): raise RuntimeError('Stock system missing after layer removal')
        finally: run('umount', str(root))
    mount(nodes['home'], home, '-o', 'ro')
    try:
        for resource in media_choice.get('resources', []):
            profile = json.loads((home / 'oxp3-resources' / resource / 'profile.json').read_text())
            if not (home / 'oxp3-resources' / resource / profile['archive']).is_file():
                raise RuntimeError('Selected public resource was not carried into the installed home')
    finally: run('umount', str(home))
    print('OXP_DEPLOY_TEST=owned-media-detached-both-slots-and-resources-carried', flush=True)
