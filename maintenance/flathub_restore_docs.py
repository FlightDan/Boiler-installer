"""Immutable per-slot states for the fixed Flathub restore action."""
import copy,hashlib,json,re,uuid
from pathlib import Path
from .adoption_blobs import _directory,_file
from .engine import _json_write
from .resource_runtime import ResourceError
from protocol.manifest import MAX_MANIFEST,_decode_json
from protocol.flathub_contract import IDENTITY


def _raw(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def create(intent,plan,reviewed_sha256,mounts,marker):
    from . import resource_firstboot as firstboot
    from .resource_lifecycle import digest
    token=uuid.uuid4().hex;slots={}
    for slot,root in mounts.roots.items():
        other=firstboot.checked_intent(root)
        if (any(other.get(k)!=intent.get(k) for k in ('generation','selection','target_roles')) or other['slot']!=slot
                or other.get('root_partuuid','').lower()!=intent['target_roles']['rootfs-'+slot]['partuuid'].lower()):
            raise ResourceError('Flathub restore slot identity changed')
        path=root/firstboot.STATE;exists=path.exists();state=firstboot.read_private(path) if exists else firstboot._new_state(other)
        firstboot.validate_restore_binding(IDENTITY,root,other,state)
        if digest(state)!=plan['slots'][slot]['state_sha256']:raise ResourceError('Flathub slot state changed after review')
        gate=root/'var/lib/boiler-resource-approved/common-flathub-mirror.json'
        gate_value=firstboot.read_private(gate) if gate.exists() else None
        if gate_value is not None and gate_value!={'generation':intent['generation'],'identity':IDENTITY}:
            raise ResourceError('Flathub approval gate belongs to another installation')
        if digest(gate_value)!=plan['slots'][slot]['gate_sha256']:raise ResourceError('Flathub approval gate changed after review')
        after=copy.deepcopy(state)
        after['resources'].setdefault(IDENTITY,{}).update(status='restored',runtime='not_validated',
            restore_receipt=dict(kind='BoilerFlathubRestoreReceipt',token=token))
        after['status']='attention_required';after['maintenance_restore']=dict(identity=IDENTITY,status='restored',reviewed_sha256=reviewed_sha256)
        slots[slot]=dict(before_exists=exists,before=state,after=after,gate_before=gate_value)
    doc=dict(schema_version=1,kind='BoilerFlathubRestoreDocument',token=token,generation=intent['generation'],reviewed_sha256=reviewed_sha256,slots=slots)
    raw=_raw(doc)
    if len(raw)>MAX_MANIFEST:raise ResourceError('Flathub restore document exceeds limit')
    digest_value=hashlib.sha256(raw).hexdigest();folder=Path(marker).parent/'flathub-restore-documents'
    with _directory(folder,create=True):pass
    path=folder/(token+'-'+digest_value+'.json');_json_write(path,doc);ref=dict(token=token,document_sha256=digest_value)
    if load(ref,marker)!=doc:raise ResourceError('Flathub restore document was not retained')
    return ref


def load(ref,marker):
    if (not isinstance(ref,dict) or set(ref)!={'token','document_sha256'} or not re.fullmatch('[0-9a-f]{32}',ref['token'])
            or not re.fullmatch('[0-9a-f]{64}',ref['document_sha256'])):raise ResourceError('Invalid Flathub restore document reference')
    path=Path(marker).parent/'flathub-restore-documents'/(ref['token']+'-'+ref['document_sha256']+'.json')
    with _file(path,private=True) as (stream,info):
        if info.st_size>MAX_MANIFEST:raise ResourceError('Flathub restore document exceeds limit')
        raw=stream.read(MAX_MANIFEST+1)
    if hashlib.sha256(raw).hexdigest()!=ref['document_sha256']:raise ResourceError('Flathub restore document changed')
    value=_decode_json(raw)
    if (not isinstance(value,dict) or value.get('kind')!='BoilerFlathubRestoreDocument' or value.get('schema_version')!=1
            or value.get('token')!=ref['token'] or set(value.get('slots',{}))!={'A','B'}):raise ResourceError('Invalid Flathub restore document')
    return value
