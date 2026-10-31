"""Pure schema checking for the fixed public fan configuration.

Candidate helper only: it neither creates files nor authorizes fan activation.
"""
IDENTITY = 'resource:onexplayer-fan'
TARGET = 'var/lib/oxp3-fan/config.json'
DEFAULT = b'{"mode":"auto"}\n'


def validate(data):
    if not isinstance(data, dict) or data.get('mode') not in ('auto', 'balanced', 'performance', 'custom'):
        raise ValueError('Invalid fan mode')
    if data['mode'] == 'custom':
        points = data.get('points')
        if not isinstance(points, list) or not 2 <= len(points) <= 8:
            raise ValueError('Custom fan curve requires 2..8 points')
        last_t, last_p = -1, -1
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError('Invalid fan curve point')
            t, p = point
            if type(t) is not int or type(p) is not int or not 30 <= t <= 85 or not 35 <= p <= 100 or t <= last_t or p < last_p:
                raise ValueError('Invalid fan curve values')
            last_t, last_p = t, p
        if (last_t, last_p) != (85, 100):
            raise ValueError('Fan curve must end at 85C / 100%')
    return data
