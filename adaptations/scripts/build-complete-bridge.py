# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rebuild the fixed X2 identity/advisory overlay from an explicit OXP3 .8 package.

The shared implementation and journals keep their oxp3 identifiers. Only fixed
external package identities and model-name advice change. No input code is run.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from protocol.manifest import validate_package,confined

BASE_MANIFEST='cb39c94bbe7eb351221c380ebdb7a875a097b52c17e90bfb0f1edc22b12ab684'
INPUTS={
 'adapter.py':'0784a37d0986d8a88f7b8a6f9f068668298a5b97e705dfaab8c35a5676ff3ca3',
 'maintenance_adapter.py':'fe65b80ed256655672b162f25a0cdd0b762c8025eaaea68a74e9f34e6f91ee78',
 'upstream-repairs/speaker/speaker_route.py':'b47f916aa80036d5730c0df26deac7aedcd8f9af14cc615058d9f6432e9c7739',
 'upstream-repairs/xevmafix/load.py':'bf49070cb295c872c3059480cc0a393c77bc1b129ffdaba05e12e90ec78dc646',
}
OUTPUTS={
 'adapter.py':'069fa460c03e543231d14047a1d2a7a4d9f9544ab1de491e56cb9c5118e3f159',
 'maintenance_adapter.py':'b43d91a92be68b5907cdf0dc56a6f26e8ce07d5cf570c4ecc870c77840962050',
 'upstream-repairs/speaker/speaker_route.py':'18f96b4addc06004d4548506965be424ffab4b5508dbd38bbd26240a7a81d01c',
 'upstream-repairs/xevmafix/load.py':'ed193cd420a4f2288f49487cde3ac4c8b9065aeaa2e90e13fc4c9059ddc29cfb',
}
LOCK_SHA256='a88f7d51fbe3b54d3aa1aab9ddfd8b5acc094c589dd9cfe1ee1415b2903c553d'

def sha(data):return hashlib.sha256(data).hexdigest()

def replace_once(value,old,new):
    if value.count(old)!=1:raise ValueError('Reviewed source transform no longer matches')
    return value.replace(old,new)

def transform(name,data):
    if sha(data)!=INPUTS[name]:raise ValueError('Reviewed base source changed: '+name)
    value=data.decode('utf-8')
    if name in ('adapter.py','maintenance_adapter.py'):
        value=replace_once(value,"manifest.get('id') != 'device:oxp3'","manifest.get('id') not in ('device:oxp3', 'device:x2-mini')")
    elif name.endswith('speaker_route.py'):
        value=replace_once(value,"""def supported(root=Path('/')):
    try:
        dmi = root/'sys/class/dmi/id'
        return ((dmi/'sys_vendor').read_text().strip() == 'ONE-NETBOOK' and
                (dmi/'product_name').read_text().strip() == 'ONEXPLAYER 3')
    except OSError:
        return False
""","""def supported(root=Path('/')):
    # Model names are advice. The actual codec/card/jack checks below decide
    # whether this route repair is applicable.
    try:
        dmi = root/'sys/class/dmi/id'
        matched = ((dmi/'sys_vendor').read_text().strip() == 'ONE-NETBOOK' and
                   (dmi/'product_name').read_text().strip() == 'ONEXPLAYER 3')
    except OSError:
        matched = False
    if not matched:
        print('Device model differs from OXP3 or is unavailable; continuing with actual audio checks.', file=__import__('sys').stderr)
    return True
""")
    else:
        value=replace_once(value,"""    if (read('/sys/class/dmi/id/sys_vendor'), read('/sys/class/dmi/id/product_name')) != (
        'ONE-NETBOOK', 'ONEXPLAYER 3'
    ):
        raise RuntimeError('xevmafix requires ONE-NETBOOK / ONEXPLAYER 3')
""","""    try:
        matched = (read('/sys/class/dmi/id/sys_vendor'), read('/sys/class/dmi/id/product_name')) == ('ONE-NETBOOK', 'ONEXPLAYER 3')
    except OSError:
        matched = False
    if not matched:
        print('Device model differs from OXP3 or is unavailable; continuing with the exact kernel and authenticated module checks.', file=__import__('sys').stderr)
""")
    output=value.encode('utf-8')
    if sha(output)!=OUTPUTS[name]:raise ValueError('Overlay transform differs from reviewed output: '+name)
    return output

def build(base,output):
    base,output=Path(base).absolute(),Path(output).absolute()
    for path in (output,*output.parents):
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise ValueError('Output cannot traverse links')
    if output.exists() or output.resolve().is_relative_to(base.resolve()):raise ValueError('Fresh output outside the base is required')
    manifest=validate_package(base,expected_manifest_sha256=BASE_MANIFEST)
    if manifest['id']!='device:oxp3':raise ValueError('Explicit complete OXP3 base is required')
    prepared={name:transform(name,confined(base,name).read_bytes()) for name in INPUTS}
    records=[dict(path=name,source=name,size=len(data),sha256=sha(data),base_sha256=INPUTS[name]) for name,data in prepared.items()]
    lock=dict(schema_version=1,kind='BoilerX2CompleteOverlay',role='bridge',base_manifest_sha256=BASE_MANIFEST,files=records,
        provenance=dict(source='Reviewed Boiler OXP3 bridge; fixed external model identity reuse only',license='AGPL-3.0-or-later',internal_component_ids='Preserved oxp3:* for shared implementation and journal recovery'))
    raw=(json.dumps(lock,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    if sha(raw)!=LOCK_SHA256:raise ValueError('Reviewed overlay lock differs')
    output.mkdir(parents=True)
    for name,data in prepared.items():
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (output/'overlay-lock.json').write_bytes(raw)
    return dict(directory=str(output),files=len(prepared),lock_sha256=sha(raw),base_manifest_sha256=BASE_MANIFEST)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.base,args.output),indent=2))
