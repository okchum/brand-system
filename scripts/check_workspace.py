"""Check that a brand workspace holds what its phase requires.

Usage: check_workspace.py WORKSPACE --phase N [--release]
Exit: 0 clean, 1 findings, 2 usage (bad arguments or WORKSPACE is not a directory).

The checker preserves the original file, status, and named gate checks. UI
configuration and IR unit checks are added for the phases that create them.
"""
import json
import os
import re
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
UI_PLATFORMS = ("web", "desktop", "ios", "android")
STACK_PROFILES = ("html-css-js", "react")
DELIVERY_STATUSES = ("preview-only", "handoff-ready")
UNIT_KINDS = ("page-map", "layout", "component", "page", "platform-adaptation")
UNIT_STATUSES = ("in-progress", "in-review", "approved", "changes-requested", "completed")
MANIFEST_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ROOT = Path(__file__).resolve().parents[1]


def load_json(ws, rel, problems):
    try:
        return json.loads((ws / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        problems.append("%s: cannot read JSON (%s)" % (rel, e))
        return None


def contract_files(phase):
    path = ROOT / "config/phase_requirements.json"
    try:
        requirements = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    # The frozen UI contract is checked at its creation phases; the release
    # phase retains the pre-existing file and gate semantics.
    result = []
    for entry in requirements.get("phases", []):
        if isinstance(entry, dict) and type(entry.get("phase")) is int and entry["phase"] <= phase and phase <= 4:
            result.extend(rel for rel in entry.get("creates", []) if isinstance(rel, str))
    return result


def check_files(ws, phase, problems):
    required = list(sum((REQUIRED[p] for p in range(phase + 1)), []))
    required.extend(contract_files(phase))
    for rel in required:
        if not (ws / rel).is_file():
            marker = "contract" if rel in contract_files(phase) else "phase %d" % next(
                p for p, files in REQUIRED.items() if rel in files
            )
            problems.append("missing %s (%s)" % (rel, marker))
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


def present(record, key):
    value = record.get(key)
    return isinstance(value, str) and bool(value.strip())


def check_frontend(frontend, label, problems):
    if not isinstance(frontend, dict):
        problems.append("%s must be an object" % label)
        return
    for key in ("platforms", "stackProfile", "deliveryStatus"):
        if key not in frontend:
            problems.append("%s.%s is required" % (label, key))
    platforms = frontend.get("platforms")
    if not isinstance(platforms, list) or not platforms:
        problems.append("%s.platforms must be a non-empty array" % label)
    elif len(platforms) != len(set(platforms)) or any(p not in UI_PLATFORMS for p in platforms):
        problems.append("%s.platforms contains an invalid platform" % label)
    if frontend.get("stackProfile") not in STACK_PROFILES:
        problems.append("%s.stackProfile must be one of %s" % (label, list(STACK_PROFILES)))
    if frontend.get("deliveryStatus") not in DELIVERY_STATUSES:
        problems.append("%s.deliveryStatus must be one of %s" % (label, list(DELIVERY_STATUSES)))


def check_brief(brief, problems):
    if not isinstance(brief, dict):
        return
    constraints = brief.get("constraints")
    if not isinstance(constraints, dict):
        return
    frontend = constraints.get("frontend")
    if frontend is not None:
        check_frontend(frontend, "brief.constraints.frontend", problems)


def check_claim_statuses(value, label, problems):
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower().replace("-", "")
            if lowered in {"status", "deliverystatus", "implementationstatus", "verification"} and child in {"implemented", "verified"}:
                problems.append("%s.%s cannot claim %s; use preview-only or handoff-ready" % (label, key, child))
            check_claim_statuses(child, "%s.%s" % (label, key), problems)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check_claim_statuses(child, "%s[%d]" % (label, index), problems)


def check_token_values(value, label, problems):
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            lowered = key_text.lower().replace("-", "_")
            if lowered in {"tokenvalue", "tokenvalues", "token_value", "token_values"}:
                problems.append("%s.%s must use token references, not token value" % (label, key_text))
                continue
            if "token" in lowered and "source" not in lowered and "ref" not in lowered and "name" not in lowered:
                if isinstance(child, (str, int, float, bool)):
                    problems.append("%s.%s must use token references, not token value" % (label, key_text))
                    continue
            check_token_values(child, "%s.%s" % (label, key_text), problems)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check_token_values(child, "%s[%d]" % (label, index), problems)


def check_adapter(adapter, problems):
    if adapter is None:
        return
    if not isinstance(adapter, dict):
        problems.append("config/ui.json: adapter must be an object")
        return
    for platform, details in adapter.items():
        if platform not in UI_PLATFORMS:
            problems.append("config/ui.json: adapter has invalid platform %s" % platform)
            continue
        if not isinstance(details, dict):
            problems.append("config/ui.json: adapter.%s must be an object" % platform)
            continue
        delivery = details.get("deliveryStatus", details.get("status"))
        if delivery is not None and delivery not in DELIVERY_STATUSES:
            problems.append("config/ui.json: adapter.%s status must be preview-only or handoff-ready" % platform)
        evidence = details.get("evidence")
        if delivery == "handoff-ready" and (not isinstance(evidence, list) or not evidence or any(not present({"x": x}, "x") for x in evidence)):
            problems.append("config/ui.json: adapter.%s.evidence is required for handoff-ready" % platform)
        check_token_values(details, "config/ui.json.adapter.%s" % platform, problems)


def check_ui_config(ui, brief, problems):
    if not isinstance(ui, dict):
        problems.append("config/ui.json: must be an object")
        return
    required = ("version", "platforms", "stackProfile", "tokenSource", "deliveryStatus")
    for key in required:
        if key not in ui:
            problems.append("config/ui.json: %s is required" % key)
    if not present(ui, "version"):
        problems.append("config/ui.json: version must be a non-empty string")
    check_frontend(ui, "config/ui.json", problems)
    if ui.get("tokenSource") != "tokens/src":
        problems.append("config/ui.json: tokenSource must be tokens/src")
    evidence = ui.get("evidence")
    if evidence is not None and (not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence)):
        problems.append("config/ui.json: evidence must be a non-empty string array")
    if ui.get("deliveryStatus") == "handoff-ready" and (not isinstance(evidence, list) or not evidence):
        problems.append("config/ui.json: handoff-ready requires evidence")
    check_adapter(ui.get("adapter"), problems)
    check_claim_statuses(ui, "config/ui.json", problems)
    check_token_values(ui, "config/ui.json", problems)
    constraints = brief.get("constraints") if isinstance(brief, dict) else None
    frontend = constraints.get("frontend") if isinstance(constraints, dict) else None
    if isinstance(frontend, dict):
        for key in ("platforms", "stackProfile", "deliveryStatus"):
            if key in frontend and key in ui and frontend[key] != ui[key]:
                problems.append("config/ui.json: %s does not match brief.constraints.frontend" % key)


def check_manifest(manifest, ws, problems):
    if not isinstance(manifest, dict):
        problems.append("src/ui/ir/manifest.json: must be an object")
        return None
    for key in ("manifestVersion", "hash", "units"):
        if key not in manifest:
            problems.append("src/ui/ir/manifest.json: %s is required" % key)
    if not present(manifest, "manifestVersion"):
        problems.append("src/ui/ir/manifest.json: manifestVersion must be a non-empty string")
    if not isinstance(manifest.get("hash"), str) or not MANIFEST_HASH_RE.match(manifest.get("hash", "")):
        problems.append("src/ui/ir/manifest.json: manifest hash must match sha256:<64 lowercase hex>")
    units = manifest.get("units")
    if not isinstance(units, list) or not units:
        problems.append("src/ui/ir/manifest.json: units must be a non-empty array")
        return None
    by_id = {}
    dependencies = {}
    for index, unit in enumerate(units):
        label = "manifest unit #%d" % index
        if not isinstance(unit, dict):
            problems.append("%s must be an object" % label)
            continue
        unit_id = unit.get("id")
        if not present(unit, "id"):
            problems.append("%s.id must be a non-empty string" % label)
        elif unit_id in by_id:
            problems.append("duplicate unit id %s" % unit_id)
        else:
            by_id[unit_id] = unit
        if unit.get("kind") not in UNIT_KINDS:
            problems.append("%s.kind is invalid" % label)
        if unit.get("status") not in UNIT_STATUSES:
            problems.append("%s.status is invalid" % label)
        files = unit.get("files")
        if not isinstance(files, list) or not files or any(not isinstance(path, str) or not path.strip() for path in files) or len(files) != len(set(files or [])):
            problems.append("%s.files must be a non-empty unique string array" % label)
        platforms = unit.get("platforms")
        if not isinstance(platforms, list) or not platforms or len(platforms) != len(set(platforms or [])) or any(p not in UI_PLATFORMS for p in platforms):
            problems.append("%s.platforms contains an invalid platform" % label)
        hints = unit.get("platformHints")
        if hints is not None and (not isinstance(hints, list) or len(hints) != len(set(hints or [])) or any(p not in UI_PLATFORMS for p in hints)):
            problems.append("%s.platformHints contains an invalid platform" % label)
        depends = unit.get("dependsOn", [])
        if not isinstance(depends, list) or len(depends) != len(set(depends or [])) or any(not isinstance(dep, str) or not dep.strip() for dep in depends):
            problems.append("%s.dependsOn must be a unique string array" % label)
            depends = []
        dependencies[unit_id] = depends
        for dep in depends:
            if dep == unit_id:
                problems.append("unit %s has a self dependency" % unit_id)
            elif dep not in by_id and not any(isinstance(item, dict) and item.get("id") == dep for item in units):
                problems.append("unit %s dependsOn missing unit %s" % (unit_id, dep))
        check_token_values(unit, "manifest unit %s" % unit_id, problems)
    visiting = set()
    visited = set()

    def visit(unit_id):
        if unit_id in visiting:
            return True
        if unit_id in visited or unit_id not in dependencies:
            return False
        visiting.add(unit_id)
        has_cycle = any(visit(dep) for dep in dependencies[unit_id])
        visiting.remove(unit_id)
        visited.add(unit_id)
        return has_cycle

    if any(visit(unit_id) for unit_id in dependencies):
        problems.append("manifest dependency cycle detected")
    return {unit_id: by_id[unit_id] for unit_id in by_id}


def check_status(ws, phase, problems):
    status = load_json(ws, "project/status.json", problems)
    if status is None:
        return None
    if not isinstance(status, dict):
        problems.append("project/status.json: must be an object")
        return None
    if type(status.get("phase")) is not int:
        problems.append("project/status.json: phase must be an integer")
    elif status["phase"] != phase:
        problems.append("project/status.json says phase %d, checked as phase %d" % (status["phase"], phase))
    blockers = status.get("blockers")
    if not isinstance(blockers, list):
        problems.append("project/status.json: blockers must be a list")
    else:
        for i, b in enumerate(blockers):
            missing = [k for k in BLOCKER_FIELDS if not (isinstance(b, dict) and b.get(k))]
            if missing:
                problems.append("project/status.json: blocker #%d missing %s" % (i, ", ".join(missing)))
    units = status.get("units")
    if units is not None and not isinstance(units, list):
        problems.append("project/status.json: units must be a list")
    return status


def valid_file_scope(value):
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict) or not present(item, "path"):
            return False
        if type(item.get("startLine")) is not int or item["startLine"] < 1:
            return False
        if type(item.get("endLine")) is not int or item["endLine"] < item["startLine"]:
            return False
    return True


def check_unit_review(record, index, latest_reviews, problems):
    unit_id = record.get("unitId")
    if not present(record, "unitId"):
        problems.append("approval #%d (unit-review): unitId is required" % index)
    if record.get("status") not in UNIT_STATUSES:
        problems.append("approval #%d (unit-review): status is invalid" % index)
    for key in ("manifestVersion", "manifestHash"):
        if not present(record, key):
            problems.append("approval #%d (unit-review): %s is required" % (index, key))
    if not isinstance(record.get("manifestHash"), str) or not MANIFEST_HASH_RE.match(record.get("manifestHash", "")):
        problems.append("approval #%d (unit-review): manifestHash is invalid" % index)
    if not valid_file_scope(record.get("fileScope")):
        problems.append("approval #%d (unit-review): fileScope is invalid" % index)
    reviewer = record.get("reviewer")
    if not isinstance(reviewer, dict) or reviewer.get("type") != "subagent" or not present(reviewer, "name"):
        problems.append("approval #%d (unit-review): reviewer must be a named subagent" % index)
    if record.get("conclusion") not in ("approved", "changes-requested"):
        problems.append("approval #%d (unit-review): conclusion is invalid" % index)
    evidence = record.get("evidence")
    if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence):
        problems.append("approval #%d (unit-review): evidence must be a non-empty string array" % index)
    if "gate" in record:
        problems.append("approval #%d: unit-review cannot be a gate" % index)
    if present(record, "unitId"):
        latest_reviews[unit_id] = record


def check_approvals(ws, phase, release, problems):
    records = load_json(ws, "project/approvals.json", problems)
    if records is None:
        return {}, {}
    if not isinstance(records, list):
        problems.append("project/approvals.json: must be a list of gate or unit-review records")
        records = []
    latest_gates = {}
    latest_reviews = {}
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            problems.append("approval #%d: needs gate in %s" % (i, GATES))
            continue
        kind = record.get("kind")
        if kind == "unit-review":
            check_unit_review(record, i, latest_reviews, problems)
            continue
        if kind not in (None, "gate"):
            problems.append("approval #%d: kind must be gate or unit-review" % i)
            continue
        gate = record.get("gate")
        if not isinstance(gate, str) or gate not in GATES:
            problems.append("approval #%d: needs gate in %s" % (i, GATES))
            continue
        state = record.get("status")
        if state not in GATE_STATES:
            problems.append("approval #%d (%s): status must be one of %s" % (i, gate, list(GATE_STATES)))
            state = "invalid"
        elif state == "approved":
            missing = [k for k in APPROVAL_FIELDS if not present(record, k)]
            if not (present(record, "version") or present(record, "hash")):
                missing.append("version or hash")
            if missing:
                problems.append("approval #%d (%s): approved without %s" % (i, gate, ", ".join(missing)))
                state = "invalid"
        latest_gates[gate] = state
    needed = GATES[:max(phase - 1, 0)] + (["G5"] if release else [])
    for gate in needed:
        if latest_gates.get(gate) != "approved":
            problems.append("phase %d requires gate %s approved (latest record decides)" % (phase, gate))
    return latest_gates, latest_reviews


def output_exists(ws, unit):
    files = unit.get("files", []) if isinstance(unit, dict) else []
    return bool(files) and all((ws / path).is_file() and (ws / path).stat().st_size > 0 for path in files)


def review_matches(review, manifest):
    return isinstance(review, dict) and review.get("manifestVersion") == manifest.get("manifestVersion") and review.get("manifestHash") == manifest.get("hash")


def check_units(ws, phase, status, manifest, latest_reviews, problems):
    if manifest is None or not isinstance(status, dict) or phase < 4:
        return
    units = status.get("units")
    if not isinstance(units, list):
        problems.append("project/status.json: units are required when the UI manifest is present")
        return
    manifest_units = manifest
    for review_id in latest_reviews:
        if review_id not in manifest_units:
            problems.append("unit-review %s does not name a manifest unit" % review_id)
    status_by_id = {}
    for index, item in enumerate(units):
        label = "project/status.json unit #%d" % index
        if not isinstance(item, dict):
            problems.append("%s must be an object" % label)
            continue
        allowed = {"unitId", "status", "updatedAt", "note"}
        unknown = sorted(set(item) - allowed)
        if unknown:
            problems.append("%s has unknown fields %s" % (label, ", ".join(unknown)))
        unit_id = item.get("unitId")
        if not present(item, "unitId") or unit_id not in manifest_units:
            problems.append("%s.unitId must name a manifest unit" % label)
            continue
        if unit_id in status_by_id:
            problems.append("duplicate status unit %s" % unit_id)
        status_by_id[unit_id] = item.get("status")
        if item.get("status") not in UNIT_STATUSES:
            problems.append("unit %s has invalid status" % unit_id)
    if any(status_value == "changes-requested" for status_value in status_by_id.values()) or any(
        unit.get("status") == "changes-requested" for unit in manifest_units.values()
    ):
        problems.append("changes-requested blocks phase progression")
    for unit_id, unit in manifest_units.items():
        state = status_by_id.get(unit_id, "not-started")
        depends = unit.get("dependsOn", []) if isinstance(unit, dict) else []
        if state == "in-progress" and any(status_by_id.get(dep, "not-started") not in ("approved", "completed") for dep in depends):
            problems.append("unit %s: in-progress requires approved dependencies" % unit_id)
        if state == "in-review" and not output_exists(ws, unit):
            problems.append("unit %s: in-review requires output" % unit_id)
        review = latest_reviews.get(unit_id)
        if state in ("approved", "completed"):
            if not review_matches(review, manifest) or review.get("conclusion") != "approved":
                problems.append("unit %s: %s requires an approved unit-review matching the latest manifest" % (unit_id, state))
        if unit_id not in latest_reviews:
            problems.append("unit %s requires unit-review" % unit_id)


def check(ws, phase, release=False):
    ws = Path(ws)
    problems = []
    check_files(ws, phase, problems)
    brief = load_json(ws, "brand.brief.json", problems) if (ws / "brand.brief.json").is_file() else None
    check_brief(brief, problems)
    ui = load_json(ws, "config/ui.json", problems) if (ws / "config/ui.json").is_file() else None
    if phase >= 3 and ui is not None:
        check_ui_config(ui, brief, problems)
    manifest = load_json(ws, "src/ui/ir/manifest.json", problems) if (ws / "src/ui/ir/manifest.json").is_file() else None
    manifest_units = check_manifest(manifest, ws, problems) if phase >= 4 and manifest is not None else None
    status = check_status(ws, phase, problems) if (ws / "project/status.json").is_file() else None
    _, latest_reviews = check_approvals(ws, phase, release, problems) if (ws / "project/approvals.json").is_file() else ({}, {})
    check_units(ws, phase, status, manifest_units, latest_reviews, problems)
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
