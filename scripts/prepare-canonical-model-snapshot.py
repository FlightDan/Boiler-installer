"""Export consolidated model TOONs from an explicitly supplied public commit.

The output is a source snapshot, not a trust grant or installation package.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.resource_pool import decoder, json_document, relative
from scripts.resources.model_source import validate_model_index

REPOSITORY='FlightDan/Boiler-adaptations'
MEMBERS=('brands/onexplayer/oxp3/oxp3.toon','brands/onexplayer/x2-mini/x2-mini.toon')

def prepare(checkout,commit,output):
    if not re.fullmatch(r'[0-9a-f]{40}',commit): raise ValueError('Exact commit required')
    checkout=Path(checkout); output=Path(output)
    if output.exists(): raise ValueError('Use a fresh snapshot destination')
    def git(*args): return subprocess.check_output(['git','-C',str(checkout),*args],stderr=subprocess.PIPE)
    origin=git('remote','get-url','origin').decode().strip()
    if origin not in ('https://github.com/'+REPOSITORY,'https://github.com/'+REPOSITORY+'.git','git@github.com:'+REPOSITORY+'.git'):
        raise ValueError('Expected canonical repository origin')
    if git('rev-parse',commit+'^{commit}').decode().strip()!=commit: raise ValueError('Commit does not resolve exactly')
    toon=decoder(ROOT/'adaptations'); contents={}; rows=[]
    inventory_raw=git('show',commit+':model-source-files.json')
    inventory=json_document(inventory_raw)
    if inventory.get('kind')!='CanonicalAdaptationSourceFiles' or inventory.get('schema_version')!=1 or not isinstance(inventory.get('files'),list):
        raise ValueError('Invalid canonical source inventory')
    seen=set()
    for entry in inventory['files']:
        if not isinstance(entry,dict) or set(entry)!={'path','size','sha256'}: raise ValueError('Invalid canonical source inventory member')
        member=relative(entry['path'])
        if member in seen or member=='model-source-files.json' or any(x.casefold() in ('.git','.local','.codex','.agents','.aws') for x in member.split('/')):
            raise ValueError('Duplicate or private canonical source member')
        seen.add(member)
        raw=git('show',commit+':'+member)
        if type(entry['size']) is not int or len(raw)!=entry['size'] or hashlib.sha256(raw).hexdigest()!=entry['sha256']:
            raise ValueError('Canonical source inventory differs: '+member)
        path='adaptations/'+member; contents[path]=raw
        rows.append(dict(path=path,size=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
    indexed=validate_model_index({name.removeprefix('adaptations/'):raw for name,raw in contents.items()},toon)
    if not indexed and not set(MEMBERS)<=seen: raise ValueError('Canonical model TOON missing from source inventory')
    for member in (() if indexed else MEMBERS):
        raw=contents['adaptations/'+member]
        if len(raw)>1024*1024: raise ValueError('Model source too large')
        guide=toon.loads(raw.decode('utf-8'),strict=True); identity='device:'+Path(member).stem
        if guide.get('kind')!='ModelInstallGuide' or guide.get('id')!='guide:'+Path(member).stem or guide.get('resource_model',{}).get('id')!=identity:
            raise ValueError('Model guide and resource identities differ')
    path='adaptations/model-source-files.json'; contents[path]=inventory_raw
    rows.append(dict(path=path,size=len(inventory_raw),sha256=hashlib.sha256(inventory_raw).hexdigest()))
    output.mkdir(parents=True)
    for name,raw in contents.items():
        path=output/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)
    lock=dict(schema_version=1,kind='CanonicalModelSnapshot',repository=REPOSITORY,commit=commit,files=rows)
    (output/'adaptations/canonical-model-snapshot.json').write_text(json.dumps(lock,indent=2)+'\n',encoding='utf-8',newline='\n')
    return lock

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-checkout',type=Path,required=True); parser.add_argument('--source-commit',required=True); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); print(json.dumps(prepare(args.source_checkout,args.source_commit,args.output),indent=2))