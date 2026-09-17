# -*- coding: utf-8 -*-
"""把单一 `worldbook.md` 拆成 `worldbook/` 目录下的多文件（一次性，2026-09-17）。

保真原则
--------
· 条目块按**行原样搬运**，不重新格式化、不重排字段；
· 只改两处：① 地球流浪组的 order（200–215 → 600–615）② 组标题文字。

分组依据（小辞 2026-09-17 定）
--------
利莫里亚 = 覆灭 + 金沙 + 罗镜城 + 鲸落城四个连续文件，提到「地球流浪」之前；
地球流浪降级到最后（逸闻闲聊级，游戏描写极少）；新增「与用户的相遇」占位组。

用法：python _split_worldbook.py [--dry]
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "worldbook.md")
DST = os.path.join(HERE, "worldbook")

ENTRY_RE = re.compile(r"^###\s+(.*)$")
GROUP_RE = re.compile(r"^##\s+(.*)$")
ORDER_RE = re.compile(r"^(-?\s*order\s*:\s*)(\d+)(\s*)$")

# (文件名, 新组标题, [旧 order...], order 增量)
PLAN = [
    ("10_present-places-people.md", "组 0 · 现在 · 临空市与当下",
     [100, 102, 104, 105, 106, 107, 110, 115, 120, 125, 130], 0),
    ("12_present-objects-toys.md", "组 0 · 现在 · 临空市与当下",
     [135, 140, 145, 150, 155, 160, 163], 0),
    ("14_present-worldview.md", "组 0 · 现在 · 临空市与当下",
     [165, 170, 175, 180, 185, 190], 0),
    ("20_encounters-with-user.md", "组 1 · 现在 · 与用户的相遇与事件", [], 0),
    ("30_lemuria-fall.md", "组 2 · 利莫里亚 · 覆灭",
     [300, 305, 310, 315, 320, 325], 0),
    ("40_lemuria-golden-sea.md", "组 3 · 利莫里亚 · 金沙时期（菲罗斯星 · 三万年后）",
     [400, 430], 0),
    ("50_lemuria-mirror-city.md", "组 4 · 利莫里亚 · 罗镜城时期（菲罗斯星 · 万年后）",
     [450, 455, 460, 465], 0),
    ("60_lemuria-whalefall.md", "组 5 · 利莫里亚 · 起源 · 鲸落城与海神祭典（菲罗斯星 · 最早）",
     [500], 0),
    ("70_earth-wanderer.md", "组 6 · 地球 · 流浪与假身份",
     [200, 205, 210, 215], 400),
    ("90_if-line.md", "组 7 · IF 线（极少提及，压到最后）",
     [900], 0),
    ("99_forms-of-address.md", "组 8 · 称呼（行为规则类，放在角色设定之后）",
     [990, 995], 0),
]

PLACEHOLDER_NOTE = {
    "20_encounters-with-user.md": (
        "> **占位文件** —— 这一组留给「你在临空市与她经历的各种相遇与事件」，"
        "等小辞整理后填入。\n"
        "> 说明：本文件没有 `### 条目`，解析时**不产生任何世界书条目**，可安全保留。\n"
        "> 新增条目直接接在下面，order 落在 **200–299** 区间。\n"
    ),
    "90_if-line.md": (
        "> 目前只收录了**武神线**；其余 IF 线待小辞整理后补入本文件。\n"
    ),
}


def read_src():
    with io.open(SRC, "r", encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n").replace("\r", "\n").split("\n")


def split_chunks(lines):
    """切成块：('header'|'group'|'entry', 名字, [行...])。"""
    chunks = []
    kind, name, buf = "header", "", []
    for line in lines:
        m_e, m_g = ENTRY_RE.match(line), GROUP_RE.match(line)
        if m_e or m_g:
            chunks.append((kind, name, buf))
            kind = "entry" if m_e else "group"
            name = (m_e or m_g).group(1).strip()
            buf = [line]
        else:
            buf.append(line)
    chunks.append((kind, name, buf))
    return chunks


def block_order(blk):
    for line in blk:
        m = ORDER_RE.match(line)
        if m:
            return int(m.group(2))
    return None


def clean_tail(blk):
    """剥掉块尾的空行与 md 分离线（md2card 本来也会剥，这里让文件更干净）。"""
    b = list(blk)
    while b and b[-1].strip() in ("", "---", "----", "-----"):
        b.pop()
    return b


def renumber(blk, new_order):
    out = []
    for line in blk:
        m = ORDER_RE.match(line)
        out.append(m.group(1) + str(new_order) + m.group(3) if m else line)
    return out


def main():
    dry = "--dry" in sys.argv
    lines = read_src()
    chunks = split_chunks(lines)

    entry_blocks = {}
    for kind, name, blk in chunks:
        if kind == "entry":
            o = block_order(blk)
            if o is None:
                raise SystemExit("[FAIL] 条目「%s」没有 order 行" % name)
            if o in entry_blocks:
                raise SystemExit("[FAIL] order 重复：%d" % o)
            entry_blocks[o] = blk

    total = sum(len(v) for v in entry_blocks.values())
    print("[读入] %s：%d 个条目" % (os.path.basename(SRC), len(entry_blocks)))
    if len(entry_blocks) != 44:
        raise SystemExit("[FAIL] 期望 44 个条目，实际 %d 个" % len(entry_blocks))

    planned = [o for _, _, os_, _ in PLAN for o in os_]
    if sorted(planned) != sorted(entry_blocks):
        miss = set(entry_blocks) - set(planned)
        extra = set(planned) - set(entry_blocks)
        raise SystemExit("[FAIL] 分配表与 md 不符：漏 %s / 多 %s" % (sorted(miss), sorted(extra)))

    if dry:
        for fname, gtitle, orders, delta in PLAN:
            print("  %-32s %2d 条  order %s" % (fname, len(orders), gtitle))
        print("[dry] 未写文件")
        return

    if not os.path.isdir(DST):
        os.makedirs(DST)

    for fname, gtitle, orders, delta in PLAN:
        parts = ["## " + gtitle, ""]
        note = PLACEHOLDER_NOTE.get(fname)
        if note:
            parts += note.rstrip("\n").split("\n") + [""]
        for o in orders:
            parts += renumber(clean_tail(entry_blocks[o]), o + delta)
            parts.append("")
        text = "\n".join(parts).rstrip("\n") + "\n"
        with io.open(os.path.join(DST, fname), "wb") as f:
            f.write(text.replace("\n", "\r\n").encode("utf-8"))
        print("  [写] %-32s %2d 条" % (fname, len(orders)))

    print("[OK] 已写入 %s" % DST)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
