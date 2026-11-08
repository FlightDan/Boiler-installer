"""Model context from checked project TOONs, never an old model package/cache."""
import hashlib
from pathlib import Path

from protocol.resource_pool import decoder, local
from .model_source import onexplayer_source


def models(project):
    adaptations = Path(project)/'adaptations'
    toon = decoder(adaptations)
    index = toon.loads(local(adaptations, 'models.toon').read_text(encoding='utf-8'), strict=True)
    names = {row['source'] for row in index['models']}
    result = []
    for name in ('oxp3', 'x2-mini'):
        path = onexplayer_source(names, name)
        raw = local(adaptations, path).read_bytes(); guide = toon.loads(raw.decode(), strict=True)
        hdr = guide['resource_model']['hdr']
        completed = {'microphone', 'speaker-route', 'xevmafix'} if name == 'oxp3' else {'microphone', 'battery'}
        pending = [dict(component=c['id'], status='pending-lifecycle-extraction', source_status=c['status'], sources=c.get('sources', []),
            reason='Requires isolated install/verify/restore and dependency closure; legacy bridge is not a resource') for c in guide['components'] if c['id'] not in completed]
        value = dict(schema_version=1, kind='BoilerResourceModel', id='device:'+name, name=guide['name'],
            hardware_matching=guide['runtime']['hardware_matching'], resources=[],
            parameters=dict(hdr=dict(sdr_white_nits=hdr['full_frame'], peak_nits=hdr['peak'], status='pending-runtime-parameterization')),
            pending=pending, complete=False, official_release=False)
        result.append((value, dict(path='adaptations/'+path, sha256=hashlib.sha256(raw).hexdigest())))
    return result
