import json
import re
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

    def phase_four(self, root):
        workspace = self.init(root)
        status_path = workspace / "project/status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["phase"] = 4
        status_path.write_text(json.dumps(status), encoding="utf-8")
        (workspace / "project/approvals.json").write_text(
            json.dumps([gate("G1"), gate("G2"), gate("G3")]), encoding="utf-8"
        )
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
                ["page-map", "layout", "component", "page", "platform-adaptation"],
            )
            self.assertEqual([unit["status"] for unit in manifest["units"]], ["not-started"] * 5)
            self.assertEqual([unit["status"] for unit in status["units"]], ["not-started"] * 5)
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

    def test_phase_one_generation_does_not_require_g1(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            with mock.patch.object(workbench, "_run_codex", return_value=1):
                job_id = workbench.start_generation(workspace, 1)
                wait_for(job_id)
            self.assertNotIn("Direction B", workbench.JOBS[job_id]["prompt"])

    def test_later_generation_requires_latest_valid_g1_and_uses_its_direction(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            with self.assertRaisesRegex(ValueError, "G1"):
                workbench.start_generation(workspace, 2)
            (workspace / "project/approvals.json").write_text(json.dumps([G1_APPROVED]), encoding="utf-8")
            with mock.patch.object(workbench, "_run_codex", return_value=1):
                job_id = workbench.start_generation(workspace, 2)
                wait_for(job_id)
            self.assertIn("Direction C", workbench.JOBS[job_id]["prompt"])
            self.assertNotIn("Direction B", workbench.JOBS[job_id]["prompt"])

    def test_sequential_progression_keeps_earlier_reviews_bound_to_the_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            first = self.review(workspace, "page-map")
            self.review(workspace, "layout")
            self.assertEqual(first["manifestHash"], self.read(root, "src/ui/ir/manifest.json")["hash"])
            workbench.begin_unit_generation(workspace, "component")

    def test_completed_dependency_unblocks_downstream_generation(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            self.review(workspace, "page-map")
            workbench.set_unit_status(workspace, "page-map", "completed")
            workbench.begin_unit_generation(workspace, "layout")

    def test_generation_follows_manifest_depends_on(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            manifest = self.read(root, "src/ui/ir/manifest.json")
            self.assertEqual(manifest["units"][1]["dependsOn"], ["page-map"])
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

    def fake_codex(self, generated="<main>generated</main>\n", verdict=None, stray=None):
        calls = []

        def run(cmd, cwd, timeout):
            calls.append(cmd)
            if "read-only" in cmd:
                if verdict is not None:
                    target = Path(cmd[cmd.index("-o") + 1])
                    target.write_text(verdict if isinstance(verdict, str) else json.dumps(verdict), encoding="utf-8")
                return 0
            unit_id = re.search(r"src/ui/units/([a-z-]+)/output\.html", cmd[-1]).group(1)
            write_output(cwd, unit_id, generated)
            if stray:
                (Path(cwd) / stray[0]).write_text(stray[1], encoding="utf-8")
            return 0
        return run, calls

    def run_unit_job(self, workspace, unit_id, **fake):
        run, calls = self.fake_codex(**fake)
        failed_check = subprocess.CompletedProcess([], 1, "unit layout requires unit-review", "")
        with mock.patch.object(workbench, "_run_codex", side_effect=run), \
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
            self.assertEqual(record["reviewer"], {"type": "subagent", "name": "codex-unit-reviewer"})
            self.assertEqual(record["outputHash"], current_output_hash(workspace, "page-map"))
            self.assertIn("Layout tokens used correctly", record["evidence"])

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
            with mock.patch.object(workbench, "_run_codex", side_effect=run), \
                    mock.patch.object(workbench.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
                job = wait_for(workbench.start_review_job(workspace, 4, "page-map"))
            self.assertEqual(job["status"], "done", job)
            self.assertEqual(self.unit_status(workspace, "page-map")["status"], "changes-requested")

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
            with mock.patch.object(workbench, "_run_codex", side_effect=stray_then_timeout):
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
            with mock.patch.object(workbench, "_run_codex", return_value=1), \
                    mock.patch.object(workbench, "set_unit_status", side_effect=failing_note):
                job = wait_for(workbench.start_unit_job(workspace, 4, "page-map"))
            self.assertEqual(job["status"], "error")
            self.assertIsNone(workbench._running_job(workspace))

            with mock.patch.object(workbench, "_protected_snapshot", side_effect=OSError("unreadable")):
                with self.assertRaises(OSError):
                    workbench.start_unit_job(workspace, 4, "page-map")
            self.assertIsNone(workbench._running_job(workspace))

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
            with mock.patch.object(workbench, "_run_codex", side_effect=slow):
                job_id = workbench.start_unit_job(workspace, 4, "page-map")
                with self.assertRaisesRegex(ValueError, "任务"):
                    workbench.start_generation(workspace, 4)
                release.set()
                wait_for(job_id)

    def test_failed_phase_check_is_reported_without_failing_the_generation_job(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.init(root)
            failed = subprocess.CompletedProcess([], 1, "missing review/01-directions.html", "")
            with mock.patch.object(workbench, "_run_codex", return_value=0), \
                    mock.patch.object(workbench.subprocess, "run", return_value=failed):
                job = wait_for(workbench.start_generation(workspace, 1))
            self.assertEqual(job["status"], "done")
            self.assertFalse(job["check"]["passed"])
            self.assertIn("missing review/01-directions.html", job["check"]["output"])

    def test_unit_overview_reports_blockers_and_actions(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = self.phase_four(root)
            units = {item["id"]: item for item in workbench.unit_overview(workspace)}
            self.assertTrue(units["page-map"]["canGenerate"])
            self.assertFalse(units["layout"]["canGenerate"])
            self.assertEqual(units["layout"]["blockedBy"], ["page-map"])
            self.review(workspace, "page-map")
            units = {item["id"]: item for item in workbench.unit_overview(workspace)}
            self.assertTrue(units["layout"]["canGenerate"])
            self.assertEqual(units["page-map"]["review"]["conclusion"], "approved")


class UiWorkbenchScriptTest(unittest.TestCase):
    def test_workbench_starts_as_a_script_from_another_directory(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, str(ROOT / "scripts/workbench.py"), "--help"], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)


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

    def call(self, endpoint, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = self.urllib.request.Request(self.base + endpoint, data=data, headers={"Content-Type": "application/json"})
        try:
            with self.urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except self.urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_state_exposes_unit_overview(self):
        code, payload = self.call("/api/state?path=" + str(self.workspace))
        self.assertEqual(code, 200)
        self.assertEqual([item["id"] for item in payload["uiUnits"]][0], "page-map")
        self.assertFalse(payload["uiUnits"][0]["canGenerate"])

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
