import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_workspace  # noqa: E402
import workbench  # noqa: E402

G1_APPROVED = {
    "kind": "gate", "gate": "G1", "status": "approved", "scope": "strategy",
    "snapshot": "direction-C", "confirmation": "选 C", "approvedAt": "now", "version": "v1",
}
REVIEWER = {"type": "subagent", "name": "ui-reviewer"}


def gate(name):
    return dict(G1_APPROVED, gate=name, snapshot="fixture" if name != "G1" else "direction-C")


def write_output(workspace, unit_id, text="<main>unit</main>\n"):
    path = Path(workspace) / workbench.unit_output_path(unit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def current_output_hash(workspace, unit_id):
    manifest = json.loads((Path(workspace) / "src/ui/ir/manifest.json").read_text(encoding="utf-8"))
    unit = next(item for item in manifest["units"] if item["id"] == unit_id)
    return check_workspace.unit_output_hash(Path(workspace), unit)


def remove_backup(report):
    """Delete the temp directory a restore report points at, and nothing else."""
    saved = Path(re.search(r"保存到 (.+)）", report).group(1))
    backup = next(parent for parent in saved.parents if parent.name.startswith("brand-system-restore-"))
    shutil.rmtree(backup)


def wait_for(job_id):
    deadline = time.time() + 5
    while workbench.JOBS[job_id]["status"] == "running" and time.time() < deadline:
        time.sleep(0.02)
    return workbench.JOBS[job_id]


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

    def unit_status(self, workspace, unit_id):
        status = json.loads((Path(workspace) / "project/status.json").read_text(encoding="utf-8"))
        return next(item for item in status["units"] if item["unitId"] == unit_id)

    def review(self, workspace, unit_id, conclusion="approved"):
        write_output(workspace, unit_id)
        workbench.begin_unit_generation(workspace, unit_id)
        workbench.mark_unit_in_review(workspace, unit_id)
        return workbench.append_unit_review(
            workspace, unit_id, conclusion,
            reviewer=REVIEWER,
            evidence=["review-evidence.json"],
            file_scope=[{"path": workbench.unit_output_path(unit_id), "startLine": 1, "endLine": 1}],
            output_hash=current_output_hash(workspace, unit_id),
        )

    def phase_four(self, root, **init_options):
        workspace = self.init(root, **init_options)
        status_path = workspace / "project/status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["phase"] = 4
        status_path.write_text(json.dumps(status), encoding="utf-8")
        (workspace / "project/approvals.json").write_text(
            json.dumps([gate("G1"), gate("G2"), gate("G3")]), encoding="utf-8"
        )
        tokens = workspace / "tokens/src/color.json"
        tokens.parent.mkdir(parents=True, exist_ok=True)
        tokens.write_text('{"color": {"primary": {"value": "#123456"}}}', encoding="utf-8")
        return workspace

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
                ["page-map", "layout", "reuse-analysis", "component", "page", "platform-adaptation"],
            )
            self.assertFalse([unit for unit in manifest["units"] if "status" in unit], "progress lives in status.json only")
            self.assertEqual([unit["status"] for unit in status["units"]], ["not-started"] * 6)
            self.assertEqual(
                [unit["unitId"] for unit in status["units"]],
                [unit["id"] for unit in manifest["units"]],
            )

            brief_before = (workspace / "brand.brief.json").read_text(encoding="utf-8")
            (workspace / "config/ui.json").write_text('{"user": "owned"}\n', encoding="utf-8")
            workbench.init_workspace(workspace, "Changed", "Changed")
            self.assertEqual((workspace / "brand.brief.json").read_text(encoding="utf-8"), brief_before)
            self.assertEqual((workspace / "config/ui.json").read_text(encoding="utf-8"), '{"user": "owned"}\n')

    def test_init_rejects_unknown_stack_and_platform_values(self):
        with tempfile.TemporaryDirectory() as root:
            for kwargs, needle in (
                ({"stack_profile": "vue"}, "stack profile"),
                ({"platforms": ["mobile"]}, "platform"),
                ({"platforms": []}, "platform"),
            ):
                with self.subTest(kwargs):
                    with self.assertRaisesRegex(ValueError, needle):
                        self.init(root, **kwargs)

    def test_fresh_workspace_is_checker_clean_for_unit_states(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            findings = check_workspace.check(workspace, 4)
            self.assertFalse([f for f in findings if "in-progress requires" in f], findings)

    def test_generation_requires_satisfied_dependencies(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            with self.assertRaisesRegex(ValueError, "dependencies"):
                workbench.begin_unit_generation(workspace, "layout")
            workbench.begin_unit_generation(workspace, "page-map")
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "in-progress")
            self.assertEqual(self.unit_status(workspace, "layout")["status"], "not-started")

    def test_mark_in_review_requires_real_output_and_keeps_manifest_hash(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            workbench.begin_unit_generation(workspace, "page-map")
            with self.assertRaisesRegex(ValueError, "output"):
                workbench.mark_unit_in_review(workspace, "page-map")
            before = self.read(root, "src/ui/ir/manifest.json")["hash"]
            write_output(workspace, "page-map")
            workbench.mark_unit_in_review(workspace, "page-map")
            self.assertEqual(self.read(root, "src/ui/ir/manifest.json")["hash"], before)
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "in-review")
            self.assertTrue((workspace / "src/ui/units/page-map/metadata.json").is_file())

    def test_unit_review_requires_named_subagent_matching_output_and_unit_files(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            write_output(workspace, "page-map")
            workbench.begin_unit_generation(workspace, "page-map")
            workbench.mark_unit_in_review(workspace, "page-map")
            scope = [{"path": workbench.unit_output_path("page-map"), "startLine": 1, "endLine": 1}]
            digest = current_output_hash(workspace, "page-map")
            bad_calls = (
                ({"reviewer": {"type": "automatic", "name": "checker"}}, "subagent"),
                ({"reviewer": {"type": "subagent", "name": ""}}, "subagent"),
                ({"file_scope": [{"path": "../../etc/passwd", "startLine": 1, "endLine": 1}]}, "fileScope"),
                ({"file_scope": [{"path": workbench.unit_output_path("layout"), "startLine": 1, "endLine": 1}]}, "fileScope"),
                ({"output_hash": "sha256:" + "0" * 64}, "outputHash"),
            )
            for override, needle in bad_calls:
                kwargs = dict(reviewer=REVIEWER, evidence=["evidence.json"], file_scope=scope, output_hash=digest)
                kwargs.update(override)
                with self.subTest(override=override), self.assertRaisesRegex(ValueError, needle):
                    workbench.append_unit_review(workspace, "page-map", "approved", **kwargs)
            record = workbench.append_unit_review(
                workspace, "page-map", "approved", reviewer=REVIEWER,
                evidence=["evidence.json"], file_scope=scope, output_hash=digest,
            )
            self.assertEqual(record["outputHash"], digest)
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "approved")
            self.assertEqual(len(self.read(root, "project/approvals.json")), 1)

    def test_g1_choice_is_read_from_last_valid_approval_and_missing_choice_is_blocked(self):
        self.assertEqual(workbench.g1_choice([G1_APPROVED]), "C")
        with self.assertRaisesRegex(ValueError, "G1"):
            workbench.g1_choice([dict(G1_APPROVED, snapshot="review/01-directions.html", confirmation="confirmed")])
        with self.assertRaisesRegex(ValueError, "G1"):
            workbench.g1_choice([])

    def test_g1_choice_latest_record_decides_even_when_not_approved(self):
        for status in ("changes-requested", "pending"):
            with self.subTest(status=status):
                withdrawn = dict(G1_APPROVED, status=status)
                with self.assertRaisesRegex(ValueError, "G1"):
                    workbench.g1_choice([G1_APPROVED, withdrawn])
        later_gate = dict(G1_APPROVED, gate="G2")
        self.assertEqual(workbench.g1_choice([G1_APPROVED, later_gate]), "C")

    def at_phase(self, workspace, phase):
        status_path = Path(workspace) / "project/status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["phase"] = phase
        status_path.write_text(json.dumps(status), encoding="utf-8")
        return workspace

    def test_phase_generation_only_runs_for_the_current_phase(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            with self.assertRaisesRegex(ValueError, "只能为当前阶段"):
                workbench.start_generation(workspace, 2)
            self.assertIsNone(workbench._running_job(workspace))

    def test_phase_one_generation_does_not_require_g1(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.at_phase(self.init(root), 1)
            with mock.patch.object(workbench, "_run_agent", return_value=1):
                job_id = workbench.start_generation(workspace, 1)
                wait_for(job_id)
            self.assertNotIn("Direction B", workbench.JOBS[job_id]["prompt"])

    def test_later_generation_requires_latest_valid_g1_and_uses_its_direction(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.at_phase(self.init(root), 2)
            with self.assertRaisesRegex(ValueError, "G1"):
                workbench.start_generation(workspace, 2)
            (workspace / "project/approvals.json").write_text(json.dumps([G1_APPROVED]), encoding="utf-8")
            with mock.patch.object(workbench, "_run_agent", return_value=1):
                job_id = workbench.start_generation(workspace, 2)
                wait_for(job_id)
            self.assertIn("Direction C", workbench.JOBS[job_id]["prompt"])
            self.assertNotIn("Direction B", workbench.JOBS[job_id]["prompt"])

    def test_sequential_progression_keeps_earlier_reviews_bound_to_the_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            first = self.review(workspace, "page-map")
            self.review(workspace, "layout")
            with self.assertRaisesRegex(ValueError, "reuse-analysis"):
                workbench.begin_unit_generation(workspace, "component")
            self.review(workspace, "reuse-analysis")
            self.assertEqual(first["manifestHash"], self.read(root, "src/ui/ir/manifest.json")["hash"])
            workbench.begin_unit_generation(workspace, "component")

    def test_unit_generation_requires_design_tokens(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            for item in (workspace / "tokens/src").iterdir():
                item.unlink()
            with self.assertRaisesRegex(ValueError, "tokens/src"):
                workbench.start_unit_job(workspace, 4, "page-map")
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "not-started")

    def test_generation_follows_manifest_depends_on(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            manifest = self.read(root, "src/ui/ir/manifest.json")
            self.assertEqual(manifest["units"][1]["dependsOn"], ["page-map"])
            component = next(unit for unit in manifest["units"] if unit["id"] == "component")
            self.assertEqual(component["dependsOn"], ["reuse-analysis"])
            manifest["units"][1]["dependsOn"] = []
            manifest["units"][0]["dependsOn"] = ["platform-adaptation"]
            (workspace / "src/ui/ir/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            workbench.begin_unit_generation(workspace, "layout")
            with self.assertRaisesRegex(ValueError, "platform-adaptation"):
                workbench.begin_unit_generation(workspace, "page-map")

    def test_dependency_needs_latest_review_approved(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            self.review(workspace, "page-map")
            approvals_path = workspace / "project/approvals.json"
            approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
            approvals.append(dict(approvals[-1], status="changes-requested", conclusion="changes-requested"))
            approvals_path.write_text(json.dumps(approvals), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "page-map"):
                workbench.begin_unit_generation(workspace, "layout")

    def test_missing_output_never_matches_an_output_hash(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            write_output(workspace, "page-map")
            workbench.begin_unit_generation(workspace, "page-map")
            workbench.mark_unit_in_review(workspace, "page-map")
            (workspace / workbench.unit_output_path("page-map")).unlink()
            with self.assertRaisesRegex(ValueError, "outputHash"):
                workbench.append_unit_review(
                    workspace, "page-map", "approved", reviewer=REVIEWER, evidence=["evidence.json"],
                    file_scope=[{"path": workbench.unit_output_path("page-map"), "startLine": 1, "endLine": 1}],
                    output_hash=None,
                )
            manifest = self.read(root, "src/ui/ir/manifest.json")
            unit = manifest["units"][0]
            review = {"manifestVersion": manifest["manifestVersion"], "manifestHash": manifest["hash"], "conclusion": "approved"}
            self.assertFalse(check_workspace.review_current(workspace, review, manifest, unit))

    def test_dependency_output_changed_after_review_is_not_satisfied(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            self.review(workspace, "page-map")
            write_output(workspace, "page-map", "<main>edited after review</main>\n")
            with self.assertRaisesRegex(ValueError, "page-map"):
                workbench.begin_unit_generation(workspace, "layout")

    def test_regenerating_approved_unit_sends_started_downstream_back(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            self.review(workspace, "page-map")
            self.review(workspace, "layout")
            workbench.begin_unit_generation(workspace, "page-map")
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "in-progress")
            layout = self.unit_status(workspace, "layout")
            self.assertEqual(layout["status"], "changes-requested")
            self.assertIn("page-map", layout["note"])
            self.assertEqual(self.unit_status(workspace, "component")["status"], "not-started")

    def fake_codex(self, generated="<main>generated</main>\n", verdict=None, stray=None, claude_error=None):
        """Stand-in for both engines: codex writes the verdict via -o, Claude Code prints a JSON result."""
        calls = []

        def run(cmd, cwd, timeout, output_path=None):
            calls.append(cmd)
            claude = cmd[0] == "claude"
            if "read-only" in cmd or "--json-schema" in cmd:
                if verdict is not None:
                    text = verdict if isinstance(verdict, str) else json.dumps(verdict)
                    if claude:
                        structured = None if isinstance(verdict, str) else verdict
                        Path(output_path).write_text(json.dumps({
                            "type": "result", "is_error": False, "result": text, "structured_output": structured,
                            "modelUsage": {"claude-sonnet-5-5": {}},
                        }), encoding="utf-8")
                    else:
                        Path(cmd[cmd.index("-o") + 1]).write_text(text, encoding="utf-8")
                return 0
            unit_id = re.search(r"src/ui/units/([a-z-]+)/output\.html", cmd[-1]).group(1)
            write_output(cwd, unit_id, generated)
            if stray:
                (Path(cwd) / stray[0]).write_text(stray[1], encoding="utf-8")
            if claude:
                Path(output_path).write_text(json.dumps({
                    "type": "result", "is_error": claude_error is not None, "result": claude_error or "done",
                    "modelUsage": {"claude-sonnet-5-5": {}},
                }), encoding="utf-8")
            return 0
        return run, calls

    def run_unit_job(self, workspace, unit_id, **fake):
        run, calls = self.fake_codex(**fake)
        failed_check = subprocess.CompletedProcess([], 1, "unit layout requires unit-review", "")
        with mock.patch.object(workbench, "_run_agent", side_effect=run), \
                mock.patch.object(workbench.subprocess, "run", return_value=failed_check):
            job = wait_for(workbench.start_unit_job(workspace, 4, unit_id))
        return job, calls

    def test_unit_job_generates_then_runs_independent_read_only_review(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            verdict = {"conclusion": "approved", "summary": "Layout tokens used correctly", "findings": []}
            job, calls = self.run_unit_job(workspace, "page-map", verdict=verdict)
            self.assertEqual(job["status"], "done", job)
            self.assertFalse(job["check"]["passed"])
            self.assertEqual(len(calls), 2)
            self.assertIn("workspace-write", calls[0])
            self.assertIn("read-only", calls[1])
            self.assertIn(str(ROOT / "assets/unit-review-verdict.schema.json"), calls[1])
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "approved")
            record = self.read(root, "project/approvals.json")[-1]
            self.assertEqual(
                {key: record["reviewer"][key] for key in ("type", "name", "engine")},
                {"type": "subagent", "name": "codex-unit-reviewer", "engine": "codex"},
            )
            self.assertEqual(record["outputHash"], current_output_hash(workspace, "page-map"))
            self.assertIn("Layout tokens used correctly", record["evidence"])

    def test_agent_settings_are_validated_and_stored_at_init(self):
        with tempfile.TemporaryDirectory() as root:
            agents = {"generation": {"engine": "codex", "model": "gpt-6.1-sol", "reasoningEffort": "high"},
                      "review": {"engine": "claude", "reasoningEffort": "max"}}
            self.init(root, agents=agents)
            self.assertEqual(self.read(root, "config/ui.json")["agents"], agents)
        for bad, needle in (
            ({"generation": {"engine": "gemini"}}, "engine"),
            ({"review": {"engine": "codex", "reasoningEffort": "max"}}, "reasoningEffort"),
            ({"review": {"engine": "claude", "model": "--dangerously-skip-permissions"}}, "model"),
            ({"review": {"engine": "claude", "model": "opus 5"}}, "model"),
            ({"planning": {"engine": "codex"}}, "planning"),
        ):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as root:
                with self.assertRaisesRegex(ValueError, needle):
                    self.init(root, agents=bad)
        with tempfile.TemporaryDirectory() as root:
            self.init(root, agents={"generation": {"engine": "codex", "model": "", "reasoningEffort": ""}})
            self.assertNotIn("agents", self.read(root, "config/ui.json"))

    def test_agent_settings_merge_into_an_existing_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            agents = {"review": {"engine": "claude", "reasoningEffort": "high"}}
            workbench.init_workspace(workspace, "Tidewell", "Manage feedback", agents=agents)
            self.assertEqual(self.read(root, "config/ui.json")["agents"], agents)

    def test_claude_permissions_refuse_unsafe_paths_and_unit_ids(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "Brand [v2] *x"
            workbench.init_workspace(workspace, "Tidewell", "x", agents={"generation": {"engine": "claude"}})
            with self.assertRaisesRegex(ValueError, "Claude Code"):
                workbench._agent_command(workspace, {"engine": "claude"}, "prompt", unit_id="page-map")
            workbench._agent_command(workspace, {"engine": "codex"}, "prompt", unit_id="page-map")
        for unit_id in ("../../..", "Page Map", ""):
            with self.subTest(unit_id=unit_id), self.assertRaisesRegex(ValueError, "unit id"):
                workbench.unit_dir(unit_id)

    def test_claude_result_ignores_other_json_lines_and_picks_the_main_model(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "out.json"
            output.write_text('{"type": "result", "is_error": false, "modelUsage": {"claude-haiku-4-5": {"outputTokens": 40}, "claude-sonnet-5-5": {"outputTokens": 900}}}\n{"warning": "x"}\n', encoding="utf-8")
            data = workbench._claude_result(output)
            self.assertEqual(workbench._agent_identity({"engine": "claude"}, data)["model"], "claude-sonnet-5-5")
            output.write_text('{"warning": "only a warning"}\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "没有给出可读的结果"):
                workbench._claude_result(output)

    def test_codex_default_is_not_guessed_when_a_profile_is_active(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "config.toml"
            config.write_text("model = 'gpt-single'\n", encoding="utf-8")
            with mock.patch.object(workbench, "CODEX_CONFIG", config):
                self.assertEqual(workbench._codex_config_value("model"), "gpt-single")
                config.write_text('profile = "fast"\nmodel = "gpt-base"\n', encoding="utf-8")
                self.assertIsNone(workbench._codex_config_value("model"))

    def test_claude_generation_and_review_stay_inside_their_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root, agents={
                "generation": {"engine": "claude", "model": "sonnet", "reasoningEffort": "high"},
                "review": {"engine": "claude", "reasoningEffort": "max"},
            })
            verdict = {"conclusion": "approved", "summary": "Claude review ok", "findings": []}
            job, calls = self.run_unit_job(workspace, "page-map", verdict=verdict)
            self.assertEqual(job["status"], "done", job["error"])
            generate, review = calls
            own = "Write(/%s/**)" % (workspace / workbench.unit_dir("page-map"))
            self.assertEqual(generate[:2], ["claude", "-p"])
            self.assertIn("dontAsk", generate)
            self.assertIn(own, generate)
            self.assertNotIn("Bash", generate)
            self.assertEqual(generate[generate.index("--model") + 1], "sonnet")
            self.assertEqual(generate[generate.index("--effort") + 1], "high")
            self.assertIn("--json-schema", review)
            self.assertNotIn("$schema", json.loads(review[review.index("--json-schema") + 1]))
            self.assertFalse([part for part in review if part.startswith(("Write", "Edit"))])
            self.assertNotIn("--model", review)
            self.assertEqual(review[review.index("--effort") + 1], "max")
            record = self.read(root, "project/approvals.json")[-1]
            self.assertEqual(record["reviewer"], {
                "type": "subagent", "name": "claude-unit-reviewer", "engine": "claude",
                "model": "claude-sonnet-5-5", "reasoningEffort": "max",
            })
            self.assertEqual(json.loads((workspace / workbench.unit_dir("page-map") / "review.json").read_text(encoding="utf-8")), verdict)
            metadata = json.loads((workspace / workbench.unit_dir("page-map") / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["generatedBy"], {"engine": "claude", "model": "claude-sonnet-5-5", "reasoningEffort": "high"})

    def test_claude_reported_error_fails_the_unit_job(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root, agents={"generation": {"engine": "claude"}})
            job, _ = self.run_unit_job(workspace, "page-map", claude_error="Not logged in · Please run /login")
            self.assertEqual(job["status"], "error")
            self.assertIn("Not logged in", job["error"])
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "in-progress")

    def test_codex_model_and_effort_are_passed_and_recorded(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root, agents={"generation": {"engine": "codex", "model": "gpt-test", "reasoningEffort": "high"}})
            config = Path(root) / "codex-config.toml"
            config.write_text('model = "gpt-default"\nmodel_reasoning_effort = "medium"\n\n[projects."x"]\nmodel = "ignored"\n', encoding="utf-8")
            verdict = {"conclusion": "approved", "summary": "ok", "findings": []}
            with mock.patch.object(workbench, "CODEX_CONFIG", config):
                job, calls = self.run_unit_job(workspace, "page-map", verdict=verdict)
            self.assertEqual(job["status"], "done", job["error"])
            generate, review = calls
            self.assertEqual(generate[generate.index("-m") + 1], "gpt-test")
            self.assertEqual(generate[generate.index("-c") + 1], 'model_reasoning_effort="high"')
            self.assertNotIn("-m", review)
            record = self.read(root, "project/approvals.json")[-1]
            self.assertEqual(record["reviewer"]["engine"], "codex")
            self.assertEqual(record["reviewer"]["model"], "gpt-default")
            self.assertEqual(record["reviewer"]["reasoningEffort"], "medium")

    def test_unit_job_with_invalid_verdict_leaves_unit_in_review(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            job, _ = self.run_unit_job(workspace, "page-map", verdict="not json")
            self.assertEqual(job["status"], "error")
            unit = self.unit_status(workspace, "page-map")
            self.assertEqual(unit["status"], "in-review")
            self.assertIn("审查", unit["note"])
            self.assertEqual(self.read(root, "project/approvals.json")[3:], [])

            verdict = {"conclusion": "changes-requested", "summary": "Navigation misses focus state", "findings": [
                {"severity": "P1", "location": "output.html nav", "problem": "no focus-visible style"}]}
            run, _ = self.fake_codex(verdict=verdict)
            with mock.patch.object(workbench, "_run_agent", side_effect=run), \
                    mock.patch.object(workbench.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
                job = wait_for(workbench.start_review_job(workspace, 4, "page-map"))
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "changes-requested")
            # The verdict is about the output on disk, so the page must not call it stale.
            review = workbench.unit_overview(workspace)["units"][0]["review"]
            self.assertEqual((review["conclusion"], review["current"]), ("changes-requested", True))

    def test_unit_job_restores_files_outside_its_unit_and_fails(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            before = (workspace / "project/approvals.json").read_text(encoding="utf-8")
            job, calls = self.run_unit_job(
                workspace, "page-map", verdict={"conclusion": "approved", "summary": "ok", "findings": []},
                stray=("project/approvals.json", "[]"),
            )
            self.assertEqual(job["status"], "error")
            self.assertIn("project/approvals.json", job["error"])
            self.assertEqual((workspace / "project/approvals.json").read_text(encoding="utf-8"), before)
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "in-progress")

    def test_timed_out_unit_job_still_restores_files_outside_its_unit(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            before = (workspace / "project/approvals.json").read_text(encoding="utf-8")

            def stray_then_timeout(cmd, cwd, timeout):
                (Path(cwd) / "project/approvals.json").write_text("[]", encoding="utf-8")
                write_output(cwd, "layout", "<main>written by the wrong job</main>\n")
                raise TimeoutError("codex 进程超过 10 分钟未完成")
            with mock.patch.object(workbench, "_run_agent", side_effect=stray_then_timeout):
                job = wait_for(workbench.start_unit_job(workspace, 4, "page-map"))
            self.assertEqual(job["status"], "error")
            self.assertIn("超过 10 分钟", job["error"])
            self.assertIn("project/approvals.json", job["error"])
            self.assertEqual((workspace / "project/approvals.json").read_text(encoding="utf-8"), before)
            self.assertFalse((workspace / workbench.unit_output_path("layout")).exists())

    def test_unit_job_failure_path_always_releases_the_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            real_set = workbench.set_unit_status

            def failing_note(path, unit_id, status, note=None):
                if note:
                    raise OSError("disk full")
                return real_set(path, unit_id, status, note)
            with mock.patch.object(workbench, "_run_agent", return_value=1), \
                    mock.patch.object(workbench, "set_unit_status", side_effect=failing_note):
                job = wait_for(workbench.start_unit_job(workspace, 4, "page-map"))
            self.assertEqual(job["status"], "error")
            self.assertIsNone(workbench._running_job(workspace))

    def test_snapshot_failure_releases_the_job_without_touching_unit_states(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.review(workspace, "page-map")
            self.review(workspace, "layout")
            with mock.patch.object(workbench, "_protected_snapshot", side_effect=OSError("unreadable")):
                with self.assertRaises(OSError):
                    workbench.start_unit_job(workspace, 4, "page-map")
            self.assertIsNone(workbench._running_job(workspace))
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "approved")
            self.assertEqual(self.unit_status(workspace, "layout")["status"], "approved")

    def test_failed_restore_after_timeout_reports_both_problems(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)

            def timeout(cmd, cwd, timeout):
                raise TimeoutError("codex 进程超过 10 分钟未完成")
            with mock.patch.object(workbench, "_run_agent", side_effect=timeout), \
                    mock.patch.object(workbench, "_restore_outside_unit", side_effect=PermissionError("approvals.json locked")):
                job = wait_for(workbench.start_unit_job(workspace, 4, "page-map"))
            self.assertEqual(job["status"], "error")
            for needle in ("超过 10 分钟", "approvals.json locked", "可能仍被改动"):
                self.assertIn(needle, job["error"])

    def test_unit_job_restores_shared_tokens_and_brief(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            tokens = workspace / "tokens/src/color.json"
            tokens.parent.mkdir(parents=True, exist_ok=True)
            tokens.write_text('{"primary": "#123456"}', encoding="utf-8")
            brief = (workspace / "brand.brief.json").read_text(encoding="utf-8")

            def rewrite_shared(cmd, cwd, timeout):
                write_output(cwd, "page-map")
                (Path(cwd) / "tokens/src/color.json").write_text('{"primary": "#ff0000"}', encoding="utf-8")
                (Path(cwd) / "brand.brief.json").write_text("{}", encoding="utf-8")
                return 0
            with mock.patch.object(workbench, "_run_agent", side_effect=rewrite_shared):
                job = wait_for(workbench.start_unit_job(workspace, 4, "page-map"))
            self.assertEqual(job["status"], "error")
            self.assertEqual(tokens.read_text(encoding="utf-8"), '{"primary": "#123456"}')
            self.assertEqual((workspace / "brand.brief.json").read_text(encoding="utf-8"), brief)

    def test_unit_jobs_get_no_write_access_to_the_skill_repo(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            verdict = {"conclusion": "approved", "summary": "ok", "findings": []}
            _, calls = self.run_unit_job(workspace, "page-map", verdict=verdict)
            self.assertEqual(len(calls), 2)
            for cmd in calls:
                self.assertNotIn("--add-dir", cmd)
            self.assertIn(str(ROOT / "references/ui.md"), calls[0][-1])
            self.assertIn(str(ROOT / "references/ui.md"), calls[1][-1])

    def test_regeneration_prompt_carries_the_previous_review_and_note(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            write_output(workspace, "page-map")
            workbench.begin_unit_generation(workspace, "page-map")
            workbench.mark_unit_in_review(workspace, "page-map")
            workbench.append_unit_review(
                workspace, "page-map", "changes-requested", reviewer=REVIEWER,
                evidence=["src/ui/units/page-map/review.json", "Navigation misses focus state", "P1 nav：no focus-visible style"],
                file_scope=[{"path": workbench.unit_output_path("page-map"), "startLine": 1, "endLine": 1}],
                output_hash=current_output_hash(workspace, "page-map"),
            )
            with mock.patch.object(workbench, "_run_agent", return_value=1):
                job = wait_for(workbench.start_unit_job(workspace, 4, "page-map"))
            self.assertIn("no focus-visible style", job["prompt"])
            self.assertIn("Navigation misses focus state", job["prompt"])

    def test_phase_job_cannot_change_approvals_units_or_unit_progress(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.review(workspace, "page-map")
            approvals = (workspace / "project/approvals.json").read_text(encoding="utf-8")
            output = (workspace / workbench.unit_output_path("page-map")).read_text(encoding="utf-8")

            def phase_job(cmd, cwd, timeout):
                status_path = Path(cwd) / "project/status.json"
                status = json.loads(status_path.read_text(encoding="utf-8"))
                status["state"] = "in-review"
                status["units"] = []
                status_path.write_text(json.dumps(status), encoding="utf-8")
                (Path(cwd) / "project/approvals.json").write_text("[]", encoding="utf-8")
                write_output(cwd, "page-map", "<main>overwritten by the phase job</main>\n")
                (Path(cwd) / "review/04-assets.html").parent.mkdir(parents=True, exist_ok=True)
                (Path(cwd) / "review/04-assets.html").write_text("<main>assets</main>", encoding="utf-8")
                return 0
            with mock.patch.object(workbench, "_run_agent", side_effect=phase_job), \
                    mock.patch.object(workbench.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
                job = wait_for(workbench.start_generation(workspace, 4))
            self.assertEqual(job["status"], "error")
            self.assertIn("project/approvals.json", job["error"])
            self.assertEqual((workspace / "project/approvals.json").read_text(encoding="utf-8"), approvals)
            self.assertEqual((workspace / workbench.unit_output_path("page-map")).read_text(encoding="utf-8"), output)
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "approved")
            self.assertEqual(self.read(root, "project/status.json")["state"], "in-review")
            self.assertTrue((workspace / "review/04-assets.html").is_file())

    def test_failed_phase_job_leaves_status_as_it_found_it(self):
        # status.json is the progress authority; a run that died must not leave its claims of completed work there.
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            before = (workspace / "project/status.json").read_text(encoding="utf-8")

            def claim_then_timeout(cmd, cwd, timeout):
                status_path = Path(cwd) / "project/status.json"
                status = json.loads(status_path.read_text(encoding="utf-8"))
                status.update(state="in-review", completed=["phase 4 assets"])
                status_path.write_text(json.dumps(status), encoding="utf-8")
                raise TimeoutError("codex 进程超过 30 分钟未完成")
            with mock.patch.object(workbench, "_run_agent", side_effect=claim_then_timeout):
                job = wait_for(workbench.start_generation(workspace, 4))
            self.assertEqual(job["status"], "error")
            self.assertEqual((workspace / "project/status.json").read_text(encoding="utf-8"), before)

    def test_unit_job_requires_phase_four_gates_and_one_job_per_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            with self.assertRaisesRegex(ValueError, "Phase 4"):
                workbench.start_unit_job(workspace, 4, "page-map")
            workspace = self.phase_four(root)
            (workspace / "project/approvals.json").write_text(json.dumps([gate("G1"), gate("G2")]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "G3"):
                workbench.start_unit_job(workspace, 4, "page-map")
            (workspace / "project/approvals.json").write_text(
                json.dumps([gate("G1"), gate("G2"), gate("G3")]), encoding="utf-8"
            )
            release = threading.Event()

            def slow(cmd, cwd, timeout):
                release.wait(5)
                return 1
            with mock.patch.object(workbench, "_run_agent", side_effect=slow):
                job_id = workbench.start_unit_job(workspace, 4, "page-map")
                with self.assertRaisesRegex(ValueError, "任务"):
                    workbench.start_generation(workspace, 4)
                release.set()
                wait_for(job_id)

    def test_phase_job_that_rewrites_status_without_units_keeps_unit_progress(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.review(workspace, "page-map")

            def documented_shape(cmd, cwd, timeout):
                (Path(cwd) / "project/status.json").write_text(json.dumps({
                    "phase": 4, "state": "in-review", "completed": [], "next": [], "blockers": [],
                }), encoding="utf-8")
                return 0
            with mock.patch.object(workbench, "_run_agent", side_effect=documented_shape), \
                    mock.patch.object(workbench.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "units pending", "")):
                job = wait_for(workbench.start_generation(workspace, 4))
            self.assertEqual(job["status"], "done", job["error"])
            self.assertEqual(self.read(root, "project/status.json")["state"], "in-review")
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "approved")

    def test_unit_progress_restore_handles_every_status_shape(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            status_path = workspace / "project/status.json"
            status_path.parent.mkdir(parents=True)
            units = [{"unitId": "page-map", "status": "in-review"}]
            cases = (
                ({"phase": 4}, {"phase": 4, "units": [{"unitId": "page-map", "status": "approved"}]}, True, None),
                ({"phase": 4, "units": units}, {"phase": 4}, False, units),
                ({"phase": 4, "units": units}, {"phase": 4, "units": None}, True, units),
                ({"phase": 4, "units": units}, {"phase": 4, "units": list(reversed(units + [{"unitId": "x", "status": "approved"}]))}, True, units),
            )
            for before, after, tampered, expected in cases:
                with self.subTest(before=before, after=after):
                    status_path.write_text(json.dumps(after), encoding="utf-8")
                    changed = workbench._restore_unit_progress(workspace, json.dumps(before).encode("utf-8"))
                    self.assertEqual(bool(changed), tampered)
                    restored = json.loads(status_path.read_text(encoding="utf-8"))
                    self.assertEqual(restored.get("units"), expected)

    def test_failed_phase_check_is_reported_without_failing_the_generation_job(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.at_phase(self.init(root), 1)
            failed = subprocess.CompletedProcess([], 1, "missing review/01-directions.html", "")
            with mock.patch.object(workbench, "_run_agent", return_value=0), \
                    mock.patch.object(workbench.subprocess, "run", return_value=failed):
                job = wait_for(workbench.start_generation(workspace, 1))
            self.assertEqual(job["status"], "done")
            self.assertFalse(job["check"]["passed"])
            self.assertIn("missing review/01-directions.html", job["check"]["output"])

    def test_phase_job_is_sandboxed_to_the_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.at_phase(self.init(root), 1)
            seen = []

            def capture(cmd, cwd, timeout):
                seen.append(cmd)
                return 0
            with mock.patch.object(workbench, "_run_agent", side_effect=capture), \
                    mock.patch.object(workbench.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
                wait_for(workbench.start_generation(workspace, 1))
            cmd = seen[0]
            self.assertEqual(cmd[cmd.index("-s") + 1], "workspace-write")
            self.assertEqual(cmd[cmd.index("-C") + 1], str(workspace))
            self.assertNotIn("--add-dir", cmd)

    def test_empty_output_is_not_sent_to_review(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            write_output(workspace, "page-map", "")
            workbench.begin_unit_generation(workspace, "page-map")
            with self.assertRaises(ValueError):
                workbench.mark_unit_in_review(workspace, "page-map")

    def test_gate_validity_matches_the_checker(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            incomplete = {key: value for key, value in G1_APPROVED.items() if key != "confirmation"}
            (workspace / "project/approvals.json").write_text(json.dumps([incomplete]), encoding="utf-8")
            self.assertEqual(workbench._missing_gates(workspace, 2), ["G1"])
            (workspace / "project/approvals.json").write_text(json.dumps([G1_APPROVED, dict(G1_APPROVED, kind="Gate")]), encoding="utf-8")
            self.assertEqual(workbench._missing_gates(workspace, 2), ["G1"])

    def test_unit_overview_reports_blockers_and_actions(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            overview = workbench.unit_overview(workspace)
            units = {item["id"]: item for item in overview["units"]}
            self.assertEqual(overview["phaseBlocker"], "")
            self.assertEqual(overview["currentUnit"], "page-map")
            self.assertTrue(units["page-map"]["canGenerate"])
            self.assertFalse(units["layout"]["canGenerate"])
            self.assertEqual(units["layout"]["blockedBy"], ["page-map"])
            self.assertNotIn("phaseBlocker", units["page-map"])
            write_output(workspace, "page-map")
            workbench.begin_unit_generation(workspace, "page-map")
            workbench.mark_unit_in_review(workspace, "page-map")
            workbench.append_unit_review(
                workspace, "page-map", "approved", reviewer=REVIEWER, evidence=["review.json", "detail"],
                file_scope=[{"path": workbench.unit_output_path("page-map"), "startLine": 1, "endLine": 1}],
                output_hash=current_output_hash(workspace, "page-map"), summary="Every page has a clear job",
            )
            overview = workbench.unit_overview(workspace)
            units = {item["id"]: item for item in overview["units"]}
            self.assertEqual(overview["currentUnit"], "layout")
            self.assertTrue(units["layout"]["canGenerate"])
            self.assertEqual(units["page-map"]["review"]["conclusion"], "approved")
            self.assertEqual(units["page-map"]["review"]["summary"], "Every page has a clear job")


class UiWorkbenchRobustnessTest(unittest.TestCase):
    def test_agent_timeout_holds_while_a_partial_line_is_pending(self):
        script = "import sys, time; sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(30)"
        with tempfile.TemporaryDirectory() as cwd:
            started = time.time()
            with self.assertRaises(TimeoutError):
                workbench._run_agent([sys.executable, "-c", script], cwd, 1)
        self.assertLess(time.time() - started, 10)

    def test_background_commands_stop_with_the_agent(self):
        with tempfile.TemporaryDirectory() as cwd:
            output = Path(cwd) / "out.txt"
            code = workbench._run_agent(["/bin/sh", "-c", "sleep 30 & echo $!"], cwd, 10, output_path=output)
            self.assertEqual(code, 0)
            pid = int(output.read_text(encoding="utf-8").strip())
            deadline = time.time() + 3
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("background sleep %d survived the agent" % pid)
        self.assertEqual(workbench.AGENT_GROUPS, set())

    def test_agent_exit_is_seen_while_a_background_child_keeps_printing(self):
        with tempfile.TemporaryDirectory() as cwd:
            output = Path(cwd) / "out.txt"
            started = time.time()
            code = workbench._run_agent(["/bin/sh", "-c", "(while :; do echo x; sleep 0.1; done) & echo done; exit 0"], cwd, 10, output_path=output)
            self.assertEqual(code, 0)
            self.assertLess(time.time() - started, 5)
            self.assertIn("done", output.read_text(encoding="utf-8"))

    def test_restore_replaces_a_symlink_instead_of_writing_through_it(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            workspace = Path(root)
            (workspace / "project").mkdir()
            (workspace / "project/approvals.json").write_text("[]", encoding="utf-8")
            outside = Path(elsewhere) / "dotfile"
            outside.write_text("keep me", encoding="utf-8")
            before = workbench._snapshot(workspace, ("project",))
            (workspace / "project/approvals.json").unlink()
            (workspace / "project/approvals.json").symlink_to(outside)
            reported = workbench._restore(workspace, ("project",), before)
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep me")
            self.assertFalse((workspace / "project/approvals.json").is_symlink())
            self.assertEqual((workspace / "project/approvals.json").read_text(encoding="utf-8"), "[]")
            remove_backup(reported[0])

    def test_restore_does_not_write_through_a_swapped_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            workspace = Path(root)
            (workspace / "project/sub").mkdir(parents=True)
            (workspace / "project/top.txt").write_text("mine", encoding="utf-8")
            (workspace / "project/sub/a.txt").write_text("a", encoding="utf-8")
            (Path(elsewhere) / "top.txt").write_text("outside", encoding="utf-8")
            before = workbench._snapshot(workspace, ("project",))
            shutil.rmtree(workspace / "project")
            (workspace / "project").symlink_to(elsewhere)
            workbench._restore(workspace, ("project",), before)
            self.assertEqual((Path(elsewhere) / "top.txt").read_text(encoding="utf-8"), "outside")
            self.assertFalse((Path(elsewhere) / "sub").exists())
            self.assertFalse((workspace / "project").is_symlink())
            self.assertEqual((workspace / "project/sub/a.txt").read_text(encoding="utf-8"), "a")

    def test_restore_never_deletes_through_a_linked_parent(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            workspace = Path(root)
            (workspace / "src/ui/ir").mkdir(parents=True)
            (workspace / "src/ui/ir/manifest.json").write_text("{}", encoding="utf-8")
            (Path(elsewhere) / "ir/sub").mkdir(parents=True)
            (Path(elsewhere) / "ir/precious.txt").write_text("keep", encoding="utf-8")
            (Path(elsewhere) / "ir/sub/f").write_text("keep", encoding="utf-8")
            before = workbench._snapshot(workspace, ("src/ui/ir",))
            shutil.rmtree(workspace / "src/ui")
            (workspace / "src/ui").symlink_to(elsewhere)
            workbench._restore(workspace, ("src/ui/ir",), before)
            self.assertEqual((Path(elsewhere) / "ir/precious.txt").read_text(encoding="utf-8"), "keep")
            self.assertEqual((Path(elsewhere) / "ir/sub/f").read_text(encoding="utf-8"), "keep")
            self.assertFalse((workspace / "src/ui").is_symlink())
            self.assertEqual((workspace / "src/ui/ir/manifest.json").read_text(encoding="utf-8"), "{}")

    def test_jobs_refuse_a_protected_root_that_is_a_link(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as elsewhere:
            workspace = Path(root)
            (workspace / "project").symlink_to(elsewhere)
            with self.assertRaisesRegex(ValueError, "project"):
                workbench._require_real_roots(workspace, ("project/approvals.json",))
            workbench._require_real_roots(workspace, ("config",))

    def test_backups_of_a_link_and_a_same_named_file_do_not_collide(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            (workspace / "project").mkdir()
            before = workbench._snapshot(workspace, ("project",))
            (workspace / "project/x").symlink_to("target")
            (workspace / "project/x.symlink.txt").mkdir()
            (workspace / "project/x.symlink.txt/f").write_text("real", encoding="utf-8")
            reported = workbench._restore(workspace, ("project",), before)
            saved = sorted(Path(m.group(1)).read_bytes() for m in (re.search(r"保存到 (.+)）", r) for r in reported))
            self.assertEqual(saved, [b"real", b"target"])
            # Directories the job created may stay empty; no file or link it added does.
            self.assertEqual([p for p in (workspace / "project").rglob("*") if p.is_file() or p.is_symlink()], [])
            remove_backup(reported[0])

    def test_restore_brings_back_an_existing_symlink_as_a_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            (workspace / "project").mkdir()
            (workspace / "project/top.txt").write_text("mine", encoding="utf-8")
            (workspace / "project/ln").symlink_to("top.txt")
            before = workbench._snapshot(workspace, ("project",))
            (workspace / "project/ln").unlink()
            (workspace / "project/ln").write_text("replaced", encoding="utf-8")
            workbench._restore(workspace, ("project",), before)
            self.assertTrue((workspace / "project/ln").is_symlink())
            self.assertEqual(os.readlink(workspace / "project/ln"), "top.txt")

    def test_restore_keeps_a_copy_of_what_it_overwrites(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            (workspace / "project").mkdir()
            (workspace / "project/approvals.json").write_text("[]", encoding="utf-8")
            before = workbench._snapshot(workspace, ("project",))
            (workspace / "project/approvals.json").write_text('["edited meanwhile"]', encoding="utf-8")
            [reported] = workbench._restore(workspace, ("project",), before)
            self.assertEqual((workspace / "project/approvals.json").read_text(encoding="utf-8"), "[]")
            copy = Path(re.search(r"保存到 (.+)）", reported).group(1))
            self.assertEqual(copy.read_text(encoding="utf-8"), '["edited meanwhile"]')
            remove_backup(reported)

    def test_file_count_is_capped(self):
        with tempfile.TemporaryDirectory() as root:
            for index in range(5):
                (Path(root) / ("f%d" % index)).write_text("x", encoding="utf-8")
            with mock.patch.object(workbench, "SCAN_FILE_LIMIT", 3):
                self.assertEqual(workbench.count_files(Path(root)), "3+ 个可读取文件")
            self.assertEqual(workbench.count_files(Path(root)), "5 个可读取文件")


class WorkbenchPageTest(unittest.TestCase):
    html = (ROOT / "assets/workbench.html").read_text(encoding="utf-8")

    def test_page_script_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed; CI runners have it")
        scripts = re.findall(r"<script>(.*?)</script>", workbench.page_html(), re.S)
        self.assertTrue(scripts)
        with tempfile.TemporaryDirectory() as d:
            for index, body in enumerate(scripts):
                path = Path(d) / ("page%d.js" % index)
                path.write_text(body, encoding="utf-8")
                result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_label_names_its_control(self):
        ids = set(re.findall(r'id="([^"]+)"', self.html))
        for label in re.findall(r"<label\b[^>]*>.*?</label>", self.html, re.S):
            target = re.match(r'<label[^>]*\bfor="([^"]+)"', label)
            if target:
                self.assertIn(target.group(1), ids, label)
            else:
                self.assertRegex(label, r"<(input|select|textarea)\b", "label neither names nor wraps a control: " + label[:80])

    def test_page_gets_its_choices_from_the_checker(self):
        page = workbench.page_html()
        self.assertNotIn("__", re.sub(r"__proto__", "", "".join(re.findall(r"const UI=.*?;", page))))
        for value in check_workspace.STACK_PROFILES + check_workspace.UI_PLATFORMS:
            self.assertNotIn('<option value="%s"' % value, self.html)
        # The unit panel sits in the branch of the phase that owns UI units.
        before_panel = self.html[:self.html.index('id="unitPanel"')]
        self.assertEqual(re.findall(r"i===(\d)\?", before_panel)[-1], str(workbench.UI_UNIT_PHASE))

    def test_phase_log_is_escaped_before_it_reaches_the_page(self):
        self.assertIn("esc(phaseLogs[i].join(", self.html)
        self.assertNotIn("'+phaseLogs[i].join(", self.html)


class UiWorkbenchScriptTest(unittest.TestCase):
    def test_workbench_starts_as_a_script_from_another_directory(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, str(ROOT / "scripts/workbench.py"), "--help"], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)


    def test_script_prints_the_real_address_and_links_the_workspace(self):
        import urllib.parse
        import urllib.request
        with tempfile.TemporaryDirectory() as root:
            workspace = workbench.init_workspace(Path(root) / "brand", "Tidewell", "Manage feedback")
            proc = subprocess.Popen([sys.executable, str(ROOT / "scripts/workbench.py"), root, "--port", "0", "--workspace", "brand"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                url = proc.stdout.readline().split(": ", 1)[1].strip()
                self.assertNotIn(":0/", url)
                self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["workspace"], [str(workspace)])
                with urllib.request.urlopen(url) as response:
                    self.assertEqual(response.status, 200)
            finally:
                proc.terminate()
                proc.communicate(timeout=10)

    def test_workspace_outside_the_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, str(ROOT / "scripts/workbench.py"), root, "--port", "0", "--workspace", "../elsewhere"],
                                    capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("启动目录", result.stderr)


class UiWorkbenchHttpTest(unittest.TestCase):
    def setUp(self):
        import urllib.request
        from http.server import ThreadingHTTPServer
        self.urllib = urllib
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.workspace = workbench.init_workspace(self.root / "brand", "Tidewell", "Manage feedback")
        workbench.Handler.root = self.root
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), workbench.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, endpoint, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = self.urllib.request.Request(self.base + endpoint, data=data, headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with self.urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except self.urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def set_phase(self, phase):
        status_path = self.workspace / "project/status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["phase"] = phase
        status_path.write_text(json.dumps(status), encoding="utf-8")

    def test_gate_records_carry_kind_and_writes_are_refused_while_a_job_runs(self):
        self.set_phase(1)
        code, payload = self.call("/api/approve", {"path": str(self.workspace), "gate": "G1", "choice": "B"})
        self.assertEqual(code, 200, payload)
        record = json.loads((self.workspace / "project/approvals.json").read_text(encoding="utf-8"))[-1]
        self.assertEqual(record["kind"], "gate")
        self.assertEqual(record["snapshot"], "direction-B")
        workbench.JOBS["fake-running"] = {"status": "running", "path": str(self.workspace), "logs": [], "unitId": None}
        try:
            for endpoint, body in (
                ("/api/approve", {"path": str(self.workspace), "gate": "G1", "choice": "A"}),
                ("/api/advance", {"path": str(self.workspace), "fromPhase": 1}),
                ("/api/init", {"path": str(self.workspace), "official": "Other", "oneLiner": "x"}),
            ):
                code, payload = self.call(endpoint, body)
                self.assertEqual(code, 400, endpoint)
                self.assertIn("任务正在运行", payload["error"])
            code, payload = self.call("/api/state?path=" + str(self.workspace))
            self.assertEqual(payload["activeJob"]["id"], "fake-running")
        finally:
            workbench.JOBS.pop("fake-running", None)

    def test_requests_from_other_sites_are_refused(self):
        self.set_phase(1)
        approve = {"path": str(self.workspace), "gate": "G1", "choice": "B"}
        code, _ = self.call("/api/approve", approve, {"Origin": "http://evil.example"})
        self.assertEqual(code, 403)
        self.assertEqual(json.loads((self.workspace / "project/approvals.json").read_text(encoding="utf-8")), [])
        code, _ = self.call("/api/state?path=" + str(self.workspace), headers={"Host": "evil.example:%d" % self.server.server_address[1]})
        self.assertEqual(code, 403)
        code, payload = self.call("/api/approve", approve, {"Origin": self.base})
        self.assertEqual(code, 200, payload)

    def test_preview_is_sandboxed_and_served_with_its_own_type(self):
        (self.workspace / "review").mkdir(exist_ok=True)
        (self.workspace / "review/notes.md").write_text("# <b>notes</b>\n", encoding="utf-8")
        (self.workspace / "review/page.html").write_text("<main>page</main>", encoding="utf-8")
        for name, content_type in (("notes.md", "text/plain; charset=utf-8"), ("page.html", "text/html; charset=utf-8")):
            url = "%s/preview?path=%s&file=review/%s" % (self.base, self.urllib.parse.quote(str(self.workspace)), name)
            with self.urllib.request.urlopen(url) as response:
                self.assertEqual(response.headers["Content-Type"], content_type)
                self.assertIn("sandbox", response.headers["Content-Security-Policy"])
                self.assertNotIn("allow-same-origin", response.headers["Content-Security-Policy"])

    def test_init_stays_inside_the_startup_directory(self):
        code, payload = self.call("/api/init", {"path": "../outside", "official": "X", "oneLiner": "x"})
        self.assertEqual(code, 400)
        self.assertIn("启动目录", payload["error"])
        self.assertFalse((self.root.parent / "outside").exists())
        code, payload = self.call("/api/init", {"path": "third", "official": "X", "oneLiner": "x"})
        self.assertEqual(code, 200, payload)
        self.assertEqual(payload["path"], str(self.root / "third"))

    def test_gate_can_be_withdrawn_from_the_page(self):
        self.set_phase(2)
        self.assertEqual(self.call("/api/approve", {"path": str(self.workspace), "gate": "G1", "choice": "B"})[0], 200)
        code, payload = self.call("/api/approve", {"path": str(self.workspace), "gate": "G1", "status": "changes-requested"})
        self.assertEqual(code, 200, payload)
        self.assertEqual(workbench._missing_gates(self.workspace, 2), ["G1"])
        self.assertEqual(check_workspace.check(self.workspace, 2).count("phase 2 requires gate G1 approved (latest record decides)"), 1)

    def test_withdrawing_a_gate_withdraws_the_later_ones(self):
        self.set_phase(3)
        for body in ({"gate": "G1", "choice": "B"}, {"gate": "G2"}):
            self.assertEqual(self.call("/api/approve", dict(body, path=str(self.workspace)))[0], 200)
        code, payload = self.call("/api/approve", {"path": str(self.workspace), "gate": "G1", "status": "changes-requested"})
        self.assertEqual((code, payload["withdrawn"]), (200, ["G1", "G2"]))
        # The withdrawn gate's own phase is what has to change, so the workspace goes back there.
        status = json.loads((self.workspace / "project/status.json").read_text(encoding="utf-8"))
        self.assertEqual((status["phase"], status["state"]), (1, "draft"))
        self.call("/api/approve", {"path": str(self.workspace), "gate": "G1", "choice": "A"})
        self.assertEqual(workbench._missing_gates(self.workspace, 3), ["G2"])

    def test_unexpected_errors_are_500_without_internals(self):
        with mock.patch.object(workbench, "unit_overview", side_effect=RuntimeError("secret /internal/path")), \
                mock.patch.object(workbench.sys, "stderr", io.StringIO()) as log:
            code, payload = self.call("/api/state?path=" + str(self.workspace))
        self.assertEqual(code, 500)
        self.assertNotIn("/internal/path", payload["error"])
        self.assertIn("secret /internal/path", log.getvalue())

    def test_advance_requires_the_gates_the_phase_contract_names(self):
        self.set_phase(1)
        code, payload = self.call("/api/advance", {"path": str(self.workspace), "fromPhase": 1})
        self.assertEqual(code, 400)
        self.assertIn("G1", payload["error"])

    def test_init_endpoint_stores_agent_settings(self):
        agents = {"review": {"engine": "claude", "model": "opus", "reasoningEffort": "xhigh"}}
        code, payload = self.call("/api/init", {
            "path": str(self.root / "second"), "official": "Second", "oneLiner": "x", "agents": agents,
        })
        self.assertEqual(code, 200, payload)
        ui = json.loads((self.root / "second/config/ui.json").read_text(encoding="utf-8"))
        self.assertEqual(ui["agents"], agents)

    def test_state_exposes_unit_overview(self):
        code, payload = self.call("/api/state?path=" + str(self.workspace))
        self.assertEqual(code, 200)
        self.assertEqual([item["id"] for item in payload["uiUnits"]["units"]][0], "page-map")
        self.assertFalse(payload["uiUnits"]["units"][0]["canGenerate"])
        self.assertIn("Phase 4", payload["uiUnits"]["phaseBlocker"])

    def test_state_exposes_latest_gate_states(self):
        # The page hides the backfill button once a gate is approved; it needs the checker's verdict to do that.
        code, payload = self.call("/api/state?path=" + str(self.workspace))
        self.assertEqual((code, payload["gates"]), (200, {}))
        (self.workspace / "project/approvals.json").write_text(json.dumps([G1_APPROVED]), encoding="utf-8")
        code, payload = self.call("/api/state?path=" + str(self.workspace))
        self.assertEqual(payload["gates"], {"G1": "approved"})

    def test_unit_generation_and_review_endpoints_enforce_phase_four(self):
        code, payload = self.call("/api/generate", {"path": str(self.workspace), "phase": 0, "unitId": "page-map"})
        self.assertEqual(code, 400)
        self.assertIn("Phase 4", payload["error"])
        code, payload = self.call("/api/review", {"path": str(self.workspace), "phase": 0, "unitId": "page-map"})
        self.assertEqual(code, 400)
        self.assertIn("Phase 4", payload["error"])

    def test_unit_review_endpoint_requires_output_hash(self):
        write_output(self.workspace, "page-map")
        workbench.begin_unit_generation(self.workspace, "page-map")
        workbench.mark_unit_in_review(self.workspace, "page-map")
        body = {
            "path": str(self.workspace), "kind": "unit-review", "unitId": "page-map", "conclusion": "approved",
            "reviewer": REVIEWER, "evidence": ["evidence.json"],
            "fileScope": [{"path": workbench.unit_output_path("page-map"), "startLine": 1, "endLine": 1}],
        }
        code, payload = self.call("/api/approve", body)
        self.assertEqual(code, 400)
        self.assertIn("outputHash", payload["error"])
        body["outputHash"] = current_output_hash(self.workspace, "page-map")
        code, payload = self.call("/api/approve", body)
        self.assertEqual(code, 200, payload)


if __name__ == "__main__":
    unittest.main()
