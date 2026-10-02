"""Structural release checks for standalone SVG files.

Usage: svg_lint.py PATH [PATH ...]   # files or directories (recursive, any-case .svg)
Exit: 0 clean, 1 findings, 2 usage or unreadable input.

Checks: no DOCTYPE (aborted before any entity expands), well-formed XML,
<svg> root with viewBox, no <script>, no on* event attributes, no
<foreignObject>, no <image> (embedded or linked raster), no xml:base, no href,
src, CSS url(), image-set() or @import pointing outside the document (in any
attribute or <style>), no CSS backslash escapes (they can hide the above), no
animation that rewrites href, no duplicate ids. Rendering differences are not
covered; compare renders separately.
"""
import re
import sys
import xml.etree.ElementTree as ET
import xml.parsers.expat
from pathlib import Path

CSS_URL = re.compile(r"url\(\s*['\"]?\s*([^'\")\s]*)", re.I)
ANIMATIONS = {"set", "animate", "animateMotion", "animateTransform"}


def local(name):
    return name.rsplit("}", 1)[-1]


def css_problem(text):
    low = text.lower()
    if "\\" in text:
        return "CSS backslash escape"
    if "@import" in low or "image-set(" in low or any(not t.startswith("#") for t in CSS_URL.findall(text)):
        return "references an external resource"
    return None


class Doctype(Exception):
    pass


def has_doctype(data):
    def stop(*_):
        raise Doctype()
    parser = xml.parsers.expat.ParserCreate()
    parser.StartDoctypeDeclHandler = stop
    try:
        parser.Parse(data, True)
    except Doctype:
        return True
    except xml.parsers.expat.ExpatError:
        pass  # reported by the real parse below
    return False


def lint_file(path):
    data = Path(path).read_bytes()
    # expat honours the BOM/encoding declaration, so this works for UTF-16 too
    if has_doctype(data):
        return ["DOCTYPE declaration (not allowed in release SVG)"]
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return ["invalid XML: %s" % e]
    problems = []
    if local(root.tag) != "svg":
        problems.append("root element is <%s>, not <svg>" % local(root.tag))
    if "viewBox" not in root.attrib:
        problems.append("missing viewBox")
    ids = set()
    for el in root.iter():
        tag = local(el.tag)
        if tag in ("script", "foreignObject", "image"):
            problems.append("<%s> element" % tag)
        if tag == "style" and el.text and css_problem(el.text):
            problems.append("<style>: %s" % css_problem(el.text))
        if tag in ANIMATIONS and local(el.attrib.get("attributeName", "")).endswith("href"):
            problems.append("<%s> rewrites href" % tag)
        for attr, value in el.attrib.items():
            name = local(attr)
            if name.lower().startswith("on"):
                problems.append("event attribute %s on <%s>" % (name, tag))
            elif name in ("href", "src") and not value.startswith("#"):
                problems.append("external %s %r on <%s>" % (name, value[:60], tag))
            elif name == "base":
                problems.append("xml:base on <%s>" % tag)
            elif name == "id":
                if value in ids:
                    problems.append("duplicate id %r" % value)
                ids.add(value)
            elif css_problem(value):
                problems.append("%s on <%s>: %s" % (name, tag, css_problem(value)))
    return problems


def iter_svgs(paths):
    for p in map(Path, paths):
        if p.is_dir():
            yield from sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() == ".svg")
        else:
            yield p


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    failed = False
    try:
        for path in iter_svgs(argv):
            problems = lint_file(path)
            failed |= bool(problems)
            print("%s %s" % ("FAIL" if problems else "ok  ", path))
            for msg in problems:
                print("     - " + msg)
    except OSError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
