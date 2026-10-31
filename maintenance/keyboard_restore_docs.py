"""Root-private immutable before/after documents for keyboard restore."""
import copy,hashlib,json,re,uuid
from pathlib import Path
from .adoption_blobs import _directory,_file
from .engine import _json_write
from .resource_runtime import ResourceError
from protocol.manifest import MAX_MANIFEST,_decode_json

IDENTITY='resource:common-kde-keyboard'


def _raw(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def create(intent,plan,reviewed_sha256,mounts,marker):
    from . import resource_firstboot as firstboot
    from .keyboard_target import binding
    token=uuid.uuid4().hex;slots={}
    for slot,root in mounts.roots.items():
        other=firstboot.checked_intent(root)
        if (any(other.get(k)!=intent.get(k) for k in ('generation','selection','target_roles')) or other['slot']!=slot
                or other.get('root_partuuid','').lower()!=intent['target_roles']['rootfs-'+slot]['partuuid'].lower()):
            raise ResourceError('Keyboard restore intent changed')
        if binding(root,expected_label=slot)!=plan['slots'][slot]['binding']:
            raise ResourceError('Keyboard restore target changed')
        path=root/firstboot.STATE;exists=path.exists()
        state=firstboot.read_private(path) if exists else firstboot._new_state(other)
        from .resource_lifecycle import digest
        if digest(state)!=plan['slots'][slot]['state_sha256']:raise ResourceError('Keyboard restore state changed after review')
        firstboot.validate_restore_binding(IDENTITY,root,other,state)
        gate=root/'var/lib/boiler-resource-approved/common-kde-keyboard.json'
        gate_value=firstboot.read_private(gate) if gate.exists() else None
        if gate_value is not None and gate_value!={'generation':intent['generation'],'identity':IDENTITY}:
            raise ResourceError('Keyboard approval gate belongs to another installation')
        if digest(gate_value)!=plan['slots'][slot]['gate_sha256']:
            raise ResourceError('Keyboard approval gate changed after review')
        after=copy.deepcopy(state)
        after['resources'].setdefault(IDENTITY,{}).update(status='restored',runtime='not_validated',
            restore_receipt=dict(kind='BoilerKeyboardRestoreReceipt',token=token))
        after['status']='attention_required'
        after['maintenance_restore']=dict(identity=IDENTITY,status='restored',reviewed_sha256=reviewed_sha256)
        slots[slot]=dict(before_exists=exists,before=state,after=after,gate_before=gate_value)
    document=dict(schema_version=1,kind='BoilerKeyboardRestoreDocument',token=token,generation=intent['generation'],
                  reviewed_sha256=reviewed_sha256,slots=slots)
    raw=_raw(document)
    if len(raw)>MAX_MANIFEST:raise ResourceError('Keyboard restore document exceeds limit')
    digest=hashlib.sha256(raw).hexdigest();folder=Path(marker).parent/'keyboard-restore-documents'
    with _directory(folder,create=True):pass
    path=folder/(token+'-'+digest+'.json');_json_write(path,document)
    ref=dict(token=token,document_sha256=digest)
    if load(ref,marker)!=document:raise ResourceError('Keyboard restore document was not durably retained')
    return ref


def load(ref,marker):
    if (not isinstance(ref,dict) or set(ref)!={'token','document_sha256'}
            or not re.fullmatch('[0-9a-f]{32}',ref['token']) or not re.fullmatch('[0-9a-f]{64}',ref['document_sha256'])):
        raise ResourceError('Invalid keyboard restore document reference')
    path=Path(marker).parent/'keyboard-restore-documents'/(ref['token']+'-'+ref['document_sha256']+'.json')
    with _file(path,private=True) as (stream,info):
        if info.st_size>MAX_MANIFEST:raise ResourceError('Keyboard restore document exceeds limit')
        raw=stream.read(MAX_MANIFEST+1)
    if hashlib.sha256(raw).hexdigest()!=ref['document_sha256']:raise ResourceError('Keyboard restore document changed')
    doc=_decode_json(raw)
    if (not isinstance(doc,dict) or doc.get('kind')!='BoilerKeyboardRestoreDocument' or doc.get('schema_version')!=1
            or doc.get('token')!=ref['token'] or set(doc.get('slots',{}))!={'A','B'}):
        raise ResourceError('Invalid keyboard restore document')
    return doc
