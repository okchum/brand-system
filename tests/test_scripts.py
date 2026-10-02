import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_prompt  # noqa: E402
import check_workspace  # noqa: E402
import contrast  # noqa: E402
import icon_verify  # noqa: E402
import svg_lint  # noqa: E402


def png_bytes(w, h):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00" * (w * 4) for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def ico_bytes(sizes):
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries, images = b"", b""
    offset = 6 + 16 * len(sizes)
    for s in sizes:
        img = png_bytes(s, s)
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset)
        images += img
        offset += len(img)
    return header + entries + images


class ContrastTest(unittest.TestCase):
    def test_black_on_white_is_21(self):
        self.assertAlmostEqual(contrast.ratio("#000000", "#ffffff"), 21.0, places=2)

    def test_threshold_depends_on_size(self):
        r = contrast.ratio("#777777", "#ffffff")
        self.assertLess(r, 4.5)
        self.assertFalse(contrast.passes(r, "normal"))
        self.assertTrue(contrast.passes(r, "large"))

    def test_alpha_foreground_is_composited_over_background(self):
        self.assertAlmostEqual(
            contrast.ratio("#00000080", "#ffffff"), contrast.ratio("#7f7f7f", "#ffffff"), places=1
        )

    def test_bad_input_exits_2_not_1(self):
        with tempfile.TemporaryDirectory() as d:
            empty = Path(d) / "pairs.json"
            empty.write_text("[]")
            for argv in (["#fff", "#000", "--size", "huge"], ["#ggg", "#000"], ["#fff", "#000", "--size"],
                         ["--matrix", str(empty)], ["--matrix", str(Path(d) / "missing.json")]):
                with self.subTest(argv):
                    self.assertEqual(contrast.main(argv), 2)

    def test_matrix_reports_failures(self):
        rows = contrast.check_matrix([
            {"fg": "#000000", "bg": "#ffffff", "usage": "body", "size": "normal"},
            {"fg": "#777777", "bg": "#ffffff", "usage": "body", "size": "normal"},
        ])
        self.assertEqual([r["pass"] for r in rows], [True, False])


class SvgLintTest(unittest.TestCase):
    def lint(self, body, attrs='viewBox="0 0 24 24"'):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.svg"
            p.write_text('<svg xmlns="http://www.w3.org/2000/svg" %s>%s</svg>' % (attrs, body))
            return svg_lint.lint_file(p)

    def test_clean_svg_passes(self):
        self.assertEqual(self.lint('<path id="a" d="M0 0h24v24z"/><use href="#a"/>'), [])

    def test_each_violation_is_reported(self):
        cases = {
            "script": "<script>alert(1)</script>",
            "event": '<path onload="x()" d="M0 0"/>',
            "raster": '<image href="data:image/png;base64,AAAA"/>',
            "external": '<use href="https://example.com/a.svg#b"/>',
            "foreignObject": "<foreignObject/>",
            "duplicate id": '<g id="a"/><g id="a"/>',
            "css url": '<style>.a{fill:url(https://example.com/x)}</style>',
            "presentation url": '<rect fill="url(https://example.com/x.svg#g)"/>',
            "relative url": '<rect filter="url(ext.svg#f)"/>',
            "animated href": '<a><set attributeName="href" to="javascript:alert(1)"/></a>',
            "animated xlink": '<animate attributeName="xlink:href" values="https://example.com"/>',
            "css escape": '<style>@\\69mport "http://x/a.css";</style>',
            "image-set": '<rect style="fill:image-set(\'http://x/a.png\' 1x)"/>',
            "xml:base": '<g xml:base="http://example.com/"/>',
            "src": '<font-face-uri src="http://example.com/f.woff"/>',
        }
        for name, body in cases.items():
            with self.subTest(name):
                self.assertNotEqual(self.lint(body), [])

    def test_quoted_local_url_passes(self):
        defs = '<linearGradient id="g"/>'
        self.assertEqual(self.lint(defs + '<style>.a{fill:url("#g")}</style><rect style="fill:url( #g)"/>'), [])

    def test_doctype_is_rejected_in_any_encoding(self):
        doc = '<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg viewBox="0 0 1 1">&a;</svg>'
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding), tempfile.TemporaryDirectory() as d:
                p = Path(d) / "x.svg"
                p.write_bytes(doc.encode(encoding))
                self.assertNotEqual(svg_lint.lint_file(p), [])

    def test_doctype_word_in_comment_is_fine(self):
        self.assertEqual(self.lint("<!-- no <!DOCTYPE here -->"), [])

    def test_directory_mode_includes_uppercase_extension(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "sub").mkdir()
            (Path(d) / "sub" / "A.SVG").write_text("<svg/>")
            self.assertEqual([p.name for p in svg_lint.iter_svgs([d])], ["A.SVG"])

    def test_missing_viewbox_is_reported(self):
        self.assertNotEqual(self.lint("<path d='M0 0'/>", attrs=""), [])

    def test_invalid_xml_is_reported(self):
        self.assertNotEqual(self.lint("<path>"), [])


class IconVerifyTest(unittest.TestCase):
    def verify(self, name, data):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / name
            p.write_bytes(data)
            return icon_verify.verify(p)

    def test_png_reports_dimensions(self):
        info = self.verify("a.png", png_bytes(32, 16))
        self.assertEqual((info["format"], info["sizes"], info["errors"]), ("png", [(32, 16)], []))

    def test_png_renamed_to_ico_fails(self):
        self.assertNotEqual(self.verify("favicon.ico", png_bytes(32, 32))["errors"], [])

    def test_real_ico_lists_all_sizes(self):
        info = self.verify("favicon.ico", ico_bytes([16, 32, 256]))
        self.assertEqual(info["errors"], [])
        self.assertEqual(info["sizes"], [(16, 16), (32, 32), (256, 256)])

    def test_ico_without_images_fails(self):
        self.assertNotEqual(self.verify("favicon.ico", ico_bytes([]))["errors"], [])

    def test_truncated_or_empty_png_fails(self):
        for name, data in (("cut header", png_bytes(8, 8)[:20]), ("cut body", png_bytes(8, 8)[:40]),
                           ("zero size", png_bytes(0, 0))):
            with self.subTest(name):
                self.assertNotEqual(self.verify("a.png", data)["errors"], [])

    def test_ico_entry_must_point_at_real_image_data(self):
        data = bytearray(ico_bytes([16]))
        data[6 + 8:6 + 16] = struct.pack("<II", 0, 0)  # size 0, offset 0
        self.assertNotEqual(self.verify("favicon.ico", bytes(data))["errors"], [])

    def test_empty_icns_element_fails(self):
        elem = b"ic10" + struct.pack(">I", 8)
        self.assertNotEqual(self.verify("a.icns", b"icns" + struct.pack(">I", 16) + elem)["errors"], [])

    def test_icns_reports_sizes_of_known_elements(self):
        elem = b"ic07" + struct.pack(">I", 9) + b"x" + b"is32" + struct.pack(">I", 9) + b"x"
        info = self.verify("a.icns", b"icns" + struct.pack(">I", 8 + len(elem)) + elem)
        self.assertEqual((info["errors"], info["sizes"]), ([], [(128, 128), (16, 16)]))

    def test_icns_length_must_match_file(self):
        elem = b"ic07" + struct.pack(">I", 9) + b"x"
        good = b"icns" + struct.pack(">I", 17) + elem
        self.assertEqual(self.verify("a.icns", good)["errors"], [])
        self.assertNotEqual(self.verify("a.icns", b"icns" + struct.pack(">I", 8))["errors"], [])
        self.assertNotEqual(self.verify("a.icns", b"icns" + struct.pack(">I", 99))["errors"], [])


class CheckWorkspaceTest(unittest.TestCase):
    def make(self, d, phase=0, approvals=None, status=None):
        d = Path(d)
        for rel in sum((check_workspace.REQUIRED[p] for p in range(phase + 1)), []):
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text("x")
        (d / "brand.brief.json").write_text("{}")
        (d / "project/status.json").write_text(json.dumps(status or {"phase": phase, "blockers": []}))
        (d / "project/approvals.json").write_text(json.dumps(approvals or []))
        return d

    def test_complete_phase0_passes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(check_workspace.check(self.make(d), 0), [])

    def test_missing_review_page_fails_phase1(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertNotEqual(check_workspace.check(self.make(d, phase=0), 1), [])

    def test_approval_without_version_fails(self):
        with tempfile.TemporaryDirectory() as d:
            ws = self.make(d, approvals=[{"gate": "G1", "status": "approved", "confirmation": "选 B"}])
            self.assertNotEqual(check_workspace.check(ws, 0), [])

    APPROVED_G1 = {"gate": "G1", "status": "approved", "scope": "strategy, scope, direction B",
                   "snapshot": "review/01-directions.html", "version": "0.1.0", "confirmation": "选 B",
                   "approvedAt": "2026-10-03T10:00:00+08:00"}

    def findings(self, phase, approvals, release=False, status=None):
        with tempfile.TemporaryDirectory() as d:
            ws = self.make(d, phase=phase, approvals=approvals, status=status)
            return check_workspace.check(ws, phase, release=release)

    def test_later_phase_requires_earlier_gates_approved(self):
        self.assertNotEqual(self.findings(2, [{"gate": "G1", "status": "pending"}]), [])
        self.assertEqual(self.findings(2, [self.APPROVED_G1]), [])

    def test_latest_record_per_gate_wins(self):
        withdrawn = [self.APPROVED_G1, {"gate": "G1", "status": "changes-requested"}]
        self.assertNotEqual(self.findings(2, withdrawn), [])
        reapproved = [{"gate": "G1", "status": "changes-requested"}, self.APPROVED_G1]
        self.assertEqual(self.findings(2, reapproved), [])

    def test_release_requires_g5(self):
        gates = [dict(self.APPROVED_G1, gate="G%d" % k) for k in range(1, 5)]
        self.assertEqual(self.findings(5, gates), [])
        self.assertNotEqual(self.findings(5, gates, release=True), [])
        self.assertEqual(self.findings(5, gates + [dict(self.APPROVED_G1, gate="G5")], release=True), [])

    def test_malformed_approval_records_are_findings_not_crashes(self):
        for record in ({"gate": ["G1"], "status": "approved"}, {"status": "approved"},
                       {"gate": "G9", "status": "pending"}, "G1",
                       {k: v for k, v in self.APPROVED_G1.items() if k != "snapshot"}):
            with self.subTest(record):
                self.assertNotEqual(self.findings(0, [record]), [])

    def test_status_phase_must_match_checked_phase(self):
        self.assertNotEqual(self.findings(0, [], status={"phase": 1, "blockers": []}), [])

    def test_bad_cli_input_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            for argv in ([str(Path(d) / "nope"), "--phase", "0"], [d, "--phase", "\u00b2"], [d, "--phase", "7"]):
                with self.subTest(argv):
                    self.assertEqual(check_workspace.main(argv), 2)

    def test_broken_symlink_is_a_finding(self):
        with tempfile.TemporaryDirectory() as d:
            ws = self.make(d)
            (ws / "docs/dangling.md").symlink_to(ws / "nowhere.md")
            self.assertTrue(any("dangling" in f for f in check_workspace.check(ws, 0)))

    def test_status_shape(self):
        bad = [{"phase": True, "blockers": []}, {"phase": 0, "blockers": ["x"]},
               {"phase": 0, "blockers": [{"reason": "no network"}]}]
        for status in bad:
            with self.subTest(status), tempfile.TemporaryDirectory() as d:
                self.assertNotEqual(check_workspace.check(self.make(d, status=status), 0), [])

    def test_known_empty_files_are_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            ws = self.make(d)
            (ws / "src").mkdir()
            (ws / "src/__init__.py").write_text("")
            (ws / ".nojekyll").write_text("")
            (ws / ".venv").mkdir()
            (ws / ".venv/empty").write_text("")
            self.assertEqual(check_workspace.check(ws, 0), [])

    def test_required_files_match_manifest_phase_tags(self):
        import re
        tree = (ROOT / "references/structure-manifest.md").read_text(encoding="utf-8")
        stack, tagged = [], {}
        for line in tree.split("```text", 1)[1].split("```", 1)[0].splitlines()[1:]:
            m = re.match(r"^([│├└─ ]*)(\S+)(?:\s+\[(\d)\])?\s*(.*)$", line)
            depth = len(m.group(1)) // 4
            stack[depth:] = [m.group(2)]
            path = "/".join(s.rstrip("/") for s in stack[1:])  # drop the workspace root
            if m.group(3) and not m.group(2).endswith("/") and path not in self.CONDITIONAL:
                tagged[path] = int(m.group(3))
        required = {rel: p for p, rels in check_workspace.REQUIRED.items() for rel in rels}
        for path, phase in tagged.items():
            self.assertEqual(required.get(path), phase, path)
        # review pages and the QA report live in tagged directories, so pin them by name instead
        for path in set(required) - set(tagged):
            if path.startswith("review/"):
                self.assertEqual(required[path], int(path[len("review/0")]), path)
            else:
                self.assertEqual((path, required[path]), ("reports/qa-report.md", 5))

    # tagged files the manifest marks as created only when needed
    CONDITIONAL = {"docs/assumptions.md", "THIRD_PARTY_NOTICES.md"}

    def test_empty_file_fails(self):
        with tempfile.TemporaryDirectory() as d:
            ws = self.make(d)
            (ws / "docs/strategy.md").write_text("")
            self.assertNotEqual(check_workspace.check(ws, 0), [])


class PackageTest(unittest.TestCase):
    def test_brief_template_matches_schema(self):
        schema = json.loads((ROOT / "assets/brief.schema.json").read_text(encoding="utf-8"))
        template = json.loads((ROOT / "assets/brief.template.json").read_text(encoding="utf-8"))
        self.assertEqual(set(template), set(schema["properties"]) - {"$schema"})
        for key, value in template.items():
            nested = schema["properties"][key].get("properties")
            if nested:
                self.assertEqual(set(value), set(nested), key)

    def test_example_brief_uses_only_schema_fields(self):
        schema = json.loads((ROOT / "assets/brief.schema.json").read_text(encoding="utf-8"))
        example = json.loads((ROOT / "evals/example-brief.json").read_text(encoding="utf-8"))
        self.assertTrue(set(schema["required"]) <= set(example) <= set(schema["properties"]))
        for key, value in example.items():
            nested = schema["properties"][key].get("properties")
            if nested:
                self.assertTrue(set(value) <= set(nested), key)
        allowed = set(schema["properties"]["deliverables"]["items"]["enum"])
        self.assertTrue(set(example["deliverables"]) <= allowed)

    def test_single_file_prompt_keeps_section_order(self):
        import re
        out = build_prompt.build()
        self.assertFalse(out.startswith("---"))
        heads = re.findall(r"(?m)^## (\d\d|附录 [A-Z])", out)
        nums = [h for h in heads if h.isdigit()]
        self.assertEqual(nums, sorted(nums))
        self.assertEqual(heads[len(nums):], sorted(heads[len(nums):]))
        self.assertNotIn("END OF", out)

    def test_skill_documents_the_state_fields_the_checker_enforces(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for word in (sorted(check_workspace.GATE_STATES) + list(check_workspace.BLOCKER_FIELDS)
                     + list(check_workspace.APPROVAL_FIELDS) + ["version", "hash", "--release"]):
            self.assertIn(word, skill)

    def test_usage_errors_exit_2(self):
        self.assertEqual(build_prompt.main(["--help"]), 2)
        self.assertEqual(svg_lint.main(["/nonexistent/x.svg"]), 2)
        self.assertEqual(icon_verify.main(["/nonexistent/x.ico"]), 2)

    def test_single_file_prompt_contains_every_reference(self):
        out = build_prompt.build()
        for ref in (ROOT / "references").glob("*.md"):
            first_heading = ref.read_text(encoding="utf-8").splitlines()[0]
            self.assertIn(first_heading, out, ref.name)

    def test_skill_reference_table_points_to_existing_files(self):
        import re
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        # bare names are skill references; workspace files are always written with a path prefix
        named = set(re.findall(r"`([\w-]+\.md)`", skill)) - {"AGENTS.md", "CLAUDE.md"}
        self.assertEqual(named, {p.name for p in (ROOT / "references").glob("*.md")})
        for script in set(re.findall(r"scripts/(\w+\.py)", skill)):
            self.assertTrue((ROOT / "scripts" / script).exists(), script)


if __name__ == "__main__":
    unittest.main()
