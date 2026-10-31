"""Fixed public definition validation for the unintegrated Flatpak adapter."""
import base64
import configparser
import hashlib
import io

DEFINITION_SHA256 = '3371dd250e61d9e1633630073fefda153cd4426f72f4afa0c3373ae2e8fea03a'
KEY_SHA256 = '8bdc20abc4e19c0796460beb5bfe0e7aa4138716999e19c6f2dbdd78cc41aeaa'
FINGERPRINT = '6e5c05d979c76daf93c081354184dd4d907a7cae'
MIRROR = 'https://mirrors.ustc.edu.cn/flathub'


def render(raw):
    if hashlib.sha256(raw).hexdigest() != DEFINITION_SHA256:
        raise ValueError('Flathub definition differs from the reviewed public input')
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(raw.decode('utf-8'))
    if parser.sections() != ['Flatpak Repo'] or parser['Flatpak Repo'].get('Url') != 'https://dl.flathub.org/repo/':
        raise ValueError('Unexpected Flathub repository definition')
    key = base64.b64decode(parser['Flatpak Repo']['GPGKey'], validate=True)
    if len(key) < 3 or key[0] != 0x99 or len(key) < 3 + int.from_bytes(key[1:3], 'big') or key[3] != 4:
        raise ValueError('Invalid OpenPGP primary key packet')
    packet = key[:3 + int.from_bytes(key[1:3], 'big')]
    if hashlib.sha256(key).hexdigest() != KEY_SHA256 or hashlib.sha1(packet).hexdigest() != FINGERPRINT:
        raise ValueError('Flathub signing key differs from the reviewed key')
    parser['Flatpak Repo']['Url'] = MIRROR
    out = io.StringIO(); parser.write(out)
    return out.getvalue().encode('utf-8')
