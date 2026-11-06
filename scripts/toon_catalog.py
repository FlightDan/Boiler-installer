"""Model TOON sources, deterministic legacy JSON views and fixed build dispatch.

The official pinned TOON decoder owns syntax. This module owns project semantics.
It never interprets shell commands, loads model code, signs or deploys anything.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ADAPTATIONS = ROOT/'adaptations'
ADAPTERS = {
    'shared-v1': ('adaptations/brands/onexplayer/oxp3/bridge/scripts/build-shared-adaptations.py',
                  {'base':'--base','inputs':'--inputs'}),
    'oxp3-upstream-v1': ('adaptations/scripts/build-upstream-repairs.py',
                       {'base':'--base','decky':'--decky','xevma':'--xevma'}),
    'x2-current-v1': ('adaptations/brands/onexplayer/x2-mini/bridge/scripts/build.py', {}),
    'x2-complete-v1': ('adaptations/scripts/build-x2-complete.py',
        {'base':'--base','bridge_overlay':'--bridge-overlay','hdr_overlay':'--hdr-overlay'}),
    'oxp3-current-v1': ('adaptations/scripts/build-current-opti.py',
        {'base':'--base','plugin_zip':'--plugin-zip','plugin_lock':'--plugin-lock','decky':'--decky','xevma':'--xevma',
         'historical_plugin_zip':'--historical-plugin-zip','optiscaler_094':'--optiscaler-094',
         'optiscaler_079':'--optiscaler-079','optiscaler_nightly':'--optiscaler-nightly'}),
}

def adapter(model):
    return ADAPTERS[model['build']['adapter']]


def catalog_module(name):
    """Load the single implementation from the installer's pinned source snapshot."""
    path = local(ADAPTATIONS, 'scripts/' + name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def device_sources(adaptations):
    return sorted(Path(adaptations).glob('devices/*/*.toon'))


OXP_CURRENT_VERSION='0.1.0-localtest.20261030.4'
OXP_CURRENT_ANCHOR='a56006b425522d1c753254d3e0b9d11f7e79aea21c221a4a481830508c410393'
OXP_BASE_ANCHOR='510342dec0bfe6ce39389f5e13cae0d504755aa4072d90b73641b4dd2d5aead9'
OXP_SHARED_VERSION='0.1.0-localtest.20261030.3'
HISTORICAL_INPUT_LOCK='adaptations/records/oxp3-shared-base/opti031-inputs.json'
HISTORICAL_INPUT_LOCK_SHA256='f8cf3f4007e1ff6f436b0b914bf10762fe3737c0ec013d2bacc36bcd222abfa6'
OXP_SHARED_ANCHOR='c80efd9cce07d35c5f8828d17b5fab8c15fbc177a10476754cfac04db3f27485'
OXP_FULL_ANCHOR='cb39c94bbe7eb351221c380ebdb7a875a097b52c17e90bfb0f1edc22b12ab684'
LEVELS = {'required','recommended','optional','conditional','pending','excluded'}
class CatalogError(ValueError):
    pass


def require(value, message):
    if not value: raise CatalogError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def _relative_syntax(value):
    require(isinstance(value,str) and 0 < len(value) <= 4096 and '\\' not in value and ':' not in value
        and not value.startswith('/') and not any(ord(c)<32 or ord(c)==127 for c in value)
        and all(p not in ('','.','..') and not p.endswith(('.',' ')) for p in value.split('/')), 'Unsafe relative path')
    reserved = {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}
    require(not any(p.split('.')[0].upper() in reserved for p in value.split('/')), 'Reserved path')
    return value


def relative(value):
    _relative_syntax(value)
    require(not any(p.casefold() in {'.local','.git','.codex','.agents','.aws'} for p in value.split('/')),
        'Private/internal paths cannot be model inputs')
    return value


def guest_target(value):
    """Guest desktop paths are distinct from private project input paths."""
    _relative_syntax(value)
    parts=value.split('/')
    for index,part in enumerate(parts):
        require(part.casefold() not in {'.git','.codex','.agents','.aws'},'Private/internal guest target')
        if part.casefold()=='.local':
            require(index==2 and parts[:3]==['home','deck','.local'],'Unsupported guest .local target')
    return value


def local(root, name):
    root = Path(root).absolute(); current = root
    require(not root.is_symlink() and not getattr(root,'is_junction',lambda:False)(),'Linked root')
    for part in relative(name).split('/'):
        current = current/part
        require(not current.is_symlink() and not getattr(current,'is_junction',lambda:False)(), 'Linked source path: '+name)
    require(current.resolve().is_relative_to(root.resolve()),'Path escapes root')
    return current


def json_document(raw):
    def pairs(entries):
        result = {}
        for key,value in entries:
            require(key not in result,'Duplicate JSON key'); result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda v: (_ for _ in ()).throw(CatalogError('Invalid JSON constant')))


def decoder(adaptations=ADAPTATIONS):
    """Authenticate the vendored release files before loading executable code."""
    adaptations = Path(adaptations)
    lock = json_document((adaptations/'toon-parser-lock.json').read_bytes())
    require(lock.get('package')=='toon-format' and lock.get('version')=='1.2.0'
        and lock.get('spec')=='4.4','Unsupported TOON parser lock')
    vendor = (adaptations/'vendor').absolute()
    authenticated_sources = {}
    for row in lock['files']:
        path = local(vendor,row['path'])
        require(path.is_file(), 'Missing vendored TOON parser member: '+row['path'])
        raw = path.read_bytes()
        require(len(raw)==row['size'] and hashlib.sha256(raw).hexdigest()==row['sha256'],
            'Vendored TOON parser differs from pinned release: '+row['path'])
        if path.suffix == '.py': authenticated_sources[str(path)] = raw
    expected = {row['path'] for row in lock['files']}
    actual = {p.relative_to(vendor).as_posix() for p in vendor.rglob('*') if p.is_file() and p.suffix!='.pyc' and '__pycache__' not in p.parts}
    if 'toon-parser-lock.json' in actual:
        require(json_document((vendor/'toon-parser-lock.json').read_bytes())==lock,'Vendored parser lock differs')
        actual.remove('toon-parser-lock.json')
    require(actual==expected,'Uninventoried TOON parser member')
    # Python -B suppresses cache writes, but still reads .pyc. Load only the
    # authenticated source snapshots, including package submodules; never
    # execute cache bytecode or reuse modules loaded by a previous importer.
    class VerifiedSourceLoader(importlib.machinery.SourceFileLoader):
        def get_code(self, fullname):
            return compile(authenticated_sources[self.path], self.path, 'exec', dont_inherit=True)

    class VerifiedSourceFinder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname != 'toon_format' and not fullname.startswith('toon_format.'):
                return None
            member = vendor.joinpath(*fullname.split('.'))
            source = member/'__init__.py' if member.is_dir() else member.with_suffix('.py')
            require(str(source) in authenticated_sources, 'Uninventoried TOON import: '+fullname)
            return importlib.util.spec_from_file_location(fullname, source,
                loader=VerifiedSourceLoader(fullname, str(source)))

    for name in list(sys.modules):
        if name == 'toon_format' or name.startswith('toon_format.'):
            del sys.modules[name]
    # Upstream __version__ uses importlib.metadata to read the pinned dist-info.
    sys.path.insert(0,str(vendor))
    finder = VerifiedSourceFinder()
    sys.meta_path.insert(0,finder)
    try:
        import toon_format
    finally:
        sys.meta_path.remove(finder)
    require(toon_format.__version__=='1.2.0' and toon_format.__toon_spec__=='4.4','Unexpected TOON implementation')
    return toon_format


def load(path, adaptations=ADAPTATIONS):
    path = Path(path)
    require(path.is_file() and path.stat().st_size<=1024*1024,'Missing/oversized TOON source')
    raw = path.read_text(encoding='utf-8')
    # strict=True enforces official lengths, indentation, escapes and key rules.
    model = decoder(adaptations).loads(raw,strict=True)
    require(isinstance(model,dict),'Model TOON must contain an object')
    return model


def expand_package(model, adaptations):
    """Resolve explicit, hash-pinned reusable component data, never code."""
    assembly=model.get('package')
    if not isinstance(assembly,dict) or assembly.get('kind')!='BoilerPackageAssembly': return model
    require(set(assembly)=={'kind','metadata','components'} and isinstance(assembly['metadata'],dict)
        and 'components' not in assembly['metadata'] and isinstance(assembly['components'],list),'Invalid package assembly')
    # JSON cloning deliberately accepts only the JSON/TOON data model.
    package=json_document(json.dumps(assembly['metadata'],ensure_ascii=False))
    package['components']=[]
    for row in assembly['components']:
        require(isinstance(row,dict) and set(row)=={'id','definition','key','sha256'},'Invalid component definition reference')
        path=local(adaptations,row['definition'])
        require(isinstance(row['sha256'],str) and re.fullmatch('[0-9a-f]{64}',row['sha256'])
            and path.is_file() and sha(path)==row['sha256'],'Component definition lock differs')
        definitions=load(path,adaptations)
        require(set(definitions)=={'schema_version','kind','definitions'} and definitions['schema_version']==1
            and definitions['kind']=='BoilerComponentDefinitions' and isinstance(definitions['definitions'],dict),
            'Invalid reusable component definitions')
        require(row['key'] in definitions['definitions'],'Missing reusable component definition')
        component=json_document(json.dumps(definitions['definitions'][row['key']],ensure_ascii=False))
        require(isinstance(component,dict) and 'id' not in component,'Reusable component identity belongs to the model')
        component['id']=row['id']; package['components'].append(component)
    model=dict(model,package=package)
    return model


def definition_sources(adaptations=ADAPTATIONS):
    names=set()
    paths = device_sources(adaptations) or [path for path in (Path(adaptations)/'brands').glob('*/*/*.toon')
                                            if path.name == path.parent.name+'.toon']
    for path in paths:
        model=load(path,adaptations); package=model.get('package',{})
        if package.get('kind')=='BoilerPackageAssembly':
            for row in package.get('components',[]): names.add(relative(row['definition']))
    return names


def runtime_metadata(model, manifest):
    value=model['runtime']
    require(isinstance(value,dict) and set(value)=={'id','name','hardware_matching'}
        and value['id']=='device:'+model['id'].split(':',1)[1],'Invalid model runtime identity')
    def text(value,maximum=512):
        return isinstance(value,str) and 0<len(value)<=maximum and value.strip() and not any(ord(c)<32 or 127<=ord(c)<=159 for c in value)
    require(isinstance(value['name'],dict) and set(value['name'])=={'en','zh'}
        and all(text(v) for v in value['name'].values()),'Invalid bilingual runtime model name')
    hardware=value['hardware_matching']
    require(isinstance(hardware,dict) and set(hardware)=={'mode','pairs'} and hardware['mode']=='advisory-exact-dmi-v1'
        and isinstance(hardware['pairs'],list) and 1<=len(hardware['pairs'])<=32,'Invalid advisory runtime hardware metadata')
    seen=set()
    for pair in hardware['pairs']:
        require(isinstance(pair,dict) and set(pair)=={'manufacturer','model'} and all(text(v,256) for v in pair.values()),'Invalid runtime DMI pair')
        key=(pair['manufacturer'],pair['model'])
        require(key not in seen,'Duplicate runtime DMI pair'); seen.add(key)
    return dict(value,guide_id=model['id'],manifest=manifest)


def validate_model(model, path):
    required = {'schema_version','kind','id','name','installable','guide','source_inventory','source_revision','components','package','build','runtime'}
    v2 = 'canonical_id' in model
    if v2:
        required |= {'canonical_id','legacy_aliases','device_record','device_record_path','resource_model'}
    require(set(model) in (required,required|{'resource_model'}),'Unknown or missing model fields: '+str(path))
    if 'resource_model' in model:
        resource=model['resource_model']
        if v2:
            runtime=model['runtime']
            require(isinstance(runtime,dict) and isinstance(runtime.get('name'),dict), 'Invalid runtime projection')
            require('name' not in resource and 'hardware_matching' not in resource, 'Duplicate resource runtime metadata')
            resource=dict(resource,name=runtime['name'].get('en'),hardware_matching=runtime.get('hardware_matching'))
        resource=validate_resource_model_source(resource,path)
        require(resource['id']==model.get('id','').replace('guide:','device:',1),'Resource/guide identity differs')
    require(type(model['schema_version']) is int and model['schema_version']==1 and model['kind']=='ModelInstallGuide'
        and model['installable'] is False,'Source guide must remain non-installable')
    require(isinstance(model['id'],str) and re.fullmatch('guide:[a-z0-9][a-z0-9-]*',model['id']),'Invalid model ID')
    parts = path.parts
    if len(parts) == 3 and parts[0] == 'devices' and path.suffix == '.toon':
        require(v2, 'Device model source must declare a branded identity')
        parts = ('brands', parts[1], path.stem, path.name)
    require(len(parts)==4 and parts[0]=='brands' and parts[3]==parts[2]+'.toon','Expected devices/vendor/model.toon or a historical model path')
    require(all(re.fullmatch('[a-z][a-z0-9-]*',part) for part in parts[1:3]),'Invalid vendor/model directory')
    if v2:
        canonical='device:'+parts[1]+'-'+parts[2]
        aliases=model['legacy_aliases']
        require(model['canonical_id']==canonical and isinstance(aliases,dict) and set(aliases)=={'guide','package'},'Invalid canonical identity')
        legacy={('onexplayer','oxp3'):('guide:oxp3','device:oxp3'),('onexplayer','x2-mini'):('guide:x2-mini','device:x2-mini')}.get((parts[1],parts[2]))
        require(aliases==({'guide':[legacy[0]],'package':[legacy[1]]} if legacy else {'guide':[],'package':[]}),'Invalid legacy aliases')
        expected_guide=legacy[0] if legacy else 'guide:'+parts[1]+'-'+parts[2]
        require(model['runtime']['id']==(legacy[1] if legacy else canonical),'Runtime identity differs')
    else: expected_guide='guide:'+parts[2]
    require(model['id']==expected_guide and model['guide']=='/'.join(parts[:3])+'/model.md','Model identity/path mismatch')
    runtime_metadata(model,Path(model['guide']).with_name('manifest.json').as_posix())
    relative(model['source_inventory']); relative(model['guide'])
    source=model['source_revision']
    require(isinstance(source,dict) and source.get('kind')=='github' and isinstance(source.get('repository'),str)
        and re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',source['repository'])
        and re.fullmatch('[0-9a-f]{40}',source.get('commit','')),'Exact public source provenance required')
    require(isinstance(model['components'],list) and model['components'],'Component list required')
    components={}
    fields={'id','name','recommendation','default','dependencies','sources','status','notes'}
    for row in model['components']:
        require(isinstance(row,dict) and set(row)==fields,'Invalid component fields')
        require(isinstance(row['id'],str) and re.fullmatch('[a-z][a-z0-9-]*',row['id']) and row['id'] not in components,'Duplicate/invalid component ID')
        require(row['recommendation'] in LEVELS and (row['default'] is False or (row['default'] is True and model['id'] in ('guide:oxp3', 'guide:x2-mini') and row['id']=='fingerprint-unlock')),'Guide recommendation cannot enable installation defaults')
        require(all(isinstance(row[k],str) and row[k] for k in ('name','status','notes')),'Missing component description/status')
        for key in ('dependencies','sources'):
            require(isinstance(row[key],list) and all(isinstance(v,str) for v in row[key]) and len(set(row[key]))==len(row[key]),'Invalid component '+key)
        for name in row['sources']: relative(name)
        require(row['recommendation']!='pending' or not row['sources'],'Missing implementation cannot claim sources')
        components[row['id']]=row
    for row in components.values():
        for name in row['dependencies']:
            require(name in components and components[name]['recommendation']!='excluded','Missing/excluded component dependency')
    visiting,done=set(),set()
    def visit(name):
        require(name not in visiting,'Cyclic component dependencies')
        if name in done: return
        visiting.add(name)
        for dep in components[name]['dependencies']: visit(dep)
        visiting.remove(name); done.add(name)
    for name in components: visit(name)
    build=model['build']
    require(isinstance(build,dict) and build.get('adapter') in ADAPTERS,'Invalid build adapter')
    expected_fields={'adapter','complete','missing','materials','inputs'}
    if isinstance(build,dict) and build.get('adapter')=='oxp3-current-v1': expected_fields.add('plugin_pins')
    require(isinstance(build,dict) and set(build)==expected_fields,'Invalid build fields')
    require(build['adapter'] in ADAPTERS and type(build['complete']) is bool and isinstance(build['missing'],list)
        and all(isinstance(v,str) and v for v in build['missing']),'Invalid build adapter/completeness')
    require(not build['complete'] or not build['missing'],'Complete model cannot declare missing implementation')
    if build['adapter']=='oxp3-current-v1':
        pins=build['plugin_pins']
        require(isinstance(pins,dict) and set(pins)=={'lock_sha256','sha256','source_commit','size'}
            and all(isinstance(pins[k],str) and re.fullmatch('[0-9a-f]{64}',pins[k]) for k in ('lock_sha256','sha256'))
            and isinstance(pins['source_commit'],str) and re.fullmatch('[0-9a-f]{40}',pins['source_commit'])
            and type(pins['size']) is int and pins['size']>0,'Invalid independent plugin pins')
    if build['adapter'] in ('x2-current-v1','x2-complete-v1'):
        require(parts[1:3]==('onexplayer','x2-mini'),'X2 adapter requires the ONEXPLAYER X2 Mini model')
    if parts[1:3]==('onexplayer','x2-mini'):
        require(build['adapter'] in ('shared-v1','x2-current-v1','x2-complete-v1'),'Invalid X2 build adapter')
        if build['adapter'] != 'x2-complete-v1':
            require(build['complete'] is False and build['missing'],'Partial X2 package must declare missing features')
        else:
            require(build['complete'] is True and not build['missing'],'Complete X2 adapter must declare its full scope')
    require(isinstance(build['materials'],list) and build['materials'],'Locked source material required')
    locks=set()
    for row in build['materials']:
        require(isinstance(row,dict) and set(row)=={'scope','lock','sha256'},'Invalid material record')
        require(row['scope'] in ('common','brand','model','external'),'Invalid material scope')
        relative(row['lock']); require(re.fullmatch('[0-9a-f]{64}',row['sha256']),'Invalid material digest')
        require(row['lock'] not in locks,'Duplicate material lock'); locks.add(row['lock'])
    expected=ADAPTERS[build['adapter']][1]
    require(isinstance(build['inputs'],list) and len(build['inputs'])==len(expected),'Adapter input inventory mismatch')
    names=set()
    for row in build['inputs']:
        require(isinstance(row,dict) and set(row)=={'name','kind','path','lock','sha256','note'},'Invalid artifact input fields')
        require(row['name'] in expected and row['name'] not in names,'Unknown/duplicate adapter input')
        names.add(row['name']); relative(row['path']); relative(row['lock'])
        require(row['kind'] in ('package','file','locked-directory') and re.fullmatch('[0-9a-f]{64}',row['sha256'])
            and isinstance(row['note'],str) and row['note'],'Unpinned artifact input')
    if build['adapter']=='oxp3-current-v1':
        by_name={row['name']:row for row in build['inputs']}
        require(by_name['base']['kind']=='package' and by_name['base']['sha256']==OXP_BASE_ANCHOR,
            'Current OXP3 pipeline must start from the reviewed .2 shared base, never a prebuilt .3 or .4')
        require(all(by_name[name]['sha256']==build['plugin_pins']['lock_sha256'] for name in ('plugin_zip','plugin_lock')),
            'Plugin input lock and independent TOON pin differ')
        require(all(by_name[name]['kind']=='file' and by_name[name]['lock']==HISTORICAL_INPUT_LOCK
            and by_name[name]['sha256']==HISTORICAL_INPUT_LOCK_SHA256
            for name in ('historical_plugin_zip','optiscaler_094','optiscaler_079','optiscaler_nightly')),
            'Historical public artifact inputs must use the reviewed public lock')
    if build['adapter']=='x2-complete-v1':
        by_name={row['name']:row for row in build['inputs']}
        require(parts[1:3]==('onexplayer','x2-mini') and by_name['base']['kind']=='package'
            and by_name['base']['sha256']==OXP_FULL_ANCHOR,
            'Complete X2 build requires the reviewed full OXP3 base')
        require(all(by_name[name]['kind']=='locked-directory'
            and by_name[name]['lock']==by_name[name]['path']+'/overlay-lock.json'
            for name in ('bridge_overlay','hdr_overlay')),
            'Complete X2 build requires explicit locked overlays')
    package=model['package']
    require(isinstance(package,dict),'Executable package definition or inherited component references required')
    identity=model['runtime']['id'] if v2 else 'device:'+parts[2]
    if package.get('kind')=='DevicePackage':
        require(package.get('id')==identity and package.get('schema_version')==1 and isinstance(package.get('components'),list)
            and package['components'],'Invalid executable package definition')
        ids=set(); package_targets=set()
        for component in package['components']:
            require(isinstance(component,dict) and component.get('kind')=='ComponentManifest'
                and isinstance(component.get('id'),str) and component['id'].startswith(parts[2]+':')
                and re.fullmatch('[a-z0-9-]+:[a-z0-9-]+',component['id']) and component['id'] not in ids,
                'Duplicate/invalid executable component')
            ids.add(component['id'])
            require(not component.get('hooks') and component.get('default') is False,'TOON package uses static opt-in components')
            if component.get('dynamic_handler') or (parts[1:3]==('onexplayer','x2-mini') and component['id']=='x2-mini:keyboard'):
                sys.path.insert(0,str(ROOT))
                from maintenance.keyboard_contract import check
                check(component)
            else:
                require(isinstance(component.get('files'),list) and component['files'],'Executable file records required')
            targets=set()
            for row in component['files']:
                require(isinstance(row,dict) and {'source','target','size','sha256'}<=set(row),'Invalid executable file record')
                relative(row['source']); guest_target(row['target'])
                require(row['target'].casefold() not in targets and type(row['size']) is int and row['size']>=0
                    and isinstance(row['sha256'],str) and re.fullmatch('[0-9a-f]{64}',row['sha256'])
                    and type(row.get('mode',420)) is int and 0<=row.get('mode',420)<=0o777,'Invalid executable target/hash/mode')
                require(row['target'].casefold() not in package_targets,'Duplicate executable target across components')
                targets.add(row['target'].casefold())
                package_targets.add(row['target'].casefold())
        require(not build['complete'] or package.get('delivery_scope')!='partial-shared-adaptations','Partial package cannot be a complete model')
    else:
        require(set(package)=={'kind','base_input','inherited_components','inherited_legacy_capabilities','definitions'}
            and package['kind']=='BoilerInheritedPackageDefinition' and package['base_input'] in names,'Invalid inherited package definition')
        for key in ('inherited_components','inherited_legacy_capabilities'):
            require(isinstance(package[key],list) and all(isinstance(v,str) and re.fullmatch('[a-z0-9-]+:[a-z0-9-]+',v) for v in package[key])
                and len(set(package[key]))==len(package[key]),'Invalid inherited component references')
        for row in package['definitions']:
            require(isinstance(row,dict) and set(row)=={'id','source','lock','sha256'},'Invalid reusable component definition reference')
            relative(row['source']); relative(row['lock'])
            require(re.fullmatch('[a-z0-9-]+:[a-z0-9-]+',row['id']) and re.fullmatch('[0-9a-f]{64}',row['sha256']),
                'Invalid reusable component definition identity/lock')
        if build['adapter']=='x2-complete-v1':
            require(package['base_input']=='base' and not package['definitions'],
                'Complete X2 build inherits the full base package')
            require(all(name.startswith('oxp3:') and name.split(':',1)[1] in components
                for name in package['inherited_legacy_capabilities']),
                'Every inherited OXP3 capability must be listed in the X2 model guide')
    return model


def validate_resource_model_source(value, path):
    required={'schema_version','kind','id','name','hardware_matching','parameters','resources'}
    require(isinstance(value,dict) and required <= set(value) and not set(value)-required-{'hdr','common_references'}, 'Invalid model resource source fields: '+str(path))
    require(type(value['schema_version']) is int and value['schema_version']==1 and value['kind']=='BoilerModelResourceSource', 'Invalid model resource source schema: '+str(path))
    require(isinstance(value['id'],str) and re.fullmatch(r'device:[a-z][a-z0-9-]*',value['id']) and isinstance(value['name'],str) and value['name'].strip() and isinstance(value['parameters'],dict), 'Invalid resource model identity')
    matching=value['hardware_matching'];require(isinstance(matching,dict) and set(matching)=={'mode','pairs'} and matching['mode']=='advisory-exact-dmi-v1' and isinstance(matching['pairs'],list) and len(matching['pairs'])<=32 and all(isinstance(x,dict) and set(x)=={'manufacturer','model'} and all(isinstance(x[k],str) and x[k].strip() for k in ('manufacturer','model')) for x in matching['pairs']), 'Invalid advisory model names')
    rows=value['resources'];require(isinstance(rows,list) and bool(rows) and all(isinstance(r,dict) and 'id' in r and set(r)<={'id','default'} and ('default' not in r or type(r['default']) is bool) and isinstance(r['id'],str) and re.fullmatch(r'resource:[a-z0-9][a-z0-9-]*',r['id']) for r in rows), 'Invalid resource references')
    ids=[r['id'] for r in rows];require(len(ids)==len(set(ids)),'Duplicate resource reference')
    if 'hdr' in value:
        hdr=value['hdr'];require(isinstance(hdr,dict) and set(hdr)=={'profile','full_frame','peak'} and isinstance(hdr['profile'],str) and type(hdr['full_frame']) is int and type(hdr['peak']) is int, 'Invalid resource HDR settings')
    return value


def models(adaptations=ADAPTATIONS):
    adaptations=Path(adaptations); result=[]; seen=set(); resource_seen=set()
    root=adaptations/'brands'
    current=device_sources(adaptations)
    if current:
        for path in current:
            name=path.relative_to(adaptations)
            local(adaptations,name.as_posix())
            model=validate_model(expand_package(load(path,adaptations),adaptations),name)
            reference=model['resource_model'].get('common_references')
            reference_path=Path(model['guide']).with_name('common-device-sources.toon').as_posix()
            if reference is not None:
                require(load(local(adaptations,reference_path),adaptations)==reference,
                        'Generated common references differ from model TOON')
            ids={model['id'],model['canonical_id'],*model['legacy_aliases']['package']}
            require(not ids.intersection(seen),'Conflicting canonical or package identity')
            seen.update(ids)
            result.append((name.as_posix(),model))
        require(not any(p.name == p.parent.name+'.toon' for p in root.glob('*/*/*.toon')),
                'Both current and historical model sources are present')
        index=load(local(adaptations,'models.toon'),adaptations)
        expected=[dict(id=model['canonical_id'],source=name) for name,model in result]
        require(index==dict(schema_version=2,kind='BoilerModelSourceIndex',generated=True,models=expected),
                'Model TOON index differs from device sources')
        return result
    # A differently nested/renamed model TOON must fail visibly, rather than silently
    # disappearing from the generated catalog.
    for path in sorted(root.rglob('*.toon')):
        relative_name=path.relative_to(adaptations)
        if (len(relative_name.parts)==4 and relative_name.parts[2:] == ('common','components.toon')):
            continue
        local(adaptations,relative_name.as_posix())
        if len(relative_name.parts)==4 and relative_name.name=='common-device-sources.toon':
            guide_name=relative_name.with_name(relative_name.parts[2]+'.toon')
            guide=load(adaptations/guide_name,adaptations)
            require(load(path,adaptations)==guide.get('resource_model',{}).get('common_references'),
                'Generated common references differ from model TOON')
            continue
        model=validate_model(expand_package(load(path,adaptations),adaptations),relative_name)
        if (adaptations/'model-source-files.json').is_file():
            require('resource_model' in model,'Canonical model lacks resource declarations')
        require(model['id'] not in seen,'Duplicate model identity'); seen.add(model['id'])
        if 'canonical_id' in model:
            identities={model['canonical_id'],*model['legacy_aliases']['package']}
            require(not identities.intersection(resource_seen),'Conflicting canonical or package identity')
            resource_seen.update(identities)
        result.append((relative_name.as_posix(),model))
    require(result,'No model TOON sources found')
    return result


def check_sources(root, model, adaptations=None):
    """Source guide validation never requires binary installation artifacts."""
    root=Path(root); adaptations=Path(adaptations) if adaptations is not None else root/'adaptations'; sources=set(); locks={}
    # Current canonical snapshots preserve historical lock bytes and record
    # the one approved BMI source relocation separately.
    relocation={}
    index_path=adaptations/'model-source-files.json'
    if index_path.is_file():
        index=json_document(index_path.read_bytes())
        require(index.get('kind')=='CanonicalAdaptationSourceFiles' and index.get('schema_version')==1,'Invalid canonical source inventory')
        for item in index['files']:
            original=local(adaptations,item['path'])
            require(original.is_file() and original.stat().st_size==item['size'] and sha(original)==item['sha256'],
                'Canonical source differs from inventory: '+item['path'])
            sources.add(item['path'])
        migration=json_document(local(adaptations,'records/migrations/bmi260-source-relocation-20261103.json').read_bytes())
        require(migration.get('kind')=='BoilerSourceRelocation' and migration.get('schema_version')==1,'Invalid BMI source relocation')
        for item in migration['files']:
            old=item['old_path'];new=item['new_path']
            require(old.startswith('brands/onexplayer/common/scripts/gyro/') and new=='common/sensors/bosch/bmi260/gyro/sources/'+old.rsplit('/',1)[1]
                and old not in relocation and not local(adaptations,old).exists(),'Invalid BMI source relocation member')
            target=local(adaptations,new)
            require(target.is_file() and target.stat().st_size==item['size'] and sha(target)==item['sha256'],'BMI relocation bytes differ')
            relocation[old]=new
    for row in model['build']['materials']:
        path=(local(adaptations,row['lock'].removeprefix('adaptations/'))
              if row['lock'].startswith('adaptations/') else local(root,row['lock']))
        require(path.is_file() and sha(path)==row['sha256'],'Missing/changed source lock: '+row['lock'])
        lock=json_document(path.read_bytes()); locks[row['lock']]=lock
        # Reviewed inventories use paths relative to adaptations. No recursive
        # scan imports sibling sources or changes the fixed public CPU baseline.
        for item in lock.get('files',[]):
            if isinstance(item,dict) and {'path','size','sha256'}<=set(item):
                original=local(adaptations,relocation.get(item['path'],item['path']))
                require(type(item['size']) is int and original.is_file() and original.stat().st_size==item['size']
                    and sha(original)==item['sha256'],'Source differs from reviewed inventory: '+item['path'])
                sources.add(item['path'])
        if lock.get('kind')=='X2SharedSourceLock':
            require(isinstance(lock.get('inputs'),list) and lock['inputs'],'X2 byte source inventory required')
            for item in lock['inputs']:
                require(isinstance(item,dict) and {'location','source','output','size','sha256'}<=set(item)
                    and item['location'] in ('project','bridge'),'Invalid X2 source lock input')
                base=root if item['location']=='project' else path.parent
                original=local(base,item['source']); relative(item['output'])
                require(type(item['size']) is int and original.is_file() and original.stat().st_size==item['size']
                    and sha(original)==item['sha256'],'X2 source differs from byte inventory: '+item['source'])
                if original.is_relative_to(adaptations): sources.add(original.relative_to(adaptations).as_posix())
        if lock.get('kind')=='KeyboardRuntimeSourceInventory':
            for item in lock.get('runtime_sources',[]):
                original=local(root,item['source'])
                require(original.is_file() and original.stat().st_size==item['size'] and sha(original)==item['sha256'],
                    'Keyboard host runtime differs from reviewed inventory: '+item['source'])
    baseline=locks.get('adaptations/source-lock.json')
    require(baseline is not None and baseline.get('source')==model['source_revision'],'Guide/source provenance mismatch')
    for component in model['components']:
        for source in component['sources']:
            require(source in sources,'Uninventoried component source: '+source)
    if model['package']['kind']=='BoilerInheritedPackageDefinition':
        for definition in model['package']['definitions']:
            require(definition['lock'] in locks,'Unreviewed reusable component definition lock')
            source=definition['source']
            require(source.startswith('adaptations/') and source.removeprefix('adaptations/') in sources,
                'Reusable component definition is outside reviewed source inventory')
            lock_path=local(adaptations,definition['lock'].removeprefix('adaptations/'))
            require(sha(lock_path)==definition['sha256'],'Reusable definition lock differs')
            record=json_document(local(adaptations,source.removeprefix('adaptations/')).read_bytes())
            require(record.get('id')==definition['id'],'Reusable component definition identity differs')
    return True


def views(adaptations=ADAPTATIONS):
    return catalog_module('model-identity').views(adaptations)


def generate(adaptations=ADAPTATIONS,check=False):
    outputs=views(adaptations)
    for name,raw in outputs.items():
        path=local(adaptations,name)
        if check: require(path.is_file() and path.read_bytes()==raw,'Generated view is stale: '+name)
        else:
            path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)
    return dict(models=len(models(adaptations)),generated=len(outputs),checked=check)


def artifact_path(value):
    """Explicit CLI artifacts may live elsewhere; their anchors stay mandatory."""
    path=Path(value).absolute()
    require(not any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (path,*path.parents)),
        'Linked explicit artifact input')
    return path


def check_directory_input(row, path, lock, root):
    """Fail before the expensive first stage when fixed native inputs are absent."""
    pin=json_document(lock.read_bytes())
    if row['name']=='decky':
        manifest=artifact_path(path/'manifest.json')
        require(manifest.is_file(),'Prepared Decky manifest missing; run scripts/prepare-decky.py with the explicit raw input first')
        metadata=json_document(manifest.read_bytes())
        require(metadata.get('id')=='core:decky' and metadata.get('available') is True and metadata.get('version')==pin['version'],
            'Prepared Decky identity/version differs from reviewed lock')
        expected={'decky/PluginLoader':pin['file']}
        expected.update({'sources/'+entry['source']:entry for entry in pin['notices']})
        license=local(root,'components/licenses/decky/LICENSE')
        expected['sources/LICENSE']=dict(size=license.stat().st_size,sha256=sha(license))
        records=metadata.get('files',[])+metadata.get('notices',[])
        require(len(records)==len(expected) and {entry['source'] for entry in records}==set(expected),'Prepared Decky inventory differs')
        for entry in records:
            data=local(path,entry['source']); original=expected[entry['source']]
            require(entry['size']==original['size'] and entry['sha256']==original['sha256'] and data.is_file()
                and data.stat().st_size==original['size'] and sha(data)==original['sha256'],'Prepared Decky bytes differ: '+entry['source'])
    elif row['name']=='xevma':
        module=pin['module']; name=relative(module['file']); data=artifact_path(path/name)
        require(data.is_file() and data.stat().st_size==module['size'] and sha(data)==module['sha256'],
            'Exact xevma module is missing or differs from reviewed lock')


def preflight(root, model, complete=True, input_paths=None):
    errors=[]; build=model['build']; resolved={}
    input_paths=input_paths or {}
    known={row['name'] for row in build['inputs']}
    require(set(input_paths)<=known,'Unknown explicit input mapping')
    overrides={name:artifact_path(value if Path(value).is_absolute() else Path(root)/value) for name,value in input_paths.items()}
    aliases={row['path']:overrides[row['name']] for row in build['inputs'] if row['name'] in overrides}
    if build['adapter'] in ('oxp3-current-v1','x2-complete-v1'):
        errors+=['Explicit --input mapping required: '+name for name in sorted(known-set(overrides))]
    if complete and not build['complete']: errors+=['Missing implementation: '+v for v in build['missing']]
    scripts=[adapter(model)[0]]
    if build['adapter']=='oxp3-current-v1': scripts += ['adaptations/scripts/build-upstream-repairs.py','adaptations/scripts/build-shared-base.py']
    for name in scripts:
        if not local(root,name).is_file(): errors.append('Missing reviewed fixed build entry: '+name)
    try: check_sources(root,model)
    except (OSError,ValueError) as exc: errors.append(str(exc))
    for row in build['inputs']:
        try:
            path=overrides.get(row['name']) or local(root,row['path'])
            if row['kind']=='package' and row['lock']==row['path']+'/manifest.json':
                lock=artifact_path(path/'manifest.json')
            elif (build['adapter']=='x2-complete-v1' and row['kind']=='locked-directory'
                  and row['lock']==row['path']+'/overlay-lock.json'):
                lock=artifact_path(path/'overlay-lock.json')
            else:
                lock=aliases.get(row['lock']) or local(root,row['lock'])
            require(lock.is_file() and sha(lock)==row['sha256'],'Missing/changed input lock: '+row['lock'])
            require(path.is_file() if row['kind']=='file' else path.is_dir(),'Missing explicit artifact: '+row['path']+'; '+row['note'])
            if row['kind']=='package':
                require((path/'manifest.json').resolve()==lock.resolve(),'Package lock must be its exact manifest')
                sys.path.insert(0,str(root))
                from protocol.manifest import validate_package
                validate_package(path,expected_manifest_sha256=row['sha256'])
            elif row['kind']=='locked-directory':
                check_directory_input(row,path,lock,root)
            resolved[row['name']]=path
        except (OSError,ValueError) as exc: errors.append(str(exc))
    if build['adapter']=='oxp3-current-v1' and 'plugin_zip' in resolved:
        pins=build['plugin_pins']; path=resolved['plugin_zip']
        if path.stat().st_size!=pins['size'] or sha(path)!=pins['sha256']:
            errors.append('Plugin ZIP differs from independent TOON size/SHA-256 pin')
    if build['adapter']=='oxp3-current-v1':
        try:
            lock=local(root,HISTORICAL_INPUT_LOCK)
            require(sha(lock)==HISTORICAL_INPUT_LOCK_SHA256,'Historical public input lock differs')
            pins=json_document(lock.read_bytes())
            expected={'historical_plugin_zip':pins['plugin_archive']}
            expected.update(dict(zip(('optiscaler_094','optiscaler_079','optiscaler_nightly'),pins['optiscaler_release']['archives'])))
            for name,pin in expected.items():
                if name in resolved:
                    path=resolved[name]
                    require(path.stat().st_size==pin['size_bytes'] and sha(path)==pin['sha256'],
                        'Historical public artifact size/SHA-256 differs: '+name)
        except (OSError,ValueError) as exc: errors.append(str(exc))
    return resolved,errors


def build(root, model, output, complete=True, executor=None, input_paths=None):
    root=Path(root).absolute(); output=Path(output).absolute()
    require(not output.exists() and not output.is_symlink(),'Use a new output directory')
    require(not any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (output,*output.parents)),'Linked output path')
    resolved,errors=preflight(root,model,complete,input_paths=input_paths)
    require(not errors,'\n'.join(errors))
    script,arguments=adapter(model)
    command=[sys.executable,'-I','-B',str(local(root,script))]
    if model['build']['adapter'] in ('x2-current-v1','x2-complete-v1'): command+=['--project-root',str(root)]
    for name,flag in arguments.items(): command += [flag,str(resolved[name])]
    command += ['--output',str(output)]
    if model['build']['adapter']=='oxp3-current-v1':
        require(executor is None,'Pipeline execution must verify its actual intermediate package')
        pins=model['build']['plugin_pins']
        output.mkdir(parents=True)
        historical=[sys.executable,'-I','-B',str(local(root,'adaptations/scripts/build-shared-base.py')),
            '--project-root',str(root),'--base',str(resolved['base']),
            '--plugin-zip',str(resolved['historical_plugin_zip'])]
        for name in ('optiscaler_094','optiscaler_079','optiscaler_nightly'):
            historical+=['--optiscaler-archive',str(resolved[name])]
        historical+=['--output',str(output/'shared')]
        subprocess.run(historical,cwd=root,check=True)
        sys.path.insert(0,str(root))
        from protocol.manifest import validate_package
        shared=output/'shared/package'
        package=validate_package(shared,expected_manifest_sha256=OXP_SHARED_ANCHOR)
        require(package['id']=='device:oxp3' and package['version']==OXP_SHARED_VERSION,'Unexpected shared .3 intermediate')
        first=[sys.executable,'-I','-B',str(local(root,script)),'--project-root',str(root),
            '--base',str(shared),'--plugin-zip',str(resolved['plugin_zip']),
            '--plugin-lock',str(resolved['plugin_lock']),'--version',OXP_CURRENT_VERSION,
            '--expected-plugin-lock-sha256',pins['lock_sha256'],'--expected-plugin-sha256',pins['sha256'],
            '--expected-plugin-source-commit',pins['source_commit'],'--output',str(output/'opti')]
        subprocess.run(first,cwd=root,check=True)
        sys.path.insert(0,str(root))
        from protocol.manifest import validate_package
        intermediate=output/'opti/package'
        package=validate_package(intermediate,expected_manifest_sha256=OXP_CURRENT_ANCHOR)
        require(package['id']=='device:oxp3' and package['version']==OXP_CURRENT_VERSION,'Unexpected current Opti intermediate')
        second=[sys.executable,'-I','-B',str(local(root,'adaptations/scripts/build-upstream-repairs.py')),
            '--base',str(intermediate),'--decky',str(resolved['decky']),'--xevma',str(resolved['xevma']),
            '--output',str(output/'final')]
        subprocess.run(second,cwd=root,check=True)
        report_path=output/'final/verification.json'
    else:
        if executor is not None: return executor(command,root)
        subprocess.run(command,cwd=root,check=True)
        report_path=output/'verification.json'
    # Existing adapters verify/import their ZIPs, but this boundary also binds
    # the selected model to the actual report and exact final archive bytes.
    report=json_document(report_path.read_bytes())
    candidates=report.get('packages',[report])
    identity='device:'+model['id'].split(':',1)[1]
    candidates=[row for row in candidates if row.get('id',identity)==identity]
    require(len(candidates)==1,'Builder did not produce exactly one selected model')
    selected=candidates[0]
    if model['build']['adapter'] in ('x2-current-v1','x2-complete-v1'):
        if model['build']['adapter']=='x2-complete-v1':
            require(selected.get('full_x2_adaptation_complete') is True,'X2 complete builder must prove its scope')
        else:
            require(selected.get('full_x2_adaptation_complete') is False,'X2 builder must preserve incomplete scope')
        selected=dict(selected,directory=str(output/'package'),path=str(output/relative(selected['archive'])))
    directory=Path(selected['directory']).absolute(); archive=Path(selected['path']).absolute()
    require(directory.is_relative_to(output) and archive.is_relative_to(output),'Builder output escapes destination')
    local(output,directory.relative_to(output).as_posix()); local(output,archive.relative_to(output).as_posix())
    require(archive.is_file() and archive.stat().st_size==selected['size'] and sha(archive)==selected['sha256'],'Built archive differs from report')
    sys.path.insert(0,str(root))
    from protocol.manifest import validate_package
    manifest=validate_package(directory,expected_manifest_sha256=selected['manifest_sha256'])
    require(manifest['id']==identity,'Built model identity differs')
    definition=model['package']
    if definition['kind']=='DevicePackage':
        require(manifest['components']==definition['components'],'Built executable components/files differ from authoritative TOON')
    else:
        expected=set(definition['inherited_components'])|{row['id'] for row in definition['definitions']}
        require({row['id'] for row in manifest['components']}==expected,'Built inherited/reusable component set differs from TOON')
        require({row['id'] for row in manifest.get('legacy_capabilities',[])}==set(definition['inherited_legacy_capabilities']),
            'Built legacy capability set differs from TOON')
    result=dict(status='built',id=identity,version=manifest['version'],complete=model['build']['complete'],
        official_release=False,hardware_accepted=False,adapter=model['build']['adapter'],
        directory=directory.relative_to(output).as_posix(),archive=archive.relative_to(output).as_posix(),
        size=selected['size'],sha256=selected['sha256'],manifest_sha256=selected['manifest_sha256'])
    (output/'model-build-result.json').write_bytes(json_bytes(result))
    return result


def canonical_build(root, model, output, input_paths, base_manifest_sha256, version=None):
    """Build one local DevicePackage from the selected authoritative guide."""
    root=Path(root).resolve();output=Path(output).absolute()
    require(set(input_paths)=={'base','stage','public-installer'},
        'Canonical build requires explicit base, stage and public-installer inputs')
    require(isinstance(base_manifest_sha256,str) and re.fullmatch('[0-9a-f]{64}',base_manifest_sha256),
        'Canonical build requires the exact base manifest SHA-256')
    check_sources(root,model)
    sys.path[:0]=[str(root),str(root/'scripts')]
    from resources import model_source
    model_source.sources(root)
    from protocol.manifest import validate_package
    base=artifact_path(input_paths['base']);stage=artifact_path(input_paths['stage']);public=artifact_path(input_paths['public-installer'])
    identity=model['resource_model']['id']
    require(validate_package(base,expected_manifest_sha256=base_manifest_sha256)['id']==identity,
        'Selected guide and base package identity differ')
    spec=importlib.util.spec_from_file_location('_canonical_model_finalizer',local(root,'scripts/finalize-canonical-model.py'))
    implementation=importlib.util.module_from_spec(spec);spec.loader.exec_module(implementation)
    content,stage_proof=implementation.stage_snapshot(stage)
    snapshot=json_document(local(root,model_source.SNAPSHOT).read_bytes())
    require(stage_proof.get('source_commit')==snapshot['commit'],
        'Canonical build requires the current canonical snapshot stage')
    reference,_=implementation.stage_reference(content,identity)
    require(reference==model['resource_model']['common_references'],'Selected guide and canonical stage differ')
    options={} if version is None else {'version':version}
    result=implementation.build(base,base_manifest_sha256,stage,public,output,**options)
    require(result.get('id')==identity and result.get('official_release') is False,
        'Canonical builder returned a different model or trust state')
    return result


def main():
    if sys.argv[1:2] == ['index-candidate']:
        return catalog_module('build-index').main(sys.argv[2:])
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('generate','check','legacy-json','index-candidate','preflight','build','canonical-build'))
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--catalog-root',type=Path,default=ADAPTATIONS)
    parser.add_argument('--model',help='Canonical device ID or declared historical guide/package ID')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--allow-incomplete',action='store_true')
    parser.add_argument('--base-manifest-sha256')
    parser.add_argument('--version')
    parser.add_argument('--input',action='append',default=[],metavar='NAME=PATH',
        help='Explicit authenticated artifact mapping; never persisted to TOON')
    args=parser.parse_args()
    input_paths={}
    for item in args.input:
        require('=' in item,'Expected --input NAME=PATH')
        name,path=item.split('=',1)
        require(name and path and name not in input_paths,'Empty/duplicate explicit input mapping')
        input_paths[name]=path
    if args.action == 'legacy-json':
        require(args.output is not None,'Legacy JSON generation requires --output')
        output=artifact_path(args.output)
        require(not output.exists(),'Use a fresh legacy output directory')
        result=catalog_module('model-identity').protocol_views(args.catalog_root)
        output.mkdir(parents=True)
        for name, raw in result.items():
            path=local(output,name)
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw)
        result={'generated':sorted(result)}
    elif args.action in ('generate','check'):
        result=generate(args.catalog_root,check=args.action=='check')
        if args.action=='check':
            for _,model in models(args.catalog_root): check_sources(args.root,model,args.catalog_root)
    else:
        selected=[]
        for name,model in models(args.catalog_root):
            identities={model['id']}
            if 'canonical_id' in model:
                identities.add(model['canonical_id'])
                identities.update(model['legacy_aliases']['guide'])
                identities.update(model['legacy_aliases']['package'])
            else:
                legacy={'brands/onexplayer/oxp3/oxp3.toon':'device:oxp3',
                        'brands/onexplayer/x2-mini/x2-mini.toon':'device:x2-mini'}
                if name in legacy: identities.add(legacy[name])
            if args.model in identities: selected.append(model)
        require(len(selected)==1,'Select one uniquely known --model identity')
        if args.action=='canonical-build':
            require(args.output is not None,'Canonical build requires --output')
            require(Path(args.root).resolve()==ROOT.resolve() and Path(args.catalog_root).resolve()==ADAPTATIONS.resolve(),
                'Canonical build must use the current checked source snapshot')
            result=canonical_build(args.root,selected[0],args.output,input_paths,args.base_manifest_sha256,version=args.version)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        if args.action=='preflight':
            _,errors=preflight(args.root,selected[0],complete=not args.allow_incomplete,input_paths=input_paths)
            result=dict(ready=not errors,blockers=errors)
            print(json.dumps(result,ensure_ascii=False,indent=2)); return 1 if errors else 0
        require(args.output is not None,'Build requires --output')
        result=build(args.root,selected[0],args.output,complete=not args.allow_incomplete,input_paths=input_paths)
    print(json.dumps(result,ensure_ascii=False,indent=2)); return 0


if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:
        print(str(exc),file=sys.stderr); raise SystemExit(1)
