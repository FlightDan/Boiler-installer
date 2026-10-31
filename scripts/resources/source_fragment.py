"""Compile the initial declarative files-v1 sources into an unsigned fragment.

This is a narrow, incomplete migration checkpoint, not the final pool compiler.
"""
from pathlib import Path
import difflib
import json
import io
import importlib.util
import re
import tempfile
import tarfile
import zipfile
from protocol.resource_pool import decoder, local, sha, require, validate_pool

FIRMWARE_SHA256={
 '000-iwlwifi-sc-a0-fm-c0-c101.ucode':'3475bdb1f59d15707d5018144484d370a3b1f4868d5f4e6f38ef83a5004704c2',
 '001-iwlwifi-sc-a0-fm-c0-c102.ucode':'4efdd46b024b6fa5aaae8f9b921fc10d6cde04e035fa6d5a93c2f419ebfbdeec',
 '002-iwlwifi-sc-a0-fm-c0-c103.ucode':'cce159597d0b8915e3d1166a8680c00a3c12133c4c2ec577d7f09f60636edd9c',
 '003-iwlwifi-sc-a0-fm-c0-c106.ucode':'7a4b428edf7dfc5ec1573bc1b8f98804b52e3a469aeec8187cae9aae66b583fd',
 '004-ibt-00a0-0291-iml.sfi':'a9401eeea677c80fb027d316e4530e3a839463967b74ab3e610921ba1b215903',
 '005-ibt-00a0-0291-pci.ddc':'e0fb10fe29f60607d6507ced3f697492d9611f62c4765feaee59c4bd4101d3e7',
 '006-ibt-00a0-0291-pci.sfi':'6d417061ab9890658e33e1b7015a05a40d0fae0df7a4a6ab652a5bb6936b48cd',
 '007-ibt-00a0-0291-usb.ddc':'e0fb10fe29f60607d6507ced3f697492d9611f62c4765feaee59c4bd4101d3e7',
 '008-ibt-00a0-0291-usb.sfi':'b0987a2b366dce2812e53ff0f3e04e48368424da90e16a2618d87b4127fec778',
}

def build(source_root, output, *, sdk_identity=None, desktop_archive=None, input_store=None, seven_zip=None):
    source_root, output = Path(source_root), Path(output)
    for path in (source_root.absolute(),*source_root.absolute().parents):
        require(not path.is_symlink() and not getattr(path,'is_junction',lambda:False)(),'Linked source root or ancestor')
    require(not output.exists(), 'Use a fresh output directory')
    toon = decoder()
    source_reads={}
    def read(name):
        from protocol.manifest import _filesystem_path
        data=_filesystem_path(local(source_root, name)).read_bytes();source_reads[name]=dict(path=name,size=len(data),sha256=sha(data));return data
    from .source_envelope import validate as validate_envelope,legacy
    def doc(name):
        value=toon.loads(read(name).decode('utf-8'), strict=True)
        return legacy(value) if value.get('kind')=='BoilerResourceSource' and 'renderer' in value else value
    repository = doc('repository.toon')
    require(repository.get('kind') == 'BoilerAdaptationsSourceRepository', 'Invalid source repository')
    require(set(repository)=={'schema_version','kind','name','state','configuration_format','complete','publication_ready','baseline','models','sdk','resource_index','export_inventory'},'Invalid source repository fields')
    require(repository['export_inventory']=='locks/base-resource-sources.toon','Invalid export inventory')
    index=repository['resource_index'];require(isinstance(index,list) and index,'Invalid unified resource index')
    envelopes={};lock_members={};names_seen=set();locks_seen=set()
    for row in index:
        require(set(row)=={'id','declaration','source_lock'} and row['id'] not in envelopes and row['declaration'] not in names_seen and row['source_lock'] not in locks_seen,'Duplicate or invalid resource index entry')
        env=validate_envelope(toon.loads(read(row['declaration']).decode('utf-8'),strict=True))
        require(env['id']==row['id'] and env['source_lock']==row['source_lock'],'Source index identity differs')
        pin=doc(row['source_lock']);require(set(pin)=={'schema_version','kind','resource','files'} and type(pin['schema_version']) is int and pin['schema_version']==1 and pin['kind']=='BoilerResourceSourceLock' and pin['resource']==row['id'],'Invalid per-resource source lock')
        members=set()
        for item in pin['files']:
            require(set(item)=={'path','size','sha256'} and item['path'] not in members,'Invalid resource source lock entry')
            raw=read(item['path']);require(len(raw)==item['size'] and sha(raw)==item['sha256'],'Resource source lock mismatch: '+item['path']);members.add(item['path'])
        require(row['declaration'] in members and 'LICENSE' in members and 'THIRD_PARTY_NOTICES.md' in members,'Resource source lock lacks declaration or notices')
        envelopes[row['id']]=env;lock_members[row['id']]=members;names_seen.add(row['declaration']);locks_seen.add(row['source_lock'])
    by_renderer={}
    for row in index:by_renderer.setdefault(envelopes[row['id']]['renderer'],[]).append(row['declaration'])
    for key,renderer in [('resources','files-v1'),('firmware_sources','firmware-v1'),('hdr_sources','gamma22-shared-v1'),('cpu_sources','fixed-public-cpu-v1'),('boot_sources','fixed-boot-settings-v1')]:repository[key]=by_renderer.get(renderer,[])
    repository['cpu_sources']+=by_renderer.get('fixed-public-cpu-live-v1',[])
    repository['desktop_hdr_sources']=by_renderer.get('fixed-desktop-hdr-v1',[])
    repository['resource_sources_lock']=repository['export_inventory']
    input_names=by_renderer.get('fixed-input-r5-v1',[]);require(len(input_names)==2 and {envelopes[i]['id'] for i in envelopes if envelopes[i]['renderer']=='fixed-input-r5-v1'}=={'resource:onexplayer-input','resource:bosch-bmi260-gyro'},'Input resource declarations differ')
    require(envelopes['resource:onexplayer-input']['recipe']==envelopes['resource:bosch-bmi260-gyro']['recipe'],'Shared input recipe differs')
    repository['input_sources']=[next(r['declaration'] for r in index if r['id']=='resource:onexplayer-input')]
    repository['rapl_source']=next((r['declaration'] for r in index if r['id']=='resource:common-intel-rapl'),None)
    lock = doc(repository['resource_sources_lock'])
    require(lock.get('kind') == 'BoilerBaseResourceSources', 'Invalid resource source lock')
    baseline = doc('locks/public-baseline.toon')
    require(baseline['source']['commit'] == '72f864e0498c10366493c74608c5ffed62cc99ca', 'Invalid baseline')
    require(sha(read('LICENSE')) == '0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0', 'Project license mismatch')
    locked = {}
    for row in lock['files'] + baseline['files']:
        name = row['path']; require(name not in locked, 'Duplicate source lock member')
        raw = read(name)
        require(len(raw) == row['size'] and sha(raw) == row['sha256'], 'Source lock mismatch: '+name)
        locked[name] = row
    imported_names=set()
    for index_name in ('locks/additional-sources.toon','locks/rapl-sources.toon'):
        imported=doc(index_name)
        for row in imported['files']:
            raw=read(row['path']);require(len(raw)==row['size'] and sha(raw)==row['sha256'],'Additional source lock mismatch: '+row['path'])
            require(row['path'] not in imported_names and row['path'] not in locked,'Source lock collision');imported_names.add(row['path'])
    for name in repository['models']:read(name)
    parser_lock_raw=read('locks/toon-parser.toon')
    require(sha(parser_lock_raw)=='22afc358ad90c2ef1eed6e846cd45db1c1dfc59c52f33e318b8a13c37de921a5','Source parser lock differs')
    parser_lock=toon.loads(parser_lock_raw.decode('utf-8'),strict=True);vendor=set()
    for row in parser_lock['files']:
        name='vendor/'+row['path'];raw=read(name);require(len(raw)==row['size'] and sha(raw)==row['sha256'],'Source parser member differs');vendor.add(name)
    owned={'.gitignore','LICENSE','README.md','THIRD_PARTY_NOTICES.md','repository.toon','locks/public-baseline.toon','locks/toon-parser.toon','tools/check.py','tools/materialize_baseline.py','tests/test_standalone.py',*repository['models']}
    allowed=owned|set(locked)|imported_names|vendor|{'locks/base-resource-sources.toon','locks/additional-sources.toon','locks/rapl-sources.toon'}
    from protocol.manifest import _filesystem_path
    source_io=_filesystem_path(source_root);actual=set()
    for item in source_io.rglob('*'):
        rel=item.relative_to(source_io)
        if '.git' in rel.parts or '__pycache__' in rel.parts:continue
        require(not item.is_symlink() and not getattr(item,'is_junction',lambda:False)(),'Linked source member')
        if item.is_file():actual.add(rel.as_posix())
    require(actual==allowed,'Source export differs from explicit inventory')
    discovered=set()
    old_kinds={'BoilerResource','BoilerAdditionalResourceSource','BoilerFirmwareSource','BoilerHdrSource','BoilerPublicCpuSource','BoilerBootSource','BoilerInputSource'}
    for name in actual:
        if not name.endswith('.toon'):continue
        raw=read(name)
        if b'BoilerResourceSource' not in raw and not any(k.encode() in raw for k in old_kinds):continue
        value=toon.loads(raw.decode('utf-8'),strict=True)
        if isinstance(value,dict) and value.get('kind')=='BoilerResourceSource':discovered.add(name)
        require(not isinstance(value,dict) or value.get('kind') not in old_kinds,'Legacy resource source declaration is forbidden')
    require(discovered==names_seen,'Unindexed or missing resource source declaration')
    for row in index:
        env=envelopes[row['id']];folder=row['declaration'].rsplit('/',1)[0];members=lock_members[row['id']]
        required={row['declaration'],'LICENSE','THIRD_PARTY_NOTICES.md'}
        required.update(name for name in actual if name.startswith(folder+'/') and (name not in names_seen or name==row['declaration']))
        def references(value):
            if isinstance(value,str):
                if value in actual:required.add(value)
                relative=folder+'/'+value
                if relative in actual:required.add(relative)
            elif isinstance(value,dict):
                for item in value.values():references(item)
            elif isinstance(value,list):
                for item in value:references(item)
        references(env['recipe'])
        if env['renderer'] in ('fixed-public-cpu-v1','fixed-public-cpu-live-v1'):required.update(name for name in actual if name.startswith('baseline/brands/onexplayer/oxp3/scripts/controls/cpu/'))
        if env['renderer']=='fixed-public-rapl-v1':required.add('baseline/brands/onexplayer/oxp3/scripts/controls/rapl/provider.py')
        if env['renderer']=='files-v1':required.add('baseline/common/scripts/licenses/upstream-fixes-MIT.txt')
        if any(name.startswith('baseline/') for name in required):required.add('locks/public-baseline.toon')
        if row['id'] in ('resource:onexplayer-battery-common','resource:onexplayer-battery-charge-backend','resource:onexplayer-microphone'):
            required.add('locks/public-00d26ea-provenance.toon');references(doc('locks/public-00d26ea-provenance.toon'))
        require(required<=members,'Per-resource source lock omits inputs: '+row['id']+' '+repr(sorted(required-members)))
    names = repository['resources']
    require(isinstance(names,list) and names and len(set(names)) == len(names), 'Invalid resource index')
    require(all(name in locked for name in names), 'Unpinned resource declaration')
    output.mkdir(parents=True)
    def write(name, data):
        from protocol.manifest import _filesystem_path
        dest=_filesystem_path(local(output,name)); dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():require(dest.read_bytes()==data,'Output collision: '+name)
        else:dest.write_bytes(data)
    def document(name, value): write(name,(toon.dumps(value)+'\n').encode('utf-8'))
    if sdk_identity is not None:
        manifest=Path(sdk_identity['manifest']).read_bytes();require(sha(manifest)==sdk_identity['sha256'],'SDK identity changed during build')
        write('provenance/sdk-source-contract.toon',manifest)
        document('provenance/sdk-build-identity.toon',dict(kind='VerifiedSdkBuildIdentity',manifest='provenance/sdk-source-contract.toon',sha256=sdk_identity['sha256'],capabilities=sdk_identity['capabilities']))
    for name in ('repository.toon','locks/public-baseline.toon',repository['resource_sources_lock']):
        write('provenance/source/'+name,read(name))
    for license_name in ('LICENSE','baseline/common/scripts/licenses/upstream-fixes-MIT.txt'):
        write('licenses/'+license_name.rsplit('/',1)[-1],read(license_name))
    resources=[]; used=set(); targets=set()
    for name in names:
        spec=doc(name)
        write('provenance/source/'+name,read(name))
        required={'schema_version','kind','id','version','dependencies','external_component_dependencies','hardware_requires','payload','install','verify','restore','license_files'}
        require(set(spec)==required and type(spec['schema_version']) is int and spec['schema_version']==1 and spec['kind']=='BoilerResourceSource', 'Invalid resource source')
        identity=spec['id'];require(isinstance(identity,str) and re.fullmatch(r'resource:[a-z][a-z0-9-]*',identity) and identity not in used,'Invalid resource identity');used.add(identity)
        require(isinstance(spec['version'],str) and spec['version'], 'Invalid resource version')
        require(spec['install']=={'kind':'files-v1'} and spec['verify']=={'kind':'sha256-v1'} and spec['restore']=={'kind':'transaction-backup-v1'}, 'Unsupported lifecycle')
        require(spec['license_files']==['LICENSE','baseline/common/scripts/licenses/upstream-fixes-MIT.txt'], 'Unsupported license mapping')
        folder=name.removesuffix('/resource.toon');payload=[];install=[];origins=[];stored={}
        for row in spec['payload']:
            require(set(row) in ({'source','renderer','target','mode','scope'},{'source','renderer','target','mode','scope','member'}) and row['renderer'] in ('identity-v1','toon-json-v1','locked-archive-member-v1'), 'Unsupported payload renderer')
            require(('member' in row)==(row['renderer']=='locked-archive-member-v1'),'Invalid archive member')
            src=row['source'];require(src in locked,'Unpinned payload source')
            target=row['target'];local(output,target)
            require((row['scope'],target) not in targets,'Duplicate target');targets.add((row['scope'],target))
            require(row['scope']==('shared-home' if target.startswith('home/') else 'root-slots') and type(row['mode']) is int and row['mode'] in (420,493),'Invalid payload target')
            raw=read(src)
            if row['renderer']=='identity-v1':data=raw
            elif row['renderer']=='toon-json-v1':data=(json.dumps(toon.loads(raw.decode('utf-8'),strict=True),ensure_ascii=False,indent=2)+'\n').encode('utf-8')
            else:
                require(identity=='resource:intel-xevmafix-724' and row['member']=='module-lock.json','Unsupported archived source member')
                with zipfile.ZipFile(io.BytesIO(raw)) as zipped:data=zipped.read(row['member'])
            key=(src,row['renderer'],row.get('member'))
            if key not in stored:
                member=folder+'/files/'+str(len(stored)).zfill(3)+'-'+target.rsplit('/',1)[-1];stored[key]=member
                write(member,data);payload.append(dict(path=member,size=len(data),sha256=sha(data)))
                origins.append(dict(payload=member,source_path=src,source_sha256=sha(raw),output_sha256=sha(data),transform=row['renderer']))
            install.append(dict(source=stored[key],target=target,mode=row['mode'],scope=row['scope']))
        value=dict(schema_version=1,kind='BoilerResource',id=identity,version=spec['version'],status='public-source-static-candidate',
            dependencies=spec['dependencies'],external_component_dependencies=spec['external_component_dependencies'],hardware_requires=spec['hardware_requires'],
            compatibility=dict(architectures=['x86_64'],os_ids=['steamos'],os_versions=['3.10']),payload=dict(files=payload),
            install=dict(kind='files-v1',files=install),verify=spec['verify'],restore=spec['restore'],
            license_files=['licenses/LICENSE','licenses/upstream-fixes-MIT.txt'],
            provenance=dict(source_declaration='provenance/source/'+name,source_declaration_sha256=sha(read(name)),files=origins),
            acceptance=dict(source_hashes=True,build_chain_reproduced=True,file_lifecycle_tested=False,hardware_verified=False),
            limitations=['Offline file placement only. Live commands and firmware effects require separate acceptance.', 'Selected model names are advisory.'])
        if identity=='resource:common-waydroid':
            license_dir='common/virtualization/waydroid/licenses/'
            names=['binder-GPL-2.0.txt','gbinder-LICENSE.txt','glibutil-LICENSE.txt','python-gbinder-LICENSE.txt','waydroid-LICENSE.txt','lxc-LICENSE.LGPL2.1.txt','lxc-LICENSE.GPL2.txt','lxc-COPYING.txt','dnsmasq-COPYING.txt','dnsmasq-COPYING-v3.txt']
            for item in names:
                source=license_dir+item;require(source in locked,'Missing Waydroid upstream license')
                dest='licenses/waydroid/'+item;write(dest,read(source));value['license_files'].append(dest)
            value['limitations']=['The project-built host packages require the explicit local-test authorization.','Android images are downloaded by the official initialization flow and may require retry.','Physical-device and Android-container acceptance are not yet complete.']
        if identity=='resource:onexplayer-light':
            licenses=[member for (src,renderer,_),member in stored.items() if src.rsplit('/',1)[-1] in ('LICENSE','LICENSE-HHD','LICENSE-HueSync')]
            require(len(licenses)==3,'Missing light source licenses')
            value['license_files']+=licenses
            value['license_expression']='BSD-3-Clause AND LGPL-2.1-or-later'
        if identity=='resource:oxp3-speaker-route':
            licenses=[member for (src,renderer,_),member in stored.items() if src.endswith('/UPSTREAM_LICENSE')]
            require(len(licenses)==1,'Missing speaker upstream license')
            value['license_files']+=licenses
            evidence=folder+'/source-provenance.toon';require(evidence in locked,'Missing speaker provenance')
            proof=doc(evidence)
            require(proof.get('kind')=='SpeakerSourceProvenance' and proof.get('commit')=='e72e814da1944da3792076689995b6fe961f5768','Invalid speaker provenance')
            archive=proof['metadata_archive'];require(archive in locked,'Unpinned speaker archive')
            with zipfile.ZipFile(io.BytesIO(read(archive))) as zipped:
                members=proof['metadata_members']
                require(set(zipped.namelist())=={item['member'] for item in members} and len(zipped.namelist())==len(members),'Speaker archive members mismatch')
                for item in members:
                    raw=zipped.read(item['member']);require(len(raw)==item['size'] and sha(raw)==item['sha256'],'Speaker archived source mismatch')
            require(len(proof['transforms'])==3,'Missing speaker transforms')
            for transform in proof['transforms']:
                for key in ('upstream','patch','output'):
                    require(transform[key] in locked and sha(read(transform[key]))==transform[key+'_sha256'],'Speaker transform hash mismatch')
                original=read(transform['upstream']).decode('utf-8');rendered=read(transform['output']).decode('utf-8');filename=transform['upstream'].rsplit('/',1)[-1]
                patch=''.join(difflib.unified_diff(original.splitlines(keepends=True),rendered.splitlines(keepends=True),fromfile='upstream/'+filename,tofile='resource/'+filename)).encode()
                require(patch==read(transform['patch']),'Speaker patch mismatch')
            evidence_dest='provenance/speaker-source-provenance.toon';write(evidence_dest,read(evidence));value['provenance']['source_provenance']=evidence_dest
            for item in [proof['metadata_archive']]+[t[k] for t in proof['transforms'] for k in ('upstream','patch','output')]:
                require(item in locked,'Unpinned speaker evidence');write('provenance/speaker/'+item.removeprefix(folder+'/'),read(item))
        if identity=='resource:intel-xevmafix-724':
            module=next((read(row['source']) for row in spec['payload'] if row['target'].endswith('/xevmafix.ko')),None)
            require(module is not None and len(module)==397512 and sha(module)=='10a473fca8031ab43206f7a9146413cf781355ddedd3ff5a66cf59bb5369461b' and module.startswith(b'\x7fELF\x02') and int.from_bytes(module[18:20],'little')==62 and b'vermagic=7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9 SMP preempt mod_unload ' in module,'Xevma retained module mismatch')
            licenses=[member for (src,renderer,_),member in stored.items() if src.endswith('/LICENSE')]
            require(len(licenses)==1,'Missing xevma GPL license');value['license_files']+=licenses
            value['compatibility']['kernels']=['7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9']
            value['acceptance']['build_chain_reproduced']=False;value['acceptance']['explicit_prebuilt_module_verified']=True
            value['limitations'].append('Retained prebuilt module; an active module requires disabling activation and reboot to remove.')
            evidence=folder+'/source-provenance.toon';require(evidence in locked,'Missing xevma source provenance');proof=doc(evidence)
            require(proof.get('kind')=='XevmaSourceProvenance' and proof.get('commit')=='e72e814da1944da3792076689995b6fe961f5768' and proof.get('module_sha256')==sha(module),'Invalid xevma provenance')
            archive=proof['metadata_archive'];require(archive in locked,'Unpinned xevma archive')
            with zipfile.ZipFile(io.BytesIO(read(archive))) as zipped:
                require(sha(zipped.read('module-lock.json'))==proof['module_lock_sha256'],'Xevma module lock mismatch')
            require(len(proof['transforms'])==1,'Missing xevma patch')
            for transform in proof['transforms']:
                for key in ('upstream','patch','output'):
                    require(transform[key] in locked and sha(read(transform[key]))==transform[key+'_sha256'],'Xevma transform mismatch')
                before=read(transform['upstream']).decode();after=read(transform['output']).decode()
                patch=''.join(difflib.unified_diff(before.splitlines(keepends=True),after.splitlines(keepends=True),fromfile='upstream/load.py',tofile='resource/load.py')).encode()
                require(patch==read(transform['patch']),'Xevma patch mismatch')
            for item in [evidence,archive]+proof['build_evidence']+[t[k] for t in proof['transforms'] for k in ('upstream','patch','output')]:
                require(item in locked,'Unpinned xevma build evidence');write('provenance/xevma/'+item.removeprefix(folder+'/'),read(item))
            value['provenance']['source_provenance']='provenance/xevma/source-provenance.toon'
        if identity in ('resource:onexplayer-battery-common','resource:onexplayer-battery-charge-backend','resource:onexplayer-microphone'):
            evidence='locks/public-00d26ea-provenance.toon';require(evidence in locked,'Missing public source provenance')
            proof=doc(evidence);require(proof.get('commit')=='00d26ea7ed0aa4f1d1002216242199bc72736e80' and proof.get('repository')=='FlightDan/Boiler-installer','Invalid public source proof')
            public={row['path'].removeprefix('adaptations/'):row for row in proof['files']}
            archive=proof['metadata_archive'];require(archive in locked,'Unpinned public source archive')
            with zipfile.ZipFile(io.BytesIO(read(archive))) as zipped:
                for row in spec['payload']:
                    source_row=locked[row['source']]; upstream=source_row['upstream'];require(upstream in public,'Public source proof missing member')
                    upstream_proof=public[upstream]
                    if source_row['origin']=='semantic-json-to-toon':
                        original=zipped.read(upstream.removeprefix('brands/onexplayer/common/'))
                        require(toon.loads(read(row['source']).decode(),strict=True)==json.loads(original),'Semantic source conversion mismatch')
                    elif source_row['origin']=='derived-public-00d26ea':
                        original=read(proof['transforms'][0]['upstream'])
                    else:original=read(row['source'])
                    require(len(original)==upstream_proof['size'] and sha(original)==upstream_proof['sha256'],'Public source bytes mismatch')
                    import hashlib
                    require(hashlib.sha1(b'blob '+str(len(original)).encode()+b'\0'+original).hexdigest()==upstream_proof['git_blob'],'Public source git blob mismatch')
            for transform in proof['transforms']:
                for key in ('upstream','patch','output'):
                    require(transform[key] in locked and sha(read(transform[key]))==transform[key+'_sha256'],'Public source transform mismatch')
                before=read(transform['upstream']).decode();after=read(transform['output']).decode();filename=transform['upstream'].rsplit('/',1)[-1]
                patch=''.join(difflib.unified_diff(before.splitlines(keepends=True),after.splitlines(keepends=True),fromfile='upstream/'+filename,tofile='resource/'+filename)).encode()
                require(patch==read(transform['patch']),'Public source patch mismatch')
            for item in [evidence,proof['metadata_archive']]+[t[k] for t in proof['transforms'] for k in ('upstream','patch','output')]:
                require(item in locked,'Unpinned public source evidence');write('provenance/public-00d26ea/'+item,read(item))
            value['provenance']['source_provenance']='provenance/public-00d26ea/'+evidence
        member=folder+'/resource.toon';document(member,value);resources.append(dict(id=identity,version=spec['version'],path=member,sha256=sha(local(output,member).read_bytes())))
    from protocol.firmware_contract import validate_contract as validate_firmware
    firmware_names=repository.get('firmware_sources',[])
    require(isinstance(firmware_names,list) and len(firmware_names)<=1,'Invalid firmware source index')
    for name in firmware_names:
        require(name in locked,'Unpinned firmware source declaration');spec=doc(name)
        require(spec.get('kind')=='BoilerFirmwareSource' and type(spec.get('schema_version')) is int and spec['schema_version']==1,'Invalid firmware source')
        resource=spec['resource'];validate_firmware(resource)
        expected={r['path'] for r in resource['payload']['files']}|set(resource['license_files'])|{'devices/network/intel/be201/sources/002-provenance.json'}
        require(len(spec['files'])==12 and {r['output'] for r in spec['files']}==expected,'Invalid firmware source inventory')
        for row in spec['files']:
            require(set(row)=={'source','renderer','output'} and row['source'] in locked and row['renderer'] in ('identity-v1','toon-json-v1'),'Invalid firmware input')
            raw=read(row['source']);data=raw if row['renderer']=='identity-v1' else (json.dumps(toon.loads(raw.decode('utf-8'),strict=True),ensure_ascii=False,indent=2)+'\n').encode()
            expected_row=next((r for r in resource['payload']['files'] if r['path']==row['output']),None)
            if expected_row:
                require(len(data)==expected_row['size'] and sha(data)==expected_row['sha256'],'Firmware payload differs from declaration')
                require(FIRMWARE_SHA256.get(row['output'].rsplit('/',1)[-1])==sha(data),'Firmware differs from fixed reviewed collection')
            write(row['output'],data)
        require(spec['original_metadata'] in locked,'Unpinned firmware metadata evidence')
        write('provenance/source/'+name,read(name));write('provenance/firmware-original-metadata.zip',read(spec['original_metadata']))
        resource['provenance']['source_lock']='provenance/wifi-single-source-lock.json'
        resource['provenance']['source_recipe_sha256']=sha(read(name))
        write('provenance/wifi-single-source-lock.json',(json.dumps(dict(kind='ProjectSingleFirmwareSourceLock',resource=resource['id'],source_recipe='provenance/source/'+name,source_recipe_sha256=sha(read(name)),origin=spec['origin'],files=spec['files'],old_package_or_archive_read=False),ensure_ascii=False,indent=2)+'\n').encode())
        path='devices/network/intel/be201/resource.toon';document(path,resource)
        resources.append(dict(id=resource['id'],version=resource['version'],path=path,sha256=sha(local(output,path).read_bytes())))
    hdr_names=repository.get('hdr_sources',[])
    require(isinstance(hdr_names,list) and len(hdr_names)<=1,'Invalid HDR source index')
    for name in hdr_names:
        require(name in locked,'Unpinned HDR declaration');spec=doc(name)
        require(spec.get('upstream') in locked and spec.get('license') in locked,'Unpinned HDR input')
        from . import hdr_game
        with tempfile.TemporaryDirectory(prefix='hdr-source-fragment-') as temp:
            hdr_root=Path(temp)/'fragment';hdr_game.build(Path(__file__).resolve().parents[2],hdr_root,source_root=source_root,source_spec=spec)
            result=validate_pool(hdr_root);require(len(result['resources'])==1,'Invalid HDR fragment')
            ref=result['resource_refs'][0]
            for row in result['files']:
                if row['path'].startswith('models/'):continue
                write(row['path'],local(hdr_root,row['path']).read_bytes())
            write('provenance/source/'+name,read(name));resources.append(ref)
    desktop_names=repository['desktop_hdr_sources']
    require(len(desktop_names)<=1,'Invalid desktop HDR source index')
    for name in desktop_names:
        identity='resource:gamma22-hdr-desktop';env=envelopes[identity];recipe=env['recipe'];owned=lock_members[identity]
        require(name in owned and recipe['native_manifest'] in owned,'Unpinned desktop HDR declaration or native manifest')
        for row in [*recipe['runtime_sources'],*recipe['license_sources']]:
            require(isinstance(row,dict) and row.get('source') in owned,'Desktop source outside its resource lock')
            read(row['source'])
        read(recipe['native_manifest'])
        require(desktop_archive is not None,'An explicit frozen desktop native archive is required')
        from . import desktop_hdr
        with tempfile.TemporaryDirectory(prefix='desktop-hdr-source-fragment-') as temp:
            fragment=Path(temp)/'fragment'
            desktop_hdr.build(Path(__file__).resolve().parents[2],fragment,source_root=source_root,source_spec=env,native_archive=desktop_archive,locked_paths=owned)
            result=validate_pool(fragment);require(len(result['resources'])==1,'Invalid desktop HDR fragment')
            ref=result['resource_refs'][0]
            for row in result['files']:
                if row['path'].startswith('models/'):continue
                write(row['path'],local(fragment,row['path']).read_bytes())
            write('provenance/source/'+name,read(name));resources.append(ref)
    cpu_names=repository.get('cpu_sources',[])
    require(isinstance(cpu_names,list) and len(cpu_names)<=1,'Invalid CPU source index')
    for name in cpu_names:
        require(name in locked,'Unpinned CPU declaration');spec=doc(name)
        require(spec.get('deployment') in locked and all(item in locked for item in spec.get('templates',[])),'Unpinned CPU source')
        sdk=Path(__file__).resolve().parents[2]
        loader=importlib.util.spec_from_file_location('source_cpu_builder',sdk/'scripts/prepare-cpu-resource.py');builder=importlib.util.module_from_spec(loader);loader.loader.exec_module(builder)
        with tempfile.TemporaryDirectory(prefix='cpu-source-fragment-') as temp:
            cpu_root=Path(temp)/'fragment'
            is_live=spec.get('renderer')=='fixed-public-cpu-live-v1'
            (builder.build_live if is_live else builder.build)(sdk,cpu_root,source_root=source_root,source_spec=spec)
            result=validate_pool(cpu_root,_cpu_fragment=True) if is_live else validate_pool(cpu_root)
            require(len(result['resources'])==1,'Invalid CPU fragment');ref=result['resource_refs'][0]
            cpu=result['resources'][0]
            if not is_live:
                cpu['dependencies']=['resource:common-intel-rapl'];cpu['external_component_dependencies']=['decky']
            cpu_raw=(toon.dumps(cpu)+'\n').encode();ref=dict(ref,sha256=sha(cpu_raw))
            for row in result['files']:
                if row['path'].startswith('brands/onexplayer/models/'):continue
                write(row['path'],cpu_raw if row['path']==ref['path'] else local(cpu_root,row['path']).read_bytes())
            write('provenance/source/'+name,read(name));resources.append(ref)
    sdk=Path(__file__).resolve().parents[2]
    require(repository.get('rapl_source')=='common/CPU/Intel/rapl/resource.toon','Invalid RAPL source index')
    from . import additional,rapl
    with tempfile.TemporaryDirectory(prefix='additional-source-fragment-') as temp:
        extra_root=Path(temp)/'additional';additional.build(sdk,extra_root,source_root=source_root,source_names=by_renderer.get('additional-files-v1',[])+by_renderer.get('fixed-ec-profile-v1',[]));extra=validate_pool(extra_root)
        for row in extra['files']:
            if row['path'].startswith('brands/onexplayer/models/'):continue
            write(row['path'],local(extra_root,row['path']).read_bytes())
        resources.extend(extra['resource_refs'])
        rapl_root=Path(temp)/'rapl';rapl_ref=rapl.build(sdk,rapl_root,source_root=source_root)
        for row in rapl_ref['files']:write(row['path'],local(rapl_root,row['path']).read_bytes())
        rapl_resource=toon.loads(local(rapl_root,rapl_ref['path']).read_text(encoding='utf-8'),strict=True)
        resources.append(dict(id=rapl_ref['id'],version=rapl_resource['version'],path=rapl_ref['path'],sha256=rapl_ref['sha256']))
    boot_names=repository.get('boot_sources',[])
    require(isinstance(boot_names,list) and len(boot_names)<=1,'Invalid boot source index')
    for name in boot_names:
        require(name in locked,'Unpinned boot source');spec=doc(name)
        require(set(spec)=={'schema_version','kind','renderer','resource','license_sources'} and type(spec['schema_version']) is int and spec['schema_version']==1 and spec['kind']=='BoilerBootSource' and spec['renderer']=='fixed-boot-settings-v1','Invalid boot source')
        from maintenance.boot_resources import validate_contract as validate_boot
        resource=validate_boot(spec['resource'])
        require(len(spec['license_sources'])==2 and len(resource['license_files'])==2,'Boot licenses missing')
        for src,dest in zip(spec['license_sources'],resource['license_files']):require(src in locked,'Unpinned boot license');write(dest,read(src))
        for src in resource['provenance']['source_paths']:require(src in locked,'Unpinned boot upstream source')
        write('provenance/source/'+name,read(name));resource['provenance']['source_declaration']='provenance/source/'+name
        path='common/boot/fixes/resource.toon';document(path,resource);resources.append(dict(id=resource['id'],version=resource['version'],path=path,sha256=sha(local(output,path).read_bytes())))
    flatpak_names=by_renderer.get('fixed-flathub-mirror-v1',[])
    require(len(flatpak_names)<=1,'Duplicate Flathub source')
    for name in flatpak_names:
        from protocol.flathub_contract import IDENTITY as FLATHUB_ID,KIND as FLATHUB_KIND,TARGET as FLATHUB_TARGET,SHA256 as FLATHUB_SHA,MIRROR,validate_contract
        env=envelopes.get(FLATHUB_ID);require(env is not None and env['renderer']=='fixed-flathub-mirror-v1','Invalid Flathub identity')
        recipe=env['recipe'];definition=recipe['definition']
        require(name in locked and definition in locked and recipe['license_files']==['LICENSE'] and recipe['mirror_url']==MIRROR,'Invalid pinned Flathub recipe')
        raw=read(definition);require(len(raw)==4040 and sha(raw)==FLATHUB_SHA,'Public Flathub definition differs')
        member='common/flatpak/flathub/files/flathub.flatpakrepo';write(member,raw)
        resource=dict(schema_version=1,kind='BoilerResource',id=FLATHUB_ID,version=env['version'],status='fixed-host-capability-candidate',
            dependencies=env['dependencies'],external_component_dependencies=env['external_component_dependencies'],hardware_requires=env['hardware_requires'],
            compatibility=env['compatibility'],payload=dict(files=[dict(path=member,size=len(raw),sha256=sha(raw))]),
            install=dict(kind=FLATHUB_KIND,files=[dict(source=member,target=FLATHUB_TARGET,mode=420,scope='root-slots')]),
            verify=env['verify'],restore=env['restore'],license_files=['licenses/LICENSE'],
            provenance=dict(source_declaration='provenance/source/'+name,source_declaration_sha256=sha(read(name)),definition=definition,definition_sha256=sha(raw)),
            acceptance=dict(source_hashes=True,build_chain_reproduced=True,file_lifecycle_tested=False,hardware_verified=False),
            limitations=['Remote activation requires the fixed host provider; mirror network access is separately verified.'])
        validate_contract(resource);write('provenance/source/'+name,read(name))
        path='common/flatpak/flathub/resource.toon';document(path,resource)
        resources.append(dict(id=FLATHUB_ID,version=env['version'],path=path,sha256=sha(local(output,path).read_bytes())))
    overlay_names=by_renderer.get('fixed-telemetry-overlay-v1',[])
    require(len(overlay_names)<=1,'Duplicate overlay source')
    for name in overlay_names:
        from protocol.overlay_contract import IDENTITY as OID,KIND as OKIND,FILES as OFILES,validate_contract
        env=envelopes.get(OID);require(env is not None and env['renderer']=='fixed-telemetry-overlay-v1','Invalid overlay identity')
        recipe=env['recipe'];require(recipe['license_files']==['LICENSE'] and name in locked,'Invalid overlay license')
        payload=[];files=[]
        for key,src,target,mode,size,digest in OFILES:
            require(recipe[key]==src and src in locked,'Invalid overlay fixed input')
            raw=read(src);require(len(raw)==size and sha(raw)==digest,'Overlay fixed input differs');write(src,raw)
            payload.append(dict(path=src,size=size,sha256=digest));files.append(dict(source=src,target=target,mode=mode,scope='root-slots'))
        resource=dict(schema_version=1,kind='BoilerResource',id=OID,version=env['version'],status='fixed-host-capability-candidate',
            dependencies=env['dependencies'],external_component_dependencies=env['external_component_dependencies'],hardware_requires=env['hardware_requires'],
            compatibility=env['compatibility'],payload=dict(files=payload),install=dict(kind=OKIND,files=files),verify=env['verify'],restore=env['restore'],
            license_files=['licenses/LICENSE'],provenance=dict(source_declaration='provenance/source/'+name,source_declaration_sha256=sha(read(name))),
            acceptance=dict(source_hashes=True,build_chain_reproduced=True,file_lifecycle_tested=False,hardware_verified=False),
            limitations=['Runtime and hardware acceptance require independent verification.'])
        validate_contract(resource);write('provenance/source/'+name,read(name));path='common/telemetry/overlay/resource.toon';document(path,resource)
        resources.append(dict(id=OID,version=env['version'],path=path,sha256=sha(local(output,path).read_bytes())))
    keyboard_names=by_renderer.get('fixed-kde-keyboard-v1',[])
    require(len(keyboard_names)<=1,'Duplicate keyboard source')
    for name in keyboard_names:
        require(name in locked,'Unpinned keyboard source declaration')
        env=next(e for e in envelopes.values() if e['renderer']=='fixed-kde-keyboard-v1')
        require(env['recipe']=={'license_files':['LICENSE']},'Invalid keyboard license recipe')
        resource=dict(schema_version=1,kind='BoilerResource',id=env['id'],version=env['version'],
            status='fixed-host-capability-candidate',dependencies=env['dependencies'],
            external_component_dependencies=env['external_component_dependencies'],hardware_requires=env['hardware_requires'],
            compatibility=env['compatibility'],payload=dict(files=[]),install=dict(kind=env['install_kind'],files=[]),
            verify=env['verify'],restore=env['restore'],license_files=['licenses/LICENSE'],
            provenance=dict(source_declaration='provenance/source/'+name,source_declaration_sha256=sha(read(name))),
            acceptance=dict(source_hashes=True,build_chain_reproduced=True,file_lifecycle_tested=False,hardware_verified=False),
            limitations=['Fixed host adapter and shared keyboard ledger require separately reviewed firstboot and maintenance integration.'])
        from protocol.keyboard_resource_contract import validate_contract as validate_keyboard
        validate_keyboard(resource)
        write('provenance/source/'+name,read(name))
        path='common/session/kde-keyboard/resource.toon';document(path,resource)
        resources.append(dict(id=resource['id'],version=resource['version'],path=path,sha256=sha(local(output,path).read_bytes())))
    opti_names=by_renderer.get('fixed-opti2xmx-v1',[])
    require(len(opti_names)<=1,'Duplicate Opti source')
    for name in opti_names:
        require(name in locked,'Unpinned Opti source declaration')
        env=next(e for e in envelopes.values() if e['renderer']=='fixed-opti2xmx-v1')
        from .opti_renderer import render as render_opti
        resource=render_opti(source_root,env,toon,read,write,input_store=input_store,seven_zip=seven_zip)
        write('provenance/source/'+name,read(name))
        path='common/plugins/opti2xmx/resource.toon';document(path,resource)
        resources.append(dict(id=resource['id'],version=resource['version'],path=path,sha256=sha(local(output,path).read_bytes())))
    input_names=repository.get('input_sources',[])
    require(isinstance(input_names,list) and len(input_names)<=1,'Invalid input source index')
    for name in input_names:
        require(name in locked,'Unpinned input source');spec=doc(name)
        require(set(spec)=={'schema_version','kind','renderer','archive','archive_size','archive_sha256','variant','native_build_sha256','native_artifact_type','resources'} and type(spec['schema_version']) is int and spec['schema_version']==1 and spec['kind']=='BoilerInputSource' and spec['renderer']=='fixed-input-r5-v1' and spec['variant']=='r5' and spec['native_artifact_type']=='retained-source-built-runtime' and spec['resources']==['resource:onexplayer-input','resource:bosch-bmi260-gyro'],'Invalid input source')
        archive=spec['archive'];require(archive in locked,'Unpinned input source archive');raw=read(archive)
        require(len(raw)==spec['archive_size'] and sha(raw)==spec['archive_sha256'],'Input source archive differs')
        sdk=Path(__file__).resolve().parents[2];loader=importlib.util.spec_from_file_location('source_input_builder',sdk/'scripts/prepare-input-resource.py');builder=importlib.util.module_from_spec(loader);loader.loader.exec_module(builder)
        with tempfile.TemporaryDirectory(prefix='input-source-fragment-') as temp:
            temp=Path(temp);library=temp/'source';library.mkdir();seen=set();total=0
            from protocol.manifest import _filesystem_path
            with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as stream:
                for member in stream:
                    require(member.isfile() and member.name.startswith('adaptations/') and member.name not in seen and member.size<=256*1024*1024,'Invalid input source archive member')
                    seen.add(member.name);total+=member.size;require(total<=512*1024*1024,'Input source archive too large')
                    dest=_filesystem_path(local(library,member.name));dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(stream.extractfile(member).read())
            lock=_filesystem_path(local(library,'adaptations/brands/onexplayer/common/resource-inputs/input-runtime-v1/r5/source-input-lock.toon'))
            source_lock=toon.loads(lock.read_text(encoding='utf-8'),strict=True)
            require(source_lock['native_build_sha256']==spec['native_build_sha256'],'Input native source build proof differs')
            declared=source_lock['files']+source_lock['definitions'];names=[row['path'] for row in declared]
            require(len(names)==len(set(names)) and seen==set(names)|{'adaptations/brands/onexplayer/common/resource-inputs/input-runtime-v1/r5/source-input-lock.toon'},'Input archive differs from explicit source inventory')
            for row in declared:
                value=_filesystem_path(local(library,row['path'])).read_bytes()
                require(len(value)==row['size'] and sha(value)==row['sha256'],'Input source archive member differs: '+row['path'])
            input_root=temp/'fragment';builder.build(sdk,input_root,source_root=library,multifunction=True)
            result=validate_pool(input_root);require({r['id'] for r in result['resources']}==set(spec['resources']),'Input fragment identities differ')
            closure_name='provenance/input-runtime/native-closure.toon';closure_raw=_filesystem_path(local(input_root,closure_name)).read_bytes()
            require(sha(closure_raw)=='cd4758ec1e9a8d36ba558b3eaf58b152a2799c60cafce30c646cd76fb902fc60','Input native closure differs from reviewed build')
            closure=toon.loads(closure_raw.decode('utf-8'),strict=True);native={row['path']:row for row in closure['files']}
            input_resource=next(r for r in result['resources'] if r['id']=='resource:onexplayer-input')
            gyro_resource=next(r for r in result['resources'] if r['id']=='resource:bosch-bmi260-gyro')
            installed={row['target']:row for r in (input_resource,gyro_resource) for row in r['install']['files']}
            native_targets={target.removeprefix('usr/lib/boiler-input/runtime/'):row for target,row in installed.items() if target.startswith('usr/lib/boiler-input/runtime/')}
            require(set(native_targets)==set(native),'Input native runtime closure differs')
            for target,row in native_targets.items():
                data=_filesystem_path(local(input_root,row['source'])).read_bytes()
                require(len(data)==native[target]['size'] and sha(data)==native[target]['sha256'],'Input native member differs from reviewed build')
            for target,source in [('usr/lib/boiler-input/input-hid-binding.py','maintenance/input_hid_binding.py'),('usr/lib/boiler-input/prepare-profile.py','maintenance/input_profile_runtime.py'),('var/lib/oxp3-gyro-v1/gyro_binding.py','maintenance/input_gyro_binding.py')]:
                require(sha(_filesystem_path(local(input_root,installed[target]['source'])).read_bytes())==sha(local(sdk,source).read_bytes()),'Input executable helper differs from executing SDK')
            for target,digest in [('usr/lib/boiler-input/hid-oxp.ko','7a3aa7204a0f7427bf8ad867b42eea76d973a1f627aaede1709e281702e5d1ed'),('var/lib/oxp3-gyro-v1/oxp3_bmi260.ko','9d576c2b8ae7e9b1f4fedea7fdde296e80767fab46eac4812d14f5b90aaae395')]:
                require(sha(_filesystem_path(local(input_root,installed[target]['source'])).read_bytes())==digest,'Input module differs from reviewed build')
            for row in result['files']:
                if row['path'].startswith('brands/onexplayer/models/'):continue
                write(row['path'],_filesystem_path(local(input_root,row['path'])).read_bytes())
            write('provenance/source/'+name,read(name));resources.extend(result['resource_refs'])
    require(len(resources)==len(index) and {r['id'] for r in resources}==set(envelopes),'Emitted resource identities differ from source index')
    for ref in resources:
        env=envelopes[ref['id']];resource=toon.loads(local(output,ref['path']).read_text(encoding='utf-8'),strict=True)
        require(resource['install']['kind']==env['install_kind'],'Renderer lifecycle differs from source envelope')
        for field in ('version','dependencies','external_component_dependencies','hardware_requires','compatibility','verify','restore'):
            if field in resource or env[field]:resource[field]=env[field]
        raw=(toon.dumps(resource)+'\n').encode();local(output,ref['path']).write_bytes(raw);ref['version']=env['version'];ref['sha256']=sha(raw)
    catalog=dict(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False,resources=resources,models=[],files=[])
    from . import model_source
    model_source.apply(output,Path(__file__).resolve().parents[2],catalog,source_root=source_root)
    document('provenance/source-read-closure.toon',dict(schema_version=1,kind='AdaptationsSourceReadClosure',source_files=[source_reads[k] for k in sorted(source_reads)],note='All indexed baseline and imported sources are read and verified; runtime archive members are covered by the locked archive and internal inventory.'))
    from protocol.manifest import _filesystem_path
    output_io=_filesystem_path(output)
    catalog['files']=[dict(path=p.relative_to(output_io).as_posix(),size=p.stat().st_size,sha256=sha(p.read_bytes())) for p in sorted(output_io.rglob('*')) if p.is_file()]
    document('catalog.toon',catalog);write('catalog.json',(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n').encode())
    validated=validate_pool(output)
    owners={};resources_by_id={row['id']:row for row in validated['resources']}
    for resource in validated['resources']:
        for row in resource['install']['files']:
            target=(row['scope'],row['target'])
            require(target not in owners,'Target owned by multiple resources: '+row['target']);owners[target]=resource['id']
        if resource['install']['kind']=='boiler-boot-settings-v1':
            for target_name in resource['fixed_targets']:
                target=('root-slots',target_name);require(target not in owners,'Boot target owned by multiple resources: '+target_name);owners[target]=resource['id']
    done=set();active=set()
    def visit(identity):
        require(identity not in active,'Cyclic resource dependency')
        if identity in done:return
        active.add(identity)
        for dependency in resources_by_id[identity]['dependencies']:visit(dependency)
        active.remove(identity);done.add(identity)
    for identity in resources_by_id:visit(identity)
    return catalog
