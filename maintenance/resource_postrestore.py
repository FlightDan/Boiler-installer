"""Bound progress for the two fixed callbacks that run after file restoration."""
import copy,hashlib,json,re
from pathlib import Path
from .engine import _json_write
from .resource_runtime import ResourceError

IDENTITIES={'resource:gamma22-hdr-desktop','resource:common-telemetry-overlay'}
def digest(value):
 return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def run(identity,provider,intent,receipt,state,*,root=Path('/')):
 from .resource_firstboot import STATE
 if identity not in IDENTITIES or state.get('generation')!=intent.get('generation'):
  raise ResourceError('Post-restore generation or fixed provider differs')
 row=state.get('resources',{}).get(identity,{})
 original=row.get('activation_recovery');action=state.get('maintenance_restore',{})
 if (not isinstance(original,dict) or row.get('status')!='restored' or action.get('identity')!=identity
     or action.get('status')!='restored' or not re.fullmatch('[0-9a-f]{64}',action.get('reviewed_sha256',''))):
  raise ResourceError('Post-restore requires the completed file restoration action')
 binding=dict(kind='BoilerResourcePostRestoreProgress',schema_version=1,generation=intent['generation'],identity=identity,
  activation_sha256=digest(original),receipt_sha256=digest(receipt),action_sha256=action['reviewed_sha256'])
 saved=row.get('resume_original_recovery');prior=None
 if saved is not None:
  if not isinstance(saved,dict) or set(saved)!=set(binding)|{'progress'} or any(saved[k]!=v for k,v in binding.items()):
   raise ResourceError('Post-restore progress belongs to a different action or receipt')
  if not isinstance(saved['progress'],dict):raise ResourceError('Invalid fixed post-restore progress')
  prior=copy.deepcopy(saved['progress'])
 def record(progress):
  if not isinstance(progress,dict) or len(json.dumps(progress,allow_nan=False).encode())>65536:
   raise ResourceError('Invalid fixed post-restore progress')
  row['resume_original_recovery']=dict(binding,progress=copy.deepcopy(progress))
  _json_write(Path(root)/STATE,state)
 row['resume_original_pending']=True;_json_write(Path(root)/STATE,state)
 result=provider.resume_original(identity,root=root,state=copy.deepcopy(original),prior_resume=prior,record_before_write=record)
 if not isinstance(result,dict):raise ResourceError('Fixed post-restore provider returned an invalid result')
 row['resume_original_result']=copy.deepcopy(result);row['resume_original_pending']=False
 _json_write(Path(root)/STATE,state)
 return result
