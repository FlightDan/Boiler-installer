"""Fixed public Opti 0.3.1 input CAS; candidate build helper, not a runtime hook."""
import hashlib,os,tempfile
from pathlib import Path
from protocol.fetch import _cache_lock,download
from protocol.resource_pool import decoder

RECIPE_SHA256='54c0fe968d4d591cd59925a849f3b6be022321f02fb8c75c9b64498ac41993cc'


def _digest_file(path):
 digest=hashlib.sha256();size=0
 with Path(path).open('rb') as stream:
  for chunk in iter(lambda:stream.read(1024*1024),b''):
   size+=len(chunk);digest.update(chunk)
 return digest.hexdigest(),size


def _ensure(cache,row):
 target=cache/row['sha256']
 if target.exists():
  if target.is_symlink() or not target.is_file() or _digest_file(target)!=(row['sha256'],row['size']):
   raise ValueError('Opti cached input failed content verification')
  return target
 with tempfile.TemporaryDirectory(prefix='.opti-input-',dir=cache) as directory:
  staging=Path(directory)/'download';download(row['url'],staging,maximum=row['size'],expected={'sha256':row['sha256'],'size':row['size']})
  if _digest_file(staging)!=(row['sha256'],row['size']):raise ValueError('Opti staged input differs')
  os.replace(staging,target)
 return target


def acquire(recipe,cache):
 raw=Path(recipe).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=RECIPE_SHA256:raise ValueError('Opti fixed recipe differs')
 spec=decoder().loads(raw.decode('utf-8'),strict=True)
 if spec.get('kind')!='BoilerOpti031Inputs' or {r['id'] for r in spec['inputs']}!={'plugin','runtime','optiscaler094','optiscaler079','optiscaler100pre'}:
  raise ValueError('Opti recipe identity differs')
 cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
 if cache.is_symlink():raise ValueError('Opti cache is linked')
 with _cache_lock(cache):return {row['id']:_ensure(cache,row) for row in spec['inputs']}

