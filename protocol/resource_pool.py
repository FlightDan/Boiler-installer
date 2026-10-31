"""Validate unsigned local resource candidates. Hash binding never grants trust."""
from __future__ import annotations
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys

_ROOT=Path(__file__).resolve().parents[1]
TOOLS=_ROOT/'adaptations' if (_ROOT/'adaptations/toon-parser-lock.json').is_file() else _ROOT

def require(ok,message):
    if not ok: raise ValueError(message)

def sha(data): return hashlib.sha256(data).hexdigest()

def relative(value):
    require(isinstance(value,str) and value and '\\' not in value and ':' not in value and not value.startswith('/'),'Unsafe path')
    require(all(p not in ('','.','..') and not p.endswith(('.', ' ')) and not any(ord(c)<32 for c in p) for p in value.split('/')),'Unsafe path segment')
    return value

def local(root,name):
    root=Path(root).absolute(); current=root
    require(not root.is_symlink() and not getattr(root,'is_junction',lambda:False)(),'Linked pool root')
    for part in relative(name).split('/'):
        current=current/part
        require(not current.is_symlink() and not getattr(current,'is_junction',lambda:False)(),'Linked pool member')
    require(current.resolve().is_relative_to(root.resolve()),'Escaping pool member')
    return current

def json_document(raw):
    def pairs(rows):
        result={}
        for key,value in rows:
            require(key not in result,'Duplicate JSON key'); result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))

def decoder(tools=None):
    tools = Path(tools) if tools is not None else TOOLS
    lock=json_document((tools/'toon-parser-lock.json').read_bytes())
    require(lock.get('package')=='toon-format' and lock.get('version')=='1.2.0' and lock.get('spec')=='4.4','Unexpected parser lock')
    vendor=tools/'vendor'; sources={}
    for row in lock['files']:
        path=local(vendor,row['path']); raw=path.read_bytes()
        require(len(raw)==row['size'] and sha(raw)==row['sha256'],'Parser member differs from lock')
        if path.suffix=='.py': sources[str(path)]=raw
    require({p.relative_to(vendor).as_posix() for p in vendor.rglob('*') if p.is_file() and p.suffix!='.pyc' and '__pycache__' not in p.parts}=={r['path'] for r in lock['files']},'Uninventoried parser member')
    class Loader(importlib.machinery.SourceFileLoader):
        def get_code(self,fullname): return compile(sources[self.path],self.path,'exec',dont_inherit=True)
    class Finder:
        def find_spec(self,fullname,path=None,target=None):
            if fullname!='toon_format' and not fullname.startswith('toon_format.'): return None
            member=vendor.joinpath(*fullname.split('.'))
            source=member/'__init__.py' if member.is_dir() else member.with_suffix('.py')
            require(str(source) in sources,'Uninventoried parser import')
            return importlib.util.spec_from_file_location(fullname,source,loader=Loader(fullname,str(source)))
    for name in list(sys.modules):
        if name=='toon_format' or name.startswith('toon_format.'): del sys.modules[name]
    finder=Finder(); sys.meta_path.insert(0,finder); sys.path.insert(0,str(vendor))
    try: import toon_format
    finally: sys.meta_path.remove(finder); sys.path.remove(str(vendor))
    return toon_format

def validate_pool(root,expected_catalog_sha256=None):
    root=Path(root); raw=local(root,'catalog.toon').read_bytes()
    if expected_catalog_sha256 is not None: require(sha(raw)==expected_catalog_sha256,'Catalog hash mismatch')
    toon=decoder(); catalog=toon.loads(raw.decode('utf-8'),strict=True)
    require(catalog==json_document(local(root,'catalog.json').read_bytes()),'Derived catalog JSON differs from TOON')
    require(catalog.get('schema_version')==1 and catalog.get('kind')=='BoilerResourceCatalog','Unsupported resource catalog')
    require(catalog.get('trust')=='unsigned-development' and catalog.get('official_release') is False,'Only local development candidates are supported')
    entries={}
    for row in catalog['files']:
        name=relative(row['path']); require(name not in entries,'Duplicate inventory path')
        require(name not in ('catalog.toon','catalog.json'),'Catalog cannot inventory itself')
        data=local(root,name).read_bytes()
        require(len(data)==row['size'] and sha(data)==row['sha256'],'Resource inventory mismatch: '+name)
        entries[name]=row
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    require(actual==set(entries)|{'catalog.toon','catalog.json'},'Uninventoried/missing pool members')
    resources=[]; ids=set()
    for ref in catalog['resources']:
        require(ref['path'] in entries and ref['sha256']==entries[ref['path']]['sha256'],'Resource reference mismatch')
        resource=toon.loads(local(root,ref['path']).read_text(encoding='utf-8'),strict=True)
        require(resource['id']==ref['id'] and resource['version']==ref['version'] and resource['id'] not in ids,'Resource identity mismatch')
        require(resource.get('kind')=='BoilerResource' and resource.get('schema_version')==1,'Unsupported resource')
        require(not resource.get('hooks') and not resource.get('dynamic_handler'),'Executable/unsupported resource')
        if resource['id']=='resource:gamma22-hdr-game':
            from .hdr_contract import validate_contract
            validate_contract(resource)
        if resource.get('install',{}).get('kind')=='files-v1':
            require(resource['verify']=={'kind':'sha256-v1'} and resource['restore']=={'kind':'transaction-backup-v1'},'Unsupported lifecycle')
        elif resource.get('install',{}).get('kind')=='gamma22-hdr-game-v1':
            from .hdr_contract import validate_contract
            validate_contract(resource)
        elif resource.get('install',{}).get('kind')=='intel-g3e-public-cpu-staging-v1':
            from .cpu_contract import validate_contract
            validate_contract(resource)
        else:
            from .firmware_contract import validate_contract
            validate_contract(resource)
        payload={}
        for row in resource['payload']['files']:
            require(row['path'] in entries and row==entries[row['path']] and row['path'] not in payload,'Payload inventory mismatch')
            payload[row['path']]=row
        targets=set()
        for row in resource['install']['files']:
            require(row['source'] in payload,'Install source outside resource payload')
            target=relative(row['target']); require(target not in targets,'Duplicate install target'); targets.add(target)
            require(row['scope']==('shared-home' if target.startswith('home/') else 'root-slots'),'Invalid installation scope')
            allowed_modes=(384,420) if resource['install']['kind']=='intel-g3e-public-cpu-staging-v1' else (420,493)
            require(type(row['mode']) is int and row['mode'] in allowed_modes,'Unsupported file mode')
        if resource['install']['kind'] != 'gamma22-hdr-game-v1':
            require(set(payload)=={r['source'] for r in resource['install']['files']},'Unused payload')
        require(all(p in entries for p in resource['license_files']),'Missing license')
        resources.append(resource); ids.add(resource['id'])
    for resource in resources: require(set(resource['dependencies'])<=ids,'Unknown dependency')
    models=[]
    for ref in catalog['models']:
        require(ref['path'] in entries and ref['sha256']==entries[ref['path']]['sha256'],'Model reference mismatch')
        model=toon.loads(local(root,ref['path']).read_text(encoding='utf-8'),strict=True)
        require(model['id']==ref['id'] and model['kind']=='BoilerResourceModel','Model identity mismatch')
        require(all(r['resource'] in ids for r in model['resources']),'Unknown model resource')
        models.append(model)
    result=dict(catalog); result['resource_refs']=catalog['resources']; result['resources']=resources
    result['model_refs']=catalog['models']; result['models']=models
    return result

def inventory(root):
    validate_pool(root)
    root=Path(root)
    return [dict(path=p.relative_to(root).as_posix(),size=p.stat().st_size,sha256=sha(p.read_bytes())) for p in sorted(root.rglob('*')) if p.is_file()]
