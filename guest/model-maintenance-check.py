"""Software lifecycle on an owned QEMU image using the installed Linux runtime."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

sys.path.insert(0, '/mnt/payload/components')
from offload_mounts import mount_etc, mount_opt

def command(*args):
    return subprocess.check_output(args, text=True).strip()

if Path('/sys/class/dmi/id/sys_vendor').read_text().strip() != 'QEMU':
    raise RuntimeError('Managed QEMU software test required')
if Path('/sys/class/block/vdc/ro').read_text().strip() != '0':
    raise RuntimeError('Disposable prepared image must be writable')
if Path('/run/boiler-guest-context').exists():
    raise RuntimeError('The Linux maintenance test must not fabricate installer guest authority')
# The pinned model targets SteamOS's merged /usr paths. Alpine's real tools
# may live in /bin or /sbin; expose those same binaries in the test VM only.
for name in ('findmnt','lsblk','mount','umount','blkid','btrfs'):
    executable=shutil.which(name)
    if executable is None:
        raise RuntimeError('Missing offline model probe tool: '+name)
    canonical=Path('/usr/bin')/name
    if not canonical.exists():
        canonical.symlink_to(executable)
    if canonical.resolve()!=Path(executable).resolve():
        raise RuntimeError('Conflicting offline model probe tool: '+name)
table = json.loads(command('sfdisk', '--json', '/dev/vdc'))['partitiontable']
parts = {row['name']: row['node'] for row in table['partitions']}
if not {'rootfs-A','rootfs-B','var-A','var-B','efi-A','efi-B','home'} <= parts.keys():
    raise RuntimeError('Expected an owned prepared A/B image')
probe_env=dict(os.environ,PATH='/usr/bin:/usr/sbin',LANG='C',LC_ALL='C')
if subprocess.check_output(['blkid','-s','TYPE','-o','value',parts['rootfs-A']],
                           env=probe_env,text=True).strip()!='btrfs':
    raise RuntimeError('Fixed-PATH blkid selectors must probe the actual root filesystem')
superblock=subprocess.check_output(['btrfs','inspect-internal','dump-super',parts['rootfs-B']],
                                  env=probe_env,text=True)
if not re.search(r'^num_devices\s+1\s*$',superblock,re.MULTILINE):
    raise RuntimeError('Fixed-PATH btrfs must read the actual inactive superblock')
print('BOILER_LINUX_MODEL_STEP=real-fixed-path-probe-tools-ready',flush=True)
# Alpine's minimal device manager need not populate udev's UUID aliases.
# Build aliases only from this real image's GPT, checking existing bindings.
for row in table['partitions']:
    identifier=row.get('uuid','').lower()
    node=row['node']
    if not re.fullmatch('[0-9a-f-]{36}',identifier) or not re.fullmatch('/dev/vdc[0-9]+',node):
        raise RuntimeError('Invalid fixture partition identity')
    alias=Path('/dev/disk/by-partuuid')/identifier
    alias.parent.mkdir(parents=True,exist_ok=True)
    if os.path.lexists(alias):
        if alias.resolve()!=Path(node).resolve():
            raise RuntimeError('Conflicting fixture partition alias')
    else:
        alias.symlink_to(node)
root, inactive = Path('/mnt/linux-model-root'), Path('/mnt/linux-model-inactive')
mounts = []

def mount(node, path, *flags):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(['mount', *flags, node, str(path)], check=True)
    mounts.append(path)

def fingerprint(path):
    if not path.exists():
        return None
    info = path.stat()
    return dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), mode=stat.S_IMODE(info.st_mode),
                uid=info.st_uid, gid=info.st_gid)

try:
    mount(parts['rootfs-A'], root, '-t', 'btrfs')
    if command('btrfs','property','get','-ts',str(root),'ro') != 'ro=true':
        raise RuntimeError('Expected protected source image root')
    subprocess.run(['btrfs','property','set','-ts',str(root),'ro','false'], check=True)
    mount(parts['var-A'], root/'var'); mount(parts['home'], root/'home')
    mount_etc(root, mounts); mount_opt(root, mounts)
    mount(parts['efi-A'], root/'efi', '-t', 'vfat', '-o', 'ro')
    mount(parts['rootfs-B'], inactive, '-t', 'btrfs', '-o', 'ro,rescue=nologreplay')
    mount(parts['var-B'], inactive/'var', '-t', 'ext4', '-o', 'ro,noload')
    mount_etc(inactive, mounts, readonly=True)
    mount(parts['efi-B'], inactive/'efi', '-t', 'vfat', '-o', 'ro')
    boot_paths=[path/'efi/EFI/steamos/grub.cfg' for path in (root,inactive)]
    boot_before={str(path):fingerprint(path) for path in boot_paths}
    assert all(boot_before.values())
    runtime = root/'usr/local/lib/boiler'
    sys.path.insert(0, str(runtime))
    from maintenance import models
    # Import only the on-disk installed runtime, never the transport's source.
    if Path(models.__file__).resolve().parent.parent != runtime.resolve():
        raise RuntimeError('Wrong Linux maintenance runtime was imported')
    catalog = models.catalog(runtime_root=runtime, online=False)
    assert {row['id'] for row in catalog['models']} == {'device:oxp3','device:x2-mini'}
    old_paths = [root/'home/.oxp3-installer/integration/A/request.json',
                 root/'home/.oxp3-installer/integration/A/result.json', root/'home/.boiler/oxp3-adapter/A.json']
    old_evidence = {str(path):fingerprint(path) for path in old_paths}
    record = json.loads((root/'home/.boiler/oxp3-adapter/B.json').read_text())
    inactive_paths = [item['path'] for item in record['verification']['files']
                      if not item['path'].startswith(('home/','opt/','efi/'))]
    inactive_before = {name:fingerprint(inactive/name) for name in inactive_paths}
    view = models.options(root, 'device:oxp3', trust_local_test=True, runtime_root=runtime)
    print('BOILER_LINUX_MODEL_STEP=installed-options-verified',flush=True)
    assert view['current_source'] == 'legacy-installer'
    capabilities = {row['id']:row for row in view['capabilities']}
    repairs = ['oxp3:'+name for name in ('input','multifunction','gyro','xe-dsb')]
    for identity in repairs:
        if not capabilities[identity]['available']:
            raise RuntimeError(identity + ': ' + capabilities[identity]['reason'])
    assert not capabilities['oxp3:split-lock']['available']
    assert not models.options(root,'device:x2-mini',trust_local_test=True,runtime_root=runtime)['executable']
    steps, receipts = [], []
    def action(name):
        print('BOILER_LINUX_MODEL_STEP=begin-'+name,flush=True)
        plan = models.plan_model(root,'device:oxp3',repairs,[],action=name,trust_local_test=True,
                                 acknowledge_update_hold=True,runtime_root=runtime)
        receipt = models.execute_model(root,plan,reviewed_sha256=plan['review_sha256'],
                                       trust_local_test=True,runtime_root=runtime)
        receipts.append(dict(action=name,status=receipt['status'],receipt=receipt['receipt_path']))
        print('BOILER_LINUX_MODEL_STEP=end-'+name+':'+receipt['status'],flush=True)
        return receipt
    assert action('apply')['status'] == 'verified'
    steps.append('installer-original-adoption-and-supported-defaults-apply')
    changed = root/'etc/modprobe.d/xe-oxp3.conf'
    saved = changed.read_bytes()
    try:
        changed.write_bytes(b'# owned image QA: altered model file\n')
        failed = action('verify')
        assert failed['status']=='verification-failed' and failed['result']['verified'] is False
        assert 'etc/modprobe.d/xe-oxp3.conf' in failed['result']['modified']
    finally:
        changed.write_bytes(saved)
    steps.append('modified-file-is-verification-failure')
    bundle = runtime/'cache/model-bundles/packages/oxp3'
    hidden = bundle.with_name('oxp3.qa-hidden')
    if hidden.exists():
        raise RuntimeError('Unexpected QA cache backup exists')
    bundle.rename(hidden)
    try:
        assert action('restore')['result']['status']=='restored'
        assert action('apply')['status']=='verified'
        assert action('verify')['result']['verified'] is True
    finally:
        hidden.rename(bundle)
    steps.extend(['retained-package-without-original-cache','restore-and-reinstall-from-true-originals','installed-model-files-verify'])
    assert old_evidence == {str(path):fingerprint(path) for path in old_paths}
    assert inactive_before == {name:fingerprint(inactive/name) for name in inactive_paths}
    assert boot_before == {str(path):fingerprint(path) for path in boot_paths}
    assert not Path('/run/boiler-guest-context').exists()
    desktop = root/'usr/share/applications/Boiler-Maintenance.desktop'
    assert 'Exec=/usr/local/bin/boiler-maintenance-ui' in desktop.read_text()
    assert (root/'usr/local/bin/boiler-maintenance-ui').stat().st_mode & 0o111
    result = dict(passed=True,source=catalog['source'],package=view['package'],target=view['target'],
                  steps=steps,receipts=receipts,repairs=repairs,inactive_slot_files_unchanged=len(inactive_paths),
                  installer_journals_unchanged=True,no_guest_marker=True,
                  efi_readonly_and_unchanged=True,
                  kdialog_present=os.path.lexists(root/'usr/bin/kdialog'),desktop_entry=True,
                  scope='Owned mounted QEMU image; no live-root remount, KDE visual or physical-device acceptance')
    print('BOILER_LINUX_MODEL_CHECK='+json.dumps(result),flush=True)
finally:
    if root in mounts:
        subprocess.run(['btrfs','property','set','-ts',str(root),'ro','true'],check=True)
    for path in reversed(mounts):
        subprocess.run(['umount',str(path)],check=True)
