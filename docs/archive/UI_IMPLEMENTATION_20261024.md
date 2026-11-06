# Boiler Installer prototype implementation — 2026-10-24

This is a local preview update to 0.1.0-preview. It is not a new public release.

## Implemented behavior

- The initial view occupies the application window and contains three entry
  cards: custom SteamOS, local IMG flashing, and Vanilla. The wizard is hidden
  while the entry view is active. Language selection and exit remain available.
- Each card enters its own installation route. Source selection is a single
  page; its former source sub-navigation is hidden. The sidebar can return to
  the installation methods while idle. Running operations prevent route changes.
- Local IMG flashing opens the existing imported-image route directly. One
  complete SteamOS disk IMG is sufficient; no separate recovery/3.10 image is
  requested. Compressed IMG and Boiler packages remain accepted. Complete A/B
  images retain their systems, software and data without component injection or
  a version upgrade. A five-role recovery image still needs preparation; a
  standalone rootfs image is not a complete disk image and remains rejected.
- The source has no fixed 64 GB size requirement. Its actual GPT layout is
  validated. Destination selection retains the existing minimum of 64 GiB and
  must fit the source system partitions; extra capacity extends home. The
  destination is an unallocated range, or space explicitly released by the
  existing separately confirmed data-partition deletion action. Other existing
  partitions remain protected by the existing deployment checks.
- Inspection displays the source layout's minimum destination capacity and
  uses it as the next target selection's default. A smaller selected capacity
  is rejected before installation. Raw-IMG workspace estimates include the
  inspection copy while pending, both installation copies and an 8 GiB reserve;
  physical source validation and controller preflight reject insufficient space.
  Additional boot staging or expansion still checks its own actual needs.
  ZIP estimates include declared expanded image bytes. BZip2 has no declared
  expanded length, so its UI explicitly shows a base-only estimate and defers
  full expansion/copy requirements to the existing streamed stage checks.
  Replacing an image clears its old inspection and target-capacity default.
- The reviewed official source is bound to its path, size, timestamp and SHA-256.
  Preparation copies and re-hashes that source into a private session snapshot.
  Changed inputs fail closed. The system version/build is checked against the
  prepared root before deployment; initial file inspection alone does not prove
  the root's version.
- The selected minimal boiler logo and exact Figma entry assets are embedded in
  the app. No temporary Figma asset URLs are used at runtime.
- The home view uses three columns on wide windows and a scrollable single
  column on narrow windows. Source content scrolls independently of its footer.
- The follow-up screenshot review shortened the compact English home action to
  `Back to home` and fixed sidebar reentry after scrolling to its end. Switching
  routes now brings the active step into view after layout, with stale-callback
  guards when the page or home view changes.

## Verification boundaries

The Windows source suite ran 192 tests: 165 passed and 27 were skipped for
platform/environment conditions. Synthetic source-binding checks cover changed
bytes, metadata changes, partial-copy cleanup, cancellation, missing inputs,
forbidden component injection, invalid cached input and legacy prebuilt behavior.
Independent review covered the new source commitment and route changes.

The final Debug build passed with zero warnings/errors. The final idle-workspace
UI run passed 1,387 assertions across 40 layout samples and 64 focus traversals,
in Chinese and English at 1280x800, 1060x720, 853x480 and 640x360 DIPs. It also
confirmed that route navigation creates no installation session or workspace
disk writes. The source-binding suite passed 32 checks, existing IMG/package
checks passed 79, and combined-source UI checks passed 47.

WPF geometry, focus traversal and routed control events are tested separately
from rendered pixels. An empty screenshot is never counted as visual acceptance.
The initial disconnected render environment returned empty captures. In the
connected remote session, nine nonempty WPF screenshots were produced. The home,
paired source, legacy import and component views were inspected in Chinese and
English, including the compact source page after the label and navigation fixes.
These are app-rendered captures; direct desktop input remains a separate scope.
Window sizes are device-independent units; these checks do not establish actual
OS scaling, physical keyboard/touch interaction, or device acceptance.

No physical disk installation, recovery, reboot, or real recovery/system-pair VM
installation is established by this update's synthetic checks. Earlier image and
maintenance results retain their own recorded source and artifact scope.

## Delivery checklist

| Item | State |
| --- | --- |
| Source, installation entry points, and route copy | Complete for this preview |
| Component IDs, defaults, guest scripts and Linux maintainer | Not applicable: this update changes the Windows app; component contents and Linux maintenance behavior are unchanged |
| Application assets and source export whitelist | Complete; new app sources and embedded assets are explicitly listed |
| Version and README | Not applicable: display version remains 0.1.0-preview; no published attachment is replaced |
| Source and synthetic source-binding tests | Complete within the boundaries above |
| WPF geometry and programmatic keyboard traversal | Complete within the measured scopes above |
| App-rendered screenshots and visual review | Complete for the recorded nine captures and measured sizes; direct desktop input and device acceptance remain separate |
| Fresh standalone Windows package | See the timestamped package sidecar and build-info for its source commit, size and SHA-256 |
| Public maintenance/installer publication | Blocked by the existing physical upgrade, reboot and recovery acceptance gates; this local preview does not satisfy those gates |

The local package must use a fresh whitelist-only checkout and exact locked build
inputs. Root workspace history, private device records and unpublished research
are excluded from source export and package contents.

## Single-IMG correction verification (2026-10-24)

The Release build passes with zero warnings/errors. The image/state suite passes
93 assertions, including raw A/B IMG imports of different synthetic sizes,
unchanged source bytes, validation without a second image, capacity budgets,
and rejection of a 64 GiB destination for a synthetic 96 GiB system layout.
The final WPF run passes 1,339 assertions in Chinese and English at four sizes;
nine nonempty screenshots were produced and the source views were inspected.
The targeted guest-integration suite passes 13 tests, with one Linux-only skip.
The standalone runtime was freshly verified/prepared against 703 locked files,
and the full five-component integration payload was regenerated and checked
against final source and locked inputs. The Linux maintainer contents are
unchanged, so a new maintainer release is not applicable to this correction.
These small GPT
fixtures establish parsing and routing, not a bootable operating system or
hardware installation. The final UI and standalone-package evidence is recorded
with the local candidate; public release and physical acceptance remain gated.

Known developer-mode boundary: the existing virtual deployment rehearsal still
requires its 80 GiB/protected-partition fixture. Variable-size raw import checks
and the physical target-capacity preflight do not establish support for arbitrary
images in that rehearsal. Physical writing, reboot and recovery were not run for
this correction.
