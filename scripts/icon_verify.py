"""Identify PNG / ICO / ICNS files by their header, not their extension.

Usage: icon_verify.py FILE [FILE ...]
Exit: 0 clean, 1 findings (wrong format, empty or truncated), 2 usage or unreadable input.

Reports the real format and the pixel sizes it contains, so a PNG renamed to
.ico or a truncated .icns is caught before it ships.
"""
import struct
import sys
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"
# ICNS element types with a fixed pixel size; other types (TOC, icnV, ...) carry no image size
ICNS_SIZES = {
    b"is32": 16, b"icp4": 16, b"il32": 32, b"icp5": 32, b"ic11": 32, b"ih32": 48, b"icp6": 64,
    b"ic12": 64, b"it32": 128, b"ic07": 128, b"ic13": 256, b"ic08": 256, b"ic14": 512,
    b"ic09": 512, b"ic10": 1024,
}


def sniff(data):
    if data.startswith(PNG_SIG):
        if len(data) < 24 or data[12:16] != b"IHDR":
            return "png", [], ["truncated PNG header"]
        w, h = struct.unpack(">II", data[16:24])
        errors = [] if w and h else ["zero-sized PNG"]
        # ponytail: checks the file ends in IEND, not every chunk CRC; a renderer catches corrupt data
        if data[-8:-4] != b"IEND":
            errors.append("truncated PNG (no IEND chunk at end)")
        return "png", [(w, h)], errors
    if data[:4] == b"\x00\x00\x01\x00" and len(data) >= 6:
        count = struct.unpack("<H", data[4:6])[0]
        sizes, errors = [], [] if count else ["ICO contains no images"]
        data_start = 6 + 16 * count
        for i in range(count):
            entry = data[6 + 16 * i:22 + 16 * i]
            if len(entry) < 16:
                return "ico", sizes, ["directory truncated at entry %d" % i]
            w, h, _, _, _, _, size, offset = struct.unpack("<BBBBHHII", entry)
            if size == 0 or offset < data_start or offset + size > len(data):
                errors.append("image %d does not point at image data inside the file" % i)
            sizes.append((w or 256, h or 256))
        return "ico", sizes, errors
    if data[:4] == b"icns":
        declared = struct.unpack(">I", data[4:8])[0] if len(data) >= 8 else -1
        if declared != len(data):
            return "icns", [], ["declared length %d != file size %d" % (declared, len(data))]
        sizes, pos, count = [], 8, 0
        while pos < len(data):
            kind, length = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8].rjust(4, b"\0"))[0]
            if length < 8 or pos + length > len(data):
                return "icns", sizes, ["element %r at offset %d overruns the file" % (kind, pos)]
            if kind in ICNS_SIZES:
                if length == 8:
                    return "icns", sizes, ["element %r at offset %d is empty" % (kind, pos)]
                sizes.append((ICNS_SIZES[kind], ICNS_SIZES[kind]))
            pos, count = pos + length, count + 1
        return "icns", sizes, [] if count else ["ICNS contains no elements"]
    return "unknown", [], []


def verify(path):
    path = Path(path)
    fmt, sizes, errors = sniff(path.read_bytes())
    ext = path.suffix.lower().lstrip(".")
    if fmt == "unknown":
        errors = errors + ["not a PNG, ICO or ICNS file"]
    elif ext != fmt:
        errors = errors + ["extension .%s but content is %s" % (ext, fmt.upper())]
    return {"path": str(path), "format": fmt, "sizes": sizes, "errors": errors}


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    failed = False
    for p in argv:
        try:
            info = verify(p)
        except OSError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
        failed |= bool(info["errors"])
        sizes = " ".join("%dx%d" % s for s in info["sizes"])
        print("%s %s [%s] %s" % ("FAIL" if info["errors"] else "ok  ", p, info["format"], sizes))
        for msg in info["errors"]:
            print("     - " + msg)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
