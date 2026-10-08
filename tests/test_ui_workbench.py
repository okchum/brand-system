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

# Unit tests must not start a real browser; the one test that needs Chrome restores this.
REAL_CHROME = workbench._chrome_binary
workbench._chrome_binary = lambda: None
# Restore backups and agent scratch files go to a throwaway directory, not the user's cache.
REAL_SCRATCH_ROOT = workbench.SCRATCH_ROOT
TEST_SCRATCH = tempfile.mkdtemp(prefix="brand-system-test-scratch-")
workbench.SCRATCH_ROOT = Path(TEST_SCRATCH).resolve()


def tearDownModule():
    shutil.rmtree(TEST_SCRATCH, ignore_errors=True)

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
    backup = next(parent for parent in saved.parents
                  if parent.name.startswith("restore-") and parent.parent == workbench.SCRATCH_ROOT)
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
            with mock.patch.object(workbench, "_run_agent", side_effect=RuntimeError("codex 退出码 1")):
                job_id = workbench.start_generation(workspace, 1)
                wait_for(job_id)
            self.assertNotIn("Direction B", workbench.JOBS[job_id]["prompt"])

    def test_later_generation_requires_latest_valid_g1_and_uses_its_direction(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.at_phase(self.init(root), 2)
            with self.assertRaisesRegex(ValueError, "G1"):
                workbench.start_generation(workspace, 2)
            (workspace / "project/approvals.json").write_text(json.dumps([G1_APPROVED]), encoding="utf-8")
            with mock.patch.object(workbench, "_run_agent", side_effect=RuntimeError("codex 退出码 1")):
                job_id = workbench.start_generation(workspace, 2)
                wait_for(job_id)
            self.assertIn("Direction C", workbench.JOBS[job_id]["prompt"])
            self.assertNotIn("Direction B", workbench.JOBS[job_id]["prompt"])

    def test_phase_prompts_ask_for_review_pages_that_carry_their_own_data(self):
        prompt = workbench._phase_prompt(5, "C")
        self.assertIn("写进页面", prompt)
        self.assertIn("不用 fetch", prompt)
        self.assertNotIn("必须读取", prompt)
        self.assertIn("不用 fetch", workbench._phase_prompt(1, None))

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

    def test_workbench_writes_in_a_unit_never_follow_links_the_agent_planted(self):
        # The generating agent can write anything in its unit directory, including links to files outside it.
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            workspace = self.phase_four(root)
            victim = Path(outside) / "victim.txt"
            victim.write_text("keep", encoding="utf-8")
            unit_path = workspace / workbench.unit_dir("page-map")
            write_output(workspace, "page-map")
            workbench.begin_unit_generation(workspace, "page-map")
            for name in ("metadata.json.tmp", ".preview.html", "review.json"):
                (unit_path / name).symlink_to(victim)
            workbench.mark_unit_in_review(workspace, "page-map")
            self.assertFalse((unit_path / "metadata.json").is_symlink())
            unit = workbench._unit_record(json.loads((workspace / "src/ui/ir/manifest.json").read_text(encoding="utf-8")), "page-map")
            with mock.patch.object(workbench, "_chrome_binary", return_value="chrome"), \
                    mock.patch.object(workbench, "_capture_views", return_value={"desktop-top.png": b"png"}):
                workbench._unit_screenshots(workspace, unit)
            verdict = {"conclusion": "approved", "summary": "ok", "findings": []}
            run, _ = self.fake_codex(verdict=verdict)
            with mock.patch.object(workbench, "_run_agent", side_effect=run), \
                    mock.patch.object(workbench.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
                wait_for(workbench.start_review_job(workspace, 4, "page-map"))
            self.assertEqual(victim.read_text(encoding="utf-8"), "keep")

    def test_unit_directory_turned_into_a_link_is_refused(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            workspace = self.phase_four(root)
            write_output(workspace, "page-map")
            unit_path = workspace / workbench.unit_dir("page-map")
            shutil.copytree(unit_path, Path(outside) / "unit")
            shutil.rmtree(unit_path)
            unit_path.symlink_to(Path(outside) / "unit")
            unit = workbench._unit_record(json.loads((workspace / "src/ui/ir/manifest.json").read_text(encoding="utf-8")), "page-map")
            with mock.patch.object(workbench, "_chrome_binary", return_value="chrome"), \
                    mock.patch.object(workbench, "_capture_views", return_value={"desktop-top.png": b"png"}):
                with self.assertRaises(ValueError):
                    workbench._unit_screenshots(workspace, unit)
            self.assertFalse((Path(outside) / "unit/screenshots").exists())

    def test_a_failed_screenshot_falls_back_to_a_source_only_review(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            verdict = {"conclusion": "approved", "summary": "ok", "findings": []}
            with mock.patch.object(workbench, "_unit_screenshots", side_effect=TimeoutError("Chrome 截图超时")):
                job, calls = self.run_unit_job(workspace, "page-map", verdict=verdict)
            self.assertEqual(job["status"], "done", job)
            self.assertFalse([part for part in calls[1] if part.startswith("--image")])
            self.assertIn("Chrome 截图超时", "\n".join(job["logs"]))
            self.assertIn("没有视觉截图", calls[1][-1])

    def test_a_parent_swapped_for_a_link_after_the_check_is_never_written_through(self):
        # A process the agent left running can swap a directory for a link between any check and the write.
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            workspace = self.phase_four(root)
            unit_path = workspace / workbench.unit_dir("page-map")
            unit_path.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(unit_path)
            unit_path.symlink_to(Path(outside).resolve())
            for write in (lambda: workbench._write_json(unit_path / "metadata.json", {"x": 1}),
                          lambda: workbench._write_new(unit_path / "screenshots/desktop-top.png", b"png"),
                          lambda: workbench._remove(unit_path / "victim.txt")):
                (Path(outside) / "victim.txt").write_text("keep", encoding="utf-8")
                with self.assertRaises(ValueError):
                    write()
                self.assertEqual(sorted(p.name for p in Path(outside).iterdir()), ["victim.txt"])

    def test_restoring_status_replaces_a_link_instead_of_writing_through_it(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            workspace = self.phase_four(root)
            victim = Path(outside) / "victim.txt"
            victim.write_text("keep", encoding="utf-8")
            before = (workspace / "project/status.json").read_bytes()

            def link_then_fail(cmd, cwd, timeout):
                status_path = Path(cwd) / "project/status.json"
                status_path.unlink()
                status_path.symlink_to(victim)
                raise TimeoutError("codex 进程超过 30 分钟未完成")
            with mock.patch.object(workbench, "_run_agent", side_effect=link_then_fail):
                job = wait_for(workbench.start_generation(workspace, 4))
            self.assertEqual(job["status"], "error")
            self.assertEqual(victim.read_text(encoding="utf-8"), "keep")
            self.assertFalse((workspace / "project/status.json").is_symlink())
            self.assertEqual((workspace / "project/status.json").read_bytes(), before)

    def test_workbench_scratch_files_live_where_the_agent_sandbox_cannot_write(self):
        # codex's workspace-write sandbox can write $TMPDIR and /tmp, so they are no safer than the workspace.
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            verdict = {"conclusion": "approved", "summary": "ok", "findings": []}
            job, calls = self.run_unit_job(workspace, "page-map", verdict=verdict)
            output = Path(calls[1][calls[1].index("-o") + 1]).resolve()
            self.assertTrue(output.is_relative_to(workbench.SCRATCH_ROOT), output)
            self.assertFalse(output.exists())

    def test_scratch_root_ignores_a_cache_setting_the_sandbox_could_write(self):
        home_cache = Path.home() / ".cache" / "brand-system"
        for value in ("", "relative/cache", "/tmp/cache", tempfile.gettempdir()):
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": value}):
                self.assertEqual(workbench._default_scratch_root(), home_cache, value)
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "", "HOME": "/tmp/fake-home"}):
            self.assertIsNone(workbench._default_scratch_root())
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            linked = Path(root).resolve() / "scratch"
            linked.symlink_to(outside)
            with mock.patch.object(workbench, "SCRATCH_ROOT", linked):
                with self.assertRaises(ValueError):
                    workbench._scratch_dir("verdict-")

    def test_claude_result_that_is_not_text_is_reported_as_unreadable(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root, agents={"review": {"engine": "claude"}})
            with mock.patch.object(workbench, "_run_agent_command", side_effect=[None, {"result": 5}]):
                write_output(workspace, "page-map")
                job = wait_for(workbench.start_unit_job(workspace, 4, "page-map"))
            self.assertIn("审查进程没有给出可读的结论", job["error"])

    def test_a_planted_tmp_tree_cannot_skip_the_restore_after_a_failed_run(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            approvals = (workspace / "project/approvals.json").read_text(encoding="utf-8")

            def plant_then_fail(cmd, cwd, timeout):
                # Built one level at a time, as an agent would; the full path is too long for one mkdir.
                fd = os.open(Path(cwd) / "project", os.O_RDONLY)
                for name in ["status.json.tmp"] + ["d"] * 300:
                    os.mkdir(name, dir_fd=fd)
                    child = os.open(name, os.O_RDONLY, dir_fd=fd)
                    os.close(fd)
                    fd = child
                os.close(fd)
                (Path(cwd) / "project/approvals.json").write_text("[]", encoding="utf-8")
                raise TimeoutError("codex 进程超过 30 分钟未完成")
            # A low recursion limit makes 300 levels "too deep" for the workbench while the temporary directory's own
            # cleanup, at the normal limit, can still remove them.
            limit = sys.getrecursionlimit()
            sys.setrecursionlimit(200)
            try:
                with mock.patch.object(workbench, "_run_agent", side_effect=plant_then_fail):
                    job = wait_for(workbench.start_generation(workspace, 4))
            finally:
                sys.setrecursionlimit(limit)
            self.assertEqual(job["status"], "error")
            self.assertIn("超过 30 分钟", job["error"])
            self.assertIn("层级过深", job["error"])
            self.assertEqual((workspace / "project/approvals.json").read_text(encoding="utf-8"), approvals)

    def test_a_directory_moved_during_removal_never_costs_another_directory_its_files(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root).resolve()
            (root / "trash/a/b/c").mkdir(parents=True)
            (root / "trash/a/b/c/junk").write_text("x", encoding="utf-8")
            (root / "victim").mkdir()
            (root / "victim/precious").write_text("keep", encoding="utf-8")
            real_open = os.open

            def move_after_opening_b(name, flags, *args, **kwargs):
                fd = real_open(name, flags, *args, **kwargs)
                if name == "b" and (root / "trash/a/b").exists():
                    os.rename(root / "trash/a/b", root / "victim/b")
                return fd
            with mock.patch.object(workbench.os, "open", side_effect=move_after_opening_b):
                try:
                    workbench._remove(root / "trash")
                except OSError:
                    pass  # the moved directory may make the removal fail; it must not reach other files
            self.assertEqual((root / "victim/precious").read_text(encoding="utf-8"), "keep")

    def test_restore_without_a_place_for_backups_changes_nothing(self):
        # Without a copy, overwriting would lose an edit the user made to a protected file meanwhile.
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root).resolve()
            (workspace / "project").mkdir()
            (workspace / "project/approvals.json").write_text("[]", encoding="utf-8")
            before = workbench._snapshot(workspace, ("project/approvals.json",))
            (workspace / "project/approvals.json").write_text("[edited]", encoding="utf-8")
            with mock.patch.object(workbench, "_scratch_dir", side_effect=PermissionError("read-only cache")):
                with self.assertRaises(RuntimeError) as caught:
                    workbench._restore(workspace, ("project/approvals.json",), before)
            self.assertIn("read-only cache", str(caught.exception))
            self.assertEqual((workspace / "project/approvals.json").read_text(encoding="utf-8"), "[edited]")

    def test_codex_writes_its_verdict_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            verdict = {"conclusion": "approved", "summary": "ok", "findings": []}
            job, calls = self.run_unit_job(workspace, "page-map", verdict=verdict)
            self.assertEqual(job["status"], "done", job)
            output = Path(calls[1][calls[1].index("-o") + 1])
            self.assertFalse(output.resolve().is_relative_to(workspace.resolve()))
            self.assertEqual(json.loads((workspace / workbench.unit_dir("page-map") / "review.json").read_text(encoding="utf-8"))["summary"], "ok")

    def test_review_is_given_the_preview_screenshots(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            shots = [workspace / workbench.unit_dir("page-map") / "screenshots/desktop-top.png"]
            verdict = {"conclusion": "approved", "summary": "ok", "findings": []}
            with mock.patch.object(workbench, "_unit_screenshots", return_value=shots):
                job, calls = self.run_unit_job(workspace, "page-map", verdict=verdict)
            self.assertEqual(job["status"], "done", job)
            review = calls[1]
            self.assertIn("--image=%s" % shots[0], review)
            self.assertLess(review.index("--image=%s" % shots[0]), len(review) - 1)
            self.assertIn("screenshots/desktop-top.png", review[-1])

    def test_review_without_a_browser_says_it_had_no_screenshots(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            verdict = {"conclusion": "approved", "summary": "ok", "findings": []}
            job, calls = self.run_unit_job(workspace, "page-map", verdict=verdict)
            self.assertFalse([part for part in calls[1] if part.startswith("--image")])
            self.assertIn("没有视觉截图", "\n".join(job["logs"]))
            self.assertIn("没有视觉截图", calls[1][-1])

    @unittest.skipUnless(REAL_CHROME(), "needs Chrome or Chromium")
    def test_screenshots_render_with_tokens_at_both_widths_and_scroll_positions(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            write_output(workspace, "page-map", "<html><head></head><body style='height:3000px;background:var(--color-primary)'>x</body></html>")
            unit = workbench._unit_record(json.loads((workspace / "src/ui/ir/manifest.json").read_text(encoding="utf-8")), "page-map")
            with mock.patch.object(workbench, "_chrome_binary", REAL_CHROME):
                shots = workbench._unit_screenshots(workspace, unit)
            self.assertEqual(sorted(shot.name for shot in shots),
                             ["desktop-bottom.png", "desktop-top.png", "mobile-bottom.png", "mobile-top.png"])
            for shot in shots:
                self.assertEqual(shot.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            # The token-injected copies are scaffolding and must not stay next to the output.
            self.assertFalse(list((workspace / workbench.unit_dir("page-map")).glob(".preview*")))

    @unittest.skipUnless(REAL_CHROME(), "needs Chrome or Chromium")
    def test_bottom_screenshot_shows_the_end_of_the_page(self):
        # The bottom shot must show the end of the page, not the top again or a blank frame.
        blue = "<div style='height:100vh;background:#0000ff'></div>"
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            manifest = json.loads((workspace / "src/ui/ir/manifest.json").read_text(encoding="utf-8"))
            style = "<meta name='viewport' content='width=device-width'><style>html{scroll-behavior:smooth}body{margin:0}</style>"
            write_output(workspace, "page-map", style + "<div style='height:800px;background:#ff0000'></div><div style='height:2400px'></div>" + blue)
            write_output(workspace, "layout", style + blue)
            with mock.patch.object(workbench, "_chrome_binary", REAL_CHROME):
                tall = {shot.name: shot.read_bytes() for shot in workbench._unit_screenshots(workspace, workbench._unit_record(manifest, "page-map"))}
                only_blue = {shot.name: shot.read_bytes() for shot in workbench._unit_screenshots(workspace, workbench._unit_record(manifest, "layout"))}
            for view in ("desktop", "mobile"):
                self.assertEqual(tall[view + "-bottom.png"], only_blue[view + "-top.png"], view)
                self.assertNotEqual(tall[view + "-top.png"], tall[view + "-bottom.png"], view)

    def test_the_review_conclusion_follows_the_severity_of_its_findings(self):
        # A reviewer once wrote changes-requested with only a P2 listed; the opposite would approve a unit with a P1.
        cases = (
            ({"conclusion": "approved", "summary": "ok", "findings": [{"severity": "P1", "location": "nav", "problem": "no focus"}]},
             "changes-requested"),
            ({"conclusion": "changes-requested", "summary": "minor", "findings": [{"severity": "P2", "location": "nav", "problem": "44px"}]},
             "approved"),
            # No finding gives a severity to judge by, so the reviewer's own label stands.
            ({"conclusion": "changes-requested", "summary": "focus is lost after closing the menu", "findings": []},
             "changes-requested"),
        )
        for verdict, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as root:
                workspace = self.phase_four(root)
                job, _ = self.run_unit_job(workspace, "page-map", verdict=verdict)
                self.assertEqual(job["status"], "done", job)
                self.assertEqual(self.unit_status(workspace, "page-map")["status"], expected)
                self.assertEqual(self.read(root, "project/approvals.json")[-1]["conclusion"], expected)
                record = self.read(root, "project/approvals.json")[-1]
                overridden = verdict["conclusion"] != expected
                self.assertEqual(any("按严重度记为" in item for item in record["evidence"]), overridden)

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

    def fake_phase_four(self, verdicts=None, fail_unit=None, fail_phase=False, on_generate=None):
        """Stand-in for every agent the Phase 4 run starts. verdicts maps a unit to the conclusions its reviews
        return in turn (approved once the list runs out); the phase job writes every Phase 4 file."""
        calls, prompts, verdicts = [], {}, {key: list(value) for key, value in (verdicts or {}).items()}
        self.prompts = prompts

        def run(cmd, cwd, timeout, output_path=None):
            match = re.search(r"UI unit「([a-z-]+)」", cmd[-1])
            if match is None:
                calls.append(("phase", None))
                if fail_phase:
                    raise RuntimeError("codex 退出码 1")
                for relative in workbench._phase_requirements(4):
                    target = Path(cwd) / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("{}" if relative.endswith(".json") else "assets", encoding="utf-8")
                status_path = Path(cwd) / "project/status.json"
                status = json.loads(status_path.read_text(encoding="utf-8"))
                status["state"] = "in-review"
                status_path.write_text(json.dumps(status), encoding="utf-8")
                return 0
            unit_id = match.group(1)
            if "read-only" in cmd:
                calls.append(("review", unit_id))
                queue = verdicts.get(unit_id) or []
                conclusion = queue.pop(0) if queue else "approved"
                if conclusion == "unreadable":
                    Path(cmd[cmd.index("-o") + 1]).write_text("not json", encoding="utf-8")
                    return 0
                findings = [] if conclusion == "approved" else [{"severity": "P1", "location": "nav", "problem": "focus lost"}]
                Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(
                    {"conclusion": conclusion, "summary": "%s round" % conclusion, "findings": findings}), encoding="utf-8")
                return 0
            calls.append(("generate", unit_id))
            prompts.setdefault(unit_id, []).append(cmd[-1])
            if on_generate:
                on_generate(unit_id)
            if unit_id == fail_unit:
                raise RuntimeError("codex 退出码 1")
            write_output(cwd, unit_id, "<main>%s</main>\n" % unit_id)
            return 0
        return run, calls

    def run_phase_four(self, workspace, check_passes=True, **fake):
        run, calls = self.fake_phase_four(**fake)
        check = subprocess.CompletedProcess([], 0, "0 finding(s)", "") if check_passes else \
            subprocess.CompletedProcess([], 1, "FAIL docs/licensing.md is empty", "")
        with mock.patch.object(workbench, "_run_agent", side_effect=run), \
                mock.patch.object(workbench.subprocess, "run", return_value=check):
            job = wait_for(workbench.start_phase_four_run(workspace))
        return job, calls

    def test_phase_four_run_builds_every_unit_in_order_then_the_assets(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            job, calls = self.run_phase_four(workspace)
            self.assertEqual(job["status"], "done", job)
            units = list(workbench.UI_UNIT_DEPENDENCIES)
            self.assertEqual(calls, [step for unit in units for step in (("generate", unit), ("review", unit))] + [("phase", None)])
            for unit in units:
                self.assertEqual(self.unit_status(workspace, unit)["status"], "approved")
            self.assertTrue(job["check"]["passed"])
            self.assertTrue((workspace / "review/04-assets.html").is_file())
            # G4 stays the user's: the run never records it.
            self.assertNotIn("G4", [item.get("gate") for item in self.read(root, "project/approvals.json")])

    def test_phase_four_run_regenerates_with_feedback_until_approved(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            rounds = workbench.MAX_UNIT_GENERATIONS - 1
            job, calls = self.run_phase_four(workspace, verdicts={"layout": ["changes-requested"] * rounds})
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(calls.count(("generate", "layout")), rounds + 1)
            # Each regeneration is given the findings that sent the unit back.
            self.assertNotIn("focus lost", self.prompts["layout"][0])
            self.assertIn("focus lost", self.prompts["layout"][1])
            self.assertEqual(self.unit_status(workspace, "platform-adaptation")["status"], "approved")

    def test_phase_four_run_stops_at_the_generation_limit(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            limit = workbench.MAX_UNIT_GENERATIONS
            job, calls = self.run_phase_four(workspace, verdicts={"layout": ["changes-requested"] * limit})
            self.assertEqual(job["status"], "error")
            self.assertIn("layout", job["error"])
            self.assertIn("changes-requested round", job["error"])
            self.assertEqual(calls.count(("generate", "layout")), limit)
            self.assertEqual(self.unit_status(workspace, "layout")["status"], "changes-requested")
            self.assertEqual(self.unit_status(workspace, "reuse-analysis")["status"], "not-started")
            self.assertNotIn(("phase", None), calls)
            self.assertIsNone(workbench._running_job(workspace))

    def test_phase_four_run_stops_at_a_unit_whose_generation_fails(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            job, calls = self.run_phase_four(workspace, fail_unit="layout")
            self.assertEqual(job["status"], "error")
            self.assertIn("codex 退出码 1", job["error"])
            self.assertEqual(calls[-1], ("generate", "layout"))
            self.assertIn("生成失败", self.unit_status(workspace, "layout")["note"])
            self.assertIsNone(workbench._running_job(workspace))

    def test_phase_four_run_resumes_without_redoing_finished_work(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.run_phase_four(workspace, verdicts={"page": ["changes-requested"] * workbench.MAX_UNIT_GENERATIONS})
            # A unit left waiting for its review is only reviewed again.
            write_output(workspace, "page")
            workbench.begin_unit_generation(workspace, "page")
            workbench.mark_unit_in_review(workspace, "page")
            job, calls = self.run_phase_four(workspace)
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(calls, [("review", "page"), ("generate", "platform-adaptation"), ("review", "platform-adaptation"), ("phase", None)])
            # Phase 4 assets already generated are not generated again.
            job, calls = self.run_phase_four(workspace)
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(calls, [])

    def test_phase_four_run_regenerates_assets_after_g4_is_withdrawn(self):
        for phase in (4, 5):
            with self.subTest(withdrawn_at_phase=phase), tempfile.TemporaryDirectory() as root:
                workspace = self.phase_four(root)
                self.run_phase_four(workspace)
                workbench.record_gate(workspace, "G4")
                self.at_phase(workspace, phase)
                workbench.record_gate(workspace, "G4", status="changes-requested")
                self.assertEqual(self.read(root, "project/status.json")["phase"], 4)
                job, calls = self.run_phase_four(workspace)
                self.assertEqual(job["status"], "done", job)
                self.assertEqual(calls, [("phase", None)])

    def test_withdrawing_a_gate_in_its_own_phase_returns_that_phase_to_draft(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.at_phase(self.init(root), 1)
            (workspace / "project/approvals.json").write_text(json.dumps([G1_APPROVED]), encoding="utf-8")
            status_path = workspace / "project/status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["state"] = "in-review"
            status_path.write_text(json.dumps(status), encoding="utf-8")
            workbench.record_gate(workspace, "G1", status="changes-requested")
            self.assertEqual({key: self.read(root, "project/status.json")[key] for key in ("phase", "state")},
                             {"phase": 1, "state": "draft"})

    def test_phase_four_run_regenerates_only_assets_the_checker_rejects(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            job, _ = self.run_phase_four(workspace, check_passes=False)
            self.assertEqual(job["status"], "done", job)
            self.assertFalse(job["check"]["passed"])
            # A finding the asset job cannot fix does not regenerate the assets on every resume.
            job, calls = self.run_phase_four(workspace, check_passes=False)
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(calls, [])
            # File findings the asset job may fix do: a missing deliverable, or any empty file it can write.
            for relative, broken in (("docs/licensing.md", None), ("docs/licensing.md", ""), ("exports/icon-512.png", "")):
                with self.subTest(relative=relative, broken=broken):
                    target = workspace / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.unlink(missing_ok=True) if broken is None else target.write_text(broken, encoding="utf-8")
                    job, calls = self.run_phase_four(workspace)
                    self.assertEqual(job["status"], "done", job)
                    self.assertEqual(calls, [("phase", None)])
                    target.write_text("x", encoding="utf-8")
                    job, calls = self.run_phase_four(workspace)
                    self.assertEqual(calls, [])
            # So does a project/status.json the asset job left invalid.
            status_path = workspace / "project/status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status.pop("completed", None)
            status_path.write_text(json.dumps(status), encoding="utf-8")
            job, calls = self.run_phase_four(workspace)
            self.assertEqual(calls, [("phase", None)])
            status["completed"] = []
            status_path.write_text(json.dumps(status), encoding="utf-8")
            # An empty file inside a unit is not the asset job's: its changes there are rolled back.
            (workspace / workbench.unit_dir("page") / "extra.css").write_text("", encoding="utf-8")
            job, calls = self.run_phase_four(workspace)
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(calls, [])

    def test_phase_four_run_stops_when_the_assets_fail(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            job, calls = self.run_phase_four(workspace, fail_phase=True)
            self.assertEqual(job["status"], "error")
            self.assertIn("codex 退出码 1", job["error"])
            self.assertEqual(calls[-1], ("phase", None))
            self.assertEqual(self.unit_status(workspace, "platform-adaptation")["status"], "approved")
            self.assertIsNone(workbench._running_job(workspace))

    def test_phase_four_run_stops_when_a_review_gives_no_verdict(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            job, calls = self.run_phase_four(workspace, verdicts={"layout": ["unreadable"]})
            self.assertEqual(job["status"], "error")
            self.assertEqual(calls[-1], ("review", "layout"))
            self.assertEqual(self.unit_status(workspace, "layout")["status"], "in-review")
            self.assertIn("自动审查失败", self.unit_status(workspace, "layout")["note"])

    def test_phase_four_run_refuses_a_unit_ordered_before_its_dependency(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            manifest_path = workspace / "src/ui/ir/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["units"][0]["dependsOn"] = ["layout"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            workbench.sync_manifest_hash(workspace)
            job, calls = self.run_phase_four(workspace)
            self.assertEqual(job["status"], "error")
            self.assertIn("page-map", job["error"])
            self.assertEqual(calls, [])

    def test_phase_four_run_marks_the_unit_it_is_working_on(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            seen = {}

            def look(unit_id):
                if unit_id == "layout":
                    seen.update({unit["id"]: unit["running"] for unit in workbench.unit_overview(workspace)["units"]})
            job, _ = self.run_phase_four(workspace, on_generate=look)
            self.assertEqual(job["status"], "done", job)
            self.assertEqual([unit for unit, running in seen.items() if running], ["layout"])

    def test_phase_four_run_re_reviews_a_hand_edited_unit_without_regenerating(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.run_phase_four(workspace)
            write_output(workspace, "layout", "<main>fixed by hand</main>\n")
            job, calls = self.run_phase_four(workspace)
            self.assertEqual(job["status"], "done", job)
            # The edited unit and the approved units built on it are reviewed again; nothing is regenerated.
            downstream = ["layout", "reuse-analysis", "component", "page", "platform-adaptation"]
            self.assertEqual(calls, [("review", unit) for unit in downstream])
            self.assertEqual((workspace / workbench.unit_output_path("layout")).read_text(encoding="utf-8"), "<main>fixed by hand</main>\n")
            self.assertEqual(self.read(root, "src/ui/units/layout/metadata.json")["outputHash"], current_output_hash(workspace, "layout"))
            for unit in ["page-map"] + downstream:
                self.assertEqual(self.unit_status(workspace, unit)["status"], "approved")

    def test_re_review_redoes_a_dependent_that_lost_its_output(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.run_phase_four(workspace)
            (workspace / workbench.unit_output_path("component")).unlink()
            write_output(workspace, "layout", "<main>fixed by hand</main>\n")
            job, calls = self.run_phase_four(workspace)
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(calls[:3], [("review", "layout"), ("review", "reuse-analysis"), ("generate", "component")])
            self.assertEqual(self.unit_status(workspace, "platform-adaptation")["status"], "approved")

    def test_a_changed_manifest_regenerates_instead_of_re_reviewing(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.run_phase_four(workspace)
            manifest_path = workspace / "src/ui/ir/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["manifestVersion"] = manifest["manifestVersion"] + "-next"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            workbench.sync_manifest_hash(workspace)
            write_output(workspace, "page-map", "<main>edited for the old spec</main>\n")
            self.assertEqual(workbench.reopen_unit_review(workspace, "page-map"), [])
            self.assertFalse({u["id"]: u for u in workbench.unit_overview(workspace)["units"]}["page-map"]["reviewable"])
            job, calls = self.run_phase_four(workspace)
            self.assertEqual(calls[0], ("generate", "page-map"))

    def test_re_review_survives_a_damaged_metadata_file(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.run_phase_four(workspace)
            (workspace / workbench.unit_dir("component") / "metadata.json").write_text("{bad", encoding="utf-8")
            write_output(workspace, "layout", "<main>fixed by hand</main>\n")
            job, calls = self.run_phase_four(workspace)
            self.assertEqual(job["status"], "done", job)
            self.assertNotIn(("generate", "layout"), calls)
            self.assertEqual(self.read(root, "src/ui/units/component/metadata.json")["outputHash"], current_output_hash(workspace, "component"))

    def test_re_review_moves_nothing_when_a_metadata_path_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.run_phase_four(workspace)
            metadata = workspace / workbench.unit_dir("component") / "metadata.json"
            metadata.unlink()
            metadata.mkdir()
            (metadata / "keep").write_text("x", encoding="utf-8")
            write_output(workspace, "layout", "<main>fixed by hand</main>\n")
            with self.assertRaises(OSError):
                workbench.reopen_unit_review(workspace, "layout")
            for unit in workbench.UI_UNIT_DEPENDENCIES:
                self.assertEqual(self.unit_status(workspace, unit)["status"], "approved")

    def test_re_review_waits_for_an_upstream_the_reviewer_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.run_phase_four(workspace)
            write_output(workspace, "layout", "<main>broken by hand</main>\n")
            run, _ = self.fake_phase_four(verdicts={"layout": ["changes-requested"]})
            with mock.patch.object(workbench, "_run_agent", side_effect=run), \
                    mock.patch.object(workbench.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
                wait_for(workbench.start_review_job(workspace, 4, "layout"))
            self.assertEqual(self.unit_status(workspace, "reuse-analysis")["status"], "in-review")
            self.assertFalse({u["id"]: u for u in workbench.unit_overview(workspace)["units"]}["reuse-analysis"]["canReview"])
            with self.assertRaisesRegex(ValueError, "layout"):
                workbench.start_review_job(workspace, 4, "reuse-analysis")

    def test_a_hand_edited_unit_the_reviewer_rejects_is_regenerated(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.run_phase_four(workspace)
            write_output(workspace, "layout", "<main>broken by hand</main>\n")
            job, calls = self.run_phase_four(workspace, verdicts={"layout": ["changes-requested"]})
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(calls[:3], [("review", "layout"), ("generate", "layout"), ("review", "layout")])
            # Regenerating the upstream sends the units built on it back to be redone.
            self.assertIn(("generate", "platform-adaptation"), calls)

    def test_only_re_review_reopens_an_approved_unit_whose_output_changed(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.review(workspace, "page-map")
            self.review(workspace, "layout")
            with self.assertRaisesRegex(ValueError, "重新审查"):
                workbench.start_review_job(workspace, 4, "page-map")
            self.assertFalse({u["id"]: u for u in workbench.unit_overview(workspace)["units"]}["page-map"]["canReview"])
            write_output(workspace, "page-map", "<main>edited</main>\n")
            self.assertTrue({u["id"]: u for u in workbench.unit_overview(workspace)["units"]}["page-map"]["canReview"])
            run, calls = self.fake_phase_four()
            with mock.patch.object(workbench, "_run_agent", side_effect=run), \
                    mock.patch.object(workbench.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
                job = wait_for(workbench.start_review_job(workspace, 4, "page-map"))
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(calls, [("review", "page-map")])
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "approved")
            # The approved unit built on it waits for its own review again.
            self.assertEqual(self.unit_status(workspace, "layout")["status"], "in-review")

    def test_phase_four_run_needs_the_unit_preconditions(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.at_phase(self.init(root), 4)
            with self.assertRaisesRegex(ValueError, "G1"):
                workbench.start_phase_four_run(workspace)
            self.assertIsNone(workbench._running_job(workspace))

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
            # The state stays in-progress, but nothing runs: the page must not say it is generating.
            self.assertFalse(workbench.unit_overview(workspace)["units"][0]["running"])

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
            with mock.patch.object(workbench, "_run_agent", side_effect=RuntimeError("codex 退出码 1")), \
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
            with mock.patch.object(workbench, "_run_agent", side_effect=RuntimeError("codex 退出码 1")):
                job = wait_for(workbench.start_unit_job(workspace, 4, "page-map"))
            self.assertIn("no focus-visible style", job["prompt"])
            self.assertIn("Navigation misses focus state", job["prompt"])

    def test_review_after_changes_requested_is_a_re_review(self):
        # A full re-audit of a large unit never runs out of pre-existing issues; a re-review checks what was asked.
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            unit = workbench._unit_record(json.loads((workspace / "src/ui/ir/manifest.json").read_text(encoding="utf-8")), "page-map")
            self.assertNotIn("复审", workbench._review_prompt(workspace, unit))
            self.review(workspace, "page-map", "changes-requested")
            prompt = workbench._review_prompt(workspace, unit)
            self.assertIn("复审", prompt)
            self.assertIn("review-evidence.json", prompt)

    def test_prompts_carry_review_findings_not_the_deleted_verdict_file(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            write_output(workspace, "page-map")
            workbench.begin_unit_generation(workspace, "page-map")
            workbench.mark_unit_in_review(workspace, "page-map")
            workbench.append_unit_review(
                workspace, "page-map", "changes-requested", reviewer=REVIEWER,
                evidence=["src/ui/units/page-map/review.json", "summary", "P1 nav：no focus style"],
                file_scope=[{"path": workbench.unit_output_path("page-map"), "startLine": 1, "endLine": 1}],
                output_hash=current_output_hash(workspace, "page-map"),
            )
            unit = workbench._unit_record(json.loads((workspace / "src/ui/ir/manifest.json").read_text(encoding="utf-8")), "page-map")
            for prompt in (workbench._unit_feedback(workspace, "page-map"), workbench._review_prompt(workspace, unit)):
                self.assertIn("no focus style", prompt)
                self.assertNotIn("page-map/review.json", prompt)

    def test_regeneration_prompt_keeps_every_round_since_the_last_approval(self):
        # Fixes asked for in earlier rounds must stay in front of the generator, or the next round undoes them.
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            self.review(workspace, "page-map")
            for problem in ("old approved-era issue", "focus ring too faint", "skip link missing"):
                write_output(workspace, "page-map", "<main>%s</main>\n" % problem)
                workbench.begin_unit_generation(workspace, "page-map")
                workbench.mark_unit_in_review(workspace, "page-map")
                workbench.append_unit_review(
                    workspace, "page-map", "approved" if problem.startswith("old") else "changes-requested",
                    reviewer=REVIEWER, evidence=["review.json", "P1 " + problem],
                    file_scope=[{"path": workbench.unit_output_path("page-map"), "startLine": 1, "endLine": 1}],
                    output_hash=current_output_hash(workspace, "page-map"),
                )
            feedback = workbench._unit_feedback(workspace, "page-map")
            self.assertIn("focus ring too faint", feedback)
            self.assertIn("skip link missing", feedback)
            self.assertNotIn("old approved-era issue", feedback)

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
                # Touching a protected file as well must not skip the status restore.
                (Path(cwd) / "project/approvals.json").write_text("[]", encoding="utf-8")
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
            workspace = Path(root).resolve()
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
            # An approved unit whose output changed after its review is the current unit again.
            write_output(workspace, "page-map", "<main>edited by hand</main>\n")
            self.assertEqual(workbench.unit_overview(workspace)["currentUnit"], "page-map")


class UiWorkbenchRobustnessTest(unittest.TestCase):
    def test_agent_timeout_holds_while_a_partial_line_is_pending(self):
        script = "import sys, time; sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(30)"
        with tempfile.TemporaryDirectory() as cwd:
            started = time.time()
            with self.assertRaises(TimeoutError):
                workbench._run_agent([sys.executable, "-c", script], cwd, 1)
        self.assertLess(time.time() - started, 10)

    def test_failed_agent_reports_what_it_said(self):
        # The exit code alone cannot tell a usage limit from a crash; the agent's own words can.
        events = [{"type": "turn.started"}, {"type": "error", "message": "You've hit your usage limit."},
                  {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}]
        script = "import json; [print(json.dumps(e)) for e in %r]; raise SystemExit(1)" % events
        with tempfile.TemporaryDirectory() as cwd:
            with self.assertRaises(RuntimeError) as caught:
                workbench._run_agent([sys.executable, "-c", script], cwd, 10)
            self.assertIn("usage limit", str(caught.exception))
            with self.assertRaises(RuntimeError) as caught:
                workbench._run_agent([sys.executable, "-c", "print('boom: network down'); raise SystemExit(2)"], cwd, 10)
            self.assertIn("boom: network down", str(caught.exception))

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
            workspace = Path(root).resolve()
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
            workspace = Path(root).resolve()
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
            workspace = Path(root).resolve()
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
            workspace = Path(root).resolve()
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
            workspace = Path(root).resolve()
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
            workspace = Path(root).resolve()
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

    def test_page_builds_every_preview_link_with_the_path_form(self):
        self.assertNotIn("/preview?", self.html)
        self.assertNotIn("/-/", self.html)
        page = workbench.page_html()
        self.assertIn('"previewPrefix": "%s"' % workbench.PREVIEW_PREFIX, page)
        self.assertIn('"previewSeparator": "%s"' % workbench.PREVIEW_SEPARATOR, page)

    def test_page_gets_its_choices_from_the_checker(self):
        page = workbench.page_html()
        self.assertNotIn("__", re.sub(r"__proto__", "", "".join(re.findall(r"const UI=.*?;", page))))
        for value in check_workspace.STACK_PROFILES + check_workspace.UI_PLATFORMS:
            self.assertNotIn('<option value="%s"' % value, self.html)
        # The unit panel sits in the branch of the phase that owns UI units.
        self.assertIn('"maxUnitGenerations": %d' % workbench.MAX_UNIT_GENERATIONS, page)
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
            url = self.base + workbench.preview_url(self.workspace, "review/" + name)
            with self.urllib.request.urlopen(url) as response:
                self.assertEqual(response.headers["Content-Type"], content_type)
                self.assertIn("sandbox", response.headers["Content-Security-Policy"])
                self.assertNotIn("allow-same-origin", response.headers["Content-Security-Policy"])

    def test_relative_links_in_a_preview_open_files_of_the_same_workspace(self):
        (self.workspace / "review").mkdir(exist_ok=True)
        (self.workspace / "reports").mkdir(exist_ok=True)
        (self.workspace / "review/05-release.html").write_text('<a href="../reports/qa report.md">QA</a>', encoding="utf-8")
        (self.workspace / "reports/qa report.md").write_text("# QA\n", encoding="utf-8")
        page = self.base + workbench.preview_url(self.workspace, "review/05-release.html")
        # The browser resolves the page's relative link against the preview path.
        link = self.urllib.parse.urljoin(page, self.urllib.parse.quote("../reports/qa report.md"))
        with self.urllib.request.urlopen(link) as response:
            self.assertEqual(response.read().decode("utf-8"), "# QA\n")
            self.assertIn("sandbox", response.headers["Content-Security-Policy"])

    def test_preview_redirect_cannot_inject_headers(self):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1])
        path = "/preview?path=%s&file=a%%0D%%0ASet-Cookie:%%20x=1" % self.urllib.parse.quote(str(self.workspace))
        connection.request("GET", path)
        response = connection.getresponse()
        self.assertEqual(response.status, 302)
        self.assertIsNone(response.getheader("Set-Cookie"))
        self.assertTrue(response.getheader("Location").startswith(workbench.PREVIEW_PREFIX))
        connection.close()

    def test_old_preview_addresses_redirect_to_the_path_form(self):
        (self.workspace / "review").mkdir(exist_ok=True)
        (self.workspace / "review/page.html").write_text("<main>page</main>", encoding="utf-8")
        old = "%s/preview?path=%s&file=review/page.html" % (self.base, self.urllib.parse.quote(str(self.workspace)))
        with self.urllib.request.urlopen(old) as response:
            self.assertEqual(response.url, self.base + workbench.preview_url(self.workspace, "review/page.html"))
            self.assertEqual(response.read().decode("utf-8"), "<main>page</main>")

    def test_preview_paths_cannot_leave_the_workspace(self):
        (self.root / "secret.txt").write_text("secret", encoding="utf-8")
        encoded = self.urllib.parse.quote(str(self.workspace), safe="")
        (self.workspace / "link.txt").symlink_to(self.root / "secret.txt")
        outside = self.urllib.parse.quote(str(self.root.parent), safe="")
        for prefix, tail in (
            (encoded, "../secret.txt"), (encoded, "%2E%2E/secret.txt"), (encoded, "review/%2E%2E/%2E%2E/secret.txt"),
            (encoded, "link.txt"), (encoded, "secret.txt%00.md"), (outside, self.root.name + "/secret.txt"),
        ):
            with self.subTest(tail=tail):
                request = self.urllib.request.Request("%s/preview/%s/-/%s" % (self.base, prefix, tail))
                with self.assertRaises(self.urllib.error.HTTPError) as caught:
                    self.urllib.request.urlopen(request)
                caught.exception.close()
                self.assertEqual(caught.exception.code, 400)

    def write_tokens(self):
        tokens = self.workspace / "tokens/src"
        (tokens / "semantic").mkdir(parents=True, exist_ok=True)
        (tokens / "color.json").write_text(json.dumps({
            "$schema": "x", "name": "meta",
            "color": {"mist": {"value": "#F8FBFA"}, "bad": {"value": "red;}</style><script>"}},
            "space": {"4": {"$value": "16px"}, "bad key": {"value": "1px"}},
        }), encoding="utf-8")
        (tokens / "semantic/color.json").write_text(json.dumps(
            {"color": {"light": {"canvas": {"$value": "{color.mist}"}, "ring": {"$value": "0 0 0 2px {color.mist}"}}}}
        ), encoding="utf-8")

    def test_token_css_flattens_both_value_spellings_and_aliases(self):
        self.write_tokens()
        css = workbench.token_css(self.workspace)
        for line in ("--color-mist:#F8FBFA;", "--space-4:16px;", "--color-light-canvas:var(--color-mist);",
                     "--color-light-ring:0 0 0 2px var(--color-mist);"):
            self.assertIn(line, css)
        # A value or name that could close the rule or the style element is dropped, not escaped.
        self.assertNotIn("bad", css)
        self.assertNotIn("<", css)

    def test_token_css_drops_values_that_would_swallow_later_declarations(self):
        self.write_tokens()
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "secret.json").write_text(json.dumps({"leak": {"value": "1px"}}), encoding="utf-8")
            (self.workspace / "tokens/src/linked.json").symlink_to(Path(outside) / "secret.json")
            (self.workspace / "tokens/src/odd.json").write_text(json.dumps({
                "font": {"open": {"value": '"Manrope'}, "comment": {"value": "a /* b"}, "quoted": {"value": '"SF Pro", sans-serif'}},
            }), encoding="utf-8")
            css = workbench.token_css(self.workspace)
            rule = workbench._token_rule(self.workspace)
        self.assertIn('--font-quoted:"SF Pro", sans-serif;', css)
        for dropped in ("--font-open", "--font-comment", "--leak"):
            self.assertNotIn(dropped, css)
        self.assertIn("--font-open", rule)
        self.assertIn("tokens/src/linked.json", rule)

    def test_token_values_must_close_every_string_and_bracket(self):
        accepted = ['"Kid\'s Font", serif', '"\\201C"', '"a;b"', "calc(1px + var(--space-4))", "#fff"]
        rejected = ['"\'" \'', "calc(1px", "url(x", "a /* b", "red;}", "</style>", "a\nb", "x\\"]
        for value in accepted:
            self.assertTrue(workbench._css_value_safe(value), value)
        for value in rejected:
            self.assertFalse(workbench._css_value_safe(value), value)

    def test_token_insertion_is_linear_on_unclosed_tags(self):
        # Quadratic scanning took over a minute on this input; a generous bound keeps slow runners green.
        for junk in ("<script ", "<!--", "<head ", "<title>"):
            started = time.time()
            workbench._with_tokens(junk * 40000, ":root{}")
            self.assertLess(time.time() - started, 5, junk)

    def test_token_insertion_offsets_survive_case_and_close_tag_lookalikes(self):
        css = ":root{}"
        self.assertEqual(workbench._with_tokens("İİİ<head><title>t</title>", css), "İİİ<head><style data-brand-tokens>:root{}</style><title>t</title>")
        for lookalike in ("</scripts>", "</script\u00a0>", "</script\x0b>"):
            html = "<script>var s='%s'; var t='<head>';</script><head>" % lookalike
            self.assertTrue(workbench._with_tokens(html, css).endswith("<head><style data-brand-tokens>:root{}</style>"), lookalike)
        html = "<script>var s='</scripts>'; var t='<head>';</script><head>"
        self.assertTrue(workbench._with_tokens(html, css).endswith("<head><style data-brand-tokens>:root{}</style>"))

    def test_tokens_skip_a_head_tag_inside_raw_text_elements(self):
        for html in ("<script>var a='<head>'", "<title><head>", "<textarea><head x>", "<!-- <head>", "<style>a{}<head>"):
            self.assertTrue(workbench._with_tokens(html, ":root{}").startswith("<style data-brand-tokens>"), html)

    def test_tokens_go_into_head_not_a_header_or_before_the_doctype(self):
        css = ":root{}"
        self.assertEqual(workbench._with_tokens("<!doctype html><header>h</header>", css),
                         "<!doctype html><style data-brand-tokens>:root{}</style><header>h</header>")
        self.assertEqual(workbench._with_tokens("<html><head lang='x'><header>", css),
                         "<html><head lang='x'><style data-brand-tokens>:root{}</style><header>")

    def test_unit_prompts_list_the_injected_variable_names(self):
        # Variable names keep the JSON keys' case, which a generator would otherwise guess wrong.
        self.write_tokens()
        unit = {"id": "page-map", "kind": "page-map", "files": ["src/ui/units/page-map/output.html"]}
        for prompt in (workbench._unit_prompt(self.workspace, unit, "A"), workbench._review_prompt(self.workspace, unit)):
            self.assertIn("--color-light-canvas", prompt)
            self.assertIn("--space-4", prompt)

    def test_preview_injects_tokens_into_unit_pages_only(self):
        self.write_tokens()
        for relative in ("src/ui/units/page-map/output.html", "review/page.html"):
            (self.workspace / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.workspace / relative).write_text("<html><head><title>t</title></head><body>x</body></html>", encoding="utf-8")
        def fetch(relative):
            url = self.base + workbench.preview_url(self.workspace, relative)
            with self.urllib.request.urlopen(url) as response:
                return response.read().decode("utf-8")
        unit = fetch("src/ui/units/page-map/output.html")
        self.assertIn("<head><style data-brand-tokens>:root{", unit)
        self.assertIn("--color-mist:#F8FBFA;", unit)
        self.assertNotIn("data-brand-tokens", fetch("review/page.html"))

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

    def test_withdrawing_g3_from_phase_four_returns_to_phase_three(self):
        self.set_phase(4)
        (self.workspace / "project/approvals.json").write_text(json.dumps([gate("G1"), gate("G2"), gate("G3")]), encoding="utf-8")
        code, payload = self.call("/api/approve", {"path": str(self.workspace), "gate": "G3", "status": "changes-requested"})
        self.assertEqual((code, payload["withdrawn"]), (200, ["G3"]))
        status = json.loads((self.workspace / "project/status.json").read_text(encoding="utf-8"))
        self.assertEqual((status["phase"], status["state"]), (3, "draft"))

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

    def test_check_reports_a_job_running_in_the_workspace(self):
        passed = subprocess.CompletedProcess([], 0, "0 finding(s)", "")
        with mock.patch.object(workbench, "_run_checker", return_value=passed):
            code, payload = self.call("/api/check?path=" + str(self.workspace))
            self.assertEqual((code, payload["activeJob"]), (200, None))
            job_id = workbench._create_job(self.workspace, "elsewhere")
            try:
                code, payload = self.call("/api/check?path=" + str(self.workspace))
            finally:
                workbench._finish_job(job_id, "done")
        self.assertEqual(payload["activeJob"]["id"], job_id)

    def test_phase_four_generate_without_a_unit_starts_the_whole_run(self):
        with mock.patch.object(workbench, "start_phase_four_run", return_value="run-1") as run, \
                mock.patch.object(workbench, "start_generation", return_value="phase-1") as phase:
            code, payload = self.call("/api/generate", {"path": str(self.workspace), "phase": 4})
            self.assertEqual((code, payload), (200, {"job": "run-1"}))
            code, payload = self.call("/api/generate", {"path": str(self.workspace), "phase": 3})
            self.assertEqual((code, payload), (200, {"job": "phase-1"}))
        run.assert_called_once_with(self.workspace)
        phase.assert_called_once_with(self.workspace, 3)

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
