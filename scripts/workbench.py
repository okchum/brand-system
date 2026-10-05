# -*- coding: utf-8 -*-
# Python 3.9 reads a script in fixed-size chunks and rejects a multi-byte character split across a chunk
# boundary unless the encoding is declared; the agent prompts here are long lines of Chinese text.
"""Run the browser-first brand-system workspace initializer.

Usage: python3 scripts/workbench.py [DIRECTORY] [--port PORT]
The server only writes inside the selected workspace after an explicit UI action.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import traceback
import threading
import time
import subprocess
import selectors
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import check_workspace
from check_workspace import APPROVED, CHANGES_REQUESTED, IN_PROGRESS, IN_REVIEW, NOT_STARTED

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "assets" / "brief.template.json"

HTML = (ROOT / "assets" / "workbench.html").read_text(encoding="utf-8")
DEFAULT_WORKSPACE_DIR = "brand-workspace"
# One agent run (a phase, a unit generation or a review) is stopped after this many seconds.
AGENT_TIMEOUT = 600


def page_html():
    # Choices come from the checker's tables, so the page offers exactly what validation accepts.
    contract = {
        "stacks": check_workspace.STACK_PROFILES, "platforms": check_workspace.UI_PLATFORMS,
        "defaultEngine": check_workspace.DEFAULT_ENGINE, "recordableGates": list(GATE_SPECS),
    }
    return HTML.replace("__AGENT_EFFORTS__", json.dumps({engine: list(levels) for engine, levels in check_workspace.AGENT_EFFORTS.items()})).replace("__UI_CONTRACT__", json.dumps(contract))


def is_workspace(path):
    return (path / "brand.brief.json").is_file() and (path / "project" / "status.json").is_file()


def recommended_folder(root):
    target = root / "brand"
    suffix = 2
    while target.exists():
        target = root / ("brand-%d" % suffix)
        suffix += 1
    return target


SCAN_FILE_LIMIT = 5000


def count_files(root):
    """Readable files under root, as text for the page; stops at SCAN_FILE_LIMIT so a home directory cannot stall it."""
    count = 0
    try:
        for item in root.rglob("*"):
            if item.is_file() and ".git" not in item.parts:
                count += 1
                if count >= SCAN_FILE_LIMIT:
                    return "%d+ 个可读取文件" % SCAN_FILE_LIMIT
    except OSError:
        return "部分内容无法读取（已读到 %d 个文件）" % count
    return "%d 个可读取文件" % count


def candidates(root):
    items = []
    if is_workspace(root):
        items.append({"path": str(root), "kind": "已有品牌工作区", "workspace": True})
    else:
        items.append({"path": str(root), "kind": "指定目录 · " + count_files(root), "workspace": False})
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name not in {".git", "node_modules", ".venv"}:
                items.append({"path": str(child), "kind": "已有目录" + (" · 品牌工作区" if is_workspace(child) else ""), "workspace": is_workspace(child)})
    return items


# Seeds dependsOn in a new manifest; generation and the checker both read the manifest afterwards.
UI_UNIT_DEPENDENCIES = {
    "page-map": (),
    "layout": ("page-map",),
    "reuse-analysis": ("layout",),
    "component": ("reuse-analysis",),
    "page": ("component",),
    "platform-adaptation": ("page",),
}
UI_UNIT_PHASE = check_workspace.UI_UNIT_PHASE
# ponytail: one lock for every state read-modify-write, shared by all workspaces and held across the advance
# checker run; it only guards writers inside this workbench process. Per-workspace locks if contention shows up.
STATE_LOCK = threading.RLock()


def _read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def _write_json(path, payload):
    # Write then rename, so a concurrent /api/state poll never reads half a file.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _validate_ui_selection(stack_profile, platforms):
    if stack_profile not in check_workspace.STACK_PROFILES:
        raise ValueError("未知 stack profile")
    if not isinstance(platforms, list) or not platforms or any(platform not in check_workspace.UI_PLATFORMS for platform in platforms):
        raise ValueError("platform 必须是 web、desktop、ios 或 android，且至少选择一个")
    if len(set(platforms)) != len(platforms):
        raise ValueError("platform 不能重复")


def unit_dir(unit_id):
    if not isinstance(unit_id, str) or not check_workspace.UNIT_ID_RE.match(unit_id):
        raise ValueError("unit id 无效：%r（只能用小写字母、数字和连字符）" % unit_id)
    return "src/ui/units/%s" % unit_id


def unit_output_path(unit_id):
    return unit_dir(unit_id) + "/output.html"


def _manifest_payload(stack_profile, platforms):
    # files are declared up front: they are hashed, so filling them in during progression would
    # invalidate every review already bound to the manifest.
    return {
        "manifestVersion": "1.0.0",
        "hash": "sha256:" + ("0" * 64),
        "units": [
            {
                "id": kind,
                "kind": kind,
                "files": [unit_output_path(kind)],
                "platforms": list(platforms),
                "dependsOn": list(UI_UNIT_DEPENDENCIES[kind]),
            }
            for kind in check_workspace.UNIT_KINDS
        ],
    }


def _manifest_hash(manifest):
    payload = copy.deepcopy(manifest)
    payload.pop("hash", None)
    for unit in payload.get("units", []):
        if isinstance(unit, dict):
            unit.pop("status", None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def sync_manifest_hash(path):
    path = Path(path).expanduser().resolve()
    manifest_path = path / "src/ui/ir/manifest.json"
    manifest = _read_json(manifest_path)
    manifest["hash"] = _manifest_hash(manifest)
    _write_json(manifest_path, manifest)
    return manifest["hash"]


def _unit_record(manifest, unit_id):
    for unit in manifest.get("units", []):
        if unit.get("id") == unit_id:
            return unit
    raise ValueError("未知 UI unit: %s" % unit_id)


def _unit_states(path):
    units = _read_json(path / "project/status.json").get("units", [])
    return {item.get("unitId"): item.get("status") for item in units if isinstance(item, dict)}


def set_unit_status(path, unit_id, status, note=None):
    """Write the unit status to status.json, the only record of progress; the manifest holds design content."""
    if status not in check_workspace.UNIT_STATUSES:
        raise ValueError("未知 UI unit status")
    path = Path(path).expanduser().resolve()
    with STATE_LOCK:
        unit = _unit_record(_read_json(path / "src/ui/ir/manifest.json"), unit_id)
        status_path = path / "project/status.json"
        state = _read_json(status_path)
        units = state.setdefault("units", [])
        record = next((item for item in units if item.get("unitId") == unit_id), None)
        if record is None:
            record = {"unitId": unit_id}
            units.append(record)
        record["status"] = status
        record.pop("note", None)
        if note:
            record["note"] = note
        record["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_json(status_path, state)
        return unit


def _dependents(manifest, unit_id):
    found, frontier = [], [unit_id]
    while frontier:
        current = frontier.pop()
        for unit in manifest.get("units", []):
            if current in unit.get("dependsOn", []) and unit["id"] not in found:
                found.append(unit["id"])
                frontier.append(unit["id"])
    return found


def prepare_unit_generation(path, unit_id):
    path = Path(path).expanduser().resolve()
    manifest = _read_json(path / "src/ui/ir/manifest.json")
    unit = _unit_record(manifest, unit_id)
    missing = check_workspace.unsatisfied_dependencies(
        path,
        manifest,
        _unit_states(path),
        check_workspace.latest_unit_reviews(_read_json(path / "project/approvals.json", [])),
        unit,
    )
    if missing:
        raise ValueError("dependencies 未 approved: %s" % ", ".join(missing))
    return unit


def begin_unit_generation(path, unit_id):
    """Claim a unit for (re)generation; units built on its previous output must be redone."""
    path = Path(path).expanduser().resolve()
    with STATE_LOCK:
        prepare_unit_generation(path, unit_id)
        manifest = _read_json(path / "src/ui/ir/manifest.json")
        states = _unit_states(path)
        for dependent in _dependents(manifest, unit_id):
            if states.get(dependent, NOT_STARTED) != NOT_STARTED:
                set_unit_status(path, dependent, CHANGES_REQUESTED, "上游 unit %s 已重新生成，需要按新产出重做" % unit_id)
        set_unit_status(path, unit_id, IN_PROGRESS)


def mark_unit_in_review(path, unit_id, generated_by=None):
    path = Path(path).expanduser().resolve()
    with STATE_LOCK:
        manifest = _read_json(path / "src/ui/ir/manifest.json")
        unit = _unit_record(manifest, unit_id)
        if _unit_states(path).get(unit_id) != IN_PROGRESS:
            raise ValueError("unit %s 不在生成中" % unit_id)
        missing = [relative for relative in unit.get("files", []) if not check_workspace.output_exists(path, {"files": [relative]})]
        if not unit.get("files") or missing:
            raise ValueError("unit %s 缺少 output：%s" % (unit_id, ", ".join(missing) or "未声明文件"))
        set_unit_status(path, unit_id, IN_REVIEW)
        manifest = _read_json(path / "src/ui/ir/manifest.json")
        metadata = {
            "unitId": unit_id,
            "status": IN_REVIEW,
            "files": unit["files"],
            "manifestVersion": manifest["manifestVersion"],
            "manifestHash": manifest["hash"],
            "outputHash": check_workspace.unit_output_hash(path, unit),
        }
        if generated_by:
            metadata["generatedBy"] = generated_by
        _write_json(path / unit_dir(unit_id) / "metadata.json", metadata)
        return metadata


def g1_choice(records):
    for record in reversed(records):
        if not isinstance(record, dict) or record.get("kind", "gate") != "gate" or record.get("gate") != "G1":
            continue
        # The latest G1 record decides; a withdrawal must not fall back to an older approval.
        if record.get("status") == APPROVED:
            snapshot = record.get("snapshot", "")
            confirmation = record.get("confirmation", "")
            for choice in ("A", "B", "C"):
                if snapshot == "direction-" + choice and choice in confirmation:
                    return choice
        break
    raise ValueError("缺少有效 G1 approved 方向选择")


def append_unit_review(path, unit_id, conclusion, reviewer, evidence, file_scope, output_hash, summary=None):
    if conclusion not in check_workspace.REVIEW_CONCLUSIONS:
        raise ValueError("unit review conclusion 无效")
    if not isinstance(reviewer, dict) or reviewer.get("type") != "subagent" or not str(reviewer.get("name") or "").strip():
        raise ValueError("unit review 必须由有名字的 subagent 提供")
    if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence):
        raise ValueError("unit review 必须包含非空 evidence")
    if not isinstance(file_scope, list) or not file_scope:
        raise ValueError("unit review 必须包含 fileScope")
    if summary is not None and (not isinstance(summary, str) or not summary.strip()):
        raise ValueError("unit review summary 必须是非空文字")
    path = Path(path).expanduser().resolve()
    with STATE_LOCK:
        manifest = _read_json(path / "src/ui/ir/manifest.json")
        unit = _unit_record(manifest, unit_id)
        if _unit_states(path).get(unit_id) != IN_REVIEW:
            raise ValueError("unit 尚未进入 in-review")
        for scope in file_scope:
            if not isinstance(scope, dict) or not isinstance(scope.get("path"), str) or type(scope.get("startLine")) is not int or type(scope.get("endLine")) is not int or scope["startLine"] < 1 or scope["endLine"] < scope["startLine"]:
                raise ValueError("fileScope 必须包含有效的 path 和行号")
            if scope["path"] not in unit["files"]:
                raise ValueError("fileScope 只能引用该 unit 的输出文件")
        digest = check_workspace.unit_output_hash(path, unit)
        if digest is None:
            raise ValueError("outputHash 无法核对：unit 的输出文件不存在")
        if output_hash != digest:
            raise ValueError("outputHash 与当前输出不一致：审查的不是这一版输出")
        record = {
            "kind": "unit-review",
            "unitId": unit_id,
            "manifestVersion": manifest["manifestVersion"],
            "manifestHash": manifest["hash"],
            "outputHash": output_hash,
            "fileScope": file_scope,
            "reviewer": reviewer,
            "conclusion": conclusion,
            "evidence": evidence,
            "status": conclusion,
            "reviewedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if summary is not None:
            record["summary"] = summary.strip()
        approvals_path = path / "project/approvals.json"
        approvals = _read_json(approvals_path, [])
        approvals.append(record)
        _write_json(approvals_path, approvals)
        set_unit_status(path, unit_id, conclusion)
        return record


def unit_overview(path):
    """Unit states, blockers, allowed actions and the current unit, computed here so the page only renders them."""
    path = Path(path).expanduser().resolve()
    manifest = _read_json(path / "src/ui/ir/manifest.json", {})
    if not manifest.get("units"):
        return {"units": [], "currentUnit": None, "phaseBlocker": ""}
    status = _read_json(path / "project/status.json")
    states = _unit_states(path)
    approvals = _read_json(path / "project/approvals.json", [])
    reviews = check_workspace.latest_unit_reviews(approvals)
    notes = {item.get("unitId"): item.get("note") for item in status.get("units", []) if isinstance(item, dict)}
    try:
        _require_unit_phase(path, UI_UNIT_PHASE)
        phase_blocker = ""
    except ValueError as exc:
        phase_blocker = str(exc)
    busy = _running_job(path)
    overview = []
    for unit in manifest["units"]:
        state = states.get(unit["id"], NOT_STARTED)
        blocked_by = check_workspace.unsatisfied_dependencies(path, manifest, states, reviews, unit)
        review = reviews.get(unit["id"])
        overview.append({
            "id": unit["id"],
            "kind": unit.get("kind"),
            "status": state,
            "note": notes.get(unit["id"]) or "",
            "dependsOn": unit.get("dependsOn", []),
            "blockedBy": blocked_by,
            "output": unit["files"][0],
            "outputExists": check_workspace.output_exists(path, unit),
            "review": None if review is None else {
                "conclusion": review.get("conclusion"),
                "reviewer": (review.get("reviewer") or {}).get("name"),
                "summary": review.get("summary") or "",
                "evidence": review.get("evidence", []),
                "current": check_workspace.review_current(path, review, manifest, unit),
            },
            "canGenerate": not phase_blocker and not blocked_by and not busy,
            "canReview": not phase_blocker and state == IN_REVIEW and not busy,
        })
    current = next((item["id"] for item in overview if item["status"] != APPROVED), None)
    return {"units": overview, "currentUnit": current, "phaseBlocker": phase_blocker}


def _normalize_agents(agents):
    """Drop empty fields and settings equal to the default (codex, its own model and effort), then validate."""
    if not agents:
        return {}
    if not isinstance(agents, dict):
        raise ValueError("AI 设置必须是对象")
    cleaned = {}
    for role, setting in agents.items():
        if role in check_workspace.AGENT_ROLES and isinstance(setting, dict):
            setting = {key: value for key, value in setting.items() if value not in (None, "")}
            if not setting or setting == {"engine": check_workspace.DEFAULT_ENGINE}:
                continue
        cleaned[role] = setting
    problems = check_workspace.agent_settings_problems(cleaned)
    if problems:
        raise ValueError("AI 设置无效：" + "；".join(problems))
    return cleaned


def init_workspace(path, official, one_liner, source_path=None, capabilities=None, stack_profile=check_workspace.STACK_PROFILES[0], platforms=None, agents=None):
    path = Path(path).expanduser().resolve()
    with STATE_LOCK:
        _refuse_while_running(path)
        return _init_workspace(path, official, one_liner, source_path, capabilities, stack_profile, platforms, agents)


def _init_workspace(path, official, one_liner, source_path, capabilities, stack_profile, platforms, agents):
    agents = _normalize_agents(agents)
    source = None
    if source_path:
        source = Path(source_path).expanduser().resolve()
        if not source.is_dir() or not source.is_relative_to(path.parent.parent):
            raise ValueError("参考资料目录必须是启动目录父目录下的可读取文件夹")
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()) and not is_workspace(path):
        # Existing files are safe to retain, but avoid silently claiming them as a workspace.
        if not (path / "brand.brief.json").exists():
            raise ValueError("目标目录已有内容，请选择新子目录，或明确使用已有品牌工作区")
    selected_platforms = ["web"] if platforms is None else platforms
    _validate_ui_selection(stack_profile, selected_platforms)
    if not (path / "brand.brief.json").exists():
        brief = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        brief["name"]["official"] = official or None
        brief["product"]["oneLiner"] = one_liner or None
        brief["product"]["capabilities"] = capabilities or []
        brief["name"]["final"] = bool(official)
        (path / "brand.brief.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = {
        "README.md": "# Brand workspace\n\nManaged by brand-system workbench.\n",
        "project/plan.md": "# Plan\n\nComplete brief, strategy, and three directions before G1.\n",
        "project/decisions.md": "# Decisions\n\nNo decisions recorded yet.\n",
        "project/handoff.md": "# Handoff\n\nOpen the workbench to continue.\n",
        "docs/scope-matrix.md": "# Scope matrix\n\n| module | status |\n|---|---|\n| brand foundation | required |\n",
        "docs/environment.md": "# Environment\n\nCreated by the browser workbench; verify tools during phase 0.\n",
        "docs/references.md": "# References\n\nNo external references recorded yet.\n",
        "docs/strategy.md": "# Strategy\n\nDraft pending brief completion.\n",
        "docs/voice.md": "# Voice\n\nDraft pending brief completion.\n",
    }
    for rel, content in files.items():
        target = path / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    if source_path and not (path / "project/source.json").exists():
        count = sum(1 for item in source.rglob("*") if item.is_file() and ".git" not in item.parts)
        references = path / "docs/references.md"
        if not references.exists() or references.read_text(encoding="utf-8").startswith("# References\n\nNo external"):
            references.write_text(
                "# References\n\nAI reference source directory (read-only): `%s`\n\n"
                "Files available at initialization: %d\n" % (source, count), encoding="utf-8"
            )
        source_files = [str(item.relative_to(source)) for item in sorted(source.rglob("*")) if item.is_file() and ".git" not in item.parts]
        _write_json(path / "project/source.json", {"root": str(source), "files": source_files})
    status_path = path / "project/status.json"
    if status_path.exists():
        status = _read_json(status_path)
    else:
        status = {"phase": 0, "state": "draft", "completed": [], "next": ["complete brief", "generate directions"], "blockers": []}
    if "units" not in status:
        status["units"] = [{"unitId": kind, "status": NOT_STARTED} for kind in check_workspace.UNIT_KINDS]
    _write_json(status_path, status)
    approvals_path = path / "project/approvals.json"
    if not approvals_path.exists():
        approvals_path.write_text("[]\n", encoding="utf-8")
    ui_path = path / "config/ui.json"
    if not ui_path.exists():
        ui = {"version": "1.0.0", "platforms": list(selected_platforms), "stackProfile": stack_profile, "tokenSource": "tokens/src", "deliveryStatus": "preview-only"}
        if agents:
            ui["agents"] = agents
        _write_json(ui_path, ui)
    elif agents:
        # Re-initializing keeps the existing UI config but must not silently drop newly chosen agents.
        ui = _read_json(ui_path)
        ui["agents"] = agents
        _write_json(ui_path, ui)
    manifest_path = path / "src/ui/ir/manifest.json"
    if not manifest_path.exists():
        _write_json(manifest_path, _manifest_payload(stack_profile, selected_platforms))
        sync_manifest_hash(path)
    return path


JOBS = {}
JOBS_LOCK = threading.Lock()

def _phase_requirements(phase):
    config = _read_json(ROOT / "config/phase_requirements.json")
    phases = config.get("phases", [])
    if isinstance(phases, dict):
        entry = phases.get(str(phase), {})
        return entry.get("required", entry.get("creates", []))
    for entry in phases:
        if int(entry.get("phase", -1)) == int(phase):
            return entry.get("required", entry.get("creates", []))
    return []


# Process groups of running agents; main() stops whatever is left when the workbench exits.
AGENT_GROUPS = set()


def _stop_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:  # nothing left in the group, or not ours to signal any more
        pass


def _run_agent(cmd, cwd, timeout, output_path=None):
    """Run one agent CLI (codex or Claude Code) to completion and return its exit code.

    With output_path, everything it printed is saved there for the caller to parse.
    """
    # Own process group, so stopping it also stops the commands the agent started before files are compared.
    # stdin is closed: Claude Code otherwise waits for piped input before starting.
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(cwd), start_new_session=True)
    AGENT_GROUPS.add(proc.pid)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.time() + timeout
    chunks = []
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                _stop_group(proc.pid)
                proc.wait()
                raise TimeoutError("%s 进程超过 %d 分钟未完成，已停止" % (cmd[0], timeout // 60))
            # os.read returns whatever is available; readline would block past the deadline on a partial line.
            if selector.select(timeout=min(1, remaining)):
                data = os.read(proc.stdout.fileno(), 65536)
                if not data:
                    break
                chunks.append(data)
            # Checked on every pass: a background child that keeps printing must not hide the leader's exit.
            if proc.poll() is not None:
                # The last lines (Claude's result JSON) may still be in the pipe; a child that keeps printing
                # would keep it readable forever, so draining stops after a short grace period.
                grace = time.time() + 1
                while time.time() < grace and selector.select(timeout=0.2):
                    data = os.read(proc.stdout.fileno(), 65536)
                    if not data:
                        break
                    chunks.append(data)
                break
        try:
            code = proc.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            _stop_group(proc.pid)
            proc.wait()
            raise TimeoutError("%s 进程超过 %d 分钟未完成，已停止" % (cmd[0], timeout // 60))
    finally:
        selector.close()
        proc.stdout.close()
        # Background commands the agent left running would otherwise keep writing after the restore check.
        _stop_group(proc.pid)
        AGENT_GROUPS.discard(proc.pid)
        if output_path:
            Path(output_path).write_text(b"".join(chunks).decode("utf-8", "replace"), encoding="utf-8")
    return code


def _claude_result(output_path):
    """The JSON result Claude Code printed; it can exit 0 and still report is_error (e.g. not logged in)."""
    text = Path(output_path).read_text(encoding="utf-8") if Path(output_path).is_file() else ""
    for line in reversed(text.splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("type") == "result":
            if data.get("is_error"):
                raise RuntimeError("Claude Code 报错：%s" % (data.get("result") or data.get("subtype") or "未知错误"))
            return data
    raise RuntimeError("Claude Code 没有给出可读的结果：%s" % (text.strip()[-300:] or "无输出"))


def _active_job(path):
    with JOBS_LOCK:
        for job_id, job in JOBS.items():
            if job.get("path") == str(path) and job["status"] == "running":
                return {"id": job_id, "unitId": job.get("unitId")}
    return None


def _running_job(path):
    job = _active_job(path)
    return job["id"] if job else None


def _create_job(path, prompt, unit_id=None):
    with JOBS_LOCK:
        if any(job.get("path") == str(path) and job["status"] == "running" for job in JOBS.values()):
            raise ValueError("该工作区已有正在运行的任务，请等它完成后再操作")
        job_id = str(time.time_ns())
        JOBS[job_id] = {
            "status": "running", "logs": [], "error": "", "updatedAt": time.time(), "step": "读取工作区资料",
            "prompt": prompt, "unitId": unit_id, "path": str(path), "check": None,
        }
    return job_id


def _job_log(job_id, message, step=None):
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["logs"].append("[" + time.strftime("%H:%M:%S") + "] " + message)
        job["updatedAt"] = time.time()
        if step:
            job["step"] = step


def _finish_job(job_id, status, error=""):
    with JOBS_LOCK:
        JOBS[job_id]["status"] = status
        JOBS[job_id]["error"] = error
        JOBS[job_id]["updatedAt"] = time.time()
    _job_log(job_id, "任务失败：" + error if error else "任务已完成。", "失败" if error else "已完成")


def _run_checker(path, phase):
    return subprocess.run([sys.executable, str(ROOT / "scripts/check_workspace.py"), str(path), "--phase", str(phase)], capture_output=True, text=True)


def _record_phase_check(job_id, path, phase):
    """The phase check is reported next to the job, not folded into it: mid-progression it is expected to fail."""
    _job_log(job_id, "正在运行 Phase %d 检查…" % phase, "运行 Phase %d 检查" % phase)
    check = _run_checker(path, phase)
    output = (check.stdout + check.stderr).strip()
    with JOBS_LOCK:
        JOBS[job_id]["check"] = {"passed": check.returncode == 0, "output": output}
    _job_log(job_id, ("检查通过。" if check.returncode == 0 else "检查未通过：") + ("" if check.returncode == 0 else output))


def _missing_gates(path, phase):
    """Gates config/phase_requirements.json requires for `phase` whose latest record is not approved."""
    latest = check_workspace.latest_gate_states(_read_json(path / "project/approvals.json", []), [])
    required = check_workspace.CONTRACT["phases"].get(str(phase), {}).get("requiresApprovals", [])
    return [gate for gate in required if latest.get(gate) != APPROVED]


def _require_gates(path, phase):
    missing = _missing_gates(path, phase)
    if missing:
        raise ValueError("Phase %d 生成前必须完成审批：%s" % (phase, ", ".join(missing)))


def _refuse_while_running(path):
    if _running_job(path):
        raise ValueError("该工作区有任务正在运行，完成后再记录审批、推进阶段或重新初始化")


GATE_SPECS = {
    "G1": (1, "Phase 1 strategy and selected visual direction", "用户在工作台选择方向 %s 并确认 G1"),
    "G2": (2, "Phase 2 core identity", "用户在工作台查看 Phase 2 身份审阅页并确认 G2"),
    "G3": (3, "Phase 3 design system", "用户在工作台查看 Phase 3 系统审阅页并确认 G3"),
    "G4": (4, "Phase 4 platform assets", "用户在工作台查看 Phase 4 资产审阅页并确认 G4"),
}
GATE_SNAPSHOTS = {"G2": "review-02-identity", "G3": "review-03-system", "G4": "review-04-assets"}
GATE_REVIEW_PAGES = {"G1": "方向审阅页", "G2": "身份审阅页", "G3": "系统审阅页", "G4": "资产审阅页"}


def record_gate(path, gate, choice=None, status=APPROVED):
    if gate not in GATE_SPECS:
        raise ValueError("只支持 G1、G2、G3 或 G4 审批")
    if status == CHANGES_REQUESTED:
        return _withdraw_gate(path, gate)
    if status != APPROVED:
        raise ValueError("审批状态只能是 approved 或 changes-requested")
    if gate == "G1" and choice not in ("A", "B", "C"):
        raise ValueError("G1 必须选择 A、B 或 C 方向")
    required_phase, scope, confirmation = GATE_SPECS[gate]
    with STATE_LOCK:
        _refuse_while_running(path)
        if int(_read_json(path / "project/status.json").get("phase", -1)) < required_phase:
            raise ValueError("当前阶段还不能记录 %s" % gate)
        approvals_path = path / "project/approvals.json"
        records = _read_json(approvals_path, [])
        records.append({
            "kind": "gate", "gate": gate, "status": APPROVED, "scope": scope,
            "snapshot": "direction-%s" % choice if gate == "G1" else GATE_SNAPSHOTS[gate],
            "confirmation": confirmation % choice if gate == "G1" else confirmation,
            "approvedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "version": "workbench-%s-%d" % (gate.lower(), int(time.time())),
        })
        _write_json(approvals_path, records)


def _withdraw_gate(path, gate):
    """Append a changes-requested record: the latest record decides the gate, older ones stay as history."""
    with STATE_LOCK:
        _refuse_while_running(path)
        approvals_path = path / "project/approvals.json"
        records = _read_json(approvals_path, [])
        latest = check_workspace.latest_gate_states(records, [])
        # Later gates were approved on top of this one (G2 builds on the G1 direction), so they are withdrawn too.
        withdrawn = [gate] + [later for later in GATE_SPECS if later > gate and latest.get(later) == APPROVED]
        for name in withdrawn:
            records.append({
                "kind": "gate", "gate": name, "status": CHANGES_REQUESTED, "scope": GATE_SPECS[name][1],
                "confirmation": "用户在工作台撤回 %s，要求修改" % gate,
                "requestedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
        _write_json(approvals_path, records)
        return withdrawn


def advance_phase(path, from_phase):
    # The lock is held across the checker run so no job can start between the check and the write.
    with STATE_LOCK:
        _refuse_while_running(path)
        status_path = path / "project/status.json"
        status = _read_json(status_path)
        phase = int(status["phase"])
        if phase != from_phase:
            raise ValueError("工作区阶段已经变化，请刷新后重试")
        if phase >= 5:
            raise ValueError("已经是最后阶段")
        missing = _missing_gates(path, phase + 1)
        if missing:
            raise ValueError("Phase %d 已生成交付物，但不能进入 Phase %d：请先查看%s并确认 %s" % (
                phase, phase + 1, GATE_REVIEW_PAGES.get(missing[-1], "审阅页"), "、".join(missing)))
        check = _run_checker(path, phase)
        if check.returncode != 0:
            raise ValueError("当前阶段检查未通过，不能推进：\n" + check.stdout + check.stderr)
        status["phase"] = phase + 1
        status["state"] = "draft"
        status["next"] = ["complete current phase", "run phase check"]
        _write_json(status_path, status)
        return phase + 1


def _require_unit_phase(path, phase):
    status = _read_json(path / "project/status.json")
    if phase != UI_UNIT_PHASE or int(status.get("phase", -1)) != UI_UNIT_PHASE:
        raise ValueError("UI unit 只能在 Phase 4（设计系统 G3 批准之后）生成和审查")
    _require_gates(path, UI_UNIT_PHASE)
    tokens = path / "tokens/src"
    if not tokens.is_dir() or not any(item.is_file() for item in tokens.rglob("*")):
        raise ValueError("缺少 tokens/src：UI unit 只能引用设计 token，先完成 Phase 3 的 tokens")


def _phase_prompt(phase, choice):
    deliverables = "、".join(_phase_requirements(phase))
    direction_instruction = (
        "Phase 5 发布页必须读取 config/brand.json 和 project/approvals.json，"
        "只展示 G1 已选中的当前方向（本工作区为 Direction %s），"
        "不要在最终发布页并列展示 A/B/C 方案；可在说明文字中记录选择依据。"
        % choice
        if choice
        else "Phase 1 先生成三套方向并准备后续 G1 选择，不要假设已有方向审批。"
    )
    return f"""在当前品牌工作区完成 Phase {phase} 交付。只写入当前工作区目录，不修改技能仓库或其他目录。读取 brand.brief.json、review/、docs/、project/ 和 config/ 中现有资料；如有 source.json，读取其中列出的参考资料。创建并验证本阶段必需文件：{deliverables}。保持方向选择、审批和暂定假设可追溯，不把未确认内容写成最终事实。确保每张方向卡片的 hero 标题、描述和图标有独立空间，文字与图标不能重叠，并检查浅色与深色背景下的对比度。每套方向必须包含一个较大的产品界面配色 demo，展示背景、文字、按钮、状态、层级和真实场景，不要只放色板。{direction_instruction}同步更新 project/status.json 为 phase {phase}、state in-review（其中的 units 字段原样保留，不要改动），并保存 reports/phase-{phase}-check.txt。不要只解释，直接创建文件。"""


def start_generation(path, phase):
    path = Path(path).expanduser().resolve()
    # Preconditions and the claim share the lock, so an advance or a withdrawn gate cannot slip between them.
    with STATE_LOCK:
        if int(_read_json(path / "project/status.json").get("phase", -1)) != phase or phase not in (1, 2, 3, 4, 5):
            raise ValueError("只能为当前阶段生成交付物")
        _require_gates(path, phase)
        choice = g1_choice(_read_json(path / "project/approvals.json", [])) if phase >= 2 else None
        prompt = _phase_prompt(phase, choice)
        job_id = _create_job(path, prompt)
        try:
            before = _snapshot(path, PHASE_PROTECTED_ROOTS)
            status_before = (path / "project/status.json").read_bytes()
        except Exception:
            with JOBS_LOCK:
                JOBS.pop(job_id, None)
            raise

    def run():
        cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "workspace-write", "-C", str(path), "--json", prompt]
        try:
            workspace_files = sorted(item for item in path.rglob("*") if item.is_file() and ".git" not in item.parts)
            _job_log(job_id, "已读取工作区资料：%d 个文件" % len(workspace_files))
            for item in (path / "brand.brief.json", path / "project/status.json", path / "project/source.json", path / "review/01-directions.html"):
                if item.is_file():
                    _job_log(job_id, "关键输入：" + str(item.relative_to(path)))
            _job_log(job_id, "正在生成 Phase %d 交付物…" % phase, "生成 Phase %d" % phase)
            run_error, code = None, None
            try:
                code = _run_agent(cmd, path, AGENT_TIMEOUT)
            except Exception as exc:
                run_error = exc
            # Approvals, the UI manifest, unit outputs and unit progress belong to other flows.
            try:
                changed = _restore(path, PHASE_PROTECTED_ROOTS, before) + _restore_unit_progress(path, status_before)
            except Exception as restore_error:
                raise RuntimeError(
                    ("%s；" % run_error if run_error else "")
                    + "恢复审批、UI 清单或 unit 内容时出错，这些文件可能仍被改动：%s" % restore_error
                )
            if changed:
                raise RuntimeError(
                    ("%s；" % run_error if run_error else "")
                    + "生成进程改动了审批、UI 清单或 unit 的内容，已恢复原样：" + "、".join(changed)
                )
            if run_error:
                raise run_error
            if code != 0:
                raise RuntimeError("生成进程退出码 %d" % code)
            for item in sorted(item for item in path.rglob("*") if item.is_file() and ".git" not in item.parts):
                if item not in workspace_files:
                    _job_log(job_id, "已生成：" + str(item.relative_to(path)))
            _record_phase_check(job_id, path, phase)
            _finish_job(job_id, "done")
        except Exception as exc:
            _finish_job(job_id, "error", str(exc))
    threading.Thread(target=run, daemon=True).start()
    return job_id


def _unit_feedback(path, unit_id):
    """What the last review and the unit's status note asked for, so a regeneration can act on it."""
    lines = []
    review = check_workspace.latest_unit_reviews(_read_json(path / "project/approvals.json", [])).get(unit_id)
    if review and review.get("conclusion") == CHANGES_REQUESTED:
        lines.append("最近一次审查（针对上一版输出）要求修改，逐条处理后重写，仍存在的问题都要解决：" + "；".join(review.get("evidence", [])))
    units = _read_json(path / "project/status.json").get("units", [])
    note = next((item.get("note") for item in units if isinstance(item, dict) and item.get("unitId") == unit_id), "")
    if note:
        lines.append("当前备注：" + note)
    return "".join(line + "。" for line in lines)


def _unit_prompt(path, unit, choice):
    ui = _read_json(path / "config/ui.json", {})
    units = {item["id"]: item for item in _read_json(path / "src/ui/ir/manifest.json").get("units", [])}
    inputs = "、".join(file for dep in unit.get("dependsOn", []) for file in units.get(dep, {}).get("files", [])) or "无"
    return f"""只完成 UI unit「{unit['id']}」（类型 {unit.get('kind')}），把页面写到 {'、'.join(unit['files'])}。只写入 {unit_dir(unit['id'])}/ 目录；不要修改 project/、config/、src/ui/ir/、tokens/ 以及其他 unit 的目录。读取 src/ui/ir/manifest.json、config/ui.json、brand.brief.json、tokens/src/，以及依赖 unit 的输出：{inputs}。按 {ROOT / "references/ui.md"} 中该类型的要求完成（该文件只读）；颜色、字号、间距只引用 tokens/src 的 token，不复制数值。目标平台：{'、'.join(ui.get('platforms', []))}；技术栈：{ui.get('stackProfile', '')}；Desktop 与 Mobile 以 Web preview 呈现，同时写清平台语义，方便转换为 native 代码。品牌方向为 Direction {choice}。{_unit_feedback(path, unit['id'])}不要只解释，直接创建文件。"""


def _review_prompt(unit):
    return f"""你是独立审查者，只读不写。审查 UI unit「{unit['id']}」（类型 {unit.get('kind')}）的输出 {'、'.join(unit['files'])}。对照 {ROOT / "references/ui.md"}、{ROOT / "references/accessibility.md"}、src/ui/ir/manifest.json 中该 unit 的 platforms 与 dependsOn、tokens/src/，以及依赖 unit 的输出。检查：是否满足该类型的职责，组件是否复用而不是重复造，状态（hover、focus-visible、disabled、loading、invalid、空、错误）是否齐全，颜色与间距是否只引用 token，平台语义是否写清，可访问性。每个问题给出严重度 P0–P3、位置和具体问题。只有没有 P0/P1 时结论才是 approved，否则是 changes-requested。"""


# Paths a unit job may not change outside its own unit directory.
PROTECTED_ROOTS = ("brand.brief.json", "project", "config", "tokens", "src/ui/ir", "src/ui/units")
# Paths a whole-phase job may not change: approvals, the UI manifest and unit outputs belong to other flows.
PHASE_PROTECTED_ROOTS = ("project/approvals.json", "src/ui/ir", "src/ui/units")


def _snapshot(path, roots, excluded=None):
    files = {}
    for root in roots:
        base = path / root
        items = [base] if base.is_file() else base.rglob("*") if base.is_dir() else []
        for item in items:
            if excluded and item.is_relative_to(excluded):
                continue
            # A job could swap a protected file for a link to a file outside the workspace; the restore below
            # must replace the link, not write through it.
            if item.is_symlink():
                files[str(item.relative_to(path))] = b"symlink -> " + os.fsencode(os.readlink(item))
            elif item.is_file():
                files[str(item.relative_to(path))] = item.read_bytes()
    return files


def _restore(path, roots, before, excluded=None):
    """Put protected files back as they were before a job. The job may not be the only writer (the user can edit
    in another program meanwhile), so each changed version is copied out first and its location reported."""
    after = _snapshot(path, roots, excluded)
    changed = sorted(rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel))
    backup = Path(tempfile.mkdtemp(prefix="brand-system-restore-")) if any(rel in after for rel in changed) else None
    reported = []
    for rel in changed:
        target = path / rel
        if rel in after:
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            (backup / rel).write_bytes(after[rel])
        if target.is_symlink() or rel not in before:
            target.unlink()
        if rel in before:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(before[rel])
        reported.append("%s（改动后的版本已保存到 %s）" % (rel, backup / rel) if rel in after else rel)
    return reported


def _protected_snapshot(path, unit_id):
    return _snapshot(path, PROTECTED_ROOTS, path / unit_dir(unit_id))


def _restore_outside_unit(path, unit_id, before):
    return _restore(path, PROTECTED_ROOTS, before, path / unit_dir(unit_id))


def _restore_unit_progress(path, status_bytes):
    """Put status.json's units back as they were; the phase job may still update phase and state.

    A rewrite that merely leaves units out follows the documented status shape and is repaired quietly;
    only units that are present and different count as tampering.
    """
    status_path = path / "project/status.json"
    before = json.loads(status_bytes)
    try:
        current = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        status_path.write_bytes(status_bytes)
        return ["project/status.json"]
    if not isinstance(current, dict):
        status_path.write_bytes(status_bytes)
        return ["project/status.json"]
    had_units = isinstance(before, dict) and "units" in before
    if had_units and current.get("units") == before["units"]:
        return []
    if not had_units:
        # A workspace without unit progress must not gain some from a phase job: that would count as approval.
        if "units" not in current:
            return []
        del current["units"]
        _write_json(status_path, current)
        return ["project/status.json 的 units"]
    tampered = "units" in current
    current["units"] = before["units"]
    _write_json(status_path, current)
    return ["project/status.json 的 units"] if tampered else []


CODEX_CONFIG = Path.home() / ".codex/config.toml"
VERDICT_SCHEMA = ROOT / "assets/unit-review-verdict.schema.json"


def _codex_config_value(key):
    """A top-level string setting from codex's own config (before any [table]), or None.

    With a profile active the value may come from that profile instead, so nothing is claimed.
    """
    try:
        text = CODEX_CONFIG.read_text(encoding="utf-8")
    except OSError:
        return None
    values = {}
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            break
        match = re.match(r"""\s*([A-Za-z_][\w-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""", line)
        if match:
            values[match.group(1)] = match.group(2) if match.group(2) is not None else match.group(3)
    if "profile" in values:
        return None
    return values.get(key)


def _agent_setting(path, role):
    """Engine, model and reasoning effort for unit generation or review; codex with its own defaults if unset."""
    agents = _read_json(path / "config/ui.json", {}).get("agents") or {}
    problems = check_workspace.agent_settings_problems(agents)
    if problems:
        raise ValueError("config/ui.json 的 AI 设置无效：" + "；".join(problems))
    setting = dict(agents.get(role) or {})
    setting.setdefault("engine", check_workspace.DEFAULT_ENGINE)
    return setting


def _agent_command(path, setting, prompt, unit_id=None, verdict_path=None):
    """Command line for one agent run. Without verdict_path it generates unit_id; with it, it reviews read-only."""
    review = verdict_path is not None
    model, effort = setting.get("model"), setting.get("reasoningEffort")
    if setting["engine"] == "codex":
        cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only" if review else "workspace-write", "-C", str(path)]
        cmd += ["-m", model] if model else []
        cmd += ["-c", 'model_reasoning_effort="%s"' % effort] if effort else []
        cmd += ["--output-schema", str(VERDICT_SCHEMA), "-o", str(verdict_path)] if review else ["--json"]
        return cmd + [prompt]
    # Claude Code has no OS sandbox: dontAsk denies every tool not listed, and only the unit's own directory is writable.
    tools = ["Read", "Glob", "Grep"]
    if not review:
        unsafe = sorted(set(str(path)) & set("*?[]{}()"))
        if unsafe:
            raise ValueError(
                "工作区路径含有 %s，Claude Code 的权限规则会把它当作匹配符号，无法精确限定到 unit 目录；"
                "请换用不含这些字符的路径，或把生成引擎改为 codex" % " ".join(unsafe)
            )
        own = path / unit_dir(unit_id)
        tools += ["Write(/%s/**)" % own, "Edit(/%s/**)" % own]
    cmd = ["claude", "-p", "--output-format", "json", "--permission-mode", "dontAsk", "--allowedTools"] + tools
    if review:
        schema = _read_json(VERDICT_SCHEMA)
        schema.pop("$schema", None)  # Claude Code's validator rejects the draft 2020-12 meta-schema reference
        cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    cmd += ["--model", model] if model else []
    cmd += ["--effort", effort] if effort else []
    return cmd + ["--", prompt]


def _run_agent_command(cmd, path, setting):
    """Run cmd; for Claude Code also return its parsed result, which decides success."""
    if setting["engine"] != "claude":
        return _run_agent(cmd, path, AGENT_TIMEOUT), None
    handle, output_path = tempfile.mkstemp(prefix="brand-system-claude-", suffix=".json")
    os.close(handle)
    try:
        code = _run_agent(cmd, path, AGENT_TIMEOUT, output_path=output_path)
        return code, _claude_result(output_path) if code == 0 else None
    finally:
        os.unlink(output_path)


def _agent_identity(setting, claude_data=None):
    """Engine, model and effort actually used: Claude Code reports its model; codex falls back to its config."""
    engine = setting["engine"]
    if engine == "claude":
        # modelUsage can also list a small helper model; the one that wrote the most output did the work.
        usage = (claude_data or {}).get("modelUsage") or {}
        main = max(usage, key=lambda name: (usage[name] or {}).get("outputTokens", 0), default=None)
        model, effort = main or setting.get("model"), setting.get("reasoningEffort")
    else:
        model = setting.get("model") or _codex_config_value("model")
        effort = setting.get("reasoningEffort") or _codex_config_value("model_reasoning_effort")
    identity = {"engine": engine, "model": model, "reasoningEffort": effort}
    return {key: value for key, value in identity.items() if value}


def _review_unit(job_id, path, unit_id):
    unit = _unit_record(_read_json(path / "src/ui/ir/manifest.json"), unit_id)
    setting = _agent_setting(path, "review")
    digest = check_workspace.unit_output_hash(path, unit)
    verdict_path = path / unit_dir(unit_id) / "review.json"
    if verdict_path.exists():
        verdict_path.unlink()
    _job_log(job_id, "正在启动独立审查进程（%s，只读）…" % setting["engine"], "审查 unit %s" % unit_id)
    code, claude_data = _run_agent_command(_agent_command(path, setting, _review_prompt(unit), verdict_path=verdict_path), path, setting)
    if code != 0:
        raise RuntimeError("审查进程退出码 %d" % code)
    if claude_data is not None:
        verdict = claude_data.get("structured_output")
        if not isinstance(verdict, dict):
            try:
                verdict = json.loads(claude_data.get("result") or "")
            except ValueError:
                raise ValueError("审查进程没有给出可读的结论")
        _write_json(verdict_path, verdict)
    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("审查进程没有给出可读的结论")
    findings = verdict.get("findings") if isinstance(verdict, dict) else None
    if (
        not isinstance(verdict, dict)
        or verdict.get("conclusion") not in check_workspace.REVIEW_CONCLUSIONS
        or not isinstance(verdict.get("summary"), str) or not verdict["summary"].strip()
        or not isinstance(findings, list)
        or any(not isinstance(item, dict) or item.get("severity") not in ("P0", "P1", "P2", "P3") for item in findings)
    ):
        raise ValueError("审查结论不符合 assets/unit-review-verdict.schema.json")
    with STATE_LOCK:
        if check_workspace.unit_output_hash(path, unit) != digest:
            raise ValueError("审查期间输出被改动，这次结论作废")
        evidence = [str(verdict_path.relative_to(path)), verdict["summary"].strip()] + [
            "%s %s：%s" % (item["severity"], item.get("location", ""), item.get("problem", "")) for item in findings
        ]
        scope = [
            {"path": rel, "startLine": 1, "endLine": max(1, len((path / rel).read_text(encoding="utf-8").splitlines()))}
            for rel in unit["files"]
        ]
        reviewer = dict({"type": "subagent", "name": "%s-unit-reviewer" % setting["engine"]}, **_agent_identity(setting, claude_data))
        record = append_unit_review(path, unit_id, verdict["conclusion"], reviewer, evidence, scope, digest, verdict["summary"])
    _job_log(job_id, "审查结论：%s · %s" % (verdict["conclusion"], verdict["summary"].strip()))
    return record


def _fail_unit_job(job_id, path, unit_id, exc):
    error = str(exc)
    try:
        state = _unit_states(path).get(unit_id)
        if state == IN_PROGRESS:
            set_unit_status(path, unit_id, IN_PROGRESS, "生成失败：%s" % exc)
        elif state == IN_REVIEW:
            set_unit_status(path, unit_id, IN_REVIEW, "自动审查失败：%s" % exc)
    except Exception as note_error:
        error += "（记录失败原因时也出错：%s）" % note_error
    # Always reached: a job left "running" refuses every later job, approval and advance in this workspace.
    _finish_job(job_id, "error", error)


def start_unit_job(path, phase, unit_id):
    """Generate one UI unit, then have an independent read-only agent process review it."""
    path = Path(path).expanduser().resolve()
    # Preconditions, claim and snapshot share STATE_LOCK: an approval or advance cannot land between them.
    with STATE_LOCK:
        _require_unit_phase(path, phase)
        setting = _agent_setting(path, "generation")
        _agent_setting(path, "review")  # an invalid review setting should stop the job before anything runs
        choice = g1_choice(_read_json(path / "project/approvals.json", []))
        unit = _unit_record(_read_json(path / "src/ui/ir/manifest.json"), unit_id)
        prompt = _unit_prompt(path, unit, choice)
        job_id = _create_job(path, prompt, unit_id)
        try:
            # Snapshot first: if it fails nothing has changed yet. begin_unit_generation then rewrites
            # status.json itself, and that write must count as "before" or the restore would undo it.
            before = _protected_snapshot(path, unit_id)
            begin_unit_generation(path, unit_id)
            before["project/status.json"] = (path / "project/status.json").read_bytes()
        except Exception:
            with JOBS_LOCK:
                JOBS.pop(job_id, None)
            raise

    def run():
        # No --add-dir for either engine: extra dirs become writable, and the skill repo must stay out of reach.
        cmd = _agent_command(path, setting, prompt, unit_id=unit_id)
        try:
            _job_log(job_id, "正在生成 unit %s（%s）…" % (unit_id, setting["engine"]), "生成 unit %s" % unit_id)
            run_error, code, claude_data = None, None, None
            try:
                code, claude_data = _run_agent_command(cmd, path, setting)
            except Exception as exc:
                run_error = exc
            # Restore before reporting anything: a timeout or crash may already have written outside the unit.
            try:
                changed = _restore_outside_unit(path, unit_id, before)
            except Exception as restore_error:
                raise RuntimeError(
                    ("%s；" % run_error if run_error else "")
                    + "恢复 unit 目录以外的文件时出错，这些文件可能仍被改动：%s" % restore_error
                )
            if changed:
                raise RuntimeError(
                    ("%s；" % run_error if run_error else "")
                    + "生成进程改动了 unit 目录以外的文件，已恢复原样：" + "、".join(changed)
                )
            if run_error:
                raise run_error
            if code != 0:
                raise RuntimeError("生成进程退出码 %d" % code)
            mark_unit_in_review(path, unit_id, _agent_identity(setting, claude_data))
            _job_log(job_id, "unit %s 已生成，进入审查。" % unit_id)
            _review_unit(job_id, path, unit_id)
            _record_phase_check(job_id, path, phase)
            _finish_job(job_id, "done")
        except Exception as exc:
            _fail_unit_job(job_id, path, unit_id, exc)
    threading.Thread(target=run, daemon=True).start()
    return job_id


def start_review_job(path, phase, unit_id):
    """Re-run only the independent review for a unit whose output is waiting for one."""
    path = Path(path).expanduser().resolve()
    with STATE_LOCK:
        _require_unit_phase(path, phase)
        if _unit_states(path).get(unit_id) != IN_REVIEW:
            raise ValueError("只有等待审查的 unit 可以重新审查")
        job_id = _create_job(path, "review " + unit_id, unit_id)

    def run():
        try:
            _review_unit(job_id, path, unit_id)
            _record_phase_check(job_id, path, phase)
            _finish_job(job_id, "done")
        except Exception as exc:
            _fail_unit_job(job_id, path, unit_id, exc)
    threading.Thread(target=run, daemon=True).start()
    return job_id


PREVIEW_TYPES = {".html": "text/html", ".htm": "text/html", ".md": "text/plain", ".txt": "text/plain", ".json": "application/json",
                 ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".ico": "image/x-icon", ".pdf": "application/pdf"}


class Handler(BaseHTTPRequestHandler):
    root = Path.cwd().resolve()

    @classmethod
    def workspace_path(cls, raw):
        """Resolve a workspace path the page sent; relative paths are relative to the startup directory."""
        path = Path(raw).expanduser()
        path = (path if path.is_absolute() else cls.root / path).resolve()
        if not path.is_relative_to(cls.root):
            raise ValueError("工作区必须位于启动目录内")
        return path

    def trusted_request(self):
        """Only the workbench page itself may call it: another site the user opens could otherwise POST approvals
        and start agents (CSRF), and a DNS-rebinding name would pass as same-origin without the Host check."""
        port = self.server.server_address[1]
        own = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        if self.headers.get("Host") not in own:
            return False
        origin = self.headers.get("Origin")
        # Browsers send Origin on every POST; scripts and agents calling the API directly send none.
        return origin is None or origin in {"http://" + host for host in own}

    def send_body(self, data, content_type, status=200, extra_headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload, status=200):
        self.send_body(json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def send_error_json(self, exc):
        if isinstance(exc, (ValueError, FileNotFoundError)):
            return self.send_json({"error": str(exc)}, 400)
        self.log_error("%s %s failed:\n%s", self.command, self.path, traceback.format_exc())
        self.send_json({"error": "工作台内部错误（%s），详情见启动 workbench 的终端" % type(exc).__name__}, 500)

    def do_GET(self):
        if not self.trusted_request():
            return self.send_json({"error": "forbidden"}, 403)
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                return self.send_body(page_html().encode(), "text/html; charset=utf-8")
            q = parse_qs(parsed.query)
            if parsed.path == "/preview":
                preview_path = self.workspace_path(q.get("path", [""])[0])
                relative = q.get("file", [""])[0]
                if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
                    raise ValueError("预览文件路径无效")
                target = (preview_path / relative).resolve()
                if not target.is_relative_to(preview_path) or not target.is_file():
                    raise ValueError("预览文件不存在")
                content_type = PREVIEW_TYPES.get(target.suffix.lower(), "application/octet-stream")
                if content_type.startswith("text/") or content_type == "application/json":
                    content_type += "; charset=utf-8"
                # Generated pages run in an opaque origin: their scripts still run, but cannot call this API
                # (their requests carry Origin: null) and so cannot approve their own review.
                return self.send_body(target.read_bytes(), content_type, extra_headers=(
                    ("Content-Security-Policy", "sandbox allow-scripts allow-popups"), ("X-Content-Type-Options", "nosniff")))
            if parsed.path == "/api/scan":
                target = (self.root / Path(q["path"][0]).expanduser()).resolve() if q.get("path") else self.root
                if not target.is_dir(): raise ValueError("指定路径不是可读取的文件夹")
                if not target.is_relative_to(self.root.parent): raise ValueError("为安全起见，请指定启动目录或其父目录下的文件夹")
                return self.send_json({"root": str(target), "items": candidates(target), "fileCount": count_files(target), "recommended": str(recommended_folder(target))})
            if parsed.path == "/api/state":
                path = self.workspace_path(q.get("path", [""])[0])
                status = _read_json(path / "project/status.json")
                payload = {"path": str(path), **status}
                payload["uiConfig"] = _read_json(path / "config/ui.json", {})
                payload["manifest"] = _read_json(path / "src/ui/ir/manifest.json", {})
                payload["uiUnits"] = unit_overview(path)
                payload["activeJob"] = _active_job(path)
                return self.send_json(payload)
            if parsed.path == "/api/job":
                job_id = q.get("id", [""])[0]
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                    if not job: raise ValueError("生成任务不存在或已过期")
                    return self.send_json(dict(job))
            if parsed.path == "/api/check":
                path = self.workspace_path(q.get("path", [""])[0])
                p = _run_checker(path, _read_json(path / "project/status.json")["phase"])
                return self.send_json({"ok": p.returncode == 0, "output": p.stdout + p.stderr})
            self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_error_json(exc)

    def do_POST(self):
        if not self.trusted_request():
            return self.send_json({"error": "forbidden"}, 403)
        endpoint = urlparse(self.path).path
        if endpoint not in {"/api/init", "/api/advance", "/api/generate", "/api/review", "/api/approve"}: return self.send_json({"error": "not found"}, 404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if not isinstance(body, dict):
                raise ValueError("请求体必须是 JSON 对象")
            if endpoint == "/api/init":
                path = init_workspace(self.workspace_path(body.get("path") or DEFAULT_WORKSPACE_DIR), body.get("official", ""), body.get("oneLiner", ""), body.get("sourcePath") or None, body.get("capabilities") or [], body.get("stackProfile", check_workspace.STACK_PROFILES[0]), body.get("platforms"), body.get("agents"))
                return self.send_json({"path": str(path)})
            path = self.workspace_path(body.get("path", ""))
            if endpoint == "/api/approve":
                if body.get("kind") == "unit-review":
                    with STATE_LOCK:
                        _refuse_while_running(path)
                        record = append_unit_review(
                            path,
                            body.get("unitId", ""),
                            body.get("conclusion", ""),
                            body.get("reviewer"),
                            body.get("evidence"),
                            body.get("fileScope"),
                            body.get("outputHash"),
                            body.get("summary"),
                        )
                    return self.send_json({"ok": True, "record": record})
                record_gate(path, body.get("gate"), body.get("choice"), body.get("status", APPROVED))
                return self.send_json({"ok": True, "gate": body.get("gate")})
            if endpoint == "/api/generate":
                unit_id = body.get("unitId")
                phase = int(body.get("phase", 1))
                if unit_id:
                    return self.send_json({"job": start_unit_job(path, phase, unit_id), "unitId": unit_id})
                return self.send_json({"job": start_generation(path, phase)})
            if endpoint == "/api/review":
                unit_id = body.get("unitId", "")
                return self.send_json({"job": start_review_job(path, int(body.get("phase", 0)), unit_id), "unitId": unit_id})
            return self.send_json({"path": str(path), "phase": advance_phase(path, int(body.get("fromPhase", -1)))})
        except Exception as exc:
            self.send_error_json(exc)

    def log_message(self, *_):
        pass  # one line per poll would bury the URL the user needs; failures still go through log_error

    def log_error(self, fmt, *args):
        sys.stderr.write("workbench: " + (fmt % args) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("directory", nargs="?", default="."); parser.add_argument("--port", type=int, default=8765); args = parser.parse_args(argv)
    Handler.root = Path(args.directory).expanduser().resolve(); Handler.root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("Brand System Workbench: http://127.0.0.1:%d/" % args.port, flush=True)
    if threading.current_thread() is threading.main_thread():
        for stop in (signal.SIGTERM, signal.SIGHUP):  # closing the terminal sends SIGHUP
            signal.signal(stop, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        # Job threads are daemons and die with the process; the agents they started run in their own groups.
        for pid in list(AGENT_GROUPS):
            _stop_group(pid)


if __name__ == "__main__": main()
