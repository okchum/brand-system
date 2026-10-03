"""Run a minimal phase/gate workspace smoke test using only the standard library."""
import json, tempfile
from pathlib import Path
import check_workspace

def main():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp); (ws / "project").mkdir(); (ws / "docs").mkdir()
        for rel in check_workspace.REQUIRED[0]:
            p = ws / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("[]" if rel.endswith("approvals.json") else ("{}" if p.suffix == ".json" else "ok"))
        brief = json.loads((Path(__file__).parent.parent / "evals/example-brief.json").read_text())
        (ws / "brand.brief.json").write_text(json.dumps(brief))
        (ws / "project/status.json").write_text(json.dumps({"phase": 0, "state": "draft", "completed": [], "next": [], "blockers": []}))
        assert check_workspace.check(ws, 0) == [], check_workspace.check(ws, 0)
        (ws / "project/status.json").write_text(json.dumps({"phase": 1, "state": "review", "completed": [], "next": [], "blockers": []}))
        (ws / "review").mkdir(); (ws / "review/01-directions.html").write_text("ok")
        assert check_workspace.check(ws, 1), "phase 1 must reject an invalid review page or missing gate setup"
    print("smoke test passed")

if __name__ == "__main__": main()
