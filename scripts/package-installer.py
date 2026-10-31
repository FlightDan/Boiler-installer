"""Package an independently built installer using explicit source/runtime inventories."""
import argparse, hashlib, json, shutil, subprocess, sys, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.manifest import confined, verify_file, relative_path


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def validate_integration(root, integration, *, local_test=False):
    """Derive the permitted inventory from reviewed locks and current source."""
    root=Path(root)
    components=integration['components']
    wireless={transport+':'+vendor for transport in ('wifi','bluetooth') for vendor in ('intel','realtek','mediatek')}
    def inventory(entries):
        return sorted((e['source'],e['size'],e['sha256'],e.get('target')) for e in entries)
    import importlib.util
    spec=importlib.util.spec_from_file_location('boiler_preparation',root/'scripts/prepare-integration.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    expected_graphics=module.graphics_firmware_metadata(root)
    if set(components)!={'core:decky','core:maintenance'} | wireless | set(expected_graphics):
        raise ValueError('Unapproved integration component')
    expected_wireless=module.firmware_metadata(root)
    if integration.get('graphics_candidates') != module.graphics_metadata(root):
        raise ValueError('Graphics candidate metadata differs from the current baseline source')
    for identity,item in expected_graphics.items():
        if components[identity] != item:
            raise ValueError('Graphics candidate inventory differs from the current adjustable baseline: '+identity)
    for identity in sorted(wireless):
        item=components[identity]
        if (item.get('delivery')!='on-demand' or item.get('payload_cached') is not False
                or item.get('metadata_available') is not True or item.get('available') is not True):
            raise ValueError('Production firmware must use metadata-only on-demand delivery')
        # Exact comparison includes paths, hashes, sizes, licensing and every
        # Git/alias evidence field, not just a subset of source metadata.
        if item!=expected_wireless[identity]:
            raise ValueError('Firmware metadata differs from reviewed lock: '+identity)
    decky=components['core:decky']
    if decky.get('available'):
        lock=json.loads((root/'components/locks/decky.json').read_text())
        for key in ('schema','id','architecture','version','url','license'):
            if decky.get(key)!=lock.get(key):raise ValueError('Decky provenance differs from reviewed lock')
        license_file=root/'components/licenses/decky/LICENSE'
        notices=[dict(e,source='sources/'+e['source']) for e in lock['notices']]
        notices.append(dict(source='sources/LICENSE',size=license_file.stat().st_size,sha256=digest(license_file)))
        if inventory(decky['files'])!=inventory([dict(lock['file'],source='decky/PluginLoader')]) or inventory(decky['notices'])!=inventory(notices) or decky.get('licenses'):
            raise ValueError('Decky inventory differs from reviewed lock')
    persist_models=integration.get('local_test_models') is True
    if persist_models and not local_test:
        raise ValueError('Local maintenance model assets require explicit local-test packaging')
    persist_pool=integration.get('local_test_resource_pool') is True
    if persist_pool and (not local_test or persist_models):
        raise ValueError('Shared resource pools require local-test packaging without full model bundles')
    # Rebuild from final sources to cover launchers, guides and retained model bytes.
    with tempfile.TemporaryDirectory(prefix='boiler-runtime-verification-') as temporary:
        expected=module.prepare(Path(temporary)/'payload',local_test_models=persist_models,
                                local_test_resource_pool=persist_pool, resource_pool=root/'cache/integration/resource-pool' if persist_pool else None)['components']['core:maintenance']
    if components['core:maintenance']!=expected:raise ValueError('Maintenance runtime differs from final sources')


def integration_package_files(root, integration, *, local_test=False):
    """Only core bytes belong in a distribution; reject stale firmware caches."""
    validate_integration(root,integration,local_test=local_test)
    selected={}
    for identity in ('core:decky','core:maintenance'):
        component=integration['components'][identity]
        if not component.get('available'):continue
        for entry in component.get('files',[])+component.get('licenses',[])+component.get('notices',[]):
            name=entry['source']
            if name.startswith(('wireless/','graphics/')):
                raise ValueError('Firmware bytes are not permitted in production payloads')
            if name in selected and selected[name]!=entry:raise ValueError('Conflicting integration inventory')
            selected[name]=entry
    base=Path(root)/'cache/integration'
    actual={p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file()}
    external={name for name in selected if name.startswith('resource-pool/')}
    if external:
        pool=resource_pool_package_files(root,local_test=local_test)
        if {'cache/integration/'+name for name in external}!=set(pool):
            raise ValueError('Maintenance resource references differ from the complete verified pool')
        for name in external:
            if any(selected[name][key]!=pool['cache/integration/'+name][key] for key in ('size','sha256')):
                raise ValueError('Maintenance resource reference differs from the verified pool')
    if actual!=set(selected)|{'manifest.json'}:
        raise ValueError('Integration payload contains unlisted files; firmware must be downloaded on demand')
    return selected


def resource_pool_package_files(root, *, local_test=False):
    """Include one complete verified pool; its inventory grants no production trust."""
    base=Path(root)/'cache/integration/resource-pool'
    if not base.exists():return {}
    if not local_test:
        raise ValueError('Shared resource candidates require explicit --local-test packaging')
    if (Path(root)/'cache/model-bundles/manifest.json').exists():
        raise ValueError('Shared resource pool cannot accompany legacy full model bundles')
    from protocol.resource_pool import inventory
    return {'cache/integration/resource-pool/'+entry['path']:entry for entry in inventory(base)}


def local_model_package_files(root, *, local_test=False):
    """A test installer includes only explicit anchored model inventories."""
    root=Path(root).resolve()
    descriptor=root/'cache/model-bundles/manifest.json'
    if not descriptor.exists():return {}
    if not local_test:raise ValueError('Local device candidates require explicit --local-test packaging')
    from protocol.model_catalog import bundled_packages, bundled_model_package
    files={}
    for item in bundled_packages(root):
        result=bundled_model_package(item['id'],trust_local_test=True,root=root)
        package=result['package']
        prefix=Path(item['directory']).relative_to(root).as_posix()
        if package.get('selection_contract',{}).get('kind')=='oxp3-legacy-options-v1':
            frozen=json.loads((root/prefix/'selection.json').read_text(encoding='utf-8'))
            requested=set(frozen['repairs']+frozen['plugins'])
            defaults={cap['id'] for cap in package['legacy_capabilities'] if cap.get('default')}
            available={cap['id'] for cap in package['legacy_capabilities'] if cap.get('available')}
            if defaults!=requested or not defaults<=available:
                raise ValueError('Local model UI defaults differ from the reviewed frozen selection')
        files[prefix+'/manifest.json']={'size':(root/prefix/'manifest.json').stat().st_size,
                                       'sha256':item['manifest_sha256']}
        for entry in package['files']:
            files[prefix+'/'+entry['path']]=entry
    files['cache/model-bundles/manifest.json']={'size':descriptor.stat().st_size,'sha256':digest(descriptor)}
    base=root/'cache/model-bundles'
    actual={'cache/model-bundles/'+path.relative_to(base).as_posix() for path in base.rglob('*') if path.is_file()}
    if actual!=set(files):raise ValueError('Local test model directory contains unlisted files')
    return files


def model_catalog_package_files(root, source_files=None):
    """Freeze the entire checked display snapshot inside the public whitelist."""
    from protocol.model_catalog import read_lock, snapshot_records
    root=Path(root).resolve()
    if source_files is None:
        policy=json.loads((root/'scripts/source-files.json').read_bytes())
        source_files=set(policy.get('files',[]))
    snapshot_records(root)
    lock=read_lock(root)
    names=['components/model-catalog/lock.json',*('components/model-catalog/snapshot/'+entry['path'] for entry in lock['files'])]
    if not set(names)<=set(source_files):
        raise ValueError('Pinned model snapshot is outside the reviewed source whitelist')
    return {name:dict(path=name,size=confined(root,name).stat().st_size,sha256=digest(confined(root,name))) for name in names}


def package(root, output, source_commit, configuration='Release', *, local_test=False):
    if len(source_commit)!=40 or any(c not in '0123456789abcdef' for c in source_commit):raise ValueError('Exact public source commit required')
    root=Path(root).resolve();output=Path(output).resolve()
    if output.exists() or output.with_suffix(output.suffix+'.partial').exists():raise ValueError('Package destination already exists')
    whitelist=json.loads((root/'scripts/source-files.json').read_text(encoding='utf-8'))
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),*args])
    if git('rev-parse','HEAD').decode().strip()!=source_commit:
        raise ValueError('Package must be built from the specified independent public checkout')
    tracked=set(git('ls-tree','-r','--name-only',source_commit).decode().splitlines())
    if tracked!=set(whitelist['files']):raise ValueError('Committed source differs from reviewed whitelist')
    committed={}
    for name in whitelist['files']:
        content=git('show',source_commit+':'+name)
        committed[name]={'size':len(content),'sha256':hashlib.sha256(content).hexdigest()}
        if committed[name]['sha256']!=digest(confined(root,name)):
            raise ValueError('Working source differs from public source commit: '+name)
    catalog_files=model_catalog_package_files(root,set(committed))
    records={}
    def add(name, expected=None):
        relative_path(name);p=confined(root,name)
        if not p.is_file():raise ValueError('Missing package input: '+name)
        if expected:verify_file(p,expected)
        entry={'path':name,'size':expected['size'] if expected else p.stat().st_size,'sha256':expected['sha256'] if expected else digest(p)}
        if name in records and records[name]!=entry:raise ValueError('Conflicting package inventory')
        records[name]=entry
    # Public runtime source and only necessary product scripts, never a workspace walk.
    for name in whitelist['files']:
        if name.startswith(('guest/','components/','maintenance/','protocol/','schemas/','scripts/','licenses/','docs/')) or name in ('LICENSE','THIRD_PARTY_NOTICES.md','README.md'):
            add(name,committed[name])
    for name,entry in catalog_files.items(): add(name,entry)
    lock=json.loads((root/'scripts/runtime-dependencies.lock.json').read_text(encoding='utf-8'))
    groups=set(lock['profiles']['runtime'])
    for archive in lock['archives']:
        for entry in archive.get('members',[]):
            if entry['group'] in groups:add(entry['target'],entry)
    # Reconstruct Python from its locked archives, then reject stale or extra installed files.
    with tempfile.TemporaryDirectory(prefix='boiler-python-package-') as temporary:
        rebuilt=Path(temporary)/'python'
        subprocess.run([sys.executable,'-B',str(root/'scripts/prepare-python.py'),'--output',str(rebuilt),'--cache',str(root/'cache/python-dependencies')],check=True,stdout=subprocess.PIPE)
        expected={p.relative_to(rebuilt).as_posix():p for p in rebuilt.rglob('*') if p.is_file()}
        actual={p.relative_to(root/'runtime/python').as_posix() for p in (root/'runtime/python').rglob('*') if p.is_file()}
        if set(expected)!=actual:raise ValueError('Python runtime inventory differs from locked rebuild')
        for name,path in expected.items():add('runtime/python/'+name,{'size':path.stat().st_size,'sha256':digest(path)})
    integration_bytes=confined(root,'cache/integration/manifest.json').read_bytes()
    integration=json.loads(integration_bytes)
    integration_files=integration_package_files(root,integration,local_test=local_test)
    if local_test and (root/'cache/model-bundles/manifest.json').exists() and integration.get('local_test_models') is not True:
        raise ValueError('Local model tests must synchronize the on-device maintenance model assets')
    pool_files=resource_pool_package_files(root,local_test=local_test)
    if pool_files and integration.get('local_test_resource_pool') is not True:
        raise ValueError('Offline pool must be retained by the on-device maintenance component')
    for name,entry in pool_files.items():add(name,entry)
    add('cache/integration/manifest.json',{'size':len(integration_bytes),'sha256':hashlib.sha256(integration_bytes).hexdigest()})
    for name,entry in integration_files.items():add('cache/integration/'+name,entry)
    # Display metadata remains pinned and verified even for offline test use.
    from protocol.model_catalog import load_models
    catalog=load_models(root=root,online=False)
    for name,entry in local_model_package_files(root,local_test=local_test).items():add(name,entry)
    # The runtime component must reflect the exact final sources in this package.
    for entry in integration['components']['core:maintenance']['component']['files']:
        relative=entry['source'].removeprefix('maintenance-runtime/')
        if relative.startswith(('maintenance/','protocol/')):verify_file(root/relative,entry)
    add('BoilerInstaller.exe')
    for suffix in ('.dll','.deps.json','.runtimeconfig.json'):
        add('app/bin/'+configuration+'/net10.0-windows/OxpInstaller'+suffix)
    release=json.loads((root/'components/release.json').read_text(encoding='utf-8'))
    info={'schema':1,'product':'Boiler Installer','version':release['version'],'display_version':release['display_version'],
          'source_commit':source_commit,'built_utc':datetime.now(timezone.utc).isoformat(),'configuration':configuration,
          'package_channel':'local-test' if local_test else 'preview','source_published':not local_test,
          'model_catalog_source':catalog['source'],
          'resource_pool':{'bundled':bool(pool_files),'files':len(pool_files),'delivery':'single-copy-offline'},
          'dependency_lock_sha256':digest(root/'scripts/runtime-dependencies.lock.json'),
          'files':[records[name] for name in sorted(records)],
          'acceptance':('local physical-test candidate; no device acceptance claimed' if local_test else 'preview; see release record for separate hardware/release gates')}
    output.parent.mkdir(parents=True,exist_ok=True)
    partial=output.with_suffix(output.suffix+'.partial')
    with zipfile.ZipFile(partial,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
        for name,entry in sorted(records.items()):
            written=0;hashed=hashlib.sha256()
            with confined(root,name).open('rb') as source,archive.open(name,'w',force_zip64=True) as target:
                for block in iter(lambda:source.read(1024*1024),b''):
                    written+=len(block)
                    if written>entry['size']:raise ValueError('Package input grew while copying: '+name)
                    hashed.update(block);target.write(block)
            if written!=entry['size'] or hashed.hexdigest()!=entry['sha256']:
                raise ValueError('Package input changed while copying: '+name)
        archive.writestr('build-info.json',json.dumps(info,indent=2)+'\n')
    partial.rename(output)
    result={'path':str(output),'size':output.stat().st_size,'sha256':digest(output),'files':len(records),'source_commit':source_commit}
    output.with_suffix(output.suffix+'.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source-commit',required=True);parser.add_argument('--configuration',choices=('Debug','Release'),default='Release')
    parser.add_argument('--local-test',action='store_true',help='Mark an unpublished source build and permit explicitly inventoried model test packages')
    args=parser.parse_args();print(json.dumps(package(args.root,args.output,args.source_commit,args.configuration,local_test=args.local_test),indent=2))

