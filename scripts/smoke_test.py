"""Exercise real workspace CLI phase/gate transitions in an isolated directory."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import check_workspace

ROOT = Path(__file__).resolve().parent.parent


def run():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        def write(rel, value):
            path = ws / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        def check(phase, expected, release=False, needle=""):
            result = subprocess.run([sys.executable, str(ROOT / "scripts/check_workspace.py"),
                                     str(ws), "--phase", str(phase)] + (["--release"] if release else []),
                                    capture_output=True, text=True)
            if result.returncode != expected or needle not in result.stdout:
                raise RuntimeError(result.stdout + result.stderr)
        approvals = []
        for phase in range(6):
            for rel in check_workspace.REQUIRED[phase]:
                write(rel, "{}" if rel.endswith(".json") else "fixture")
            write("brand.brief.json", (ROOT / "evals/example-brief.json").read_text(encoding="utf-8"))
            write("project/status.json", json.dumps({"phase": phase, "state": "in-review",
                                                    "completed": [], "next": [], "blockers": []}))
            write("project/approvals.json", json.dumps(approvals))
            if phase == 1:
                check(phase, 1, needle="three")
                write("review/01-directions.html", (ROOT / "evals/directions.fixture.html").read_text(encoding="utf-8"))
            if phase >= 2:
                check(phase, 1, needle="requires gate")
                gate = "G%d" % (phase - 1)
                approval = {"gate": gate, "status": "approved", "scope": "fixture",
                            "snapshot": "fixture", "version": "0.1.0", "confirmation": "synthetic test only",
                            "approvedAt": "2026-10-04T00:00:00Z"}
                approvals.append(approval)
                write("project/approvals.json", json.dumps(approvals))
                check(phase, 0)
                write("project/approvals.json", json.dumps(approvals + [{"gate": gate, "status": "changes-requested"}]))
                check(phase, 1, needle="requires gate")
                write("project/approvals.json", json.dumps(approvals))
            check(phase, 0)
        check(5, 1, release=True, needle="G5")
        approvals.append(dict(approvals[-1], gate="G5"))
        write("project/approvals.json", json.dumps(approvals))
        check(5, 0, release=True)
    print("smoke test passed: phases 0–5, withdrawals, G5 release")


if __name__ == "__main__":
    run()
