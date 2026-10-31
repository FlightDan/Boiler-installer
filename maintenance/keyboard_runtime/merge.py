"""Conservative KConfig merge for the two reviewed keyboard keys only.

The retained public merge is kept byte-exact in vendor for provenance. This
wrapper fixes its section-boundary handling for indented KConfig group lines.
"""
import re


def merge(data, group, values):
    text = (data or b'').decode('utf-8')
    if '\0' in text:
        raise ValueError('Invalid NUL in keyboard configuration')
    lines = text.splitlines()
    headers = []
    for i,line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('['):
            if not re.fullmatch(r'(?:\[[^\[\]\r\n]+\])+', stripped):
                raise ValueError('Ambiguous keyboard configuration group')
            if stripped.startswith('['+group+']') and stripped != '['+group+']':
                raise ValueError('Managed keyboard group has unsupported modifiers')
            headers.append((i,stripped))
    starts = [i for i,name in headers if name == '['+group+']']
    if len(starts) > 1:
        raise ValueError('Ambiguous duplicate configuration group: '+group)
    if starts:
        start = starts[0]
        end = next((i for i,name in headers if i > start),len(lines))
    else:
        lines += ['', '['+group+']']
        start,end = len(lines)-1,len(lines)
    body = lines[start+1:end]
    for key,value in values.items():
        keys = [line.split('=',1)[0].strip() for line in body if '=' in line and not line.lstrip().startswith(('#',';'))]
        if any(name.startswith(key+'[') for name in keys):
            raise ValueError('Managed keyboard key has unsupported modifiers: '+key)
        matches = [i for i,line in enumerate(body) if '=' in line and line.split('=',1)[0].strip() == key]
        if len(matches) > 1:
            raise ValueError('Ambiguous duplicate configuration key: '+key)
        if matches:
            body[matches[0]] = key+'='+value
        else:
            body.append(key+'='+value)
    return ('\n'.join(lines[:start+1]+body+lines[end:])+'\n').encode('utf-8')
