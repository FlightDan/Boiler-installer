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


def validate_integration(root, integration):
    """Derive the permitted inventory from reviewed locks and current source."""
    components=integration['components']
    if set(components)!={'core:decky','core:maintenance','wifi:intel','wifi:realtek','wifi:mediatek'}:
        raise ValueError('Unapproved integration component')
    def inventory(entries):
        return sorted((e['source'],e['size'],e['sha256'],e.get('target')) for e in entries)
    for vendor in ('intel','realtek','mediatek'):
        item=components['wifi:'+vendor]
        if not item.get('available'):continue
        lock=json.loads((root/('components/locks/'+vendor+'.json')).read_text())
        for key in ('schema','kind','vendor','version','provenance'):
            if item.get(key)!=lock.get(key):raise ValueError('Firmware provenance differs from reviewed lock')
        files=[dict(e,source='wireless/'+vendor+'/firmware/'+e['target'].removeprefix('usr/lib/firmware/')) for e in lock['files']]
        licenses=[dict(e,source='wireless/'+vendor+'/'+e['target']) for e in lock['licenses']]
        if inventory(item['files'])!=inventory(files) or inventory(item['licenses'])!=inventory(licenses) or item.get('notices'):
            raise ValueError('Firmware inventory differs from reviewed lock')
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
    # Rebuild the small runtime from final sources to cover the launcher and targets too.
    import importlib.util
    spec=importlib.util.spec_from_file_location('boiler_preparation',root/'scripts/prepare-integration.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix='boiler-runtime-verification-') as temporary:
        expected=module.prepare(Path(temporary)/'payload')['components']['core:maintenance']
    if components['core:maintenance']!=expected:raise ValueError('Maintenance runtime differs from final sources')


def package(root, output, source_commit, configuration='Release'):
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
    validate_integration(root,integration)
    add('cache/integration/manifest.json',{'size':len(integration_bytes),'sha256':hashlib.sha256(integration_bytes).hexdigest()})
    selected=set()
    for component in integration['components'].values():
        if not component.get('available'):continue
        for entry in component.get('files',[])+component.get('licenses',[])+component.get('notices',[]):
            selected.add(entry['source']);add('cache/integration/'+entry['source'],entry)
    actual={p.relative_to(root/'cache/integration').as_posix() for p in (root/'cache/integration').rglob('*') if p.is_file()}
    if actual!=selected|{'manifest.json'}:raise ValueError('Integration payload contains unlisted files')
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
          'dependency_lock_sha256':digest(root/'scripts/runtime-dependencies.lock.json'),
          'files':[records[name] for name in sorted(records)],'acceptance':'preview; see release record for separate hardware/release gates'}
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
    args=parser.parse_args();print(json.dumps(package(args.root,args.output,args.source_commit,args.configuration),indent=2))
