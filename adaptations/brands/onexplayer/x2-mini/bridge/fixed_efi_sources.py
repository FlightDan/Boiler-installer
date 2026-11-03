"""Derive the X2 fixed-EFI bridge from the reviewed local bridge bytes.

This source transform is preparation only. It grants no package authority;
the final X2 provider closure still needs a separately reviewed installer lock.
The inputs are local reviewed sources, not an assertion that they were published.
"""
import hashlib

INPUT_SHA256 = {
    'adapter.py': '3a02c2e7f98023f3b35b08c9f8a9edd2a8528961fbbdb05e523da26c3879a3ee',
    'maintenance_adapter.py': 'f11aad955b288a7107c5eec88b8df47ad4d147537b854d02e37a5e712b3b988d',
}


def _replace_once(value, old, new):
    if value.count(old) != 1:
        raise ValueError('Fixed X2 bridge source anchor differs')
    return value.replace(old, new)


def transform(name, raw):
    if name not in INPUT_SHA256 or hashlib.sha256(raw).hexdigest() != INPUT_SHA256[name]:
        raise ValueError('Fixed X2 bridge source differs from reviewed input')
    value = raw.decode('utf-8')
    value = _replace_once(value, "manifest.get('id') != 'device:oxp3'",
                          "manifest.get('id') != 'device:x2-mini'")
    if name == 'adapter.py':
        value = _replace_once(value, "efi_context['source'].get('package_id') != 'device:oxp3'",
                              "efi_context['source'].get('package_id') != 'device:x2-mini'")
        value = _replace_once(value, "'/mnt/payload/components/oxp3_efi_provider.py'",
                              "'/mnt/payload/components/x2_efi_provider.py'")
        value = _replace_once(value, "'/mnt/payload/components/oxp3_efi_provider.lock.json'",
                              "'/mnt/payload/components/x2_efi_provider.lock.json'")
    return value.encode('utf-8')
