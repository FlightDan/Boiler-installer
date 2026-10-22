"""Export only the reviewed source whitelist into a fresh, history-free directory."""
import argparse,hashlib,json,shutil
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.manifest import confined


def export(destination):
    destination=Path(destination)
    if destination.exists():raise ValueError('Use a new isolated source checkout directory')
    files=json.loads((ROOT/'scripts/source-files.json').read_text(encoding='utf-8'))['files']
    if len(set(files))!=len(files):raise ValueError('Duplicate source whitelist entry')
    inventory=[]
    for name in files:
        source=confined(ROOT,name)
        if not source.is_file():raise ValueError('Whitelisted source missing: '+name)
        target=confined(destination,name);target.parent.mkdir(parents=True,exist_ok=True)
        with source.open('rb') as source_file,target.open('xb') as target_file:shutil.copyfileobj(source_file,target_file)
        inventory.append({'path':name,'size':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    return {'directory':str(destination),'files':inventory,'history':'none'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination',type=Path);args=parser.parse_args()
    print(json.dumps(export(args.destination),indent=2))
