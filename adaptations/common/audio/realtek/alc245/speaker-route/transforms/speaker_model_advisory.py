"""Selected pure speaker advisory transform; source function SHA-256:
abaeb47246a8ec7990e3c94512db79e3ee8e8f087f2e291e1076343f1f9e12f8."""
def speaker_model_advisory(raw):
    old="    if not supported():\n        return 0\n"
    new="    if not supported():\n        print('Speaker route: model name is advisory; checking actual ALC245/audio interfaces.', flush=True)\n"
    text=raw.decode()
    if text.count(old)!=1: raise ValueError('Changed speaker model gate anchor')
    return text.replace(old,new).encode()
