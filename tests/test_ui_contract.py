import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / "assets/brief.schema.json").read_text(encoding="utf-8"))
        cls.template = json.loads((ROOT / "assets/brief.template.json").read_text(encoding="utf-8"))
        cls.defs = cls.schema["$defs"]

    def test_frontend_constraints_keep_legacy_tech_stack(self):
        constraints = self.schema["properties"]["constraints"]
        self.assertIn("techStack", constraints["properties"])
        self.assertEqual(constraints["properties"]["techStack"], {"$ref": "#/$defs/text"})
        self.assertEqual(constraints["properties"]["frontend"], {"$ref": "#/$defs/frontendConstraints"})
        frontend = self.defs["frontendConstraints"]
        self.assertEqual(
            set(frontend["properties"]),
            {"platforms", "stackProfile", "deliveryStatus"},
        )
        self.assertEqual(frontend["required"], ["platforms", "stackProfile", "deliveryStatus"])
        self.assertEqual(
            frontend["properties"]["platforms"]["items"],
            {"$ref": "#/$defs/platform"},
        )
        self.assertEqual(
            self.defs["platform"]["enum"],
            ["web", "desktop", "ios", "android"],
        )
        self.assertNotIn("mobile", self.defs["platform"]["enum"])
        self.assertEqual(self.defs["stackProfile"]["enum"], ["html-css-js", "react"])
        self.assertEqual(
            self.defs["deliveryStatus"]["enum"],
            ["preview-only", "handoff-ready"],
        )

    def test_template_has_compatible_frontend_shape(self):
        constraints = self.template["constraints"]
        self.assertIn("techStack", constraints)
        self.assertIn("frontend", constraints)
        self.assertEqual(
            set(constraints["frontend"]),
            {"platforms", "stackProfile", "deliveryStatus"},
        )
        self.assertEqual(constraints["frontend"]["platforms"], [])
        self.assertIn(constraints["frontend"]["stackProfile"], ["html-css-js", "react"])
        self.assertIn(
            constraints["frontend"]["deliveryStatus"],
            ["preview-only", "handoff-ready"],
        )

    def test_ui_and_ir_contracts_are_machine_readable(self):
        ui = self.defs["uiConfig"]
        self.assertEqual(
            set(ui["required"]),
            {"version", "platforms", "stackProfile", "tokenSource", "deliveryStatus"},
        )
        self.assertEqual(ui["properties"]["tokenSource"], {"const": "tokens/src"})
        self.assertEqual(ui["properties"]["platforms"]["items"], {"$ref": "#/$defs/platform"})
        manifest = self.defs["irManifest"]
        self.assertEqual(set(manifest["required"]), {"manifestVersion", "hash", "units"})
        self.assertEqual(manifest["properties"]["units"]["items"], {"$ref": "#/$defs/manifestUnit"})
        self.assertEqual(
            self.defs["unitKind"]["enum"],
            ["page-map", "layout", "reuse-analysis", "component", "page", "platform-adaptation"],
        )
        self.assertEqual(
            self.defs["unitStatus"]["enum"],
            ["not-started", "in-progress", "in-review", "approved", "changes-requested", "completed"],
        )
        self.assertEqual(
            set(self.defs["manifestUnit"]["required"]),
            {"id", "kind", "status", "files", "platforms"},
        )

    def test_status_units_and_approval_records_are_bound(self):
        status_unit = self.defs["statusUnit"]
        self.assertEqual(set(status_unit["required"]), {"unitId", "status"})
        self.assertEqual(status_unit["properties"]["status"], {"$ref": "#/$defs/unitStatus"})
        approval = self.defs["approvalRecord"]
        self.assertEqual(len(approval["oneOf"]), 2)
        gate = self.defs["gateApproval"]
        self.assertEqual(gate["properties"]["kind"], {"const": "gate"})
        review = self.defs["unitReviewApproval"]
        self.assertEqual(review["properties"]["kind"], {"const": "unit-review"})
        self.assertEqual(
            set(review["required"]),
            {
                "kind",
                "status",
                "unitId",
                "manifestVersion",
                "manifestHash",
                "outputHash",
                "fileScope",
                "reviewer",
                "conclusion",
                "evidence",
            },
        )
        self.assertEqual(review["properties"]["reviewer"]["properties"]["type"], {"const": "subagent"})
        self.assertEqual(
            review["properties"]["conclusion"]["enum"],
            ["approved", "changes-requested"],
        )
        self.assertEqual(review["properties"]["fileScope"]["items"], {"$ref": "#/$defs/fileRange"})

    def test_phase_requirements_keep_legacy_contract_and_ui_extensions(self):
        requirements = json.loads((ROOT / "config/phase_requirements.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(requirements), {"states", "gates", "phases", "releaseApprovals", "uiContract"}
        )
        self.assertEqual(requirements["gates"], ["G1", "G2", "G3", "G4", "G5"])
        self.assertEqual(requirements["phases"]["1"]["requiresApprovals"], [])
        self.assertEqual(requirements["phases"]["2"]["requiresApprovals"], ["G1"])
        self.assertEqual(requirements["releaseApprovals"], ["G5"])
        self.assertEqual(requirements["uiContract"], {
            "schemaVersion": "1.0.0",
            "phases": [
                {"phase": 3, "creates": ["config/ui.json"]},
                {"phase": 4, "creates": ["src/ui/ir/manifest.json"]},
            ],
        })

    def test_docs_define_order_ssot_and_review_limits(self):
        ui = (ROOT / "references/ui.md").read_text(encoding="utf-8")
        tokens = (ROOT / "references/tokens.md").read_text(encoding="utf-8")
        review = (ROOT / "references/review-qa.md").read_text(encoding="utf-8")
        structure = (ROOT / "references/structure-manifest.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for term in ("页面地图", "布局", "复用", "组件", "页面", "平台适配"):
            self.assertIn(term, ui)
        self.assertIn("tokens/src/", tokens)
        self.assertIn("每个 unit", review)
        self.assertIn("subagent", review)
        self.assertIn("config/ui.json", structure)
        self.assertIn("src/ui/ir/manifest.json", structure)
        self.assertRegex(skill, r"html-css-js.*react|react.*html-css-js")
        self.assertIn("preview-only", skill)
        self.assertIn("handoff-ready", skill)
        for forbidden in ("sonnet", "opus", "fable"):
            self.assertIn(forbidden, skill.lower())


if __name__ == "__main__":
    unittest.main()
