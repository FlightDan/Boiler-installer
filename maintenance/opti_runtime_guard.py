"""Read-only guard for the fixed Opti runtime's per-game ownership."""
import re
from pathlib import Path
from maintenance.adoption_blobs import _directory,_file
from protocol.manifest import _decode_json


def _read(path,home,uid):
 with _file(path,user_from=home,user_uid=uid) as (stream,info):
  if info.st_size>1024*1024:raise RuntimeError('Opti game state exceeds limit')
  data=_decode_json(stream.read())
 if not isinstance(data,dict):raise RuntimeError('Invalid Opti game state')
 return data


def _children(path,home,uid):
 if path.is_symlink():raise RuntimeError('Linked Opti state path')
 if not path.exists():return []
 with _directory(path,user_from=home,user_uid=uid):return list(path.iterdir())


def _keyvalues(text):
 token=re.compile(r'\s+|//[^\n]*(?:\n|$)|"((?:\\.|[^"\\])*)"|([{}])')
 values=[];pos=0
 while pos<len(text):
  match=token.match(text,pos)
  if not match:raise RuntimeError('Cannot safely inspect Steam launch options')
  pos=match.end()
  if match.group(1) is not None:values.append(match.group(1).replace('\\"','"').replace('\\\\','\\'))
  elif match.group(2):values.append(match.group(2))
 index=0
 def parse(nested=False,depth=0):
  nonlocal index
  if depth>64:raise RuntimeError('Steam configuration nesting exceeds limit')
  out=[]
  while index<len(values):
   key=values[index];index+=1
   if key=='}':
    if not nested:raise RuntimeError('Invalid Steam configuration')
    return out
   if key=='{' or index>=len(values):raise RuntimeError('Invalid Steam configuration')
   value=values[index];index+=1;folded=key.casefold()
   out.append((folded,parse(True,depth+1) if value=='{' else value))
  if nested:raise RuntimeError('Incomplete Steam configuration')
  return out
 return parse()


def _steam_references(home,uid):
 roots=set();issues=[]
 for candidate in (home/'.local/share/Steam',home/'.steam/steam'):
  if not candidate.exists():continue
  resolved=candidate.resolve()
  if not resolved.is_relative_to(home):raise RuntimeError('Steam data is outside the target home; inspect its launch options before removing runtime')
  roots.add(resolved)
 def visit(value,path=()):
  if not isinstance(value,list):return
  for key,item in value:
   if key=='launchoptions' and isinstance(item,str) and ('opti2xmx' in item.casefold() or 'oxp3-opti2xmx' in item.casefold()):
    appid=next((int(x) for x in reversed(path) if re.fullmatch('[1-9][0-9]*',x)),None)
    issues.append(dict(appid=appid,kind='steam-launch',status='references-opti'))
   elif isinstance(item,list):visit(item,path+(key,))
 for root in roots:
  for user in _children(root/'userdata',home,uid):
   if not re.fullmatch('[0-9]+',user.name):continue
   config=user/'config/localconfig.vdf'
   if not config.exists() and not config.is_symlink():continue
   with _file(config,user_from=home,user_uid=uid) as (stream,info):
    if info.st_size>8*1024*1024:raise RuntimeError('Steam launch configuration exceeds limit')
    data=stream.read().decode('utf-8-sig')
   visit(_keyvalues(data))
 return issues


def blockers(home,uid):
 home=Path(home).absolute()
 if home.is_symlink():raise RuntimeError('Linked target home is not accepted')
 home=home.resolve()
 if not isinstance(uid,int) or uid<1000:raise RuntimeError('Invalid target user identity')
 root=home/'.local/share/opti2xmx';issues=[]
 for category in ('games','opti'):
  for app in _children(root/category,home,uid):
   if not re.fullmatch('[1-9][0-9]*',app.name):continue
   if app.is_symlink() or not app.is_dir():raise RuntimeError('Unsafe Opti application state')
   if category=='games':
    for state in _children(app,home,uid):
     if not state.name.startswith('setup-'):continue
     if state.is_symlink() or not state.is_dir():raise RuntimeError('Unsafe Opti game journal')
     record=_read(state/'plugin.json',home,uid)
     if record.get('appid')!=int(app.name) or record.get('status')!='restored':issues.append(dict(appid=int(app.name),kind='xmx',status=str(record.get('status','invalid'))))
     core=state/'xmx/manifest.json'
     if core.exists() or core.is_symlink():
      journal=_read(core,home,uid)
      if journal.get('status')!='restored':issues.append(dict(appid=int(app.name),kind='xmx-core',status=str(journal.get('status','invalid'))))
   else:
    current=app/'current.json'
    if current.exists() or current.is_symlink():
     record=_read(current,home,uid);status=record.get('status')
     pending=record.get('pending');undo=record.get('settings_undo') or {}
     if status not in ('removed','rolled_back') or pending or undo.get('status') in ('applying','restoring'):
      issues.append(dict(appid=int(app.name),kind='opti',status=str(status or 'invalid')))
    elif any(child.name.startswith(('revision-','baseline-')) for child in _children(app,home,uid)):
     issues.append(dict(appid=int(app.name),kind='opti',status='missing-journal'))
 return issues+_steam_references(home,uid)


def require_clear(home,uid):
 issues=blockers(home,uid)
 if issues:raise RuntimeError('Restore all managed Opti game setups and launch options before removing the runtime: '+', '.join(str(x['appid']) for x in issues))
 return dict(clear=True)
