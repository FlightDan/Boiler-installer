"""Desktop consent prompt from a minimal root-owned firstboot status marker."""
import hashlib,json,os,re,stat
from pathlib import Path
from .engine import _atomic
from .adoption_blobs import _directory,_file

MARKER='var/lib/boiler-resource-status/notice.json'
CODE='MAINTENANCE_OPERATION_FAILED'

def publish(state,*,root=Path('/'),fatal=False):
    generation=state.get('generation','0'*32)
    if not isinstance(generation,str) or not re.fullmatch('[0-9a-f]{32}',generation):generation='0'*32
    # Only public resource identities and attempt counters feed the hash. Raw
    # exceptions, logs, panel bytes and filesystem paths are never exported.
    rows=[(identity,row.get('status'),row.get('attempts',0)) for identity,row in sorted(state.get('resources',{}).items()) if row.get('status') in ('failed','recovery_required')]
    failed=bool(fatal or rows or state.get('decky',{}).get('status') in ('failed','recovery_required'))
    fingerprint=hashlib.sha256(json.dumps([generation,failed,rows],sort_keys=True).encode()).hexdigest()
    value=dict(kind='BoilerResourceFailureNotice',schema_version=1,generation=generation,
        status='attention_required' if failed else 'complete',code=CODE,fingerprint=fingerprint)
    path=Path(root)/MARKER
    with _directory(path.parent,create=True,create_mode=0o755):
        _atomic(path,(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode(),mode=0o644)

def read_notice(*,root=Path('/')):
    with _file(Path(root)/MARKER) as (stream,info):
        if info.st_size>4096 or (os.name!='nt' and (info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o644)):
            raise ValueError('Invalid firstboot notice metadata')
        from protocol.manifest import _decode_json
        value=_decode_json(stream.read(4097))
    if (not isinstance(value,dict) or set(value)!={'kind','schema_version','generation','status','code','fingerprint'}
            or value['kind']!='BoilerResourceFailureNotice' or type(value['schema_version']) is not int or value['schema_version']!=1
            or value['status'] not in ('complete','attention_required') or value['code']!=CODE
            or not isinstance(value['generation'],str) or not re.fullmatch('[0-9a-f]{32}',value['generation'])
            or not isinstance(value['fingerprint'],str) or not re.fullmatch('[0-9a-f]{64}',value['fingerprint'])):
        raise ValueError('Invalid firstboot notice')
    return value

def show_pending(ui,*,root=Path('/'),cache=None):
    """Runs as the desktop user; acknowledgment grants no reporting consent."""
    value=read_notice(root=root)
    if value['status']!='attention_required':return False
    cache=Path(cache) if cache is not None else Path.home()/'.cache/boiler/resource-notice'
    if cache.exists() and cache.read_text(encoding='ascii').strip()==value['fingerprint']:return False
    cache.parent.mkdir(parents=True,exist_ok=True)
    _atomic(cache,(value['fingerprint']+'\n').encode(),mode=0o600)
    ui.dialog.message(ui.tr('部分机型适配未完成。请打开 Boiler 维护器查看详情并重试。',
        'Some device support could not be completed. Open Boiler maintenance for details and retry.'),error=True)
    ui.offer_failure(CODE)
    return True

def main():
    from .model_ui import ModelUI,KDialog,Backend,language,KDIALOG
    if not hasattr(os,'geteuid') or os.geteuid()==0 or not Path(KDIALOG).is_file() or not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):return 0
    lang=language(None)
    try:show_pending(ModelUI(KDialog(lang),Backend(),lang=lang))
    except (OSError,ValueError):return 0
    return 0
