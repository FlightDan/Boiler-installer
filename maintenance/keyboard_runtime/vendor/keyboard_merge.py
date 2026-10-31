def _merge_group(data, group, values):
    """KConfig permits localized keys; preserve unrelated lines verbatim."""
    lines = (data or b'').decode('utf-8').splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == '[' + group + ']']
    if len(starts) > 1:
        raise ValueError('Ambiguous duplicate configuration group: ' + group)
    if starts:
        start = starts[0]
    else:
        lines += ['', '[' + group + ']']
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('[')), len(lines))
    body = lines[start + 1:end]
    for key, value in values.items():
        matches = [i for i, line in enumerate(body) if line.split('=', 1)[0].strip() == key]
        if len(matches) > 1:
            raise ValueError('Ambiguous duplicate configuration key: ' + key)
        if matches:
            body[matches[0]] = key + '=' + value
        else:
            body.append(key + '=' + value)
    return ('\n'.join(lines[:start + 1] + body + lines[end:]) + '\n').encode()
