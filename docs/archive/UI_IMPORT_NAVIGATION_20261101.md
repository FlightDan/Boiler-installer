# Local import and wizard navigation acceptance

## Behavior

- Explicitly browsing for or dropping a local device package now validates and selects it in one operation. The import no longer asks for a source-trust checkbox or a second Use/Associate click.
- Recognized files in an unambiguous batch are associated automatically. Conflicting assignments, unsupported active inputs, changed files and invalid inventories still report the actual problem without committing a partial configuration. Ignored unrelated files do not prevent valid imports.
- Every component in a newly selected device package is checked initially, including components whose metadata previously requested explicit opt-in. Users can clear individual items or the entire device adaptation group. Selecting the same package again preserves those edits.
- Device selection uses the same wizard sidebar, content area and fixed footer as System source. Device support has a visible navigation entry; subsequent steps are numbered consistently.
- Returning from System source preserves the selected package and shows its name on the device choice screen. Starting a new installation mode clears the previous intake list.
- Navigation is held while import validation prepares the configuration. Cancellation and final file-stamp checks happen before the configuration commit.

## Boundaries

Local imports retain `developer-session` identity. They are not promoted to `signed-index`. Signed-index anchors and revocations, package inventory validation, maintenance-only package rejection, target/request binding and installation/recovery barriers remain unchanged. The removed confirmations belong to local file selection; disk execution approval is outside this change.

An ACL-denied pre-existing synthetic model cache entry was preserved. The catalog check uses an isolated fixture identity rather than changing that entry's permissions or contents.

Generic components, model payloads, runtime inventories, plan/helper/receipt formats and maintenance recovery behavior are unchanged. No device compatibility or physical-device acceptance is established by these UI changes.

## Delivery gates

| Gate | Status | Evidence / remaining work |
| --- | --- | --- |
| Windows Release build | Complete | Locked `scripts/build-app.ps1 -Configuration Release`; zero warnings and errors. |
| Local import and navigation regressions | Complete | `device-choice-checks`, `input-association-checks`, `boiler-device-checks`, `source-card-checks`, `model-catalog-checks`; evidence under `logs/ui-flow-*-20261101.txt`. |
| Rendered UI | Complete for synthetic WPF windows | Chinese/English captures at 1060×720 and 640×480; inspect sidebar/footer, selected defaults and retained opt-outs. Capture errors are reported independently. |
| Independent source review | Complete | Back-navigation package summary and mixed ignored-file findings fixed; targeted regressions added. |
| Full `boiler-checks` | Blocked | Workspace check requires 125 GiB; only approximately 19 GiB was available. No disk-space guard was bypassed. |
| Python source suite | In progress | `runtime/python/python.exe -B -m unittest discover -s tests -p 'test_*.py'`; `logs/ui-flow-python-tests-20261101.txt`. |
| Guest/Linux checks | Not applicable | No guest or Linux runtime changes in this patch; Windows source tests cannot establish Linux acceptance. |
| Installer archive/inventory/signing/public download | Not applicable | This is a local application rebuild, not a public release or repackaged installer. No published artifact is replaced. |
| Maintenance upgrade/recovery | Not applicable | No maintenance payload, version, migration, or persisted format changes. |
| Hardware / Explorer cross-integrity gesture | Not performed | Synthetic/native message fixtures do not establish physical installation or actual Explorer gesture acceptance. |

The local launcher remains `BoilerInstaller.exe` in the project root and uses the adjacent project application and locked runtime. It is not a standalone redistributable file.
