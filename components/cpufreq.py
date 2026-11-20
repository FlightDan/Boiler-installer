"""Static startup components for the standard Linux CPU frequency drivers."""
from pathlib import Path
from payload_lock import record

IDS = {'cpufreq:intel', 'cpufreq:amd'}


def selected(options):
    repairs = options.get('repairs', [])
    if not isinstance(repairs, list) or any(not isinstance(value, str) for value in repairs):
        raise ValueError('Repairs must be a list of component identities')
    values = [value for value in repairs if value.startswith('cpufreq:')]
    if len(values) > 1 or any(value not in IDS for value in values):
        raise ValueError('Choose at most one known CPU frequency vendor')
    return values[0] if values else None


def prepare(root, identity):
    if identity not in IDS:
        raise ValueError('Unknown CPU frequency component')
    vendor = identity.split(':')[1]
    unit = ('[Unit]\nDescription=Activate ' + vendor + ' CPU frequency driver\n'
            'Before=cpupower.service power-profiles-daemon.service tuned.service\n\n'
            '[Service]\nType=oneshot\nExecStart=/usr/bin/python3 -I -B /usr/lib/boiler/cpufreq-runtime.py --vendor ' + vendor + '\n').encode()
    dropin = b'[Unit]\nWants=boiler-cpufreq.service\n'
    prefix = 'cpufreq/' + vendor + '/'
    data = {prefix + 'cpufreq-runtime.py': (Path(root) / 'components/cpufreq_runtime.py').read_bytes(),
            prefix + 'boiler-cpufreq.service': unit,
            prefix + 'boiler-cpufreq.conf': dropin}
    targets = ['usr/lib/boiler/cpufreq-runtime.py', 'etc/systemd/system/boiler-cpufreq.service',
               'etc/systemd/system/multi-user.target.d/boiler-cpufreq.conf']
    files = [record(source, content, target=target, mode=0o644)
             for (source, content), target in zip(data.items(), targets)]
    component = dict(schema_version=1, kind='ComponentManifest', id=identity, version='0.1.0', files=files, requires=['core:maintenance'])
    return component, data


def metadata(root):
    result = {}
    for identity in sorted(IDS):
        component, _ = prepare(root, identity)
        result[identity] = dict(available=True, files=component['files'], component=component,
                                runtime='first_boot_required')
    return result
