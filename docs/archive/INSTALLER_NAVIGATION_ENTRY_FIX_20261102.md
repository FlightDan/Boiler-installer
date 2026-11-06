# Fresh installer navigation state

Opening an installation mode from the home screen could mark Installation
location red before the user visited it. The constructor's hidden placeholder
step, or a previous session's last step, was incorrectly recorded as a departed
page during the first synchronous mode-entry transition.

Mode entry now clears that transition's departure history and refreshes the
navigation state. Subsequent user navigation still records real departures;
incomplete visited pages and final review continue to show validation errors.
The target checks, disk selection, backup consent and execution validation are
unchanged.

The real home-card navigation checks assert that the unvisited target label is
not red after custom, imported and vanilla entry, and after selecting generic
installation from custom mode. Existing invalid-departure and capacity checks
remain separate regressions. Brush-state checks are functional WPF evidence;
they do not establish rendered-pixel or physical-device acceptance.

This is an installer-only local candidate. Maintenance27 and model13 are reused
byte-for-byte with their original producer identities and separate consumer
source associations. The running maintenance lifecycle remains bound to its
original UI15 package and unchanged maintenance/model bytes; it is not fresh
UI16 Windows application acceptance.
