"""Add the connector keyboard to a reviewed OXP3 InputPlumber template.

Derived from MIT upstream HHHHanasak1/onexplayer3-steamos-setup
e72e814da1944da3792076689995b6fe961f5768. No second event consumer or binary.
"""
import re

BLOCK = ('  # Controller connector: Console and Keyboard combinations (upstream issue #23).\n'
         '  - group: keyboard\n'
         '    unique: false\n'
         '    evdev:\n'
         '      name: HID 1a86:fe02\n'
         '      handler: event*\n')


def add_connector_keyboard(data):
    """Preserve other bytes and calibration; reject ambiguous custom sources."""
    if not isinstance(data, bytes) or len(data) > 512 * 1024 or b'\0' in data:
        raise ValueError('Expected bounded UTF-8 composite profile bytes')
    text = data.decode('utf-8')
    newline = '\r\n' if '\r\n' in text else '\n'
    normalized = text.replace('\r\n', '\n')
    if '\r' in normalized:
        raise ValueError('Unsupported profile line endings')
    if (len(re.findall(r'^source_devices:\s*$', normalized, re.M)) != 1 or
            len(re.findall(r'^kind: CompositeDevice\s*$', normalized, re.M)) != 1 or
            len(re.findall(r'^options:\s*$', normalized, re.M)) != 1):
        raise ValueError('Unsupported composite profile structure')
    start = normalized.index('source_devices:\n') + len('source_devices:\n')
    end = normalized.index('options:\n')
    if end <= start:
        raise ValueError('Unsupported source device ordering')
    if '1a86:fe02' in normalized:
        if normalized.count(BLOCK) == 1 and normalized.count('1a86:fe02') == 1:
            return data
        raise ValueError('Connector has a custom source; preserve it for review')
    return (normalized[:end] + BLOCK + normalized[end:]).replace('\n', newline).encode()
