"""Structural release checks for standalone SVG files.

Usage: svg_lint.py PATH [PATH ...]   # files or directories (recursive, any-case .svg)
Exit: 0 clean, 1 findings, 2 usage or unreadable input.

Checks, aborting before any entity expands: no DOCTYPE, no processing
instruction (<?xml-stylesheet?> fetches). Then: well-formed XML, <svg> root
with viewBox, no <script>, <foreignObject> or <image>, no on* event attribute,
no xml:base, no href/src that leaves the document, no animation that rewrites
href, no duplicate ids. CSS is read only where SVG applies it -- the style
attribute, <style> text and URL-bearing presentation attributes -- and must not
fetch (url(), image-set(), @import), embed (data: URIs; outline text and
inline vectors instead) or use backslash escapes, which can hide either.
Metadata attributes (inkscape:*, aria-*, data-*) are not CSS and are ignored.
Rendering differences are not covered; compare renders separately.
"""
import re
import sys
import xml.etree.ElementTree as ET
import xml.parsers.expat
from pathlib import Path

CSS_URL = re.compile(r"url\(\s*['\"]?\s*([^'\")\s]*)", re.I)
URL_ATTRS = {"fill", "stroke", "filter", "clip-path", "mask", "marker-start", "marker-mid", "marker-end", "cursor"}
ANIMATIONS = {"set", "animate", "animateMotion", "animateTransform"}


def local(name):
    return name.rsplit("}", 1)[-1]


def css_problem(text):
    low = text.lower()
    if "\\" in text:
        return "CSS backslash escape"
    targets = CSS_URL.findall(text)
    if "@import" in low or "image-set(" in low or any(t and t[0] != "#" and not t.lower().startswith("data:") for t in targets):
        return "references an external resource"
    if any(t.lower().startswith("data:") for t in targets):
        return "embedded resource (outline text / inline vector instead)"
    if any(not t for t in targets):
        return "empty url()"
    return None


def link_problem(name, value):
    v = value.strip()
    if v.startswith("#"):
        return None
    if v.lower().startswith("data:"):
        return "embedded resource in %s (inline the vector instead)" % name
    return "external %s %r" % (name, value[:60])


class Stop(Exception):
    pass


def prolog_problem(data):
    """DOCTYPE or processing instruction, found before the real parse can expand anything."""
    def doctype(*_):
        raise Stop("DOCTYPE declaration (not allowed in release SVG)")

    def pi(target, _):
        raise Stop("processing instruction <?%s?> (not allowed in release SVG)" % target)

    parser = xml.parsers.expat.ParserCreate()
    parser.StartDoctypeDeclHandler = doctype
    parser.ProcessingInstructionHandler = pi
    try:
        parser.Parse(data, True)
    except Stop as e:
        return str(e)
    except xml.parsers.expat.ExpatError:
        pass  # reported by the real parse
    return None


def lint_file(path):
    data = Path(path).read_bytes()
    # expat honours the BOM/encoding declaration, so this works for UTF-16 too
    found = prolog_problem(data)
    if found:
        return [found]
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
        if tag == "style" and el.text:
            found = css_problem(el.text)
            if found:
                problems.append("<style>: %s" % found)
        if tag in ANIMATIONS and local(el.attrib.get("attributeName", "")).endswith("href"):
            problems.append("<%s> rewrites href" % tag)
        for attr, value in el.attrib.items():
            name = local(attr)
            namespaced = attr.startswith("{")
            found = None
            if name.lower().startswith("on"):
                found = "event attribute %s" % name
            elif name in ("href", "src"):
                found = link_problem(name, value)
            elif name == "base":
                found = "xml:base"
            elif name == "id":
                if value in ids:
                    found = "duplicate id %r" % value
                ids.add(value)
            elif not namespaced and (name == "style" or name in URL_ATTRS):
                css = css_problem(value)
                found = css and "%s: %s" % (name, css)
            if found:
                problems.append("%s on <%s>" % (found, tag))
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
