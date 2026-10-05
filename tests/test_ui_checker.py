import json
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace  # noqa: E402


MANIFEST_HASH = "sha256:" + "a" * 64


class UiCheckerTest(unittest.TestCase):
    def make_workspace(self, phase=4, brief=None, ui=None, manifest=None, status=None, approvals=None):
        root = Path(tempfile.mkdtemp())
        for rel in sum((check_workspace.REQUIRED[p] for p in range(phase + 1)), []):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("content", encoding="utf-8")
        (root / "brand.brief.json").write_text(json.dumps(brief or {
            "constraints": {"frontend": {
                "platforms": ["web"], "stackProfile": "react", "deliveryStatus": "preview-only"
            }}
        }), encoding="utf-8")
        if phase >= 3:
            path = root / "config/ui.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(ui or self.valid_ui()), encoding="utf-8")
        if phase >= 4:
            path = root / "src/ui/ir/manifest.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(manifest or self.valid_manifest()), encoding="utf-8")
        (root / "project/status.json").write_text(json.dumps(status or {"phase": phase, "blockers": []}), encoding="utf-8")
        (root / "project/approvals.json").write_text(json.dumps(approvals or []), encoding="utf-8")
        return root

    @staticmethod
    def valid_ui():
        return {
            "version": "1.0.0",
            "platforms": ["web"],
            "stackProfile": "react",
            "tokenSource": "tokens/src",
            "deliveryStatus": "preview-only",
            "adapter": {"web": {"deliveryStatus": "preview-only", "evidence": ["review/03-system.html"]}},
        }

    @staticmethod
    def valid_manifest():
        return {
            "manifestVersion": "2026.10.05.1",
            "hash": MANIFEST_HASH,
            "units": [
                {"id": "map", "kind": "page-map", "status": "approved", "files": ["src/ui/map.html"], "platforms": ["web"]},
                {"id": "layout", "kind": "layout", "status": "in-progress", "files": ["src/ui/layout.html"], "platforms": ["web"], "dependsOn": ["map"], "platformHints": ["web"]},
            ],
        }

    @staticmethod
    def approved_review(unit_id, conclusion="approved", version="2026.10.05.1", manifest_hash=MANIFEST_HASH):
        return {
            "kind": "unit-review", "status": "approved", "unitId": unit_id,
            "manifestVersion": version, "manifestHash": manifest_hash,
            "fileScope": [{"path": "src/ui/map.html", "startLine": 1, "endLine": 2}],
            "reviewer": {"type": "subagent", "name": "ui-reviewer"},
            "conclusion": conclusion, "evidence": ["python -m unittest"],
        }

    def findings(self, **kwargs):
        root = self.make_workspace(**kwargs)
        return check_workspace.check(root, kwargs.get("phase", 4))

    def assert_finding(self, findings, text):
        self.assertTrue(any(text in finding for finding in findings), "missing %r in %r" % (text, findings))

    def test_phase_three_requires_ui_config_and_valid_frontend(self):
        root = self.make_workspace(phase=2)
        findings = check_workspace.check(root, 3)
        self.assert_finding(findings, "missing config/ui.json")
        root = self.make_workspace(phase=3, brief={"constraints": {"frontend": {
            "platforms": ["mobile"], "stackProfile": "vue", "deliveryStatus": "implemented"
        }}})
        findings = check_workspace.check(root, 3)
        self.assert_finding(findings, "frontend.platforms")
        self.assert_finding(findings, "frontend.stackProfile")
        self.assert_finding(findings, "frontend.deliveryStatus")

    def test_legacy_tech_stack_remains_accepted_without_frontend_block(self):
        brief = {"constraints": {"techStack": "html/css/js"}}
        findings = self.findings(phase=3, brief=brief)
        self.assertFalse(any("frontend" in finding for finding in findings), findings)

    def test_ui_config_validates_adapter_evidence_and_delivery_status(self):
        ui = self.valid_ui()
        ui["deliveryStatus"] = "implemented"
        findings = self.findings(phase=3, ui=ui)
        self.assert_finding(findings, "config/ui.json: deliveryStatus")
        ui = self.valid_ui()
        ui["adapter"] = {"web": {"deliveryStatus": "handoff-ready", "evidence": []}}
        findings = self.findings(phase=3, ui=ui)
        self.assert_finding(findings, "adapter.web.evidence")
        ui = self.valid_ui()
        ui["adapter"]["web"]["implementationStatus"] = "verified"
        findings = self.findings(phase=3, ui=ui)
        self.assert_finding(findings, "cannot claim verified")
        ui = self.valid_ui()
        ui["platforms"] = ["mobile"]
        findings = self.findings(phase=3, ui=ui)
        self.assert_finding(findings, "config/ui.json: platforms")

    def test_manifest_checks_identity_platforms_and_token_values(self):
        manifest = self.valid_manifest()
        manifest["units"][1]["id"] = "map"
        findings = self.findings(manifest=manifest)
        self.assert_finding(findings, "duplicate unit id")
        manifest = self.valid_manifest()
        manifest["units"][1]["platformHints"] = ["mobile"]
        findings = self.findings(manifest=manifest)
        self.assert_finding(findings, "platformHints")
        manifest = self.valid_manifest()
        manifest["units"][0]["tokenValues"] = {"color.primary": "#123456"}
        findings = self.findings(manifest=manifest)
        self.assert_finding(findings, "token value")

    def test_manifest_dependency_graph_rejects_missing_and_cycles(self):
        manifest = self.valid_manifest()
        manifest["units"][1]["dependsOn"] = ["missing"]
        self.assert_finding(self.findings(manifest=manifest), "dependsOn missing unit")
        manifest = self.valid_manifest()
        manifest["units"][0]["dependsOn"] = ["layout"]
        self.assert_finding(self.findings(manifest=manifest), "dependency cycle")

    def test_status_units_enforce_dependencies_outputs_and_review(self):
        status = {"phase": 4, "blockers": [], "units": [
            {"unitId": "map", "status": "in-progress"},
            {"unitId": "layout", "status": "in-progress"},
        ]}
        findings = self.findings(status=status)
        self.assert_finding(findings, "unit layout: in-progress requires approved dependencies")
        status["units"][1]["status"] = "in-review"
        findings = self.findings(status=status)
        self.assert_finding(findings, "unit layout: in-review requires output")
        status["units"][0]["status"] = "approved"
        findings = self.findings(status=status, approvals=[self.approved_review("map")])
        self.assert_finding(findings, "unit layout: in-review requires output")

    def test_completed_cannot_skip_approved_review_and_changes_request_blocks(self):
        status = {"phase": 4, "blockers": [], "units": [
            {"unitId": "map", "status": "completed"},
            {"unitId": "layout", "status": "in-progress"},
        ]}
        findings = self.findings(status=status, approvals=[self.approved_review("map", conclusion="changes-requested")])
        self.assert_finding(findings, "completed requires an approved unit-review")
        status["units"][0]["status"] = "changes-requested"
        findings = self.findings(status=status)
        self.assert_finding(findings, "changes-requested blocks phase progression")

    def test_unit_review_is_bound_to_latest_manifest_and_cannot_be_gate(self):
        old = self.approved_review("map", version="old", manifest_hash="sha256:" + "b" * 64)
        findings = self.findings(status={"phase": 4, "blockers": [], "units": [{"unitId": "map", "status": "approved"}]}, approvals=[old])
        self.assert_finding(findings, "requires an approved unit-review matching the latest manifest")
        mixed = dict(self.approved_review("map"), gate="G1")
        findings = self.findings(status={"phase": 4, "blockers": [], "units": [{"unitId": "map", "status": "approved"}]}, approvals=[mixed])
        self.assert_finding(findings, "unit-review cannot be a gate")

    def test_unit_review_file_scope_must_name_the_unit_outputs(self):
        status = {"phase": 4, "blockers": [], "units": [{"unitId": "map", "status": "approved"}]}
        review = self.approved_review("map")
        self.assertNotIn("fileScope", " ".join(self.findings(status=status, approvals=[review])))
        review["fileScope"] = [{"path": "src/ui/layout.html", "startLine": 1, "endLine": 1}]
        findings = self.findings(status=status, approvals=[review])
        self.assert_finding(findings, "unit-review map fileScope must name its unit files")

    def test_each_manifest_unit_requires_its_own_review(self):
        status = {"phase": 4, "blockers": [], "units": [
            {"unitId": "map", "status": "approved"},
            {"unitId": "layout", "status": "approved"},
        ]}
        findings = self.findings(status=status, approvals=[self.approved_review("map")])
        self.assert_finding(findings, "unit layout requires unit-review")

    def test_manifest_hash_format_is_checked(self):
        manifest = self.valid_manifest()
        manifest["hash"] = "not-a-hash"
        findings = self.findings(manifest=manifest)
        self.assert_finding(findings, "manifest hash")


if __name__ == "__main__":
    unittest.main()
