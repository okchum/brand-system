import json
import re
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
import directions_check  # noqa: E402
import validate_brief  # noqa: E402
import workbench  # noqa: E402


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


def icns_bytes(*kinds):
    elems = b"".join(k + struct.pack(">I", 9) + b"x" for k in kinds)
    return b"icns" + struct.pack(">I", 8 + len(elems)) + elems


class Mentions:
    def assertMentions(self, messages, needle):
        """The specific check fired, not just any check."""
        self.assertTrue(any(needle in m for m in messages), "%r not in %r" % (needle, messages))


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
            def matrix(content):
                p = Path(d) / ("m%d.json" % len(list(Path(d).iterdir())))
                p.write_text(content)
                return ["--matrix", str(p)]
            for argv in (["#fff", "#000", "--size", "huge"], ["#ggg", "#000"], ["#fff", "#000", "--size"],
                         ["#000", "#fff", "large"], matrix("[]"), matrix('{"a": 1}'), matrix('["#000"]'),
                         matrix('[{"fg": 123, "bg": "#fff"}]'), ["--matrix", str(Path(d) / "missing.json")],
                         ["--matrix"], ["+fffff", "#000"], ["# fffff", "#000"], ["-fffff", "#000"]):
                with self.subTest(argv):
                    self.assertEqual(contrast.main(argv), 2)

    def test_matrix_reports_failures(self):
        rows = contrast.check_matrix([
            {"fg": "#000000", "bg": "#ffffff", "usage": "body", "size": "normal"},
            {"fg": "#777777", "bg": "#ffffff", "usage": "body", "size": "normal"},
        ])
        self.assertEqual([r["pass"] for r in rows], [True, False])

    def test_reported_ratio_never_rounds_up_past_the_threshold(self):
        # #777777 on white is 4.478:1; showing "4.48" next to "fail" is fine, "4.5" would not be
        for row in contrast.check_matrix([{"fg": "#777777", "bg": "#ffffff"}, {"fg": "#767676", "bg": "#ffffff"}]):
            self.assertEqual(row["pass"], row["ratio"] >= row["required"])


class SvgLintTest(Mentions, unittest.TestCase):
    def lint(self, body, attrs='viewBox="0 0 24 24"', prolog=""):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.svg"
            p.write_text(prolog + '<svg xmlns="http://www.w3.org/2000/svg" '
                         'xmlns:xlink="http://www.w3.org/1999/xlink" %s>%s</svg>' % (attrs, body))
            return svg_lint.lint_file(p)

    def test_clean_svg_passes(self):
        self.assertEqual(self.lint('<path id="a" d="M0 0h24v24z"/><use href="#a"/><use xlink:href=" #a"/>'), [])

    def test_realistic_editor_exports_pass(self):
        inkscape = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
                    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
                    'viewBox="0 0 24 24" inkscape:export-filename="C:\\Users\\me\\logo.png" aria-label="see url(acme.com)">'
                    '<title>Logo</title><metadata><rdf:RDF><rdf:Description/></rdf:RDF></metadata>'
                    '<defs><linearGradient id="g"><stop offset="0"/></linearGradient><clipPath id="c"><rect/></clipPath></defs>'
                    '<g clip-path="url(#c)"><rect fill="url(#g)" style="stroke:url(&quot;#g&quot;)"/>'
                    '<path d="M0 0h24"/><circle r="1"/><polygon points="0,0 1,1"/></g></svg>')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.svg"
            p.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!-- Generator: editor -->\n' + inkscape)
            self.assertEqual(svg_lint.lint_file(p), [])

    def test_each_violation_is_reported(self):
        cases = {
            "script": ("<script>alert(1)</script>", "<script> not allowed"),
            "event": ('<path onload="x()" d="M0 0"/>', "event attribute"),
            "raster": ('<image href="#a"/>', "<image> not allowed"),
            "link": ('<a href="#a"><path d="M0 0"/></a>', "<a> not allowed"),
            "foreign element": ('<html:iframe xmlns:html="http://www.w3.org/1999/xhtml"/>', "not allowed"),
            "animation url": ('<rect><set attributeName="fill" to="url(http://evil/x)"/></rect>', "<set> not allowed"),
            "style after child": ('<style><x/>@import "http://e/a.css";</style>', "<style>"),
            "external": ('<use href="https://example.com/a.svg#b"/>', "external href"),
            "embedded href": ('<use href="data:image/svg+xml;base64,AAAA"/>', "embedded"),
            "foreignObject": ("<foreignObject/>", "<foreignObject> not allowed"),
            "duplicate id": ('<g id="a"/><g id="a"/>', "duplicate id"),
            "css url": ("<style>.a{fill:url(https://example.com/x)}</style>", "external"),
            "embedded font": ("<style>@font-face{src:url(data:font/woff2;base64,AA)}</style>", "embedded"),
            "presentation url": ('<rect fill="url(https://example.com/x.svg#g)"/>', "external"),
            "relative url": ('<rect mask="url(ext.svg#f)"/>', "external"),
            "animated xlink": ('<animate attributeName="xlink:href" values="https://example.com"/>', "<animate> not allowed"),
            "css escape": ('<style>@\\69mport "http://x/a.css";</style>', "escape"),
            "image-set": ("<rect style=\"fill:image-set('http://x/a.png' 1x)\"/>", "external"),
            "xml:base": ('<g xml:base="http://example.com/"/>', "xml:base"),
            "src": ('<rect src="http://example.com/f.woff"/>', "external src"),
            "url in any attribute": ('<rect color-profile="url(http://e/p.icc)"/>', "external"),
        }
        for name, (body, needle) in cases.items():
            with self.subTest(name):
                self.assertMentions(self.lint(body), needle)

    def test_metadata_is_not_a_hiding_place(self):
        for body, needle in (('<metadata><script>alert(1)</script></metadata>', "<script> not allowed"),
                             ('<metadata><style>@import "http://e/a.css";</style></metadata>', "<style>"),
                             ('<metadata><image href="http://e/a.png"/></metadata>', "<image> not allowed")):
            with self.subTest(body):
                self.assertMentions(self.lint(body), needle)

    def test_editor_namespace_elements_are_ignored_but_their_svg_children_are_not(self):
        sodipodi = 'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"'
        self.assertEqual(self.lint('<sodipodi:namedview pagecolor="#fff"/><path d="M0 0"/>',
                                   attrs='viewBox="0 0 1 1" ' + sodipodi), [])
        self.assertMentions(self.lint('<sodipodi:namedview><script/></sodipodi:namedview>',
                                      attrs='viewBox="0 0 1 1" ' + sodipodi), "<script> not allowed")

    def test_missing_svg_namespace_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.svg"
            p.write_text('<svg viewBox="0 0 1 1"><path d="M0 0"/></svg>')
            self.assertEqual(svg_lint.lint_file(p), ["missing SVG namespace (xmlns=\"http://www.w3.org/2000/svg\")"])

    def test_deep_nesting_does_not_crash(self):
        self.assertEqual(self.lint("<g>" * 3000 + "</g>" * 3000), [])

    def test_duplicate_xml_id_is_reported(self):
        self.assertMentions(self.lint('<g id="a"/><g xml:id="a"/>'), "duplicate id")
        self.assertEqual(self.lint('<path id="a" xml:id="a" d="M0 0"/>'), [])
        self.assertEqual(self.lint('<g id=""/><g id=""/>'), [])  # an empty id is unreachable by url(#)

    def test_editor_element_ids_still_count(self):
        # url(#g) resolves to the first element with that id, so the editor element would win
        ink = 'viewBox="0 0 1 1" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"'
        self.assertMentions(self.lint('<inkscape:g id="g"/><linearGradient id="g"/>', attrs=ink), "duplicate id")

    def test_root_must_be_svg_namespace(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.svg"
            p.write_text('<inkscape:svg xmlns="http://www.w3.org/2000/svg" '
                         'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" viewBox="0 0 1 1"/>')
            self.assertMentions(svg_lint.lint_file(p), "not the SVG namespace")
            p.write_text('<svg xmlns="http://example.com/x" viewBox="0 0 1 1"/>')
            self.assertEqual(svg_lint.lint_file(p), ["root <svg> is in namespace http://example.com/x, not the SVG namespace"])

    def test_presentation_attributes_are_parsed_as_css(self):
        # Chrome fetched each of these in a headless conformance run
        for attr in ('mask="\\75rl(http://e/a.png)"', "mask=\"image-set('http://e/a.png' 1x)\"",
                     "mask=\"-webkit-image-set('http://e/a.png' 1x)\"", 'fill="\\75rl(http://e/f.svg#g) green"',
                     'clip-path="\\75rl(http://e/c.svg#c)"', 'cursor="\\75rl(http://e/u.png), auto"'):
            with self.subTest(attr):
                self.assertNotEqual(self.lint("<rect %s/>" % attr), [])

    def test_css_animation_and_filters_are_rejected(self):
        for body in ("<style>@keyframes k{to{fill:red}}rect{animation:k 1ms forwards}</style>",
                     "<style>rect{transition:fill 1s}</style>", '<rect filter="blur(6px)"/>',
                     '<rect style="filter:drop-shadow(5px 5px 0 red)"/>', "<style>g{backdrop-filter:blur(2px)}</style>"):
            with self.subTest(body):
                self.assertMentions(self.lint(body), "flat shapes only")
        self.assertEqual(self.lint('<rect filter="none" style="fill:red"/>'), [])

    def test_css_is_checked_by_property_allowlist(self):
        # each passed the denylist and changed rendering in a headless Chrome conformance run
        for body in ("<style>@-webkit-keyframes k{to{fill:blue}}#t{-webkit-animation:k 100s both}</style>",
                     '<rect style="-webkit-filter:blur(6px)"/>', '<rect style="-webkit-transition:fill 50s"/>',
                     '<rect style="filter/**/:blur(6px)"/>', '<rect style="transition-property:fill;transition-duration:50s"/>',
                     "<style>@supports (fill:red){rect{fill:red}}</style>", "<style>rect{&amp;:hover{fill:red}}</style>",
                     "<style>rect:hover{fill:red}</style>", '<rect style="--c:red;fill:var(--c)"/>',
                     "<style>#t{fill:attr(data-c type(&lt;color&gt;))}</style>"):
            with self.subTest(body):
                self.assertMentions(self.lint(body), "flat shapes only")

    def test_static_css_from_editors_passes(self):
        self.assertEqual(self.lint(
            '<style>.st0{fill:#1a2b3c;stroke:#000;stroke-width:2;stroke-linecap:round}'
            '.st1 , g > .st2{opacity:.5;mix-blend-mode:multiply;font-family:"Inter";font-weight:600}</style>'
            '<g class="st0" style="fill-rule:evenodd; transform: translate(1px, 2px);"/>'
            '<text font-family="A\\B">x</text>'), [])

    def test_semicolons_inside_strings_do_not_split_declarations(self):
        self.assertEqual(self.lint('<text style="font-family:\'A;B\', sans-serif;fill:#000">x</text>'), [])
        self.assertMentions(self.lint('<rect style="fill:red;animation:k 1s"/>'), "animation")
        for style in ("fill:'red;animation:k 1s", "fill:url(#a;animation:k 1s"):
            with self.subTest(style):
                self.assertMentions(self.lint('<rect style="%s"/>' % style), "unbalanced")

    def test_unclosed_css_comment_fails(self):
        self.assertMentions(self.lint('<rect style="fill:red /* oops"/>'), "comment")

    def test_processing_instruction_is_rejected(self):
        self.assertMentions(self.lint("", prolog='<?xml-stylesheet href="https://x/a.css"?>'), "processing instruction")

    def test_quoted_local_url_passes(self):
        defs = '<linearGradient id="g"/>'
        self.assertEqual(self.lint(defs + '<style>.a{fill:url("#g")}</style><rect style="fill:url( #g)"/>'), [])

    def test_doctype_is_rejected_in_any_encoding(self):
        doc = '<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg viewBox="0 0 1 1">&a;</svg>'
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding), tempfile.TemporaryDirectory() as d:
                p = Path(d) / "x.svg"
                p.write_bytes(doc.encode(encoding))
                self.assertMentions(svg_lint.lint_file(p), "DOCTYPE")

    def test_doctype_word_in_comment_is_fine(self):
        self.assertEqual(self.lint("<!-- no <!DOCTYPE here -->"), [])

    def test_directory_mode_includes_uppercase_extension(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "sub").mkdir()
            (Path(d) / "sub" / "A.SVG").write_text("<svg/>")
            self.assertEqual([p.name for p in svg_lint.iter_svgs([d])], ["A.SVG"])

    def test_missing_viewbox_is_reported(self):
        self.assertMentions(self.lint("<path d='M0 0'/>", attrs=""), "viewBox")

    def test_invalid_xml_is_reported(self):
        self.assertMentions(self.lint("<path>"), "invalid XML")


class IconVerifyTest(Mentions, unittest.TestCase):
    def verify(self, name, data):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / name
            p.write_bytes(data)
            return icon_verify.verify(p)

    def test_png_reports_dimensions(self):
        info = self.verify("a.png", png_bytes(32, 16))
        self.assertEqual((info["format"], info["sizes"], info["errors"]), ("png", [(32, 16)], []))

    def test_png_renamed_to_ico_fails(self):
        self.assertMentions(self.verify("favicon.ico", png_bytes(32, 32))["errors"], "content is PNG")

    def test_real_ico_lists_all_sizes(self):
        info = self.verify("favicon.ico", ico_bytes([16, 32, 256]))
        self.assertEqual(info["errors"], [])
        self.assertEqual(info["sizes"], [(16, 16), (32, 32), (256, 256)])

    def test_declared_size_must_match_embedded_png(self):
        data = bytearray(ico_bytes([16]))
        data[6:8] = bytes([32, 32])  # directory claims 32x32, embedded PNG is 16x16
        self.assertMentions(self.verify("favicon.ico", bytes(data))["errors"], "declares 32x32 but its PNG is 16x16")
        img = png_bytes(16, 16)
        elem = b"ic08" + struct.pack(">I", 8 + len(img)) + img  # ic08 is 256x256
        icns = b"icns" + struct.pack(">I", 8 + len(elem)) + elem
        self.assertMentions(self.verify("app.icns", icns)["errors"], "declares 256x256 but its PNG is 16x16")

    def test_ico_without_images_fails(self):
        self.assertMentions(self.verify("favicon.ico", ico_bytes([]))["errors"], "no images")

    def test_png_problems_are_named_precisely(self):
        cases = (("cut header", png_bytes(8, 8)[:20], "truncated PNG header"),
                 ("cut body", png_bytes(8, 8)[:40], "truncated"),
                 ("cut in IEND crc", png_bytes(8, 8)[:-2], "truncated"),
                 ("IEND bytes inside IDAT, real IEND missing", png_bytes(8, 8)[:33] + b"IEND" * 3, "truncated"),
                 ("zero size", png_bytes(0, 0), "zero-sized"),
                 ("trailing bytes", png_bytes(8, 8) + b"\n", "after IEND"),
                 ("no IDAT", png_bytes(8, 8)[:33] + png_bytes(8, 8)[-12:], "no IDAT"),
                 ("IEND with data", png_bytes(8, 8)[:-12] + struct.pack(">I", 1) + b"IEND\0" + b"\0" * 4, "IEND"))
        for name, data, needle in cases:
            with self.subTest(name):
                self.assertMentions(self.verify("a.png", data)["errors"], needle)

    def test_ico_entry_must_point_at_real_image_data(self):
        data = bytearray(ico_bytes([16]))
        data[6 + 8:6 + 16] = struct.pack("<II", 0, 0)  # size 0, offset 0
        self.assertMentions(self.verify("favicon.ico", bytes(data))["errors"], "does not point at image data")

    def test_empty_icns_element_fails(self):
        elem = b"ic10" + struct.pack(">I", 8)
        self.assertMentions(self.verify("a.icns", b"icns" + struct.pack(">I", 16) + elem)["errors"], "is empty")

    def test_icns_reports_every_iconutil_size(self):
        # the element types iconutil writes for a standard 16-512 @1x/@2x iconset
        info = self.verify("a.icns", icns_bytes(b"ic04", b"ic11", b"ic05", b"ic12", b"ic07",
                                                b"ic13", b"ic08", b"ic14", b"ic09", b"ic10"))
        self.assertEqual(info["errors"], [])
        self.assertEqual(sorted(set(info["sizes"])), [(s, s) for s in (16, 32, 64, 128, 256, 512, 1024)])

    def test_icns_length_must_match_file(self):
        self.assertEqual(self.verify("a.icns", icns_bytes(b"ic07"))["errors"], [])
        self.assertMentions(self.verify("a.icns", b"icns" + struct.pack(">I", 8))["errors"], "no elements")
        self.assertMentions(self.verify("a.icns", b"icns" + struct.pack(">I", 99))["errors"], "declared length")


class CheckWorkspaceTest(Mentions, unittest.TestCase):
    APPROVED_G1 = {"gate": "G1", "status": "approved", "scope": "strategy, scope, direction B",
                   "snapshot": "review/01-directions.html", "version": "0.1.0", "confirmation": "选 B",
                   "approvedAt": "2026-10-03T10:00:00+08:00"}

    def make(self, d, phase=0, approvals=None, status=None):
        d = Path(d)
        for rel in sum((check_workspace.REQUIRED[p] for p in range(phase + 1)), []):
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text("x")
        (d / "brand.brief.json").write_text((ROOT / "evals/example-brief.json").read_text())
        (d / "project/status.json").write_text(json.dumps(status or {"phase": phase, "state": "draft", "completed": [], "next": [], "blockers": []}))
        (d / "project/approvals.json").write_text(json.dumps(approvals or []))
        if phase >= 1:
            (d / "review/01-directions.html").write_text((ROOT / "evals/directions.fixture.html").read_text())
        return d

    def findings(self, phase, approvals=None, release=False, status=None):
        with tempfile.TemporaryDirectory() as d:
            return check_workspace.check(self.make(d, phase, approvals, status), phase, release=release)

    def test_complete_phase0_passes(self):
        self.assertEqual(self.findings(0), [])

    def test_missing_review_page_fails_phase1(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertMentions(check_workspace.check(self.make(d, phase=0, status={"phase": 1, "blockers": []}), 1),
                                "missing review/01-directions.html")

    def test_approved_record_needs_every_field(self):
        for field in check_workspace.APPROVAL_FIELDS:
            with self.subTest(field):
                record = {k: v for k, v in self.APPROVED_G1.items() if k != field}
                self.assertMentions(self.findings(0, [record]), field)
        no_version = {k: v for k, v in self.APPROVED_G1.items() if k != "version"}
        self.assertMentions(self.findings(0, [no_version]), "version or hash")
        self.assertEqual(self.findings(0, [dict(no_version, hash="sha256:ab12")]), [])

    def test_blank_or_non_string_fields_do_not_count(self):
        for value in (" ", True, ["x"], {"a": 1}):
            with self.subTest(value):
                self.assertMentions(self.findings(2, [dict(self.APPROVED_G1, snapshot=value)]), "snapshot")

    def test_later_phase_requires_earlier_gates_approved(self):
        self.assertMentions(self.findings(2, [{"gate": "G1", "status": "pending"}]), "requires gate G1")
        self.assertEqual(self.findings(2, [self.APPROVED_G1]), [])

    def test_latest_record_per_gate_wins(self):
        for later in ({"gate": "G1", "status": "changes-requested"}, {"gate": "G1", "status": "pending"},
                      {"gate": "G1", "status": "rejected"}, dict(self.APPROVED_G1, snapshot="")):
            with self.subTest(later):
                self.assertMentions(self.findings(2, [self.APPROVED_G1, later]), "requires gate G1")
        reapproved = [{"gate": "G1", "status": "changes-requested"}, self.APPROVED_G1]
        self.assertEqual(self.findings(2, reapproved), [])

    def test_release_requires_g5(self):
        gates = [dict(self.APPROVED_G1, gate="G%d" % k) for k in range(1, 5)]

        def gate_findings(*args, **kwargs):
            # This fixture has no UI files; test_release_keeps_ui_contract covers those findings.
            return [f for f in self.findings(*args, **kwargs) if "(contract)" not in f]

        self.assertEqual(gate_findings(5, gates), [])
        self.assertMentions(gate_findings(5, gates, release=True), "requires gate G5")
        self.assertEqual(gate_findings(5, gates + [dict(self.APPROVED_G1, gate="G5")], release=True), [])

    def test_release_keeps_ui_contract(self):
        gates = [dict(self.APPROVED_G1, gate="G%d" % k) for k in range(1, 6)]
        findings = self.findings(5, gates, release=True)
        self.assertMentions(findings, "missing config/ui.json (contract)")
        self.assertMentions(findings, "missing src/ui/ir/manifest.json (contract)")

    def test_malformed_kind_withdraws_the_gate_it_names(self):
        withdrawn = self.findings(2, [self.APPROVED_G1, {"kind": "Gate", "gate": "G1", "status": "changes-requested"}])
        self.assertMentions(withdrawn, "kind must be gate or unit-review")
        self.assertMentions(withdrawn, "requires gate G1")

    def test_malformed_approval_records_are_findings_not_crashes(self):
        for record in ({"gate": ["G1"], "status": "approved"}, {"status": "approved"},
                       {"gate": "G9", "status": "pending"}, "G1"):
            with self.subTest(record):
                self.assertMentions(self.findings(0, [record]), "needs gate")

    def test_status_phase_must_match_checked_phase(self):
        self.assertMentions(self.findings(0, status={"phase": 1, "blockers": []}), "says phase 1")

    def test_status_shape(self):
        cases = (({"phase": True, "blockers": []}, "phase must be an integer"),
                 ({"phase": 0, "blockers": ["x"]}, "blocker #0"),
                 ({"phase": 0, "blockers": [{"reason": "no network"}]}, "impact, owner, workaround"))
        for status, needle in cases:
            with self.subTest(status):
                self.assertMentions(self.findings(0, status=status), needle)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            ws = str(self.make(d))
            self.assertEqual(check_workspace.main([ws, "--phase", "0"]), 0)
            self.assertEqual(check_workspace.main([ws, "--phase", "1"]), 1)
            for argv in ([ws + "/nope", "--phase", "0"], [ws, "--phase", "\u00b2"], [ws, "--phase", "7"],
                         [ws, "--phase", "4", "--release"]):
                with self.subTest(argv):
                    self.assertEqual(check_workspace.main(argv), 2)

    def test_example_brief_matches_schema(self):
        self.assertEqual(validate_brief.validate_file(ROOT / "evals/example-brief.json"), [])

    def test_validator_enforces_every_keyword_the_schema_uses(self):
        schema = json.loads((ROOT / "assets/brief.schema.json").read_text(encoding="utf-8"))

        def errors(definition, value):
            return validate_brief.validate(value, {"$ref": "#/$defs/" + definition}, root=schema)

        ui = {"version": "1", "platforms": ["web"], "stackProfile": "react", "tokenSource": "tokens/src", "deliveryStatus": "preview-only"}
        self.assertEqual(errors("uiConfig", ui), [])
        for change, needle in (
            ({"version": ""}, "too short"),
            ({"platforms": []}, "too few items"),
            ({"tokenSource": "tokens"}, "must be"),
        ):
            with self.subTest(change):
                self.assertTrue(any(needle in e for e in errors("uiConfig", dict(ui, **change))), errors("uiConfig", dict(ui, **change)))
        self.assertTrue(any("pattern" in e for e in errors("irManifest", {"manifestVersion": "1", "hash": "md5:1", "units": [
            {"id": "a", "kind": "layout", "files": ["x"], "platforms": ["web"]}]})))
        scope = {"path": "a", "startLine": 0, "endLine": 1}
        self.assertTrue(any("minimum" in e for e in errors("fileRange", scope)))
        self.assertTrue(any("integer" in e for e in errors("fileRange", dict(scope, startLine=True))))
        gate = {"kind": "gate", "gate": "G1", "status": "approved", "scope": "s", "snapshot": "s", "confirmation": "c", "approvedAt": "t", "version": "v"}
        self.assertEqual(errors("approvalRecord", gate), [])
        self.assertTrue(errors("approvalRecord", dict(gate, gate="G9")))
        no_version = {k: v for k, v in gate.items() if k != "version"}
        self.assertTrue(any("anyOf" in e for e in errors("gateApproval", no_version)))
        self.assertTrue(any("oneOf" in e for e in errors("approvalRecord", no_version)))

    def test_directions_contract_requires_three_distinct_metadata_blocks(self):
        self.assertEqual(directions_check.check(ROOT / "evals/directions.fixture.html"), [])

    def test_workbench_initializes_and_refuses_nonempty_directory(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "brand-workspace"
            source = Path(d) / "source"
            source.mkdir()
            (source / "README.md").write_text("source")
            workbench.init_workspace(target, "Acme", "A product", str(source), ["feedback", "roadmap"])
            self.assertTrue((target / "brand.brief.json").is_file())
            self.assertEqual(json.loads((target / "brand.brief.json").read_text())["product"]["capabilities"], ["feedback", "roadmap"])
            self.assertIn(str(source), (target / "docs/references.md").read_text())
            self.assertEqual(json.loads((target / "project/source.json").read_text())["files"], ["README.md"])
            self.assertEqual(json.loads((target / "project/status.json").read_text())["phase"], 0)
            with self.assertRaises(ValueError):
                workbench.init_workspace(Path(d), "Other", "Overwrite")

    def test_broken_symlink_is_a_finding(self):
        with tempfile.TemporaryDirectory() as d:
            ws = self.make(d)
            (ws / "docs/dangling.md").symlink_to(ws / "nowhere.md")
            self.assertMentions(check_workspace.check(ws, 0), "dangling")

    def test_empty_file_fails_unless_known_placeholder(self):
        with tempfile.TemporaryDirectory() as d:
            ws = self.make(d)
            (ws / "src").mkdir()
            (ws / "src/__init__.py").write_text("")
            (ws / ".nojekyll").write_text("")
            (ws / ".venv").mkdir()
            (ws / ".venv/empty").write_text("")
            self.assertEqual(check_workspace.check(ws, 0), [])
            (ws / "docs/strategy.md").write_text("")
            self.assertMentions(check_workspace.check(ws, 0), "empty file docs/strategy.md")

    # Tagged files the manifest creates only when needed, and the either/or rules file.
    CONDITIONAL = {"docs/assumptions.md", "THIRD_PARTY_NOTICES.md", "AGENTS.md / CLAUDE.md"}

    def test_required_files_match_manifest_phase_tags(self):
        tree = (ROOT / "references/structure-manifest.md").read_text(encoding="utf-8")
        stack, tagged = [], {}
        for line in tree.split("```text", 1)[1].split("```", 1)[0].splitlines()[1:]:
            m = re.match(r"^([│├└─ ]*)(\S+(?: / \S+)*)(?:\s+\[(\d)\])?\s*(.*)$", line)
            self.assertFalse(m.group(3) is None and re.search(r"\[\d\]", line), "unparsed tag: " + line)
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

    def test_skill_states_the_record_shapes_the_checker_enforces(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        cw = check_workspace
        self.assertIn("`{gate, status, %s, version 或 hash}`" % ", ".join(cw.APPROVAL_FIELDS), skill)
        self.assertIn("`gate` 取 `%s`–`%s`" % (cw.GATES[0], cw.GATES[-1]), skill)
        self.assertIn("`status` 取 `%s`" % " / ".join(cw.GATE_STATES), skill)
        self.assertIn("同一 gate 以最后一条为准", skill)
        self.assertIn("`{%s}`" % ", ".join(cw.BLOCKER_FIELDS), skill)
        self.assertIn("--release", skill)

    def test_reference_sections_keep_integer_top_level_numbers(self):
        for ref in (ROOT / "references").glob("*.md"):
            for line in ref.read_text(encoding="utf-8").splitlines():
                if line.startswith("## "):
                    self.assertRegex(line, r"^## (?:\d+\. \S.*|附录 [A-Z](?:[：:].*)?)$", ref.name)

    def test_single_file_prompt_keeps_section_order(self):
        out = build_prompt.build()
        self.assertFalse(out.startswith("---"))
        heads = re.findall(r"(?m)^## (\d\d|附录 [A-Z])", out)
        nums = [h for h in heads if h.isdigit()]
        self.assertEqual(nums, sorted(nums))
        self.assertEqual(heads[len(nums):], sorted(heads[len(nums):]))
        self.assertNotIn("END OF", out)

    def test_headings_inside_code_fences_are_not_sections(self):
        for fence in ("```", "~~~"):
            with self.subTest(fence):
                text = "## 05. A\n\n%smarkdown\n## not a section\n%s\n\n## 06. B\n" % (fence, fence)
                self.assertEqual([b.split("\n", 1)[0] for b in build_prompt.sections(text)], ["## 05. A", "## 06. B"])

    def test_inline_code_and_indented_fences(self):
        text = "## 05. A\n```x``` is inline\n   ```\n## inside\n   ```\n## 06. B\n"
        self.assertEqual([b.split("\n", 1)[0] for b in build_prompt.sections(text)], ["## 05. A", "## 06. B"])

    def test_unclosed_fence_or_no_sections_is_an_error(self):
        for text in ("## 05. A\n```\n## 06. B\n", "no headings here\n"):
            with self.subTest(text):
                with self.assertRaises(ValueError):
                    build_prompt.sections(text)

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
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        # bare names are skill references; workspace files are always written with a path prefix
        named = set(re.findall(r"`([\w-]+\.md)`", skill)) - {"AGENTS.md", "CLAUDE.md"}
        self.assertEqual(named, {p.name for p in (ROOT / "references").glob("*.md")})
        for script in set(re.findall(r"scripts/(\w+\.py)", skill)):
            self.assertTrue((ROOT / "scripts" / script).exists(), script)


if __name__ == "__main__":
    unittest.main()
