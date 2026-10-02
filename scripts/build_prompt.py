"""Assemble SKILL.md and all references into one paste-able prompt.

Usage: build_prompt.py [OUT]   # default dist/brand-system-prompt.md

For chat tools that cannot load a skill directory. Sections from SKILL.md and
references/ are merged back into section-number order (appendices last); the
bundled scripts are not available in that mode.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTE = (
    "> 单文件版，由 `scripts/build_prompt.py` 从 skill 源生成，不要手改。\n"
    "> 下文已按章节号包含全部 references，忽略“按阶段读取 references”的说明；"
    "`<skill>/scripts/` 在此模式下不可用，相应检查需自行实现并在报告里注明方法。"
)
SECTION = re.compile(r"(?m)^(?=## )")


def section_key(block):
    m = re.match(r"## (?:(\d+)|附录 ([A-Z]))", block)
    if not m:
        raise ValueError("every ## heading must be numbered or an appendix: %r" % block.split("\n", 1)[0])
    return (0, int(m.group(1)), "") if m.group(1) else (1, 0, m.group(2))


def sections(text):
    return [re.sub(r"\n-{3,}\s*$", "", b.rstrip()) for b in SECTION.split(text) if b.startswith("## ")]


def build():
    skill = re.sub(r"\A---\n.*?\n---\n+", "", (ROOT / "SKILL.md").read_text(encoding="utf-8"), flags=re.S)
    preamble = SECTION.split(skill, 1)[0].rstrip()
    preamble = re.sub(r"\n-{3,}\s*$", "", preamble)
    title, rest = preamble.split("\n", 1)
    blocks = sections(skill)
    for ref in sorted((ROOT / "references").glob("*.md")):
        blocks += sections(ref.read_text(encoding="utf-8"))
    head = title + "\n\n" + NOTE + "\n" + rest.rstrip()
    return "\n\n---\n\n".join([head] + sorted(blocks, key=section_key)) + "\n"


def main(argv):
    if len(argv) > 1 or argv[:1] and argv[0].startswith("-"):
        print(__doc__)
        return 2
    out = Path(argv[0]) if argv else ROOT / "dist" / "brand-system-prompt.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
