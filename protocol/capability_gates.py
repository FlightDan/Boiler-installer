"""Reviewed safety gates for legacy capabilities a package may not offer yet.

A gate is a policy decision, not a build accident. The handler can exist in a
package while the capability must stay unavailable until a separate, reviewed
condition is proven. This table is the single owner of those decisions: chains
*apply* it, validators *assert* it, and no chain may leave a gated capability
selectable.

This table holds only capabilities whose *handler* remains in the package but
whose availability is withheld by policy. A capability whose ownership has
moved to a model resource record is deliberately absent here: the resource
model owns it, and ``protocol.manifest``'s resource/handler conflict check is
the boundary that keeps both from writing the same targets. Listing such an
identity here as well would give one rule two definitions and two owners.

Everything here is a pure function of data. Nothing reads a package directory,
executes model code, inspects hardware or grants trust.
"""
from __future__ import annotations

import copy

POLICY_VERSION = 1


def _reason(en, zh):
    return {"en": en, "zh": zh}


# Model-independent. Keyed by the legacy capability identity that must never be
# selectable until its own verification condition is satisfied. A model that
# does not carry the capability is unaffected; this table never adds a handler.
#
# ``oxp3:wifi`` (BE201 firmware) is intentionally absent. The resource model
# (``resource:intel-be201-firmware``) owns that firmware: it carries the
# missing-only c106 policy and a transaction restore. Keeping the legacy handler
# gated here would be a second, weaker copy of a decision the resource record
# already makes, and it would re-admit the fail-open path this table replaced.
GATES = {
    "oxp3:gm7-resume": {
        "policy": "gm7-target-ssd-identity",
        "reason": _reason(
            "GM7 suspend recovery is unavailable until the target SSD identity is established.",
            "在确认目标 SSD 身份之前，GM7 挂起恢复不可用。"),
    },
}

_CATEGORIES = ("repairs", "plugins")


def evidence_row(identity):
    """The recorded proof that a reviewed policy gated this capability."""
    policy = GATES[identity]
    return {"id": identity, "policy": policy["policy"], "version": POLICY_VERSION}


def gated_ids(manifest):
    """Capabilities the table requires unavailable in this manifest, in order."""
    return [cap.get("id") for cap in manifest.get("legacy_capabilities", [])
            if isinstance(cap, dict) and cap.get("id") in GATES]


def apply_gates(manifest, selection=None):
    """Return ``(manifest, selection)`` with every reviewed gate enforced.

    Idempotent: applying to an already gated manifest changes nothing, so a
    chain may run this stage more than once. An existing non-empty ``reason`` is
    preserved verbatim; only a missing reason is filled from the policy, so a
    rebuilt base keeps its reviewed wording instead of silently drifting.
    """
    result = copy.deepcopy(manifest)
    choice = copy.deepcopy(selection) if selection is not None else None
    capabilities = result.get("legacy_capabilities")
    if capabilities is None:
        capabilities = []
    evidence = []
    for capability in capabilities:
        if not isinstance(capability, dict):
            continue
        identity = capability.get("id")
        if identity not in GATES:
            continue
        capability["available"] = False
        capability["default"] = False
        reason = capability.get("reason")
        if not isinstance(reason, dict) or not reason:
            capability["reason"] = dict(GATES[identity]["reason"])
        evidence.append(evidence_row(identity))
    if evidence:
        result["capability_gates"] = sorted(evidence, key=lambda row: row["id"])
        blocked = {row["id"] for row in evidence}
        if choice is not None:
            for category in _CATEGORIES:
                values = choice.get(category)
                if isinstance(values, list):
                    choice[category] = [value for value in values if value not in blocked]
    return result, choice


def validate_evidence(value):
    """Structural check for the manifest's recorded gate proof."""
    if not isinstance(value, list) or not value:
        raise ValueError("Capability gate evidence must be a nonempty list")
    seen = set()
    for row in value:
        if not isinstance(row, dict) or set(row) != {"id", "policy", "version"}:
            raise ValueError("Invalid capability gate evidence member")
        if not isinstance(row["id"], str) or not row["id"]:
            raise ValueError("Invalid capability gate identity")
        if not isinstance(row["policy"], str) or not row["policy"]:
            raise ValueError("Invalid capability gate policy")
        if type(row["version"]) is not int or row["version"] < 1:
            raise ValueError("Invalid capability gate policy version")
        if row["id"] in seen:
            raise ValueError("Duplicate capability gate evidence")
        seen.add(row["id"])


def violations(package, *, require_evidence=False):
    """Return one readable message per broken gate; empty means every gate holds.

    Reporting each failure separately is deliberate: a single combined error
    forces a reviewer to bisect by hand on the next rebuild, which is how a
    safety contract turns into folklore.

    ``require_evidence`` distinguishes the two legitimate audiences. A package
    already published before this table existed cannot acquire evidence
    retroactively, so changing its bytes is impossible and demanding the field
    would make every historical package unloadable. *Producing* a package,
    however, must prove which policy disabled the handler. Chains call with
    ``require_evidence=True``; loading an older package does not.
    """
    problems = []
    candidates = package.get("legacy_capabilities")
    if candidates is not None and not isinstance(candidates, list):
        return ["Capability list is not a list"]
    capabilities = [cap for cap in (candidates or []) if isinstance(cap, dict)]
    present = {cap.get("id"): cap for cap in capabilities}
    raw_evidence = package.get("capability_gates", [])
    evidence = {}
    for row in raw_evidence if isinstance(raw_evidence, list) else []:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            evidence.setdefault(row["id"], []).append(row)
    for identity, policy in GATES.items():
        capability = present.get(identity)
        if capability is None:
            if identity in evidence:
                problems.append("Stale gate evidence for an absent capability: " + identity)
            continue
        if capability.get("available") is not False:
            problems.append("Gated capability is still available: " + identity)
        if capability.get("default") is not False:
            problems.append("Gated capability is still selected by default: " + identity)
        rows = evidence.get(identity, [])
        if not rows:
            if require_evidence:
                problems.append("Gated capability lacks recorded policy evidence: " + identity)
            continue
        if len(rows) > 1:
            problems.append("Duplicate gate evidence for: " + identity)
            continue
        row = rows[0]
        if row.get("policy") != policy["policy"]:
            problems.append("Gate evidence names a different policy: " + identity)
        if row.get("version") != POLICY_VERSION:
            problems.append("Gate evidence predates the reviewed policy version: " + identity)
    return problems


def assert_gates(package, where="model package", *, require_evidence=True):
    """Raise on the first broken gate, naming exactly what failed.

    Chain callers keep the default: a chain that *produces* a package must
    record why a handler is unavailable.
    """
    problems = violations(package, require_evidence=require_evidence)
    if problems:
        raise ValueError("Unenforced capability gate in " + where + ": " + problems[0])
    return True
