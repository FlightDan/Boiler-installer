"""OXP3 ALC245 speaker route recovery at session start and after resume.

Adapted from MIT upstream 20261029 step 12, HHHHanasak1/onexplayer3-steamos-setup
e72e814da1944da3792076689995b6fe961f5768. Working profiles are retried only
after a recent Speaker UCM failure. Headsets and unrelated cards are preserved.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import time

ENV = dict(os.environ, LC_ALL='C', LANG='C')


def run(*args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=15, env=ENV)
    # None is failure; successful pactl mutations normally have empty stdout.
    return result.stdout if result.returncode == 0 else None


def supported(root=Path('/')):
    try:
        dmi = root/'sys/class/dmi/id'
        return ((dmi/'sys_vendor').read_text().strip() == 'ONE-NETBOOK' and
                (dmi/'product_name').read_text().strip() == 'ONEXPLAYER 3')
    except OSError:
        return False


def local_codec(card, root=Path('/')):
    if not re.fullmatch(r'[0-9]+', str(card)):
        return False
    try:
        return any('Codec: Realtek ALC245' in path.read_text()
                   for path in (root/'proc/asound'/('card'+str(card))).glob('codec#*'))
    except (OSError, UnicodeError):
        return False


def cards(command=run):
    try:
        value = json.loads(command('/usr/bin/pactl', '--format=json', 'list', 'cards'))
        return value if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


def identity(card):
    if not isinstance(card, dict):
        return None
    name, active = card.get('name'), card.get('active_profile')
    props = card.get('properties') or {}
    if not isinstance(props, dict):
        return None
    alsa = str(props.get('alsa.card', props.get('api.alsa.card', '')))
    if not isinstance(name, str) or not name or not isinstance(active, str):
        return None
    return name, alsa, active


def empty_jack(alsa, command=run):
    output = command('/usr/bin/amixer', '-c', alsa, 'cget', "iface=CARD,name=Headphone Jack") or ''
    return bool(re.search(r'^\s*:\s*values=off\s*$', output, re.M))


def ucm_failed(since, command=run):
    pid = command('/usr/bin/systemctl', '--user', 'show', '-p', 'MainPID', '--value',
                  'wireplumber.service')
    pid = (pid or '').strip()
    if not re.fullmatch(r'[1-9][0-9]*', pid):
        return False
    log = command('/usr/bin/journalctl', '--user', '-q', '-o', 'cat', '_PID='+pid,
                  '--since', '@'+str(int(since)))
    return bool(re.search(r'Failed to enable UCM device[ :"\x27]+Speaker\b', log or ''))


def set_profile(name, profile, command):
    result = command('/usr/bin/pactl', 'set-card-profile', name, profile)
    if result is None:
        raise RuntimeError('Could not select audio profile '+profile+' for '+name)


def check(since, *, command=run, codec=local_codec, pause=time.sleep):
    failed = ucm_failed(since, command)
    actions = []
    for card in cards(command):
        item = identity(card)
        if item is None:
            continue
        name, alsa, active = item
        if not codec(alsa) or not empty_jack(alsa, command):
            continue
        profiles = card.get('profiles') or {}
        if not isinstance(profiles, dict):
            continue
        speakers = [key for key, value in profiles.items()
                    if isinstance(key, str) and 'Speaker' in key and 'Headphones' not in key
                    and isinstance(value, dict) and value.get('available') != 'no']
        wanted = None
        if 'Headphones' in active and len(speakers) == 1:
            wanted = speakers[0]
        elif failed and active in speakers:
            wanted = active
        if wanted is None:
            continue
        fresh = next((identity(row) for row in cards(command)
                      if identity(row) is not None and identity(row)[0] == name), None)
        if fresh != item or not empty_jack(alsa, command):
            continue
        if wanted == active:
            set_profile(name, 'off', command)
            pause(1)
        try:
            set_profile(name, wanted, command)
        except (OSError, subprocess.SubprocessError, RuntimeError):
            # A failed re-enable must be visible. Try to restore the known
            # original route before returning failure, including when the
            # successful off transition left this card disabled.
            try:
                set_profile(name, active, command)
            except (OSError, subprocess.SubprocessError, RuntimeError) as error:
                print('OXP3 speaker route restoration failed:', error, flush=True)
            raise
        actions.append((name, wanted))
    return actions


def main():
    if not supported():
        print('Speaker route: model name is advisory; checking actual ALC245/audio interfaces.', flush=True)
    since = int(time.time()) - 30
    failed = False
    for delay in (3, 7, 20):
        time.sleep(delay)
        next_since = int(time.time())
        try:
            for name, profile in check(since):
                print('OXP3 speaker route:', name, '->', profile, flush=True)
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            print('OXP3 speaker route check unavailable:', error, flush=True)
            failed = True
        since = next_since
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
