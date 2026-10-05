"""Exercise workspace initialization, UI unit records, phase gates, and release checks."""
import json
import subprocess
import sys
import tempfile
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
        "confirmation": "synthetic smoke assertion",
        "approvedAt": "2026-10-05T00:00:00Z",
    }


def unit_review(manifest, unit):
    relative = unit["files"][0]
    return {
        "kind": "unit-review",
        "status": "approved",
        "unitId": unit["id"],
        "manifestVersion": manifest["manifestVersion"],
        "manifestHash": manifest["hash"],
        "fileScope": [{"path": relative, "startLine": 1, "endLine": 1}],
        "reviewer": {"type": "subagent", "name": "smoke-reviewer"},
        "conclusion": "approved",
        "evidence": ["smoke fixture evidence"],
    }


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
        status = json.loads(status_path.read_text(encoding="utf-8"))
        approvals = []

        for phase in range(6):
            status["phase"] = phase
            status["state"] = "in-review"
            write_json(status_path, status)
            write_json(approvals_path, approvals)
            if phase == 1:
                check_cli(workspace, phase, 1, needle="missing review/01-directions.html")
                write_file(workspace, "review/01-directions.html", "directions fixture")
            elif phase >= 2:
                for relative in check_workspace.REQUIRED[phase]:
                    write_file(workspace, relative)
            if phase == 4:
                units = json.loads((workspace / "src/ui/ir/manifest.json").read_text(encoding="utf-8"))["units"]
                for unit in units:
                    workbench.mark_unit_in_review(workspace, unit["id"], ["src/ui/units/%s/output.html" % unit["id"]])
                manifest = json.loads((workspace / "src/ui/ir/manifest.json").read_text(encoding="utf-8"))
                approvals.extend(unit_review(manifest, unit) for unit in manifest["units"])
            if phase >= 2:
                check_cli(workspace, phase, 1, needle="requires gate")
                gate = "G%d" % (phase - 1)
                approvals.append(approval(gate))
                write_json(approvals_path, approvals)
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
