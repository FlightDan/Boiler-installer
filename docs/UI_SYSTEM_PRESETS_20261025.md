# System presets — local UI preview

Boiler Installer remains at 0.1.0-preview. This iteration restores the missing
system/desktop language, SSH and deck user password controls on the Installation
options page. It follows the current UI/UX discussion scope; image deployment,
installer archives, maintenance releases and physical-device changes remain
deferred.

The common and device routes expose the same system presets. Language defaults
to keeping the image's language. SSH and password setup default to off. Password
setup expands two masked fields and requires matching values of at least eight
characters, within the existing UTF-8 limit. The Steam Gaming Mode language is
still selected within Steam.

Selections survive language refresh and a device/generic branch change. A new
installation mode resets them and clears both password fields. Opting out of
password setup or returning to the home page also clears both fields. Only the enable
flags and language enter the selected-options record and review summary; the
password text is excluded. Group visibility now uses an explicit preset panel
instead of hiding all children after a positional index.

These controls are explicitly marked as a feature preview. Boiler's generic
guest does not yet apply account, SSH or locale policy. Structural option
validation allows previewing and reviewing these choices, while the controller
rejects any non-default preset before creating an installation session. The
guest's independent rejection remains intact. This update must not be described
as working image customization or physical installation acceptance.

## Verification

- Debug build: zero warnings and zero errors.
- Focused WPF checks: 176 assertions passed, covering Chinese and English,
  custom and Vanilla modes, 1060x720, 853x480 and 640x360 DIPs, expanded password
  controls, scrolling, selection preservation, summary/serialization exclusion,
  reset/cleanup and controller refusal. Four nonempty rendered screenshots were
  captured and wide Chinese/compact English views inspected.
- Guest integration regression: 14 tests run, 13 passed and one skipped because
  native symlink creation is unavailable. This includes the existing rejection
  of unsupported account, SSH and language policy.
- Mechanical design scan: no reported findings. Rendered WPF and routed events
  are separate from physical keyboard/touch, OS scaling and device acceptance.
- Independent review found retained hidden password values on opt-out/home.
  Both paths now clear their inputs; the fixes passed independent re-review and
  the final focused run. The existing wireless option checks passed 52 assertions.
  The device-choice route regression passed after rerunning in the normal
  Windows user context; its earlier sandbox attempt could not access an existing
  fixture package cache. No device or installed account was changed.
- Final preset evidence: `work/system-preset-checks-1efcdc60564244c8bbbeebbfab86584d/result.json`.
  Targeted suite transcripts are retained in `logs/presets-final-*.txt`.

## Delivery checklist

| Item | State |
| --- | --- |
| Update list and local installation entry | Complete for the UI preview described above |
| Catalog, component IDs/defaults, dependencies and translations | Complete: existing catalog/defaults retained; new UI copy is paired Chinese/English |
| Guest payloads and maintenance synchronization | Not applicable: deployment support is deferred and existing rejection remains; no OS payload or maintenance source changed |
| Version/build identity | Complete: fresh local Debug program, unchanged 0.1.0-preview; no public asset replaced |
| Targeted verification | Complete within the recorded local scope; physical installation is not claimed |
| Signed maintenance release and public download/upgrade | Not applicable: user deferred release and device stages during UI/UX discussion |
| Full Windows archive and public Release | Not applicable: deferred by the same scope; local application compilation only |
| README versions | Not applicable: versions unchanged |
| Handoff | Local application and verification evidence; actual preset application remains future work |
