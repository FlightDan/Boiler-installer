"""Build a minimal portable Python runtime from exact official archives/wheels."""
import argparse,hashlib,json,shutil,sys,urllib.request,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol import safe_extract_archive,verify_file
from protocol.manifest import confined

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--cache',type=Path,default=ROOT/'cache/python-dependencies')
    args=parser.parse_args()
    if args.output.exists(): raise ValueError('Use a fresh runtime output directory')
    lock=json.loads((Path(__file__).with_name('python-runtime-lock.json')).read_text())
    args.cache.mkdir(parents=True,exist_ok=True);args.output.mkdir(parents=True)
    for item in lock['artifacts']:
        file=confined(args.cache,item['name'])
        if not file.exists():
            temporary=confined(args.cache,item['name']+'.partial')
            if not item['url'].startswith('https://'): raise ValueError('HTTPS dependency required')
            with temporary.open('xb') as output, urllib.request.urlopen(item['url'],timeout=60) as response:
                total=0
                while block:=response.read(1024*1024):
                    total+=len(block)
                    if total>item['size']: raise ValueError('Python dependency exceeded locked size')
                    output.write(block)
            verify_file(temporary,item);temporary.rename(file)
        verify_file(file,item)
        target=args.output/('.unpack-'+item['kind'])
        safe_extract_archive(file,target)
        for source in target.iterdir():
            destination=args.output/source.name
            if destination.exists(): raise ValueError('Runtime archive path collision')
            source.rename(destination)
        target.rmdir()
    (args.output/'runtime-provenance.json').write_text(json.dumps(lock,indent=2)+'\n')
    print(args.output)
if __name__=='__main__':main()
