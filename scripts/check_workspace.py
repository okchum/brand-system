"""Check that a brand workspace holds what its phase requires.

Usage: check_workspace.py WORKSPACE --phase N [--release]
Exit: 0 clean, 1 findings, 2 usage (bad arguments or WORKSPACE is not a directory).

The checker preserves the legacy file, status, brief, direction, and named
phase/gate checks. UI configuration and IR unit checks are added for the
phases that create them.

approvals.json is an append-only list. The last record naming a gate decides
that gate: a later changes-requested, pending or malformed record withdraws an
earlier approval. Unit reviews are likewise checked against the latest UI
manifest. The checker validates presence and shape only; it cannot judge
design quality or whether an approval was real.
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from validate_brief import validate_file
import directions_check

CONFIG = Path(__file__).resolve().parent.parent / "config/phase_requirements.json"
CONTRACT = json.loads(CONFIG.read_text(encoding="utf-8"))
REQUIRED = {int(k): v["required"] for k, v in CONTRACT["phases"].items()}

BLOCKER_FIELDS = ("reason", "impact", "owner", "workaround")
APPROVAL_FIELDS = ("scope", "snapshot", "confirmation", "approvedAt")
GATES = CONTRACT["gates"]
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__"}
EMPTY_OK = {".gitkeep", ".nojekyll", "__init__.py", "py.typed"}
UI_PLATFORMS = ("web", "desktop", "ios", "android")
STACK_PROFILES = ("html-css-js", "react")
DELIVERY_STATUSES = ("preview-only", "handoff-ready")
UNIT_KINDS = ("page-map", "layout", "reuse-analysis", "component", "page", "platform-adaptation")
UNIT_STATUSES = ("not-started", "in-progress", "in-review", "approved", "changes-requested")
NOT_STARTED, IN_PROGRESS, IN_REVIEW, APPROVED, CHANGES_REQUESTED = UNIT_STATUSES
GATE_STATES = ("pending", APPROVED, CHANGES_REQUESTED)
REVIEW_CONCLUSIONS = (APPROVED, CHANGES_REQUESTED)
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
# A unit id names its directory and Claude Code's write permission, so it must not carry path or glob syntax.
UNIT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
AGENT_ROLES = ("generation", "review")
# Reasoning effort values each engine's CLI accepts (codex: model_reasoning_effort; Claude Code: --effort).
AGENT_EFFORTS = {"codex": ("low", "medium", "high"), "claude": ("low", "medium", "high", "xhigh", "max")}
DEFAULT_ENGINE = "codex"
AGENT_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]*$")
# The phases that create config/ui.json and the UI manifest; units are generated and reviewed in the latter.
UI_CONFIG_PHASE, UI_UNIT_PHASE = (
    next(entry["phase"] for entry in CONTRACT["uiContract"]["phases"] if rel in entry["creates"])
    for rel in ("config/ui.json", "src/ui/ir/manifest.json")
)


def load_json(ws, rel, problems):
    try:
        return json.loads((ws / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        problems.append("%s: cannot read JSON (%s)" % (rel, e))
        return None


def contract_files(phase):
    # UI-created files extend the legacy phase contract without replacing it, and stay required through release.
    return [rel for entry in CONTRACT.get("uiContract", {}).get("phases", []) if entry["phase"] <= phase for rel in entry["creates"]]


def unique_strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value) and len(value) == len(set(value))


def inside_workspace(relative):
    """A manifest path names a file under the workspace: relative, without .. segments."""
    return isinstance(relative, str) and bool(relative.strip()) and not Path(relative).is_absolute() and ".." not in Path(relative).parts


def workspace_file(ws, relative):
    """The file a manifest path names, or None when it is missing or (through a symlink) outside the workspace."""
    if not inside_workspace(relative):
        return None
    path = Path(ws) / relative
    return path if path.is_file() and path.resolve().is_relative_to(Path(ws).resolve()) else None


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
    elif not unique_strings(platforms) or any(p not in UI_PLATFORMS for p in platforms):
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


def agent_settings_problems(agents):
    """Problems with config/ui.json "agents": which engine, model and reasoning effort run unit generation and review."""
    if not isinstance(agents, dict):
        return ["agents must be an object"]
    problems = []
    for role, setting in agents.items():
        label = "agents.%s" % role
        if role not in AGENT_ROLES:
            problems.append("%s is not a known role (use %s)" % (label, " or ".join(AGENT_ROLES)))
            continue
        if not isinstance(setting, dict):
            problems.append("%s must be an object" % label)
            continue
        for key in sorted(set(setting) - {"engine", "model", "reasoningEffort"}):
            problems.append("%s.%s is not a known field" % (label, key))
        engine = setting.get("engine", DEFAULT_ENGINE)
        if not isinstance(engine, str) or engine not in AGENT_EFFORTS:
            problems.append("%s.engine must be one of %s" % (label, list(AGENT_EFFORTS)))
            continue
        model = setting.get("model")
        if model is not None and (not isinstance(model, str) or not AGENT_MODEL_RE.match(model)):
            problems.append("%s.model must be a model name without spaces that does not start with -" % label)
        effort = setting.get("reasoningEffort")
        if effort is not None and effort not in AGENT_EFFORTS[engine]:
            problems.append("%s.reasoningEffort must be one of %s for %s" % (label, list(AGENT_EFFORTS[engine]), engine))
    return problems


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
    if "agents" in ui:
        problems.extend("config/ui.json: " + problem for problem in agent_settings_problems(ui["agents"]))
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
    if not isinstance(manifest.get("hash"), str) or not SHA256_RE.match(manifest.get("hash", "")):
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
        elif not UNIT_ID_RE.match(unit_id):
            problems.append("%s.id must use lowercase letters, digits and hyphens" % label)
        elif unit_id in by_id:
            problems.append("duplicate unit id %s" % unit_id)
        else:
            by_id[unit_id] = unit
        if unit.get("kind") not in UNIT_KINDS:
            problems.append("%s.kind is invalid" % label)
        files = unit.get("files")
        if not unique_strings(files) or not files or any(not path.strip() for path in files):
            problems.append("%s.files must be a non-empty unique string array" % label)
        elif not all(inside_workspace(path) for path in files):
            problems.append("%s.files must be relative paths inside the workspace" % label)
        platforms = unit.get("platforms")
        if not unique_strings(platforms) or not platforms or any(p not in UI_PLATFORMS for p in platforms):
            problems.append("%s.platforms contains an invalid platform" % label)
        hints = unit.get("platformHints")
        if hints is not None and (not unique_strings(hints) or any(p not in UI_PLATFORMS for p in hints)):
            problems.append("%s.platformHints contains an invalid platform" % label)
        depends = unit.get("dependsOn", [])
        if not unique_strings(depends) or any(not dep.strip() for dep in depends):
            problems.append("%s.dependsOn must be a unique string array" % label)
            depends = []
        if isinstance(unit_id, str):
            dependencies[unit_id] = depends
        for dep in depends:
            if dep == unit_id:
                problems.append("unit %s has a self dependency" % unit_id)
            elif dep not in by_id and not any(isinstance(item, dict) and item.get("id") == dep for item in units):
                problems.append("unit %s dependsOn missing unit %s" % (unit_id, dep))
        check_token_values(unit, "manifest unit %s" % unit_id, problems)
    # Every step of the fixed order must exist, or an older manifest silently skips one (e.g. reuse-analysis).
    kinds = {unit.get("kind") for unit in units if isinstance(unit, dict) and isinstance(unit.get("kind"), str)}
    for kind in UNIT_KINDS:
        if kind not in kinds:
            problems.append("src/ui/ir/manifest.json: manifest is missing unit kind %s" % kind)
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
    if status.get("state") not in CONTRACT["states"]:
        problems.append("project/status.json: state must be one of %s" % CONTRACT["states"])
    for key in ("completed", "next"):
        if not isinstance(status.get(key), list) or any(not isinstance(item, str) or not item.strip() for item in status.get(key, [])):
            problems.append("project/status.json: %s must be a list of non-blank strings" % key)
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


def check_unit_review(record, index, problems):
    if not present(record, "unitId"):
        problems.append("approval #%d (unit-review): unitId is required" % index)
    if record.get("status") not in REVIEW_CONCLUSIONS:
        problems.append("approval #%d (unit-review): status is invalid" % index)
    if not present(record, "manifestVersion"):
        problems.append("approval #%d (unit-review): manifestVersion is required" % index)
    for key in ("manifestHash", "outputHash"):
        if not isinstance(record.get(key), str) or not SHA256_RE.match(record[key]):
            problems.append("approval #%d (unit-review): %s is invalid" % (index, key))
    if not valid_file_scope(record.get("fileScope")):
        problems.append("approval #%d (unit-review): fileScope is invalid" % index)
    reviewer = record.get("reviewer")
    if not isinstance(reviewer, dict) or reviewer.get("type") != "subagent" or not present(reviewer, "name"):
        problems.append("approval #%d (unit-review): reviewer must be a named subagent" % index)
    if record.get("conclusion") not in REVIEW_CONCLUSIONS:
        problems.append("approval #%d (unit-review): conclusion is invalid" % index)
    elif record.get("status") != record.get("conclusion"):
        problems.append("approval #%d (unit-review): status must equal conclusion" % index)
    if "summary" in record and not present(record, "summary"):
        problems.append("approval #%d (unit-review): summary must be non-empty text" % index)
    evidence = record.get("evidence")
    if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence):
        problems.append("approval #%d (unit-review): evidence must be a non-empty string array" % index)
    if "gate" in record:
        problems.append("approval #%d: unit-review cannot be a gate" % index)


def latest_unit_reviews(records):
    latest = {}
    for record in records if isinstance(records, list) else []:
        if isinstance(record, dict) and record.get("kind") == "unit-review" and present(record, "unitId"):
            latest[record["unitId"]] = record
    return latest


def unit_output_hash(ws, unit):
    """sha256 over the unit's declared output files, or None when one is missing."""
    digest = hashlib.sha256()
    files = unit.get("files") if isinstance(unit, dict) else None
    for relative in files if isinstance(files, list) else []:
        path = workspace_file(ws, relative)
        if path is None:
            return None
        digest.update(relative.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return "sha256:" + digest.hexdigest()


def review_current(ws, review, manifest, unit):
    """An approved review still vouches for the unit: same manifest and same output bytes."""
    digest = unit_output_hash(ws, unit)
    return (
        digest is not None
        and review_matches(review, manifest)
        and review.get("conclusion") == APPROVED
        and review.get("outputHash") == digest
    )


def unsatisfied_dependencies(ws, manifest, status_by_id, latest_reviews, unit):
    units = {item.get("id"): item for item in manifest.get("units", []) if isinstance(item, dict)}
    missing = []
    for dep in unit.get("dependsOn", []) if isinstance(unit, dict) else []:
        if status_by_id.get(dep, NOT_STARTED) != APPROVED or dep not in units or not review_current(
            ws, latest_reviews.get(dep), manifest, units[dep]
        ):
            missing.append(dep)
    return missing


def latest_gate_states(records, problems):
    """The state each gate's latest record decides. Only a well-formed approved record counts as approved;
    a malformed record still withdraws the gate it names. The workbench reads gates through this too."""
    latest_gates = {}
    for i, record in enumerate(records if isinstance(records, list) else []):
        if not isinstance(record, dict):
            problems.append("approval #%d: needs gate in %s" % (i, GATES))
            continue
        kind = record.get("kind")
        if kind == "unit-review":
            continue
        gate = record.get("gate")
        named = gate if isinstance(gate, str) and gate in GATES else None
        if kind not in (None, "gate"):
            problems.append("approval #%d: kind must be gate or unit-review" % i)
            if named:
                latest_gates[named] = "invalid"
            continue
        if named is None:
            problems.append("approval #%d: needs gate in %s" % (i, GATES))
            continue
        state = record.get("status")
        if state not in GATE_STATES:
            problems.append("approval #%d (%s): status must be one of %s" % (i, gate, list(GATE_STATES)))
            state = "invalid"
        elif state == APPROVED:
            missing = [k for k in APPROVAL_FIELDS if not present(record, k)]
            if not (present(record, "version") or present(record, "hash")):
                missing.append("version or hash")
            if missing:
                problems.append("approval #%d (%s): approved without %s" % (i, gate, ", ".join(missing)))
                state = "invalid"
        latest_gates[gate] = state
    return latest_gates


def check_approvals(ws, phase, release, problems):
    records = load_json(ws, "project/approvals.json", problems)
    if records is None:
        return {}, {}
    if not isinstance(records, list):
        problems.append("project/approvals.json: must be a list of gate or unit-review records")
        records = []
    for i, record in enumerate(records):
        if isinstance(record, dict) and record.get("kind") == "unit-review":
            check_unit_review(record, i, problems)
    latest_gates = latest_gate_states(records, problems)
    needed = CONTRACT["phases"][str(phase)]["requiresApprovals"] + (CONTRACT["releaseApprovals"] if release else [])
    for gate in needed:
        if latest_gates.get(gate) != APPROVED:
            problems.append("phase %d requires gate %s approved (latest record decides)" % (phase, gate))
    return latest_gates, latest_unit_reviews(records)


def output_exists(ws, unit):
    files = unit.get("files") if isinstance(unit, dict) else None
    return isinstance(files, list) and bool(files) and all(
        workspace_file(ws, path) is not None and (ws / path).stat().st_size > 0 for path in files)


def review_matches(review, manifest):
    return isinstance(review, dict) and review.get("manifestVersion") == manifest.get("manifestVersion") and review.get("manifestHash") == manifest.get("hash")


def check_units(ws, phase, status, manifest, latest_reviews, problems):
    if not isinstance(manifest, dict) or not isinstance(manifest.get("units"), list) or not isinstance(status, dict) or phase < UI_UNIT_PHASE:
        return
    units = status.get("units")
    if not isinstance(units, list):
        problems.append("project/status.json: units are required when the UI manifest is present")
        return
    # check_manifest already reported units whose files are malformed; reading those files here would crash.
    manifest_units = {
        unit.get("id"): unit
        for unit in manifest["units"]
        if isinstance(unit, dict) and present(unit, "id") and unique_strings(unit.get("files")) and all(inside_workspace(p) for p in unit["files"])
    }
    for review_id, review in latest_reviews.items():
        if review_id not in manifest_units:
            problems.append("unit-review %s does not name a manifest unit" % review_id)
            continue
        unit_files = manifest_units[review_id].get("files") or []
        scopes = review.get("fileScope") if isinstance(review.get("fileScope"), list) else []
        if any(not isinstance(scope, dict) or scope.get("path") not in unit_files for scope in scopes):
            problems.append("unit-review %s fileScope must name its unit files" % review_id)
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
    if CHANGES_REQUESTED in status_by_id.values():
        problems.append("changes-requested blocks phase progression")
    for unit_id, unit in manifest_units.items():
        state = status_by_id.get(unit_id, NOT_STARTED)
        if state == IN_PROGRESS and unsatisfied_dependencies(ws, manifest, status_by_id, latest_reviews, unit):
            problems.append("unit %s: in-progress requires approved dependencies" % unit_id)
        if state == IN_REVIEW and not output_exists(ws, unit):
            problems.append("unit %s: in-review requires output" % unit_id)
        # Every unit, not only those marked approved: a regenerated last unit has no downstream unit to re-check it.
        if unit_id not in latest_reviews:
            problems.append("unit %s requires unit-review" % unit_id)
        elif not review_current(ws, latest_reviews[unit_id], manifest, unit):
            problems.append("unit %s: %s requires an approved unit-review matching the latest manifest and output" % (unit_id, state))
        elif state != APPROVED:
            problems.append("unit %s: %s must be approved to complete the phase" % (unit_id, state))


def check(ws, phase, release=False):
    ws = Path(ws)
    problems = []
    check_files(ws, phase, problems)
    brief = load_json(ws, "brand.brief.json", problems) if (ws / "brand.brief.json").is_file() else None
    if (ws / "brand.brief.json").is_file():
        problems.extend("brand.brief.json: " + e for e in validate_file(ws / "brand.brief.json"))
    check_brief(brief, problems)
    review = ws / "review/01-directions.html"
    if phase >= 1 and review.is_file():
        problems.extend("review/01-directions.html: " + e for e in directions_check.check(review))
    ui = load_json(ws, "config/ui.json", problems) if (ws / "config/ui.json").is_file() else None
    if phase >= UI_CONFIG_PHASE and ui is not None:
        check_ui_config(ui, brief, problems)
    manifest = load_json(ws, "src/ui/ir/manifest.json", problems) if (ws / "src/ui/ir/manifest.json").is_file() else None
    if phase >= UI_UNIT_PHASE and manifest is not None:
        check_manifest(manifest, ws, problems)
    status = check_status(ws, phase, problems) if (ws / "project/status.json").is_file() else None
    _, latest_reviews = check_approvals(ws, phase, release, problems) if (ws / "project/approvals.json").is_file() else ({}, {})
    check_units(ws, phase, status, manifest, latest_reviews, problems)
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
