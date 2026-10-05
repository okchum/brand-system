import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_workspace  # noqa: E402
import workbench  # noqa: E402


class UiIntegrationTest(unittest.TestCase):
    def init_workspace(self, root, stack="react", platforms=None):
        return workbench.init_workspace(
            Path(root) / "brand",
            "Tidewell",
            "Manage feedback",
            stack_profile=stack,
            platforms=["web"] if platforms is None else platforms,
        )

    @staticmethod
    def read(workspace, relative):
        return json.loads((Path(workspace) / relative).read_text(encoding="utf-8"))

    @staticmethod
    def write(workspace, relative, value):
        path = Path(workspace) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def test_initialization_creates_ui_ir_and_status_units_with_legacy_brief(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init_workspace(root, stack="html-css-js", platforms=["web", "desktop"])
            brief = self.read(workspace, "brand.brief.json")
            brief["constraints"]["techStack"] = "legacy-web-stack"
            brief["constraints"].pop("frontend", None)
            (workspace / "brand.brief.json").write_text(json.dumps(brief), encoding="utf-8")

            ui = self.read(workspace, "config/ui.json")
            manifest = self.read(workspace, "src/ui/ir/manifest.json")
            status = self.read(workspace, "project/status.json")

            self.assertEqual(ui["stackProfile"], "html-css-js")
            self.assertEqual(ui["platforms"], ["web", "desktop"])
            self.assertEqual(ui["deliveryStatus"], "preview-only")
            self.assertRegex(manifest["hash"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual([unit["kind"] for unit in manifest["units"]], list(workbench.UI_UNIT_KINDS))
            self.assertEqual([unit["status"] for unit in manifest["units"]], ["in-progress"] * 5)
            self.assertEqual([unit["unitId"] for unit in status["units"]], [unit["id"] for unit in manifest["units"]])
            self.assertEqual(check_workspace.check(workspace, 0), [])

    def test_valid_stack_and_platform_selections_enter_workflow(self):
        for stack in ("html-css-js", "react"):
            for platforms in (("web",), ("desktop",), ("ios",), ("android",), ("web", "desktop", "ios", "android")):
                with self.subTest(stack=stack, platforms=platforms), tempfile.TemporaryDirectory() as root:
                    workspace = self.init_workspace(root, stack=stack, platforms=list(platforms))
                    self.assertTrue((workspace / "config/ui.json").is_file())
                    self.assertEqual(self.read(workspace, "config/ui.json")["platforms"], list(platforms))
        with tempfile.TemporaryDirectory() as root:
            for kwargs in ({"stack": "vue"}, {"platforms": ["mobile"]}, {"platforms": []}, {"platforms": ["web", "web"]}):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(ValueError):
                        self.init_workspace(root, **kwargs)

    def test_dependency_progression_requires_approved_review_and_rejects_changes_requested(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init_workspace(root)
            workbench.mark_unit_in_review(workspace, "page-map", ["src/ui/units/page-map/output.html"])
            workbench.set_unit_status(workspace, "page-map", "approved")
            with self.assertRaisesRegex(ValueError, "dependencies"):
                workbench.prepare_unit_generation(workspace, "layout")

            self.add_review(workspace, "page-map")
            workbench.prepare_unit_generation(workspace, "layout")
            workbench.set_unit_status(workspace, "page-map", "changes-requested")
            with self.assertRaisesRegex(ValueError, "dependencies"):
                workbench.prepare_unit_generation(workspace, "layout")

            workbench.set_unit_status(workspace, "page-map", "approved")
            workbench.mark_unit_in_review(workspace, "layout", ["src/ui/units/layout/output.html"])
            self.assertEqual(self.read(workspace, "project/status.json")["units"][1]["status"], "in-review")

    def test_status_updates_preserve_manifest_hash_and_review_binding(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init_workspace(root)
            workbench.mark_unit_in_review(workspace, "page-map", ["src/ui/units/page-map/output.html"])
            before = self.read(workspace, "src/ui/ir/manifest.json")
            record = workbench.append_unit_review(
                workspace,
                "page-map",
                "approved",
                reviewer={"type": "subagent", "name": "ui-reviewer"},
                evidence=["integration assertion"],
                file_scope=[{"path": "src/ui/units/page-map/output.html", "startLine": 1, "endLine": 1}],
            )
            after = self.read(workspace, "src/ui/ir/manifest.json")
            self.assertEqual(after["hash"], before["hash"])
            self.assertEqual(record["manifestVersion"], after["manifestVersion"])
            self.assertEqual(record["manifestHash"], after["hash"])

    def add_review(self, workspace, unit_id, conclusion="approved"):
        manifest = self.read(workspace, "src/ui/ir/manifest.json")
        unit = next(item for item in manifest["units"] if item["id"] == unit_id)
        file_path = unit["files"][0]
        approvals_path = Path(workspace) / "project/approvals.json"
        approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
        approvals.append({
            "kind": "unit-review",
            "status": conclusion,
            "unitId": unit_id,
            "manifestVersion": manifest["manifestVersion"],
            "manifestHash": manifest["hash"],
            "fileScope": [{"path": file_path, "startLine": 1, "endLine": 1}],
            "reviewer": {"type": "subagent", "name": "ui-reviewer"},
            "conclusion": conclusion,
            "evidence": ["focused integration assertion"],
        })
        approvals_path.write_text(json.dumps(approvals), encoding="utf-8")
        return approvals[-1]

    def prepare_complete_workspace(self, root, delivery_status="preview-only"):
        workspace = self.init_workspace(root, stack="react", platforms=["web", "desktop", "ios", "android"])
        brief = self.read(workspace, "brand.brief.json")
        brief["constraints"]["frontend"] = {
            "platforms": ["web", "desktop", "ios", "android"],
            "stackProfile": "react",
            "deliveryStatus": delivery_status,
        }
        (workspace / "brand.brief.json").write_text(json.dumps(brief), encoding="utf-8")
        status = self.read(workspace, "project/status.json")
        status["phase"] = 4
        (workspace / "project/status.json").write_text(json.dumps(status), encoding="utf-8")
        for phase in range(1, 6):
            for relative in check_workspace.REQUIRED[phase]:
                self.write(workspace, relative, "fixture")
        units = self.read(workspace, "src/ui/ir/manifest.json")["units"]
        for unit in units:
            unit_id = unit["id"]
            workbench.mark_unit_in_review(workspace, unit_id, ["src/ui/units/%s/output.html" % unit_id])
        for unit in units:
            workbench.set_unit_status(workspace, unit["id"], "completed")
        for unit in units:
            self.add_review(workspace, unit["id"])
        approvals = json.loads((workspace / "project/approvals.json").read_text(encoding="utf-8"))
        for number in range(1, 4):
            approvals.append({
                "gate": "G%d" % number,
                "status": "approved",
                "scope": "integration fixture",
                "snapshot": "integration fixture",
                "version": "fixture-%d" % number,
                "confirmation": "synthetic integration assertion",
                "approvedAt": "2026-10-05T00:00:00Z",
            })
        (workspace / "project/approvals.json").write_text(json.dumps(approvals), encoding="utf-8")
        return workspace

    def test_checker_accepts_matching_reviews_and_rejects_old_manifest_review(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.prepare_complete_workspace(root)
            self.assertEqual(check_workspace.check(workspace, 4), [])
            manifest = self.read(workspace, "src/ui/ir/manifest.json")
            manifest["manifestVersion"] = "2.0.0"
            (workspace / "src/ui/ir/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            workbench.sync_manifest_hash(workspace)
            findings = check_workspace.check(workspace, 4)
            self.assertTrue(any("matching the latest manifest" in finding for finding in findings), findings)

    def test_missing_or_changes_requested_review_blocks_checker_progression(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.prepare_complete_workspace(root)
            approvals_path = workspace / "project/approvals.json"
            approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
            approvals = [record for record in approvals if record.get("unitId") != "page-map"]
            approvals_path.write_text(json.dumps(approvals), encoding="utf-8")
            findings = check_workspace.check(workspace, 4)
            self.assertTrue(any("unit page-map requires unit-review" in finding for finding in findings), findings)

            self.add_review(workspace, "page-map", conclusion="changes-requested")
            status = self.read(workspace, "project/status.json")
            status["units"][0]["status"] = "changes-requested"
            (workspace / "project/status.json").write_text(json.dumps(status), encoding="utf-8")
            findings = check_workspace.check(workspace, 4)
            self.assertTrue(any("changes-requested blocks phase progression" in finding for finding in findings), findings)

    def test_platform_delivery_status_matches_checker_without_native_verified_claims(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.prepare_complete_workspace(root, delivery_status="handoff-ready")
            ui = self.read(workspace, "config/ui.json")
            ui["deliveryStatus"] = "handoff-ready"
            ui["evidence"] = ["review/03-system.html"]
            ui["adapter"] = {
                platform: {"deliveryStatus": "handoff-ready", "evidence": ["review/04-assets.html"]}
                for platform in ui["platforms"]
            }
            (workspace / "config/ui.json").write_text(json.dumps(ui), encoding="utf-8")
            self.assertEqual(check_workspace.check(workspace, 4), [])

            ui["adapter"]["ios"]["implementationStatus"] = "verified"
            (workspace / "config/ui.json").write_text(json.dumps(ui), encoding="utf-8")
            findings = check_workspace.check(workspace, 4)
            self.assertTrue(any("adapter.ios.implementationStatus" in finding for finding in findings), findings)

    def test_smoke_script_preserves_phase_gate_and_release_behavior(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/smoke_test.py")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("smoke test passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
