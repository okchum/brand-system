"""WCAG 2.x contrast ratio for a single pair or a pairing matrix.

Usage:
  contrast.py FG BG [--size normal|large|ui]
  contrast.py --matrix pairs.json     # [{"fg","bg","usage","size"}]
Exit: 0 all pass, 1 any pair fails, 2 usage or invalid input.

FG may carry alpha (#RRGGBBAA); it is composited over BG before measuring,
so pass the real backdrop, not the palette swatch.
"""
import json
import math
import re
import sys

THRESHOLDS = {"normal": 4.5, "large": 3.0, "ui": 3.0}


def parse(hex_color):
    h = hex_color.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    if len(h) not in (6, 8) or not re.fullmatch(r"[0-9a-fA-F]+", h):
        raise ValueError("not a hex color: %s" % hex_color)
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    alpha = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
    return rgb, alpha


def luminance(rgb):
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(fg, bg):
    (f, a), (b, b_alpha) = parse(fg), parse(bg)
    if b_alpha < 1:
        raise ValueError("background must be opaque; composite it onto its own backdrop first: %s" % bg)
    f = [a * fc + (1 - a) * bc for fc, bc in zip(f, b)]
    hi, lo = sorted((luminance(f), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def passes(r, size="normal"):
    if size not in THRESHOLDS:
        raise ValueError("size must be one of %s, got %r" % (sorted(THRESHOLDS), size))
    return r >= THRESHOLDS[size]


def shown(r):
    """Truncate for display so a failing ratio never prints as the threshold (4.4999 -> 4.49)."""
    return math.floor(r * 100) / 100


def check_matrix(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("matrix must be a non-empty JSON array of {fg, bg, size?} objects")
    out = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("fg", "bg")):
            raise ValueError("row %d must be an object with string fg and bg" % i)
        size = row.get("size", "normal")
        r = ratio(row["fg"], row["bg"])
        out.append(dict(row, ratio=shown(r), required=THRESHOLDS.get(size), **{"pass": passes(r, size)}))
    return out


def main(argv):
    try:
        return run(argv)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


def run(argv):
    if argv[:1] == ["--matrix"]:
        if len(argv) != 2:
            print(__doc__)
            return 2
        with open(argv[1], encoding="utf-8") as f:
            rows = check_matrix(json.load(f))
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0 if all(r["pass"] for r in rows) else 1
    if len(argv) not in (2, 4) or (len(argv) == 4 and argv[2] != "--size"):
        print(__doc__)
        return 2
    size = argv[3] if len(argv) == 4 else "normal"
    r = ratio(argv[0], argv[1])
    print("%.2f:1 %s (%s, needs %.1f)" % (shown(r), "PASS" if passes(r, size) else "FAIL", size, THRESHOLDS[size]))
    return 0 if passes(r, size) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
