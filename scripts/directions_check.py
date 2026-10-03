"""Validate the machine-readable contract for a phase-1 directions review page."""
import re, sys
from pathlib import Path

def check(path):
    text = Path(path).read_text(encoding="utf-8")
    blocks = re.findall(r'(<article[^>]*data-direction=["\']([^"\']+)["\'][^>]*>)(.*?)</article>', text, re.S | re.I)
    errors = []
    if len(blocks) != 3: errors.append("review must contain exactly three data-direction articles")
    names = [name.strip() for _, name, _ in blocks]
    if len(set(names)) != len(names): errors.append("direction names must be unique")
    for tag, name, body in blocks:
        body = tag + body
        for marker in ("data-primary", "data-type", "data-symbol", "data-ui", "data-risk"):
            if not re.search(r"%s=[\"'][^\"']+[\"']" % marker, body, re.I): errors.append("%s missing %s" % (name, marker))
    return errors

if __name__ == "__main__":
    if len(sys.argv) != 2: print("Usage: directions_check.py review/01-directions.html"); sys.exit(2)
    errors = check(sys.argv[1])
    for error in errors: print("FAIL " + error)
    print("%d finding(s)" % len(errors)); sys.exit(bool(errors))
