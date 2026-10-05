import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import workbench  # noqa: E402


class UiWorkbenchTest(unittest.TestCase):
    def init(self, root, **kwargs):
        return workbench.init_workspace(
            Path(root) / "brand",
            "Tidewell",
            "Manage feedback",
            **kwargs
        )

    def read(self, root, relative):
        return json.loads((Path(root) / "brand" / relative).read_text(encoding="utf-8"))

    def test_init_creates_ui_contract_files_and_units_without_overwriting_existing_files(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root, stack_profile="react", platforms=["web", "ios"])
            ui = self.read(root, "config/ui.json")
            manifest = self.read(root, "src/ui/ir/manifest.json")
            status = self.read(root, "project/status.json")

            self.assertEqual(ui["stackProfile"], "react")
            self.assertEqual(ui["platforms"], ["web", "ios"])
            self.assertEqual(ui["tokenSource"], "tokens/src")
            self.assertEqual(ui["deliveryStatus"], "preview-only")
            self.assertRegex(manifest["hash"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(
                [unit["kind"] for unit in manifest["units"]],
                ["page-map", "layout", "component", "page", "platform-adaptation"],
            )
            self.assertEqual(
                [unit["status"] for unit in manifest["units"]],
                ["in-progress"] * 5,
            )
            self.assertEqual(
                [unit["unitId"] for unit in status["units"]],
                [unit["id"] for unit in manifest["units"]],
            )

            brief_before = (workspace / "brand.brief.json").read_text(encoding="utf-8")
            (workspace / "config/ui.json").write_text('{"user": "owned"}\n', encoding="utf-8")
            workbench.init_workspace(workspace, "Changed", "Changed")
            self.assertEqual((workspace / "brand.brief.json").read_text(encoding="utf-8"), brief_before)
            self.assertEqual((workspace / "config/ui.json").read_text(encoding="utf-8"), '{"user": "owned"}\n')

    def test_selector_rejects_unknown_stack_or_platform(self):
        with tempfile.TemporaryDirectory() as root:
            for kwargs, needle in (
                ({"stack_profile": "vue"}, "stack profile"),
                ({"platforms": ["mobile"]}, "platform"),
                ({"platforms": []}, "platform"),
            ):
                with self.subTest(kwargs):
                    with self.assertRaisesRegex(ValueError, needle):
                        self.init(root, **kwargs)

    def test_generation_requires_approved_dependencies_and_only_marks_current_unit_in_review(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            manifest = self.read(root, "src/ui/ir/manifest.json")
            manifest["units"][0]["status"] = "approved"
            (workspace / "src/ui/ir/manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            workbench.sync_manifest_hash(workspace)
            with self.assertRaisesRegex(ValueError, "dependencies"):
                workbench.prepare_unit_generation(workspace, "layout")

            workbench.mark_unit_in_review(workspace, "page-map", ["src/ui/units/page-map/output.html"])
            manifest = self.read(root, "src/ui/ir/manifest.json")
            status = self.read(root, "project/status.json")
            self.assertEqual(manifest["units"][0]["status"], "in-review")
            self.assertEqual(status["units"][0]["status"], "in-review")
            self.assertTrue((workspace / "src/ui/units/page-map/output.html").is_file())
            self.assertTrue((workspace / "src/ui/units/page-map/metadata.json").is_file())
            self.assertNotEqual(manifest["units"][1]["status"], "completed")

    def test_generation_rejects_changes_requested_dependency(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            workbench.set_unit_status(workspace, "page-map", "changes-requested")
            with self.assertRaisesRegex(ValueError, "dependencies"):
                workbench.prepare_unit_generation(workspace, "layout")

    def test_unit_review_requires_external_subagent_evidence_and_reuses_approval_log(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            workbench.mark_unit_in_review(workspace, "page-map", ["src/ui/units/page-map/output.html"])
            with self.assertRaisesRegex(ValueError, "subagent"):
                workbench.append_unit_review(
                    workspace,
                    "page-map",
                    "approved",
                    reviewer={"type": "automatic", "name": "checker"},
                    evidence=["automatic check"],
                    file_scope=[{"path": "src/ui/units/page-map/output.html", "startLine": 1, "endLine": 1}],
                )
            record = workbench.append_unit_review(
                workspace,
                "page-map",
                "approved",
                reviewer={"type": "subagent", "name": "ui-reviewer"},
                evidence=["reviewer-evidence.json"],
                file_scope=[{"path": "src/ui/units/page-map/output.html", "startLine": 1, "endLine": 1}],
            )
            self.assertEqual(record["kind"], "unit-review")
            approvals = json.loads((workspace / "project/approvals.json").read_text(encoding="utf-8"))
            self.assertEqual(len(approvals), 1)
            self.assertEqual(approvals[0]["reviewer"]["type"], "subagent")
            self.assertEqual(self.read(root, "project/status.json")["units"][0]["status"], "approved")

    def test_g1_choice_is_read_from_last_valid_approval_and_missing_choice_is_blocked(self):
        approved = {
            "kind": "gate",
            "gate": "G1",
            "status": "approved",
            "scope": "strategy",
            "snapshot": "direction-C",
            "confirmation": "选 C",
            "approvedAt": "2026-10-05T00:00:00Z",
            "version": "v1",
        }
        self.assertEqual(workbench.g1_choice([approved]), "C")
        with self.assertRaisesRegex(ValueError, "G1"):
            workbench.g1_choice([dict(approved, snapshot="review/01-directions.html", confirmation="confirmed")])
        with self.assertRaisesRegex(ValueError, "G1"):
            workbench.g1_choice([])

    def test_start_generation_prompt_uses_selected_direction_not_default(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            approvals = [{
                "kind": "gate", "gate": "G1", "status": "approved", "scope": "strategy",
                "snapshot": "direction-A", "confirmation": "选 A", "approvedAt": "now", "version": "v1",
            }]
            (workspace / "project/approvals.json").write_text(json.dumps(approvals), encoding="utf-8")
            with mock.patch.object(workbench.subprocess, "Popen") as popen:
                popen.side_effect = RuntimeError("stop before process")
                job_id = workbench.start_generation(workspace, 1, unit_id="page-map")
            self.assertIn(job_id, workbench.JOBS)
            self.assertIn("Direction A", workbench.JOBS[job_id]["prompt"])
            self.assertNotIn("Direction B", workbench.JOBS[job_id]["prompt"])


if __name__ == "__main__":
    unittest.main()
