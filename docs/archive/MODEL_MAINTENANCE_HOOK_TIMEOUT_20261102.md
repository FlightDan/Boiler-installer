# Model maintenance hook timeout

The UI14 installed-system rehearsal completed its model apply with a verified
receipt, then failed during the following verification. The model `inspect`
hook exceeded its retained 900-second subprocess bound. The preserved lifecycle
outcome SHA-256 is
`bb846f6b4ee6386a44500756b5375e60a4cb6b243e7327734573b6d0ae87829d`.
The original source image remained unchanged. The incomplete run did not reach
the final inactive-slot proof or restore/reapply acceptance.

Inspect is not a metadata-only operation after installation: it verifies the
managed generation, current paths and retained snapshots. Planning invokes
inspect and plan; execution repeats planning and the locked plan check before
running the requested action. All five permitted model hooks therefore now
use the same bounded 3600-second subprocess timeout. The bound applies to each
hook, not to the entire caller's multi-hook workflow.

The subprocess interface, request and receipt formats, reviewed-plan binding,
pending recovery barrier and protected-volume behavior are unchanged. A timeout
still preserves failure evidence. No failed transaction is resumed or repaired
automatically.

Targeted tests exercise the actual subprocess budget for all five actions,
preservation of failed write receipts and pending state, and a timed-out inspect
after completed apply with unchanged installed files and receipts and no new
pending action. Full source regression, actual packaged executable checks,
fresh installed-system lifecycle, visual checks and physical-device acceptance
remain separate gates. The source CPU baseline and model13 producer artifacts
are unchanged. This candidate is an unpublished local test build.
