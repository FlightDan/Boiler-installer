"""Classify supplied fingerprint observations against source-only provider records.

No hardware is opened or probed here. The caller must obtain observations from
its actual target and authenticate the provider records. A match is only a source
candidate, never permission to bind a device or install a driver. In particular,
an ACPI HID does not establish a chip variant, GPIO wiring or driver support.
"""


def candidates(providers, observations):
    """Return unambiguous source candidates or a closed, non-installable result.

    Each observation has a unique caller-supplied target device identity, a bus,
    and an ACPI HID. Callers must report the complete candidate-sensor set; a
    missing/unknown device cannot safely be resolved by a model or host DMI.
    """
    by_match = {}
    ids = set()
    for provider in providers:
        if (not isinstance(provider, dict)
                or type(provider.get('schema_version')) is not int
                or provider['schema_version'] != 1
                or provider.get('kind') != 'BoilerFingerprintProviderSource'
                or provider.get('source_only') is not True
                or provider.get('installable') is not False):
            raise ValueError('Expected a non-installable fingerprint source record')
        identifier = provider.get('id')
        match = provider.get('matching')
        if (not isinstance(identifier, str) or not identifier.startswith('fingerprint:')
                or len(identifier) == len('fingerprint:') or identifier in ids
                or not isinstance(match, dict) or set(match) != {'bus', 'acpi_hid'}
                or match['bus'] != 'spi' or not isinstance(match['acpi_hid'], str)
                or not match['acpi_hid'] or match['acpi_hid'] != match['acpi_hid'].strip()):
            raise ValueError('Invalid fingerprint provider identity')
        key = (match['bus'], match['acpi_hid'])
        if key in by_match:
            raise ValueError('Ambiguous fingerprint provider identity')
        ids.add(identifier)
        by_match[key] = identifier

    seen, matches, unknown = set(), [], []
    for observation in observations:
        if (not isinstance(observation, dict)
                or set(observation) != {'device_id', 'bus', 'acpi_hid'}
                or any(not isinstance(observation[k], str) or not observation[k]
                       or observation[k] != observation[k].strip()
                       for k in ('device_id', 'bus', 'acpi_hid'))
                or observation['device_id'] in seen):
            raise ValueError('Invalid or duplicate fingerprint observation')
        seen.add(observation['device_id'])
        provider_id = by_match.get((observation['bus'], observation['acpi_hid']))
        if provider_id is None:
            unknown.append(observation['device_id'])
        else:
            matches.append({'device_id': observation['device_id'], 'provider_id': provider_id})
    state = ('ambiguous' if len(matches) > 1 else
             'unknown' if unknown else
             'candidate' if matches else 'absent')
    return {'state': state, 'matches': matches, 'unknown': unknown, 'installable': False}
