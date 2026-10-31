"""Fixed Opti2XMX 0.3.1 resource renderer; no user-supplied build hooks."""
import difflib, hashlib, json, os, shutil, subprocess, tarfile, tempfile, zipfile
from pathlib import Path, PurePosixPath
from protocol.resource_pool import require
from .opti_inputs import acquire

SPECS=(('optiscaler094','0.9.4','v0.9.4','D3D12_Optiscaler/D3D12Core.dll'),('optiscaler079','0.7.9','v0.7.9','D3D12_Optiscaler/D3D12Core.dll'),('optiscaler100pre','10.0.0-pre1_20261025','nightly-20261025','OptiScaler/D3D12_OptiScaler/D3D12Core.dll'))
AGILITY='07d286c306f8117321422affd9e6388c12d0fb4be1c7fc689d9e899324feeb24'

def render(source_root, env, toon, read, write, *, input_store=None, seven_zip=None):
    require(env['id']=='resource:common-opti2xmx' and env['install_kind']=='files-v1','Invalid Opti identity')
    expected={'plugin':'common/plugins/opti2xmx/plugin','frontend':'common/plugins/opti2xmx/frontend','input_recipe':'common/plugins/opti2xmx/input-recipe.toon','patches':'common/plugins/opti2xmx/patches.toon','license_files':['LICENSE','common/plugins/opti2xmx/plugin/LICENSE','common/plugins/opti2xmx/plugin/THIRD_PARTY_NOTICES.md']}
    require(env['recipe']==expected,'Opti fixed recipe changed')
    recipe=Path(source_root)/expected['input_recipe']
    cache=Path(input_store) if input_store else Path(tempfile.gettempdir())/'boiler-opti-inputs'
    inputs=acquire(recipe,cache)
    executable=seven_zip or shutil.which('7z') or shutil.which('7zz')
    if not executable:
        candidate=Path(__file__).resolve().parents[2]/'runtime/archive/7z.exe'
        if candidate.is_file():executable=str(candidate)
    require(bool(executable),'Opti cache derivation requires the SDK 7z executable')
    archive_spec=toon.loads(read(expected['input_recipe']).decode('utf-8'),strict=True)
    pins={x['id']:x for x in archive_spec['inputs']};payload=[];install=[];license_paths=[];members=set();targets=set();blobs={}
    patchdoc=toon.loads(read(expected['patches']).decode('utf-8'),strict=True)
    require(patchdoc.get('kind')=='Opti031ProjectPatches' and patchdoc.get('upstream_release_sha256')==pins['plugin']['sha256'],'Opti project patch provenance differs')
    patches={row['path']:row for row in patchdoc['files']}
    require(set(patches)=={'main.py','manager.py','opti.py','per_game.py','fsr.py','dist/index.js'},'Opti patch set differs')
    source_members={'game_plan.py'}
    with zipfile.ZipFile(inputs['plugin']) as upstream:
        for entry in upstream.infolist():
            if entry.is_dir():continue
            require(entry.filename.startswith('opti2xmx/'),'Unexpected public plugin archive member')
            name=entry.filename.removeprefix('opti2xmx/');require(name and '..' not in PurePosixPath(name).parts,'Unsafe public plugin archive member')
            old=upstream.read(entry);path=expected['plugin']+'/'+(name[:-5]+'.toon' if name.endswith('.json') else name);new=read(path)
            source_members.add(name[:-5]+'.toon' if name.endswith('.json') else name)
            if name in patches:
                row=patches[name];delta=''.join(difflib.unified_diff(old.decode('utf-8').splitlines(keepends=True),new.decode('utf-8').splitlines(keepends=True),fromfile='upstream/'+name,tofile='boiler/'+name))
                require(row['upstream_sha256']==hashlib.sha256(old).hexdigest() and row['output_sha256']==hashlib.sha256(new).hexdigest() and row['unified_diff']==delta,'Opti project patch differs: '+name)
            elif name.endswith('.json'):
                require(json.loads(old)==toon.loads(new.decode('utf-8'),strict=True),'Opti public runtime config differs: '+name)
            else:require(old==new,'Opti unmodified public member differs: '+name)
    actual={p.relative_to(Path(source_root)/expected['plugin']).as_posix() for p in (Path(source_root)/expected['plugin']).rglob('*') if p.is_file()}
    require(actual==source_members,'Unexpected Opti plugin source member')
    def add(relative,target,data,mode,scope,license=False):
        require(relative not in members and (scope,target) not in targets,'Opti output collision');members.add(relative);targets.add((scope,target))
        digest=hashlib.sha256(data).hexdigest();source='payload/opti2xmx/'+digest
        if digest not in blobs:
            write(source,data);payload.append(dict(path=source,size=len(data),sha256=digest));blobs[digest]=len(data)
        else:require(blobs[digest]==len(data),'Opti content identity differs')
        install.append(dict(source=source,target=target,mode=mode,scope=scope))
        if license and source not in license_paths:license_paths.append(source)
    prefix='common/plugins/opti2xmx/files/'
    plugin=Path(source_root)/expected['plugin']
    for path in sorted(plugin.rglob('*')):
        if not path.is_file():continue
        relative=path.relative_to(plugin).as_posix();source_name=expected['plugin']+'/'+relative;data=read(source_name)
        if relative.endswith('.toon'):
            relative=relative[:-5]+'.json';data=(json.dumps(toon.loads(data.decode('utf-8'),strict=True),ensure_ascii=False,indent=2)+'\n').encode('utf-8')
        add(prefix+'plugin/'+relative,'var/lib/boiler/decky/runtime/plugins/Opti2XMX/'+relative,data,493 if relative.endswith('.sh') else 420,'root-slots',relative=='LICENSE' or 'license' in relative.lower())
    with tempfile.TemporaryDirectory(prefix='boiler-opti-render-') as scratch:
        base=Path(scratch)
        with tarfile.open(inputs['runtime'],'r:gz') as archive:
            seen=set();expanded=0
            for member in archive:
                p=PurePosixPath(member.name)
                require(bool(p.parts) and not p.is_absolute() and '..' not in p.parts and ':' not in member.name and '\\' not in member.name and p.parts[0] in ('runtime','sdk') and member.name not in seen and (member.isdir() or member.isreg()),'Unsafe Opti runtime input')
                seen.add(member.name);expanded+=member.size;require(expanded<=2*1024*1024*1024,'Opti runtime expands beyond limit')
                if member.isdir():continue
                data=archive.extractfile(member).read();require(len(data)==member.size,'Truncated Opti runtime input')
                add(prefix+'runtime/'+member.name,'home/deck/.local/share/opti2xmx/'+member.name,data,493 if member.mode&0o111 else 420,'shared-home','license' in member.name.lower())
        for identity,version,tag,excluded in SPECS:
            target=base/version;target.mkdir()
            result=subprocess.run([str(executable),'x','-y','-o'+str(target),'-x!'+excluded,str(inputs[identity])],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=900)
            require(result.returncode==0,'Fixed Opti release extraction failed')
            rows=[]
            for path in sorted(target.rglob('*')):
                require(not path.is_symlink(),'Linked Opti release member')
                if not path.is_file():continue
                name=path.relative_to(target).as_posix();require(name.lower() != excluded.lower() and path.name.lower()!='d3d12core.dll','Excluded Microsoft DLL in derived cache')
                data=path.read_bytes();rows.append(dict(path=name,size=len(data),sha256=hashlib.sha256(data).hexdigest()))
                add(prefix+'cache/'+tag+'/'+name,'home/deck/.local/share/opti2xmx/downloads/'+tag+'/payload/'+name,data,493 if name.endswith('.sh') else 420,'shared-home','license' in name.lower())
            require(any(x['path']=='Licenses/DirectX_LICENSE.txt' for x in rows),'Missing Opti DirectX license')
            ready=(json.dumps(dict(version=version,source='boiler-derived',source_sha256=pins[identity]['sha256'],files=rows),sort_keys=True)+'\n').encode()
            add(prefix+'cache/'+tag+'/ready.json','home/deck/.local/share/opti2xmx/downloads/'+tag+'/payload/ready.json',ready,420,'shared-home')
    return dict(schema_version=1,kind='BoilerResource',id=env['id'],version=env['version'],status='public-source-candidate',dependencies=env['dependencies'],external_component_dependencies=env['external_component_dependencies'],hardware_requires=env['hardware_requires'],compatibility=env['compatibility'],payload=dict(files=payload),install=dict(kind='files-v1',files=install),verify=env['verify'],restore=env['restore'],license_files=['licenses/LICENSE',*license_paths],provenance=dict(source_declaration='provenance/source/common/plugins/opti2xmx/resource.toon',source_declaration_sha256=hashlib.sha256(read('common/plugins/opti2xmx/resource.toon')).hexdigest()),acceptance=dict(source_hashes=True,build_chain_reproduced=False,file_lifecycle_tested=False,hardware_verified=False),limitations=['DirectX Agility component is acquired from the pinned upstream archive only on explicit game request.','User game and actual hardware acceptance remain required.'])
