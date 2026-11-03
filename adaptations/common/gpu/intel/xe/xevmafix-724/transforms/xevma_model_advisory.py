"""Selected pure xe model-advisory transform; source function SHA-256:
119e7050e024074004fafdd35cae6d266dba0db5bf93dee8863744bd052f3ca7."""
from __future__ import annotations
import importlib.util
from pathlib import Path

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
    if text.count(old)!=1: raise ValueError('Changed xevma model gate anchor')
    spec=importlib.util.spec_from_file_location('xevma_kmod_transform',Path(__file__).parents[1]/'sources/kmod_transform.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.transform(text.replace(old,new).encode())
