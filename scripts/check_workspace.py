"""Check that a brand workspace holds what its phase requires.

Usage: check_workspace.py WORKSPACE --phase N [--release]
Exit: 0 clean, 1 findings, 2 usage (bad arguments or WORKSPACE is not a directory).

Verifies required files for phases 0..N exist; no file anywhere in the
workspace is empty (known placeholders aside) or a dangling link;
brand.brief.json parses; status.json says the workspace is at phase N with
well-formed blockers; and gates G1..G(N-1) are approved (plus G5 with
--release, which needs --phase 5).

approvals.json is an append-only list. The last record naming a gate decides
that gate: a later changes-requested, pending or malformed record withdraws an
earlier approval. An approved record must carry scope, snapshot, confirmation,
approvedAt and a version or hash, each a non-blank string. Records naming no
gate are reported and otherwise ignored. It checks presence and shape only; it
cannot judge design quality or whether an approval was real.
"""
import json
import os
import sys
from pathlib import Path

REQUIRED = {
    0: ["brand.brief.json", "README.md", "project/plan.md", "project/status.json", "project/approvals.json",
        "project/decisions.md", "project/handoff.md", "docs/scope-matrix.md", "docs/environment.md",
        "docs/references.md", "docs/strategy.md", "docs/voice.md"],
    1: ["review/01-directions.html"],
    2: ["review/02-identity.html", "BRAND_SYSTEM.md", "config/brand.json",
        "docs/logo.md", "docs/color.md", "docs/typography.md"],
    3: ["review/03-system.html", "docs/accessibility.md", "config/quality.json"],
    4: ["review/04-assets.html", "config/platforms.json", "config/exports.json",
        "docs/platform-specs.md", "docs/licensing.md", "manifests/assets.source.json"],
    5: ["review/05-release.html", "CHANGELOG.md", "docs/handoff-by-role.md", "reports/qa-report.md"],
}
GATE_STATES = ("pending", "approved", "changes-requested")
BLOCKER_FIELDS = ("reason", "impact", "owner", "workaround")
APPROVAL_FIELDS = ("scope", "snapshot", "confirmation", "approvedAt")
GATES = ["G%d" % k for k in range(1, 6)]
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__"}
EMPTY_OK = {".gitkeep", ".nojekyll", "__init__.py", "py.typed"}


def load_json(ws, rel, problems):
    try:
        return json.loads((ws / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        problems.append("%s: cannot read JSON (%s)" % (rel, e))
        return None


def check_files(ws, phase, problems):
    for p in range(phase + 1):
        for rel in REQUIRED[p]:
            if not (ws / rel).is_file():
                problems.append("missing %s (phase %d)" % (rel, p))
    for dirpath, dirnames, filenames in os.walk(ws):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            f = Path(dirpath) / name
            try:
                empty = f.stat().st_size == 0
            except OSError:
                problems.append("dangling or unreadable file %s" % f.relative_to(ws))
                continue
            if empty and name not in EMPTY_OK:
                problems.append("empty file %s" % f.relative_to(ws))


def check_status(ws, phase, problems):
    status = load_json(ws, "project/status.json", problems)
    if status is None:
        return
    if not isinstance(status, dict):
        problems.append("project/status.json: must be an object")
        return
    if type(status.get("phase")) is not int:
        problems.append("project/status.json: phase must be an integer")
    elif status["phase"] != phase:
        problems.append("project/status.json says phase %d, checked as phase %d" % (status["phase"], phase))
    blockers = status.get("blockers")
    if not isinstance(blockers, list):
        problems.append("project/status.json: blockers must be a list")
        return
    for i, b in enumerate(blockers):
        missing = [k for k in BLOCKER_FIELDS if not (isinstance(b, dict) and b.get(k))]
        if missing:
            problems.append("project/status.json: blocker #%d missing %s" % (i, ", ".join(missing)))


def present(record, key):
    value = record.get(key)
    return isinstance(value, str) and bool(value.strip())


def check_approvals(ws, phase, release, problems):
    records = load_json(ws, "project/approvals.json", problems)
    if records is None:
        return
    if not isinstance(records, list):
        problems.append("project/approvals.json: must be a list of gate records")
        records = []
    latest = {}
    for i, a in enumerate(records):
        gate = a.get("gate") if isinstance(a, dict) else None
        if not isinstance(gate, str) or gate not in GATES:
            problems.append("approval #%d: needs gate in %s" % (i, GATES))
            continue
        state = a.get("status")
        if state not in GATE_STATES:
            problems.append("approval #%d (%s): status must be one of %s" % (i, gate, list(GATE_STATES)))
            state = "invalid"
        elif state == "approved":
            missing = [k for k in APPROVAL_FIELDS if not present(a, k)]
            if not (present(a, "version") or present(a, "hash")):
                missing.append("version or hash")
            if missing:
                problems.append("approval #%d (%s): approved without %s" % (i, gate, ", ".join(missing)))
                state = "invalid"
        latest[gate] = state
    needed = GATES[:max(phase - 1, 0)] + (["G5"] if release else [])
    for gate in needed:
        if latest.get(gate) != "approved":
            problems.append("phase %d requires gate %s approved (latest record decides)" % (phase, gate))


def check(ws, phase, release=False):
    ws = Path(ws)
    problems = []
    check_files(ws, phase, problems)
    if (ws / "brand.brief.json").is_file():
        load_json(ws, "brand.brief.json", problems)
    if (ws / "project/status.json").is_file():
        check_status(ws, phase, problems)
    if (ws / "project/approvals.json").is_file():
        check_approvals(ws, phase, release, problems)
    return problems


def main(argv):
    release = "--release" in argv
    args = [a for a in argv if a != "--release"]
    if len(args) != 3 or args[1] != "--phase" or args[2] not in {str(p) for p in REQUIRED} \
            or (release and args[2] != "5") or not Path(args[0]).is_dir():
        print(__doc__)
        return 2
    problems = check(args[0], int(args[2]), release)
    for msg in problems:
        print("FAIL " + msg)
    print("%d finding(s)" % len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
