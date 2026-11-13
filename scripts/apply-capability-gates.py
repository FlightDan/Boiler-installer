"""Enforce the reviewed legacy-capability gates on one model candidate.

This is a chain *stage*, not a finalizer: it is model-independent, idempotent
and safe to run more than once. Running it on an already gated package changes
no byte, so a rebuild chain may always end with it and never has to remember
whether an earlier step happened to disable the handler.

It never removes a handler, never edits a component payload and never grants
trust. It only makes a capability that a reviewed policy keeps unavailable
actually unavailable, records the policy evidence, and drops that capability
from the package's frozen default selection.

The current table covers ``oxp3:gm7-resume`` only. ``oxp3:wifi`` is owned by the
``resource:intel-be201-firmware`` resource record, whose ownership boundary is
the resource/handler conflict check in ``protocol.manifest``; this stage leaves
it to that check rather than duplicating the decision.

The gate table and its semantics live in ``protocol/capability_gates.py`` so the
installer, the maintenance runtime and the canonical overlay all assert the same
single definition.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.capability_gates import GATES, apply_gates, violations
from protocol.manifest import confined, validate_package


def sha(data): return hashlib.sha256(data).hexdigest()
def json_bytes(value): return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()


def inventory(directory):
    return [dict(path=path.relative_to(directory).as_posix(), size=path.stat().st_size,
                 sha256=sha(path.read_bytes()))
            for path in sorted(Path(directory).rglob('*'))
            if path.is_file() and path != Path(directory)/'manifest.json']


def enforce(directory, *, expected_manifest_sha256):
    """Apply the gates in place and return what actually changed.

    The caller supplies the anchored digest, so an unauthenticated package is
    never rewritten.
    """
    directory = Path(directory).absolute()
    manifest = validate_package(directory, expected_manifest_sha256=expected_manifest_sha256)
    selection_name = 'selection.json'
    selection = None
    if confined(directory, selection_name).is_file():
        selection = json.loads(confined(directory, selection_name).read_bytes())
    before = json_bytes(manifest)
    gated, choice = apply_gates(manifest, selection)
    if choice is not None and choice != selection:
        confined(directory, selection_name).write_bytes(json_bytes(choice))
    if gated == manifest:
        # Idempotent: the package already carries every reviewed gate. Re-verify
        # the declared evidence rather than trusting that it was ours.
        problems = violations(manifest, require_evidence=True)
        if problems:
            raise ValueError('Existing capability gate evidence is inconsistent: ' + problems[0])
        return dict(changed=False, gates=sorted(row['id'] for row in manifest.get('capability_gates', [])),
                    manifest_sha256=expected_manifest_sha256)
    gated['files'] = inventory(directory)
    (directory/'manifest.json').write_bytes(json_bytes(gated))
    anchor = sha((directory/'manifest.json').read_bytes())
    validate_package(directory, expected_manifest_sha256=anchor)
    problems = violations(gated, require_evidence=True)
    if problems:
        raise ValueError('Gate application did not close every gate: ' + problems[0])
    return dict(changed=True, previous_manifest_sha256=expected_manifest_sha256, manifest_sha256=anchor,
                gates=sorted(row['id'] for row in gated['capability_gates']),
                selection_changed=selection is not None and choice != selection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(enforce(args.package, expected_manifest_sha256=args.manifest_sha256), indent=2))


if __name__ == '__main__':
    main()
