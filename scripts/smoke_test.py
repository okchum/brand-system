"""Exercise workspace initialization, UI unit records, phase gates, and release checks."""
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import check_workspace
import workbench

ROOT = Path(__file__).resolve().parent.parent


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_file(workspace, relative, content="fixture"):
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def approval(gate, status="approved"):
    return {
        "gate": gate,
        "status": status,
        "scope": "smoke fixture",
        "snapshot": "direction-A" if gate == "G1" else "smoke fixture",
        "version": "smoke-%s" % gate.lower(),
        "confirmation": "smoke: user chose direction A" if gate == "G1" else "synthetic smoke assertion",
        "approvedAt": "2026-10-05T00:00:00Z",
    }


def stand_in_codex(cmd, cwd, timeout):
    """Replaces only the codex process: generation writes the unit output, review returns a verdict."""
    if "read-only" in cmd:
        verdict = {"conclusion": "approved", "summary": "smoke review", "findings": []}
        Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(verdict), encoding="utf-8")
        return 0
    relative = re.search(r"src/ui/units/[a-z-]+/output\.html", cmd[-1]).group(0)
    write_file(Path(cwd), relative, "<main>%s</main>\n" % relative)
    return 0


def review_units_in_order(workspace):
    """Drive every unit through the workbench's real job path (claim, snapshot, generate, review, record)."""
    workbench._run_codex = stand_in_codex
    units = json.loads((workspace / "src/ui/ir/manifest.json").read_text(encoding="utf-8"))["units"]
    for unit in units:
        job_id = workbench.start_unit_job(workspace, 4, unit["id"])
        deadline = time.time() + 30
        while workbench.JOBS[job_id]["status"] == "running" and time.time() < deadline:
            time.sleep(0.05)
        job = workbench.JOBS[job_id]
        if job["status"] != "done":
            raise RuntimeError("unit %s job ended %s: %s" % (unit["id"], job["status"], job["error"]))


def check_cli(workspace, phase, expected, release=False, needle=""):
    command = [sys.executable, str(ROOT / "scripts/check_workspace.py"), str(workspace), "--phase", str(phase)]
    if release:
        command.append("--release")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != expected or (needle and needle not in result.stdout):
        raise RuntimeError(result.stdout + result.stderr)


def prepare_contract(workspace):
    brief = json.loads((workspace / "brand.brief.json").read_text(encoding="utf-8"))
    brief["constraints"]["frontend"] = {
        "platforms": ["web", "desktop", "ios", "android"],
        "stackProfile": "react",
        "deliveryStatus": "preview-only",
    }
    write_json(workspace / "brand.brief.json", brief)
    for phase in range(2, 6):
        for relative in check_workspace.REQUIRED[phase]:
            write_file(workspace, relative)
    write_file(workspace, "tokens/src/color.json", '{"color": {"primary": {"value": "#2457d6"}}}')


def run():
    with tempfile.TemporaryDirectory() as temporary:
        workspace = workbench.init_workspace(
            Path(temporary) / "brand",
            "Tidewell",
            "Manage feedback",
            stack_profile="react",
            platforms=["web", "desktop", "ios", "android"],
        )
        prepare_contract(workspace)
        status_path = workspace / "project/status.json"
        approvals_path = workspace / "project/approvals.json"
        approvals = []

        for phase in range(6):
            # Unit reviews rewrite status.json, so a copy held across phases would roll them back.
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["phase"] = phase
            status["state"] = "in-review"
            write_json(status_path, status)
            write_json(approvals_path, approvals)
            if phase == 1:
                check_cli(workspace, phase, 1, needle="missing review/01-directions.html")
                write_file(
                    workspace,
                    "review/01-directions.html",
                    (ROOT / "evals/directions.fixture.html").read_text(encoding="utf-8"),
                )
            elif phase >= 2:
                for relative in check_workspace.REQUIRED[phase]:
                    write_file(workspace, relative)
            if phase >= 2:
                check_cli(workspace, phase, 1, needle="requires gate")
                gate = "G%d" % (phase - 1)
                approvals.append(approval(gate))
                write_json(approvals_path, approvals)
                if phase == 4:
                    # Units are generated only after G3, as the workbench enforces.
                    review_units_in_order(workspace)
                    approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
                check_cli(workspace, phase, 0)
                approvals.append(approval(gate, status="changes-requested"))
                write_json(approvals_path, approvals)
                check_cli(workspace, phase, 1, needle="requires gate")
                approvals.pop()
            write_json(approvals_path, approvals)
            check_cli(workspace, phase, 0)

        check_cli(workspace, 5, 1, release=True, needle="requires gate G5")
        approvals.append(approval("G5"))
        write_json(approvals_path, approvals)
        check_cli(workspace, 5, 0, release=True)
    print("smoke test passed: initialization, phases 0-5, unit reviews, withdrawals, G5 release")


if __name__ == "__main__":
    run()
