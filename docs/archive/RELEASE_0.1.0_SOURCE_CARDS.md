# Boiler 0.1.0-preview — system source cards

Local UI implementation, 2026-10-25. Implements the approved Figma source-page
prototype at https://www.figma.com/design/fc5HqhauVW8TFnGgLXiCKu. The user approved
the layout and requested one addition: a Skip upgrade checkbox beside 3.10.
Project/display versions remain 0.1.0 / 0.1.0-preview.

## Delivered behavior

- System source shows full-width official recovery and SteamOS 3.10 source
  cards. Browse and Download actions align on the right. The large generic
  add-files/device-folder box no longer appears on the default source page.
- Download reveals the existing downloader, including pinned sources, manual
  URL copy, terms, resume, restart and pause. Merely opening it starts no download.
  Download completion collapses the card; failures retain their visible status.
- Ordinary recovery/root/upgrade files received through real drop or card Browse
  are content-classified, checked for conflicting roles, normalized and associated
  through the existing commit path. Associated files show a green check and
  compact summary. The check means associated, not authenticated or fully
  inspected. Next retains complete source inspection and binding.
- Pending conflicts and unsupported inputs remain reviewable in a conditional
  disclosure and do not silently replace current sources. External device scripts
  and packages retain their explicit session trust and validation requirements.
- Skip upgrade reverses the existing Upgrade310 setting. It disables and closes
  upgrade selection/download, retains a selected file for later reuse, submits
  UpgradeTo310=false and OfficialSource=null, and survives a subsequent ordinary
  file drop. Unchecking restores the upgrade controls. Local paired-image mode
  still requires both files and hides Skip; complete IMG mode keeps one source.
- Read-only 3.10 inspection status is outside the download disclosure so it stays
  visible while the download panel is closed. Source footer text has its own grid
  column instead of overlapping Back.
- Chinese/English text, narrow layouts and existing presets are preserved.
  Firmware delivery and payload contents are unaffected by these UI changes.

## Verification and limits

- SourceCardsQa compiled with zero warnings/errors. Local preview executable:
  `BoilerInstaller-source-cards-qa.exe`; `--source-cards-preview` opens this page
  in rehearsal mode with automatic disk reads disabled.
- 44 source-card assertions passed: disclosure, skip/request flags, explicit skip
  preservation, automatic source association, native WM_DROPFILES, conflicts,
  device trust, paired/IMG modes and layout at 1280 and 640 pixels in both languages.
- Regression assertions passed: 25 input-association, 33 device, 96 menu/device
  route, 176 preset and 47 missing-source/download-busy UI checks.
- Sixteen non-empty WPF captures from
  `logs/source-cards-20261024T202625356/` were visually reviewed. Later runs
  returned empty pixels even for a solid drawing probe; those are explicitly
  recorded as unavailable, not accepted as visual passes. Later changes preserve
  these visual states and add skip preservation/status visibility behavior.
- Independent state/async/trust review found no blocker. Its inspection-status
  visibility observation was corrected in the final source.
- The broader `official-system-progress-checks` planning fixture was blocked by
  the existing workspace guard (107.5 GiB available versus 125 GiB required).
  The guard was not relaxed. This is not real image or device acceptance.
- No actual source downloads, package script execution, disk installation or
  physical-device changes were performed. Synthetic 5 GiB test roots are removed
  by the source-card harness after checks; reports/screenshots remain.

## Delivery checklist

1. Complete: behavior, affected entry, checks and limits recorded here.
2. Complete: Boiler UI, download hosts, intake paths, source inspection feedback,
   checks and source whitelist synchronized. Installer component/maintenance
   catalogs and payload regeneration are not applicable: no component identity,
   deployment action or guest payload changed.
3. Not applicable: no public or component version change.
4. Complete: targeted compilation, semantic checks, visual evidence and independent
   review. Broader planning fixture is blocked by the space condition above.
5. Not applicable: maintenance release remains explicitly deferred.
6. Not applicable: public download/device upgrades remain explicitly deferred.
7. Complete: local native preview rebuilt. Full archive/public release remains
   explicitly deferred under the UI interaction scope.
8. Not applicable: README versions remain unchanged.
9. Complete: local delivery reconciled against the scope and limitations above.
