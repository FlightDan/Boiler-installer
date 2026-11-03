"""Derive the full OXP3 EFI adapter from reviewed project-local bridge bytes."""
import hashlib

ADAPTER_SHA256='3a02c2e7f98023f3b35b08c9f8a9edd2a8528961fbbdb05e523da26c3879a3ee'
MAINTENANCE_SHA256='f11aad955b288a7107c5eec88b8df47ad4d147537b854d02e37a5e712b3b988d'


def transform(name, raw):
    expected={'adapter.py':ADAPTER_SHA256,'maintenance_adapter.py':MAINTENANCE_SHA256}.get(name)
    if expected is None or hashlib.sha256(raw).hexdigest()!=expected:
        raise ValueError('Full OXP3 bridge source differs from reviewed bytes')
    if name=='maintenance_adapter.py': return raw
    text=raw.decode('utf-8')
    for old,new in (("'/mnt/payload/components/oxp3_efi_provider.py'",
                     "'/mnt/payload/components/oxp3_full_efi_provider.py'"),
                    ("'/mnt/payload/components/oxp3_efi_provider.lock.json'",
                     "'/mnt/payload/components/oxp3_full_efi_provider.lock.json'")):
        if text.count(old)!=1: raise ValueError('Full OXP3 fixed provider anchor differs')
        text=text.replace(old,new)
    return text.encode('utf-8')
