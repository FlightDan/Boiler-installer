# Unified input association — local UI/UX iteration

The user is discussing and testing UI/UX. This iteration updates the local
development application only. No installer archive, maintenance release, source
publication, network download or physical-device change is part of this work.

The home and wizard accept a batch of up to 32 ordinary local files/folders.
Further drops append to the current preview. Input contents determine recovery
disk, complete A/B disk, system partition, update bundle or model package/script
roles; file extensions alone do not certify the contents. ZIP/TAR metadata and
compressed image headers are bounded. Unsupported inputs remain visible with an
explanation. XZ disk images and oversized TAR previews require another supported
input format; no unsupported source is silently selected.

The preview does not change configuration. One association action fills only
the chosen source roles, retaining the current generic component choices,
destination and boot settings. Duplicate roles and incompatible source flows
must be resolved in the preview. Model scripts need trust for the current
selection. New installation routes clear that trust; re-associating the same
model preserves its component choices.

Ordinary system IMG files are associated by reference, without first copying or
hashing several GiB. Continue performs source inspection and full SHA-256
binding. Installation still snapshots the reviewed bytes and rejects changes.
Unusual extensions are normalized into managed copies only when the existing
execution interface requires one. No input code runs during discovery or
association. Device inventory verification precedes the synchronous UI commit;
cancellation and errors before that commit preserve the existing choices.

## Verification

- Local development build: zero warnings/errors.
- Content-discovery fixtures: GPT and CRC, system-root format, RAUC declarations,
  renamed files, bounded archives, links and unsafe paths, input changes and
  cancellation passed. These checks do not authenticate Valve signatures or
  infer a rootfs version from its filename or Btrfs header.
- Association UI/state checks: 25 passed, including multi-file native message
  intake, duplicate/conflicting roles, exact preservation of ordinary options,
  five-GiB source association without copying, cancellation, invalid inventory,
  changed inputs, numeric status metadata, same-model component choices and new
  route trust reset. Four final Chinese/English wide/compact captures reviewed.
- External source binding: five targeted checks passed, covering full-digest
  snapshots, changes with preserved metadata, cancellation cleanup and content
  inspection before binding an associated external source.
- Existing device selection checks and broader WPF layout/focus suite passed;
  exact reports remain under the local work/log directories.
- Protocol/import regression: 36 run, 35 passed, one host symlink-permission skip.
- The older complete official-system progress check stopped at its existing
  125-GiB rehearsal-space gate (121.6 GiB available); it is not reported passed.
  The targeted binding checks above cover this iteration's changed source path.

Synthetic native messages and rendered WPF captures do not establish actual
cross-integrity Explorer pointer gestures or touch feel. Manual interaction
review remains the purpose of the local preview. The existing OXP3 package at
work/ui-test-packages/oxp3-device-0.1.0-preview.1.zip can be used as an input.

## Scope checklist

| Item | State |
| --- | --- |
| UI and local source-role association | Complete for this iteration |
| Component IDs/defaults, guest logic and OS payloads | Not applicable: unchanged |
| Local development executable and focused verification | Complete within the scope above |
| Source export whitelist | Updated for the new app modules and this record; no export or commit performed |
| Installer packaging, signed maintenance and publication | Not performed: user explicitly deferred them during UI/UX discussion |
| Physical upgrade/reboot/recovery | Not performed: outside this UI interaction request |
| README and release versions | Not applicable: unchanged |

## Home drag/drop watermark and hint placement (2026-10-25)

The requested watermark illustrates the action of dragging files into the
window. An earlier preview incorrectly moved the product logo into the footer;
that interpretation is superseded. The original header logo is restored and
the footer logo is removed.

The user selected option D, the corner-framed drop target, from the four
comparison designs. The right-aligned hint now includes the selected 44x32-DIP
line drawing: four open corners, one file and an inward arrow, at 50% opacity.
Its three vector paths, #647B96 stroke and 2-unit stroke width match the selected
SVG. This native WPF vector stays sharp when scaled and requires no new image
payload. The hint has a transparent
surface; its text retains readable contrast. The drawing is decorative and
does not receive pointer or keyboard input. The complete hint remains one
accessible button with an explicit localized name.

The existing batch-file hint now sits above the three installation cards,
aligned to the right. At 1100 DIPs and wider it shares a row with the subtitle;
below that width it occupies its own row and wraps within the available space.
The original text, file picker action, drag/drop behavior and 48-DIP minimum
button target are retained. Product name and preview version stay in the header.

### Verification and delivery

- Selected D: final Debug and Release builds succeeded with zero warnings/errors.
  Both the normal launcher and the existing interaction-preview shortcut use
  updated app binaries. The 1060x790 WPF home render was inspected at
  `work/drag-drop-selected-d-20261025/home.png`; the symbol matches D in the
  selected comparison. Size, placement, opacity, localized name and file-picker
  behavior are unchanged. The mechanical layout scan returned no findings.
- The earlier independent review covered the hint layout. Earlier WPF geometry,
  focus and localization evidence below belongs to the preceding hint correction;
  the full suite was not repeated for this artwork-only replacement.
- That existing WPF suite was run with an isolated, process-only workspace so it
  did not traverse older test directories with restrictive permissions. The
  corrected report is under `work/drag-drop-watermark-20261025/ui-checks.json`.
- Its 230 home layout/focus/language/screenshot checks passed across Chinese and
  English at 1280x800, 1060x720, 853x480 and 640x360 DIPs. Final Chinese wide and
  compact captures and the English wide capture were visually inspected. The
  English hint wraps into two complete lines beside the small drawing.
- The complete older WPF suite is **not passed**: 1363 checks ran, with 1233
  passed and 130 failures. Its custom/Vanilla assertions still expect a home
  card to open source selection directly; the current workflow opens device
  adaptation first (`BoilerWorkflow.cs`). Those failures and screenshots remain
  in the report. No route logic was changed for this layout request.
- A mechanical layout scan returned no findings. These results establish WPF
  layout and programmatic focus behavior, not physical touch, OS scaling,
  installation, reboot or recovery acceptance.

| Required delivery item | State for this local preview |
| --- | --- |
| Update list and verification record | Complete: recorded above |
| Installation entry and component manifests | Complete for the Windows home entry; component IDs/defaults, guest scripts and OS payloads are unchanged and require no regeneration |
| Version and build identity | No version change: remains 0.1.0-preview; no existing public attachment replaced |
| Focused verification | Complete for the home view; full older route regression remains blocked by the mismatched expectations described above |
| Signed maintenance release | Not applicable: neither Linux maintenance implementation nor its payload changed |
| Published-maintenance download and device upgrade | Not applicable: no new maintenance release in this UI iteration |
| Complete installer archive and public release | Not delivered: a matching final whitelist-only public source commit, package build and release acceptance are still required before publication; local executable refresh is not an installer release |
| README | Not applicable: unchanged version, no prose edits |
| Final delivery audit | Local app and captures refreshed; archive/publication and physical-device acceptance are not claimed |
