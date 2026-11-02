# Short-window navigation and source footer

The UI16 packaged Windows checks passed all 32 new mode-entry color assertions,
but found 33 other functional layout/focus failures. At a requested 640 by 360
DIP window, the sidebar viewport was 41.7 DIP while wrapped navigation targets
were up to 55.56 DIP tall. The English source-page footer could also grow to
297.18 DIP as its supplementary hint wrapped in the remaining center column.

Short sidebars now release their vertical outer margin without reducing the
48-DIP controls. On client heights below 420 DIP, the source footer omits the
supplementary hint; the page's source instructions, navigation actions and
language controls remain available. The same visibility rule runs on page
navigation and window resizing, while the footer remains outside page scrolling.

The existing focus, active-tab re-entry, footer bounds and fixed-position checks
remain mandatory. Short-window hint-state assertions and child desired/actual
sizes supplement the existing diagnostics. Screenshot unavailability is separate
from functional WPF state and geometry acceptance.

This is a Windows-application-only local candidate. Maintenance27 and model13
remain byte-exact retained artifacts with original producer provenance. The
active UI15 maintenance lifecycle continues unchanged; it does not establish
new Windows UI acceptance. UI16 and its failed full-UI evidence remain preserved.
