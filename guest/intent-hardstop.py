"""Host termination before/after append, followed by fresh Linux intent recovery."""
import json
from pathlib import Path
import sys
from deployment import run, table, fs
from intent_store import IntentStore
from install_session import review, candidate, resume_review, execute_virtual, handoff_to_esp
from fixture_layout import preserved_files, profile, role_node, validate

mode = sys.argv[1]
profile_name = validate('/dev/vdd')
preserved_count = len(profile(profile_name)['preserved'])
choice = json.loads(Path('/mnt/payload/selection.json').read_text())
journal = IntentStore(Path('/mnt/session-state/oxp3'))
if mode in ('intent', 'appended'):
    accepted = review(choice, [candidate('/dev/vdd')])
    execute_virtual(choice, '/dev/vdc', '/dev/vdd', accepted, journal=journal, fault='pause-' + mode)
    raise RuntimeError('Expected host interruption did not occur')
if mode != 'recover': raise RuntimeError('Unknown recovery mode')
record = journal.load()
if record is None or record['status'] != 'prepared': raise RuntimeError('No interrupted prepared intent')
accepted = resume_review(record, candidate('/dev/vdd'))
print('OXP_INSTALL_RECOVERY=prepared-intent-with-' + str(accepted['already_appended']) + '-owned-parts', flush=True)
if fs(role_node('/dev/vdd', 'esp')): raise RuntimeError('ESP was formatted before the interrupted boundary')
result = execute_virtual(choice, '/dev/vdc', '/dev/vdd', accepted, journal=journal)
if table('/dev/vdd')['partitions'][:preserved_count] != record['before']['table']['partitions']:
    raise RuntimeError('Protected Windows partition metadata changed')
for part in preserved_files(profile_name):
    kind = 'vfat' if part['fs'] == 'vfat' else 'ntfs-3g'
    node = '/dev/vdd%d' % part['number']
    root = Path('/mnt/intent-check-windows'); root.mkdir(exist_ok=True)
    run('mount', '-t', kind, '-o', 'ro', node, str(root))
    try:
        if (root / part['sentinel']).read_text() != part['content']: raise RuntimeError('Protected file changed')
    finally: run('umount', str(root))
handoff_to_esp(result, journal)
if journal.load()['status'] != 'esp_journal_ready': raise RuntimeError('Intent handoff is not durable')
esp = next(p['node'] for p in table('/dev/vdd')['partitions'] if p.get('name') == 'esp')
root = Path('/mnt/intent-check-esp'); root.mkdir(exist_ok=True)
run('mount', '-t', 'vfat', '-o', 'ro', esp, str(root))
try:
    state = json.loads((root / 'oxp3/install-state.json').read_text())
    if state['append_intent_id'] != record['id'] or state['selection']['append_intent']['id'] != record['id']:
        raise RuntimeError('Original intent was not persisted in the ESP deployment journal')
    if state['copied'] or state['configured']: raise RuntimeError('This test must not copy or configure root systems')
finally: run('umount', str(root))
print('OXP_INSTALL_RECOVERY=esp-intent-windows-preserved-no-root-copy', flush=True)
