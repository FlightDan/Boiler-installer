"""Produce explicit source patches: model labels are hints, real interfaces gate.

Source locks stay immutable. This tool does not load a module or touch audio.
"""
import argparse
import difflib
import importlib.util
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))


def xevma_model_advisory(raw):
    old="""    if (read('/sys/class/dmi/id/sys_vendor'), read('/sys/class/dmi/id/product_name')) != (
        'ONE-NETBOOK', 'ONEXPLAYER 3'
    ):
        raise RuntimeError('xevmafix requires ONE-NETBOOK / ONEXPLAYER 3')
"""
    new="""    try:
        known_model = (read('/sys/class/dmi/id/sys_vendor'), read('/sys/class/dmi/id/product_name')) == (
            'ONE-NETBOOK', 'ONEXPLAYER 3'
        )
    except OSError:
        known_model = False
    if not known_model:
        print('xevmafix model name is advisory; checking actual Intel xe interfaces', flush=True)
    matches = []
    for device in (root / 'sys/class/drm').glob('card[0-9]*/device'):
        try:
            vendor = (device / 'vendor').read_text().strip().lower()
            fields = dict(line.split('=', 1) for line in (device / 'uevent').read_text().splitlines() if '=' in line)
            if vendor == '0x8086' and fields.get('DRIVER') == 'xe':
                matches.append(device)
        except (OSError, UnicodeError):
            continue
    if not matches:
        raise RuntimeError('xevmafix requires an actual Intel GPU bound to xe')
"""
    text=raw.decode()
    if text.count(old)!=1:raise ValueError('Changed xevma model gate anchor')
    patched = text.replace(old,new).encode()
    path=Path(__file__).resolve().parents[2]/'adaptations/common/xevma/kmod_transform.py'
    spec=importlib.util.spec_from_file_location('boiler_xevma_kmod_resource',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.transform(patched)


def build(project,output):
    from split_components import Sources,speaker_model_advisory,jb,sha
    project=Path(project);output=Path(output)
    if output.exists():raise ValueError('Use a new model-advisory patch output')
    source=Sources(project/'adaptations');output.mkdir(parents=True);rows=[]
    for name,origin,transform in (
        ('speaker_route.py','brands/onexplayer/oxp3/current/upstream-20261029/speaker_route.py',speaker_model_advisory),
        ('xevma-load.py','brands/onexplayer/oxp3/current/xevma-20261029/load.py',xevma_model_advisory)):
        before=source.read(origin);after=transform(before)
        (output/name).write_bytes(after)
        patch=''.join(difflib.unified_diff(before.decode().splitlines(keepends=True),after.decode().splitlines(keepends=True),
            fromfile='upstream/'+name,tofile='resource/'+name))
        (output/(name+'.patch')).write_text(patch,encoding='utf-8',newline='\n')
        rows.append(dict(file=name,source_path=origin,source_size=len(before),source_sha256=sha(before),
                         output_size=len(after),output_sha256=sha(after),patch=name+'.patch',patch_sha256=sha(patch.encode())))
    (output/'source-transform-lock.json').write_bytes(jb(dict(kind='HardwareModelAdvisoryPatches',
        upstream=source.upstream_lock['upstream'],upstream_source_lock_sha256=sha(source.upstream_raw),files=rows,
        notes=['Original source tree and source lock are unchanged.','Xevma kernel/OS/module digest guards are preserved and real Intel xe binding is required.',
               'Speaker still checks the actual local ALC245 codec, empty headphone jack and available speaker profile before any route write.'])))
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();rows=build(args.project,args.output);print(json.dumps(dict(patched=len(rows))))
