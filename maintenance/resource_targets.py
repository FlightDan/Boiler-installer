"""Fixed two-slot Linux maintenance target owner from installer-bound UUIDs."""
from pathlib import Path
import contextlib
import json
import os
import re
import stat
import subprocess
from .engine import Engine, _json_write
from .adoption_blobs import _directory
from .resource_runtime import ResourceError

BASE=Path('/run/boiler-resource-target')
ROLES={'rootfs-A':'btrfs','rootfs-B':'btrfs','var-A':'ext4','var-B':'ext4','home':'ext4','efi-A':'vfat','efi-B':'vfat'}


def command(*args):return subprocess.check_output(list(args),text=True).strip()
def run(*args):subprocess.run(list(args),check=True)


def _uuid(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}',value):raise ResourceError('Invalid installer-bound filesystem/partition UUID')
    return value.lower()


def probe(intent):
    expected=intent.get('target_roles')
    if not isinstance(expected,dict) or set(expected)!=set(ROLES):raise ResourceError('Installed resource intent lacks the complete A/B target binding')
    # Enumerate actual block devices once; PARTUUID alone must not silently pick
    # one of two cloned attached media. No path from a model/catalog is used.
    document=json.loads(command('lsblk','--json','--list','--paths','--output','PATH,TYPE,PKNAME,PARTUUID,UUID,FSTYPE,TRAN,MODEL'))
    rows=document.get('blockdevices',[]);result={};disks=set();seen=set()
    for role,kind in ROLES.items():
        old=expected[role]
        if old.get('filesystem')!=kind:raise ResourceError('Target role filesystem contract changed')
        partuuid=_uuid(old.get('partuuid'));fsuuid=old.get('uuid')
        if kind=='vfat':
            if not isinstance(fsuuid,str) or not re.fullmatch('[0-9a-fA-F]{4}-[0-9a-fA-F]{4}',fsuuid):raise ResourceError('Invalid bound FAT filesystem ID')
            fsuuid=fsuuid.lower()
        else:fsuuid=_uuid(fsuuid)
        matches=[r for r in rows if str(r.get('partuuid','')).lower()==partuuid]
        if len(matches)!=1:raise ResourceError('Target role is missing or duplicated among attached disks: '+role)
        row=matches[0];node=row.get('path');parent=row.get('pkname')
        if row.get('type')!='part' or row.get('fstype')!=kind or str(row.get('uuid','')).lower()!=fsuuid or not node or not parent:
            raise ResourceError('Actual target identity differs from installed intent: '+role)
        info=os.stat(node)
        if not stat.S_ISBLK(info.st_mode) or info.st_rdev in seen:raise ResourceError('Target roles are not unique block devices')
        seen.add(info.st_rdev);disks.add(parent)
        result[role]=dict(node=node,device=info.st_rdev,parent=parent,partuuid=partuuid,uuid=fsuuid,filesystem=kind)
    if len(disks)!=1:raise ResourceError('Resource target roles are not on one actual disk')
    disk=next(iter(disks)); diskrows=[r for r in rows if r.get('path')==disk and r.get('type')=='disk']
    if len(diskrows)!=1:raise ResourceError('Cannot uniquely identify the actual target disk')
    facts=dict(bus='NVMe' if diskrows[0].get('tran')=='nvme' else diskrows[0].get('tran'),model=(diskrows[0].get('model') or '').strip())
    for row in result.values():row['target_disk']=facts.copy()
    active=command('findmnt','-n','-o','PARTUUID','--target','/').lower()
    if active!=_uuid(intent['root_partuuid']) or active!=result['rootfs-'+intent['slot']]['partuuid']:
        raise ResourceError('Current root is not the installed resource target')
    for path,role in (('/var','var-'+intent['slot']),('/home','home')):
        if command('findmnt','-n','-o','UUID','--target',path).lower()!=result[role]['uuid']:raise ResourceError('Current mount does not match the bound target role: '+role)
    return result


def _check_vfat_mounts(node,*,run=None):
    # FAT permissions are superblock-wide. A second mount may silently inherit
    # an existing mount's masks, so refuse incompatible mounts before writing.
    result=(run or subprocess.run)(['findmnt','-rn','-o','OPTIONS','-S',str(node)],capture_output=True,text=True)
    if result.returncode not in (0,1):raise ResourceError('Cannot inspect existing EFI mounts')
    for line in result.stdout.splitlines():
        options=set(line.split(','))
        if not {'fmask=0133','dmask=0022'} <= options:
            raise ResourceError('Existing EFI mount has incompatible permission masks')


class TargetMounts:
    def __init__(self,intent,*,readonly,recover_window=False,home_only=False):
        if home_only and (readonly or recover_window):raise ResourceError('Invalid shared-home-only target mode')
        self.intent=intent;self.readonly=readonly;self.recover_window=recover_window;self.home_only=home_only;self.runtime=contextlib.ExitStack();self.bound=probe(intent);self.mounts=[];self.unprotected=[]
        self.roots={s:BASE/s for s in ('A','B')};self.home=BASE/'shared-home';self.opt_offload={}
        self.window=Path('/var/lib/boiler/resource-firstboot/target-window.json')
        with _directory(BASE,create=True,create_mode=0o700) as descriptor:
            if descriptor is not None:os.fchmod(descriptor,0o700)
    def revalidate(self):
        if probe(self.intent)!=self.bound:raise ResourceError('Maintenance target changed while its lease was held')
    def mount(self,source,target,*flags):
        with _directory(target,create=True,create_mode=0o755):pass
        # stat-based is_mount() misses bind mounts on the same filesystem.
        # A process interrupted before cleanup can leave precisely that view.
        existing=subprocess.run(['findmnt','-rn','--mountpoint',str(target)],capture_output=True,text=True)
        if existing.returncode==0:raise ResourceError('Maintenance mountpoint is already in use')
        if existing.returncode!=1:raise ResourceError('Cannot inspect maintenance mountpoint')
        run('mount',*flags,str(source),str(target));self.mounts.append(target)
    def bind(self,source,target):
        self.mount(source,target,'--bind')
        if self.readonly:run('mount','-o','remount,bind,ro',str(target))
    def _view(self,root,slot):
        if slot==self.intent['slot']:
            self.bind('/etc',root/'etc');self.bind('/opt',root/'opt')
            self.opt_offload[slot]=os.path.samestat((root/'opt').stat(),Path('/home/.steamos/offload/opt').stat()) if Path('/home/.steamos/offload/opt').is_dir() else False
            return
        unit=root/'usr/lib/systemd/system/etc.mount'
        if unit.is_file():
            text=unit.read_text().splitlines();base='Options=lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper,workdir=/var/lib/overlays/etc/work'
            if not all(v in text for v in ('Where=/etc','What=overlay','Type=overlay')) or len([v for v in text if v.startswith('Options=')])!=1 or next(v for v in text if v.startswith('Options=')) not in (base,base+',index=off'):raise ResourceError('Inactive slot has an unsupported etc overlay')
            for p in (unit.with_name('etc.mount.d'),root/'etc/systemd/system/etc.mount',root/'etc/systemd/system/etc.mount.d'):
                if p.exists() or p.is_symlink():raise ResourceError('Custom inactive etc overlay requires review')
            upper=root/'var/lib/overlays/etc/upper';work=root/'var/lib/overlays/etc/work'
            for p in (upper,work,root/'etc'):
                with _directory(p):pass
            options='lowerdir='+str(upper)+':'+str(root/'etc')+',ro' if self.readonly or self.home_only else 'lowerdir='+str(root/'etc')+',upperdir='+str(upper)+',workdir='+str(work)+',index=off'
            self.mount('overlay',root/'etc','-t','overlay','-o',options)
        unit=root/'usr/lib/systemd/system/opt.mount';self.opt_offload[slot]=False
        if unit.is_file():
            if not all(v in unit.read_text().splitlines() for v in ('What=/home/.steamos/offload/opt','Where=/opt','Options=bind')):raise ResourceError('Unsupported inactive opt mount')
            override=root/'etc/systemd/system/opt.mount'
            if override.is_symlink() and os.readlink(override)=='/dev/null':return
            if override.exists() or override.is_symlink() or unit.with_name('opt.mount.d').exists() or override.with_name('opt.mount.d').exists():raise ResourceError('Custom inactive opt mount requires review')
            self.bind(self.home/'.steamos/offload/opt',root/'opt');self.opt_offload[slot]=True
    def _inactive_clean(self,slot):
        for role in ('rootfs-'+slot,'var-'+slot):
            node=self.bound[role]['node']
            if subprocess.run(['findmnt','-rn','-S',node],capture_output=True).returncode==0:raise ResourceError('Inactive target role is mounted elsewhere: '+role)
            if not self.readonly:
                if role.startswith('var-'):
                    values={}
                    for line in command('tune2fs','-l',node).splitlines():
                        key,separator,value=line.partition(':')
                        if separator:values[key.strip()]=value.strip()
                    if values.get('Filesystem state')!='clean' or 'needs_recovery' in values.get('Filesystem features','').split():raise ResourceError('Inactive filesystem requires explicit recovery before resource maintenance')
                else:
                    text=command('btrfs','inspect-internal','dump-super',node)
                    if re.findall(r'^log_root\s+(\d+)\s*$',text,re.MULTILINE)!=['0']:raise ResourceError('Inactive Btrfs tree log requires explicit recovery')
    def pending_window(self):
        if not self.window.exists():return None
        from .resource_firstboot import read_private
        value=read_private(self.window)
        if value.get('phase')=='closed':return None
        inactive='B' if self.intent['slot']=='A' else 'A'
        if value.get('kind')!='BoilerResourceTargetWindow' or value.get('generation')!=self.intent['generation'] or value.get('roles')!=self.bound or value.get('roots')!=[str(self.roots[inactive])]:raise ResourceError('Interrupted target window ownership changed; preserve it for inspection')
        return value
    def open(self):
        self.revalidate()
        pending=self.pending_window()
        if pending and not self.readonly and not self.recover_window:raise ResourceError('An interrupted target window requires an explicit reviewed restore')
        self._inactive_clean('B' if self.intent['slot']=='A' else 'A')
        self.bind('/home',self.home)
        for slot,root in self.roots.items():
            role='rootfs-'+slot;current=slot==self.intent['slot'];node=self.bound[role]['node']
            if not current:
                mounted=command('findmnt','-rn','-S',node) if subprocess.run(['findmnt','-rn','-S',node],capture_output=True).returncode==0 else ''
                if mounted:raise ResourceError('Inactive root is already mounted elsewhere')
            self.mount(node,root,'-t','btrfs',*(['-o','ro,rescue=nologreplay'] if self.readonly or self.home_only else []))
            text=command('btrfs','subvolume','show',str(root))
            subvolumes=re.findall(r'^\s*UUID:\s*([0-9a-f-]{36})\s*$',text,re.MULTILINE)
            if subvolumes!=[self.intent['target_roles'][role].get('subvolume_uuid')]:raise ResourceError('Mounted Btrfs subvolume differs from installed target identity')
            protection=command('btrfs','property','get','-ts',str(root),'ro')
            if not current and protection!='ro=true' and not (pending and (self.readonly or self.recover_window)):raise ResourceError('Inactive root protection is not established')
            if not self.readonly and not self.home_only and not current:
                self.unprotected.append(root)
                _json_write(self.window,dict(kind='BoilerResourceTargetWindow',generation=self.intent['generation'],phase='unprotecting',roots=[str(p) for p in self.unprotected],roles=self.bound))
                run('btrfs','property','set','-ts',str(root),'ro','false')
            if current:self.bind('/var',root/'var')
            else:
                node=self.bound['var-'+slot]['node']
                self.mount(node,root/'var','-t','ext4',*(['-o','ro,noload'] if self.readonly else []))
            if self.intent.get('mount_efi') and not self.home_only:
                efi=self.bound['efi-'+slot]['node']
                _check_vfat_mounts(efi)
                self.mount(efi,root/'efi','-t','vfat','-o',('ro,' if self.readonly else '')+'fmask=0133,dmask=0022')
                _check_vfat_mounts(efi)
            self.bind(self.home,root/'home');self._view(root,slot)
        if len(set(self.opt_offload.values()))!=1:raise ResourceError('A/B opt offload ownership differs')
        self.revalidate();return self
    def close(self):
        errors=[]
        for root in reversed(self.unprotected):
            try:run('btrfs','property','set','-ts',str(root),'ro','true')
            except Exception as error:errors.append(str(error))
        for path in reversed(self.mounts):
            try:run('umount',str(path))
            except Exception as error:errors.append(str(error))
        self.runtime.close()
        if errors:raise ResourceError('Maintenance target cleanup incomplete; preserve target-window: '+'; '.join(errors))
        if self.unprotected:_json_write(self.window,dict(kind='BoilerResourceTargetWindow',generation=self.intent['generation'],phase='closed',roles=self.bound))
    def __enter__(self):
        try:return self.open()
        except BaseException as original:
            try:self.close()
            except Exception as cleanup:raise cleanup from original
            raise
    def __exit__(self,*args):self.close()

