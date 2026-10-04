"""Validate article metadata; this does not certify visual design quality."""
from html.parser import HTMLParser
from pathlib import Path
import sys

FIELDS = ("data-primary", "data-type", "data-symbol", "data-ui", "data-risk")


class DirectionsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article" and "data-direction" in attrs:
            self.current = dict(attrs)
            self.rows.append(self.current)
        elif self.current is not None:
            for key in FIELDS:
                if key in attrs:
                    self.current[key] = attrs[key]

    def handle_endtag(self, tag):
        if tag == "article":
            self.current = None


def check(path):
    try:
        parser = DirectionsParser()
        parser.feed(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        return ["cannot read review: %s" % exc]
    rows = parser.rows
    errors = []
    if len(rows) != 3:
        errors.append("review must contain exactly three data-direction articles")
    names = [(row.get("data-direction") or "").strip() for row in rows]
    if not all(names) or len(set(names)) != len(names):
        errors.append("direction names must be non-blank and unique")
    for name, row in zip(names, rows):
        for field in FIELDS:
            if not (row.get(field) or "").strip():
                errors.append("%s missing %s" % (name, field))
    signatures = [tuple((row.get(key) or "").strip().casefold() for key in
                        ("data-type", "data-symbol", "data-ui")) for row in rows]
    if len(set(signatures)) != len(signatures):
        errors.append("directions must differ beyond name and primary color")
    return errors


def main(argv):
    if len(argv) != 1:
        print("Usage: directions_check.py review/01-directions.html")
        return 2
    errors = check(argv[0])
    for error in errors:
        print("FAIL " + error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
