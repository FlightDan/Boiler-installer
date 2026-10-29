"""Data-only selections shared by installer and Linux model maintenance."""
from __future__ import annotations

from .manifest import ProtocolError


def validated_selection(package, repairs, plugins, *, installer_language='en-US',
                        kde_language='system', acknowledge_update_hold=False):
    if package.get('id') != 'device:oxp3' or package.get('selection_contract') != {
            'kind': 'oxp3-legacy-options-v1', 'component': 'oxp3:legacy-adaptation'}:
        raise ProtocolError('Package does not declare per-adaptation selections')
    if acknowledge_update_hold is not True:
        raise ProtocolError('Explicit SteamOS image-update hold acknowledgement is required')
    kde_languages = set(package.get('legacy_kde_languages', ['system']))
    kde_languages |= {value.replace('_', '-') for value in kde_languages}
    if installer_language not in ('zh-CN', 'en-US') or kde_language not in kde_languages:
        raise ProtocolError('Unsupported model language')
    known = {}
    capabilities = package.get('legacy_capabilities')
    if not isinstance(capabilities, list):
        raise ProtocolError('Package capability whitelist is missing')
    for item in capabilities:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not item['id'].startswith('oxp3:') or item['id'] in known
                or item.get('category') not in ('repairs', 'plugins')
                or not isinstance(item.get('dependencies'), list)):
            raise ProtocolError('Invalid package capability whitelist')
        known[item['id']] = item
    selected = set()
    for category, values in (('repairs', repairs), ('plugins', plugins)):
        if (not isinstance(values, list) or any(type(value) is not str for value in values)
                or len(values) != len(set(values))):
            raise ProtocolError('Select unique model ' + category)
        for identity in values:
            item = known.get(identity)
            if item is None or item['category'] != category or item.get('available') is not True:
                raise ProtocolError('Unavailable or unknown model option: ' + identity)
            selected.add(identity)
    if not selected:
        raise ProtocolError('Select at least one adaptation; use Restore to remove the current selection')
    ordered, visiting, done = [], set(), set()
    def visit(identity):
        if identity in done:
            return
        if identity in visiting:
            raise ProtocolError('Model dependency cycle')
        visiting.add(identity)
        for dependency in known[identity]['dependencies']:
            if type(dependency) is not str or dependency not in selected:
                raise ProtocolError('Missing model dependency: ' + str(dependency))
            visit(dependency)
        visiting.remove(identity); done.add(identity); ordered.append(identity)
    for identity in known:
        if identity in selected:
            visit(identity)
    return dict(schema_version=1, repairs=[x for x in ordered if known[x]['category'] == 'repairs'],
                plugins=[x for x in ordered if known[x]['category'] == 'plugins'],
                installer_language=installer_language, kde_language=kde_language,
                acknowledge_legacy_update_hold=True)
