# Device package source selection

## Behavior

- The device choice card separates installer-bundled packages from the online directory and manual imports. If bundled models are available, the bundled tab is the initial view. Otherwise the tabs are hidden and the online/import view is shown directly.
- Only packages declared in the current installer model catalog count as bundled. Imported files and cached signed-index downloads do not create bundled entries.
- The bundled view contains bundled model choices. The online view contains signed-index choices and guide-only models, with a separate local import area. A model can have a bundled choice and a signed online choice without merging the sources.
- Manual source/model choices survive asynchronous directory loads and language refresh. Browsing another source resets its candidate but preserves an already applied package, its component choices and provenance until a new model is selected.
- The selected source is visible. Imported packages show the input filename; GitHub cache hits retain GitHub provenance. The online path exposes the offline-cache/revocation warning even with details collapsed. Local browse/drop and input association keep their existing validation and automatic selection behavior.

## Boundaries

This is a local WPF application change. Package/index formats, signed anchors and revocations, developer-session trust, package validation, options serialization, model payloads, helper requests and recovery behavior are unchanged. Displaying a bundled model does not authorize or apply its package. No private input or research is exported and no public release is created.

## Delivery gates

| Gate | Status | Evidence / remaining work |
| --- | --- | --- |
| Windows Release build | Complete | Locked `scripts/build-app.ps1 -Configuration Release`; zero warnings and errors. |
| Device choice and import regressions | Complete | `device-choice-checks`, `boiler-device-checks`, `input-association-checks`; `logs/ui-device-sources-*-20261101.txt`. |
| Source-tab and provenance regressions | Complete | Final `model-catalog-checks` and explicit browse/drop provenance regressions passed; no automatic trust promotion. |
| Rendered UI | Complete for synthetic WPF windows | Inspected Chinese/English bundled and online views at 1060 and 640 pixels; evidence under `logs/model-catalog-20261031T203800206`. Detection shares the dropdown row so the primary device action remains visible. |
| Independent review | Complete | Reviewed source selection and import/refresh paths. Added visible offline-cache warning; provenance refresh concerns were checked against the shared refresh call and passing direct-import/drop regressions. Existing unrelated workspace changes were preserved. |
| Full `boiler-checks` | Blocked | Workspace has approximately 12.8 GiB free; the suite requires 125 GiB. The disk-space guard is retained. |
| Python source suite | In progress | `runtime/python/python.exe -B -m unittest discover -s tests -p 'test_*.py'`; `logs/ui-device-sources-python-20261101.txt`. |
| Guest/Linux validation | Not applicable | No guest or Linux runtime changes. |
| Installer packaging, signing and public download | Not applicable | Local application rebuild only; no public artifact is replaced. |
| Maintenance upgrade/recovery | Not applicable | No maintenance payload, persisted format, version or migration changes. |
| Hardware acceptance | Not performed | Synthetic UI fixtures do not establish physical-device acceptance. |

The project-root `BoilerInstaller.exe` launcher uses the application and runtime under this root. It is not a standalone redistributable installer.
