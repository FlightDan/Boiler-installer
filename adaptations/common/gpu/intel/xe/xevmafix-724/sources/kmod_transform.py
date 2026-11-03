"""Correct kmod multi-call argv in three independently reviewed loader variants."""
import hashlib

INPUT_SHA256 = frozenset((
    'bf49070cb295c872c3059480cc0a393c77bc1b129ffdaba05e12e90ec78dc646',
    'ed193cd420a4f2288f49487cde3ac4c8b9065aeaa2e90e13fc4c9059ddc29cfb',
    '9c4202e2d868daa124c1d2857d8be1dd41ecf513d306dc6ddbb7529e23484504',
))
REPLACEMENTS = (
    (b"subprocess.run(['/usr/bin/kmod', 'modprobe', 'xe'], check=True)",
     b"subprocess.run(['modprobe', 'xe'], executable='/usr/bin/kmod', check=True)"),
    (b"subprocess.run(['/usr/bin/kmod', 'insmod', str(module)], check=True)",
     b"subprocess.run(['insmod', str(module)], executable='/usr/bin/kmod', check=True)"),
)


def transform(raw):
    if hashlib.sha256(raw).hexdigest() not in INPUT_SHA256:
        raise ValueError('Unknown or already transformed xevma loader')
    for old, new in REPLACEMENTS:
        if raw.count(old) != 1 or new in raw:
            raise ValueError('Unexpected xevma kmod command anchor')
        raw = raw.replace(old, new, 1)
    return raw
