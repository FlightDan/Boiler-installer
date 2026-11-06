# Local installer UI refinement, 2026-10-26

This follow-up implements the five screenshot corrections for the Windows
0.1.0-preview interface. It remains local UI development and interaction testing.

- Choosing a catalog model prepares its package from the initial device card,
  then opens System source. The separate device subpage is no longer shown by
  that action or when returning from System source. Local/untrusted package
  import still requires its existing source confirmation and verification.
- Browse and Download sit to the right of the file/drop area on both source
  cards. They wrap below the file area in narrow windows. Upgrade opt-out,
  download disclosure and file association behavior are retained.
- One default-checked **安装机型包内所有适配 / Install all support in the device
  package** checkbox selects every component ID in the validated package. Opt-out
  clears those IDs. Reselecting the same package preserves the user's choices;
  selecting another package starts with its complete set. IDs, hashes, trust and
  guest execution contracts are unchanged.
- Short driver/system-setting notes follow the account settings at the bottom
  of the options. Workspace appears only on System source.
- System/desktop language defaults to the Windows user's UI culture, independent
  of the installer's Chinese/English toggle. Chinese region/script variants and
  supported English, Japanese, Korean, German, French, Spanish and Russian
  cultures map to the existing options. Unsupported Windows languages fall back
  to keeping the image language. Manual choices survive interface-language
  changes. Local pair/complete IMG modes continue to preserve the image language.

Package preparation disables device choices and navigation until it finishes.
Errors remain visible in the initial card. The Vanilla path promotes to custom
when choosing a device package and retains an unchanged source inspection.

## Verification

The final UiSimplifyQa build has zero warnings and errors. Five focused suites
pass 557 assertions: device routing/package choices 112, source cards 68,
system presets/language 252, input association 25 and graphics search 100.
Captures cover Chinese/English and wide/narrow layouts, package defaults,
opt-out, source-card states, language selection and bottom notes. The final
mechanical UI scan returned no findings.

Independent review found two issues in the direct model path: Vanilla promotion
and switching choices while package preparation was pending. Both were fixed
and rechecked. A delayed-action regression confirms that model editing,
branch/mode switching and returning home are blocked while preparing a package,
then restored afterward. Multi-component fixtures verify the aggregate checkbox
and manual choice preservation without executing package hooks.

The input-association fixture initially exhausted its workspace reserve by
allocating a 5 GiB zero-filled synthetic root. It now uses a sparse fixture with
the same logical bytes and marker; final checks pass without lowering production
space requirements or removing user files.

Evidence: `logs/ui-simplify-build-20261026.txt`,
`logs/ui-simplify-*-checks-20261026.json` and
`logs/ui-simplify-delivery-20261026.json`.

## Delivery checklist

| Item | Status |
| --- | --- |
| Update/release record | Complete: this record and RELEASE_0.1.0.md. |
| Installer options and selection logic | Complete: same schema and component IDs; all-package selection, Windows-language default, navigation and bilingual UI synchronized. |
| Component catalog, guest/maintenance scripts | Not applicable: no component, guest execution contract or maintenance behavior changes. |
| Payload/manifest regeneration | Not applicable: all affected runtime files are WPF UI code; no integration/maintenance payload input changed. |
| Version/README | Not applicable: still 0.1.0-preview; README untouched. |
| Source/build/interaction checks | Complete locally as recorded above. No new installation, reboot or physical-device acceptance is claimed. |
| Local preview entries/source whitelist | Complete: updated main and interaction launchers, previous entries backed up, affected paths in the explicit source whitelist. |
| Maintenance signing/release and public upgrade checks | Not applicable under the existing local-preview scope; publication remains deferred. |
| Full Windows archives/release/public downloads | Not applicable under the existing local-preview scope; no public assets replaced. |

Existing earlier preview windows must be reopened to load the new application
assembly. Direct injected desktop input remains outside the acceptance claimed
here; this environment previously denied Computer Use cursor access.
