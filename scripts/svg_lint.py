"""Structural release checks for standalone SVG files, by allowlist.

Usage: svg_lint.py PATH [PATH ...]   # files or directories (recursive, any-case .svg)
Exit: 0 clean, 1 findings, 2 usage or unreadable input.

A release logo, symbol or brand graphic needs only static shapes, so anything
outside ALLOWED fails -- scripts, links, raster <image>, <foreignObject>,
animation, filters, foreign elements -- and an unexpected construct surfaces
as a finding rather than slipping through. Flat shapes only: outline text that
must keep its form, no shadows or filters. Elements in known editor namespaces
(inkscape, sodipodi, RDF/Dublin Core/Creative Commons metadata) are ignored,
but SVG elements inside them are still checked, because browsers run and style
them wherever they sit. Namespaced attributes and aria-*/data-* are ignored.

Also, before any entity can expand: no DOCTYPE, no processing instruction
(<?xml-stylesheet?> fetches). Then: well-formed XML, <svg> root with viewBox,
no on* event attribute, no xml:base, href/src only to #fragments, every url()
in any attribute or <style> pointing to a #fragment (no fetch, no data:
embed; outline text and inline vectors instead), no image-set()/@import, no
CSS backslash escapes (they can hide the above), <style> holding text only,
no duplicate id or xml:id. Rendering differences are not covered; compare renders.
"""
import re
import sys
import xml.etree.ElementTree as ET
import xml.parsers.expat
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
# ponytail: static-shape allowlist; add an element here only when an approved brand asset needs it
ALLOWED = {
    "svg", "g", "defs", "symbol", "use", "title", "desc", "metadata", "style",
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
    "linearGradient", "radialGradient", "stop", "clipPath", "mask", "pattern",
}
XML_NS = "http://www.w3.org/XML/1998/namespace"
EDITOR_NS = {
    "http://www.inkscape.org/namespaces/inkscape", "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "http://purl.org/dc/elements/1.1/",
    "http://creativecommons.org/ns#", "http://web.resource.org/cc/",
}
CSS_URL = re.compile(r"url\(\s*['\"]?\s*([^'\")\s]*)", re.I)


def split(name):
    ns, _, local = name[1:].partition("}") if name.startswith("{") else ("", "", name)
    return ns, local


def css_problem(text):
    if "\\" in text:
        return "CSS backslash escape"
    low = text.lower()
    if "@import" in low or "image-set(" in low:
        return "references an external resource"
    for target in CSS_URL.findall(text):
        if target.lower().startswith("data:"):
            return "embedded resource (outline text / inline vector instead)"
        if not target.startswith("#"):
            return "references an external resource" if target else "empty url()"
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


def attribute_problem(attr, value):
    ns, name = split(attr)
    if name == "base" and ns == XML_NS:
        return "xml:base"
    if name in ("href", "src"):
        return link_problem(name, value)
    if ns or name.startswith(("aria-", "data-")):
        return None  # editor or accessibility metadata, never applied as CSS
    if name.lower().startswith("on"):
        return "event attribute %s" % name
    if name == "style" or "url(" in value.lower():
        css = css_problem(value)
        return css and "%s: %s" % (name, css)
    return None


def check_element(el, problems, ids):
    ns, tag = split(el.tag)
    # ids count on every element: url(#x) resolves to the first match, editor element or not
    for value in {v for a, v in el.attrib.items() if split(a) in (("", "id"), (XML_NS, "id"))}:
        if value in ids:
            problems.append("duplicate id %r" % value)
        ids.add(value)
    if ns in EDITOR_NS:
        return  # editor data; its SVG children are still visited
    if ns != SVG_NS or tag not in ALLOWED:
        problems.append("<%s> not allowed in release SVG" % (tag if ns == SVG_NS else el.tag))
    for attr, value in el.attrib.items():
        found = attribute_problem(attr, value)
        if found:
            problems.append("%s on <%s>" % (found, tag))
    if tag == "style":
        if len(el):
            problems.append("<style> must hold text only")
        found = css_problem("".join(el.itertext()))
        if found:
            problems.append("<style>: %s" % found)


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
    ns, tag = split(root.tag)
    if tag == "svg" and ns == "":
        return ['missing SVG namespace (xmlns="%s")' % SVG_NS]
    problems = []
    if tag != "svg" or ns != SVG_NS:
        problems.append("root element is <%s>, not <svg>" % root.tag)
    if "viewBox" not in root.attrib:
        problems.append("missing viewBox")
    ids = set()
    for el in root.iter():  # iterative, so deep nesting cannot exhaust the stack
        check_element(el, problems, ids)
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
