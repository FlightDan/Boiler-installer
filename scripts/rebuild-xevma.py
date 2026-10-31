# SPDX-License-Identifier: AGPL-3.0-only
"""Rebuild the locked xe workaround in a disposable offline QEMU Linux VM.

No download, installation, driver load or host-project discovery is performed.
Supply the public sources, exact package cache and a fresh output directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess


def sha(raw): return hashlib.sha256(raw).hexdigest()


def ordinary(path):
    path = Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Inputs and output must not traverse symbolic links')
    return path


def read_checked(path, row):
    path=ordinary(path); info=path.stat()
    if not stat.S_ISREG(info.st_mode): raise ValueError('Input is not a regular file: '+str(path))
    raw=path.read_bytes()
    if len(raw)!=row['size'] or sha(raw)!=row['sha256']: raise ValueError('Locked input differs: '+str(path))
    if row.get('git_blob') and hashlib.sha1(b'blob '+str(len(raw)).encode()+bytes([0])+raw).hexdigest()!=row['git_blob']:
        raise ValueError('Source Git blob differs: '+str(path))
    return raw


def recipe(path):
    raw=ordinary(path).read_bytes(); value=json.loads(raw)
    if (value.get('schema_version')!=1 or value.get('kind')!='XevmaRebuildInputs'
            or value.get('kernel')!='7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
            or value.get('source',{}).get('commit')!='e72e814da1944da3792076689995b6fe961f5768'):
        raise ValueError('Unexpected fixed rebuild recipe')
    if len(value['sources'])!=4 or {r['local_file'] for r in value['sources']}!={'xevmafix.c','Makefile','LICENSE','UPSTREAM_LICENSE'}:
        raise ValueError('Expected exactly the source, Makefile and both original license notices')
    if len(value['packages'])!=60 or len({r['local_file'] for r in value['packages']})!=60:
        raise ValueError('Expected exact headers plus 59 toolchain packages')
    for row in [*value['sources'],*value['packages']]:
        name=row['local_file']
        if Path(name).name!=name or not row['url'].startswith('https://') or len(row['sha256'])!=64:
            raise ValueError('Unsafe or incomplete input identity')
    if set(value['build_environment'])!={'KBUILD_BUILD_TIMESTAMP','KBUILD_BUILD_USER','KBUILD_BUILD_HOST'}:
        raise ValueError('Unexpected build environment')
    return value,raw


def validate(args):
    value,raw=recipe(args.recipe)
    sources={row['local_file']:read_checked(args.source_dir/row['local_file'],row) for row in value['sources']}
    for row in value['packages']: read_checked(args.package_dir/row['local_file'],row)
    return value,raw,sources


def build(args):
    value,raw,sources=validate(args)
    if os.name!='posix' or os.geteuid()!=0 or not args.disposable_offline_vm:
        raise ValueError('Build only as root in an explicitly acknowledged disposable offline QEMU Linux VM')
    if Path('/sys/class/dmi/id/sys_vendor').read_text().strip()!='QEMU': raise ValueError('A disposable QEMU VM is required')
    if {p.name for p in Path('/sys/class/net').iterdir()}!={'lo'}: raise ValueError('The build VM must have no network interface')
    output=ordinary(args.output)
    if output.exists() or any(output.is_relative_to(ordinary(p)) for p in (args.source_dir,args.package_dir)):
        raise ValueError('Use a fresh output outside the input trees')
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    packages=output/'verified-packages'; packages.mkdir(mode=0o700)
    # Snapshot the exact checked bytes; never copy an old object or module.
    for row in value['packages']:
        (packages/row['local_file']).write_bytes(read_checked(args.package_dir/row['local_file'],row))
    arch=output/'arch'
    for name in ('usr','dev','proc','tmp','w'): (arch/name).mkdir(parents=True,exist_ok=True)
    for name,target in (('bin','usr/bin'),('sbin','usr/bin'),('lib','usr/lib'),('lib64','usr/lib')): (arch/name).symlink_to(target)
    for row in value['packages']:
        subprocess.run(['tar','--zstd','-xf',str(packages/row['local_file']),'-C',str(arch)],check=True)
    for name in ('xevmafix.c','Makefile'): (arch/'w'/name).write_bytes(sources[name])
    if list((arch/'w').glob('*.o')) or (arch/'w/xevmafix.ko').exists(): raise ValueError('Fresh source build directory contains an old object/module')
    mounted=[]
    environment=dict(PATH='/usr/bin:/bin:/usr/sbin:/sbin',LC_ALL='C',HOME='/root',**value['build_environment'])
    try:
        for command,target in ((['mount','--bind','/dev',str(arch/'dev')],arch/'dev'),
                               (['mount','-t','proc','proc',str(arch/'proc')],arch/'proc')):
            subprocess.run(command,check=True);mounted.append(target)
        with (output/'build.log').open('wb') as log:
            for command in (["chroot",str(arch),'/usr/bin/gcc','--version'],
                            ['chroot',str(arch),'/usr/bin/make','-C','/usr/lib/modules/'+value['kernel']+'/build',
                             'M=/w','CONFIG_DEBUG_INFO_BTF_MODULES=','modules']):
                subprocess.run(command,env=environment,stdout=log,stderr=subprocess.STDOUT,check=True)
    finally:
        failed=[]
        for target in reversed(mounted):
            if subprocess.run(['umount',str(target)]).returncode: failed.append(str(target))
        if failed: raise RuntimeError('Preserve output: mount cleanup incomplete: '+', '.join(failed))
    artifact=(arch/'w/xevmafix.ko').read_bytes(); expected=value['expected_module']
    proof=dict(schema_version=1,kind='XevmaSourceBuildResult',fresh_source_build=True,old_module_input=False,
        recipe_sha256=sha(raw),source_sha256=sha(sources['xevmafix.c']),size=len(artifact),sha256=sha(artifact),
        elf64=artifact[:5]==b'\x7fELF\x02',machine=int.from_bytes(artifact[18:20],'little'),
        vermagic_matches=('vermagic='+expected['vermagic']).encode() in artifact,license_gpl=b'license=GPL\0' in artifact,
        module_loaded=False,hardware_accepted=False)
    proof['passed']=(proof['size']==expected['size'] and proof['sha256']==expected['sha256'] and proof['elf64']
                     and proof['machine']==62 and proof['vermagic_matches'] and proof['license_gpl'])
    (output/'rebuilt-xevmafix.ko').write_bytes(artifact)
    (output/'build-result.json').write_text(json.dumps(proof,indent=2)+'\n')
    if not proof['passed']: raise ValueError('Fresh build differs from the locked module; retain the output for diagnosis')
    return proof


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('recipe','source-dir','package-dir'): parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--output',type=Path);parser.add_argument('--verify-only',action='store_true')
    parser.add_argument('--disposable-offline-vm',action='store_true');args=parser.parse_args()
    if args.verify_only:
        value,raw,_=validate(args)
        result=dict(verified=True,recipe_sha256=sha(raw),sources=len(value['sources']),packages=len(value['packages']),module_loaded=False)
    else:
        if args.output is None:parser.error('--output is required for a source build')
        result=build(args)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
