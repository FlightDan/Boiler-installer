"""Model context from checked project TOONs, never an old model package/cache."""
import hashlib
from pathlib import Path

from protocol.resource_pool import decoder, local


def models(project):
    project = Path(project); toon = decoder(project/'adaptations'); result = []
    for name in ('oxp3', 'x2-mini'):
        path = 'brands/onexplayer/'+name+'/'+name+'.toon'
        raw = local(project/'adaptations', path).read_bytes(); guide = toon.loads(raw.decode(), strict=True)
        completed = {'microphone', 'speaker-route', 'xevmafix'} if name == 'oxp3' else {'microphone', 'battery'}
        pending = [dict(component=c['id'], status='pending-lifecycle-extraction', source_status=c['status'], sources=c.get('sources', []),
            reason='Requires isolated install/verify/restore and dependency closure; legacy bridge is not a resource') for c in guide['components'] if c['id'] not in completed]
        value = dict(schema_version=1, kind='BoilerResourceModel', id='device:'+name, name=guide['name'],
            hardware_matching=guide['runtime']['hardware_matching'], resources=[],
            parameters=dict(hdr=dict(sdr_white_nits=472 if name == 'oxp3' else 475, peak_nits=1100 if name == 'oxp3' else 700, status='pending-runtime-parameterization')),
            pending=pending, complete=False, official_release=False)
        result.append((value, dict(path='adaptations/'+path, sha256=hashlib.sha256(raw).hexdigest())))
    return result
