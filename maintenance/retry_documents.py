"""Small retry references bound to immutable root-private action documents."""
import copy,hashlib,json,re
from pathlib import Path
from .adoption_blobs import _directory,_file
from .engine import _json_write
from protocol.manifest import _decode_json,MAX_MANIFEST

FIELDS=('kind','schema_version','token','generation','group','phase','status')
PHASES=('prepared','installing','files_committed','publishing','states','closing','closed')

def _identity(value):
    if (not isinstance(value,dict) or value.get('kind')!='BoilerResourceRetryAction'
            or type(value.get('schema_version')) is not int or value['schema_version']!=1
            or any(not isinstance(value.get(k),str) or not re.fullmatch('[0-9a-f]{32}',value[k]) for k in ('token','group'))
            or not isinstance(value.get('generation'),str) or not 1<=len(value['generation'])<=128
            or value.get('phase') not in PHASES or value.get('status') not in ('reinstalling','closed')
            or (value['phase']=='closed')!=(value['status']=='closed')):
        raise ValueError('Invalid retry action identity')

def _bytes(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def _path(owner,ref):
    digest=ref.get('document_sha256')
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid retry document digest')
    return Path(owner).parent/'retry-documents'/(ref['token']+'-'+digest+'.json')

def compact(value,owner):
    _identity(value)
    if 'document_sha256' in value:raise ValueError('Cannot store an unresolved retry reference')
    raw=_bytes(value)
    if len(raw)>MAX_MANIFEST:raise ValueError('Retry action document exceeds its bound')
    ref={k:copy.deepcopy(value[k]) for k in FIELDS};ref['document_sha256']=hashlib.sha256(raw).hexdigest()
    path=_path(owner,ref)
    with _directory(path.parent,create=True):pass
    if path.exists() or path.is_symlink():
        existing=hydrate(ref,owner)
        if existing!=value:raise ValueError('Immutable retry document changed')
    else:
        _json_write(path,value)
        hydrate(ref,owner) # Confirm the durable file before publishing its reference.
    return ref

def hydrate(ref,owner):
    # Inline records are retained for existing unpublished fixtures; new writes
    # always use compact references. Runtime readers still enforce their bounds.
    if not isinstance(ref,dict) or 'document_sha256' not in ref:return copy.deepcopy(ref)
    _identity(ref)
    if set(ref)!=set(FIELDS)|{'document_sha256'}:raise ValueError('Unexpected retry reference fields')
    path=_path(owner,ref)
    with _file(path,private=True) as (stream,info):
        if info.st_size>MAX_MANIFEST:raise ValueError('Retry action document exceeds its bound')
        raw=stream.read(MAX_MANIFEST+1)
    if len(raw)>MAX_MANIFEST or hashlib.sha256(raw).hexdigest()!=ref['document_sha256']:
        raise ValueError('Retry action document differs from its reference')
    value=_decode_json(raw);_identity(value)
    if any(value.get(k)!=ref[k] for k in FIELDS) or 'document_sha256' in value:
        raise ValueError('Retry action document ownership differs')
    return value
