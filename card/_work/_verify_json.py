# -*- coding: utf-8 -*-
"""校验「世界书拆分」后生成的 JSON 是否保真（2026-09-17）。

判据：除「顺序相关字段」外，**逐条零变化**。
  · V1 `worldbook.json`：允许 order / group / uid / displayIndex 变
  · V2 卡内 `character_book.entries`：允许 insertion_order / id 变
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "_baseline")
CARD = os.path.dirname(HERE)

ALLOW_V1 = {"order", "group", "uid", "displayIndex"}
# ⚠ V2 的 `comment` 存的是**组名**（不是条目名），随分组调整而变
ALLOW_V2 = {"insertion_order", "id", "displayIndex", "uid", "comment"}
DELTA = {200: 400, 205: 400, 210: 400, 215: 400}

# 组名的预期映射（旧 → 新）
GROUP_MAP = {
    "组 1 · 过往 · 地球的流浪与假身份": "组 6 · 地球 · 流浪与假身份",
    "组 2 · 利莫里亚的覆灭": "组 2 · 利莫里亚 · 覆灭",
    "组 3 · 金沙时期（菲罗斯星 · 三万年后）": "组 3 · 利莫里亚 · 金沙时期（菲罗斯星 · 三万年后）",
    "组 4 · 罗镜城时期（菲罗斯星 · 万年后）": "组 4 · 利莫里亚 · 罗镜城时期（菲罗斯星 · 万年后）",
    "组 5 · 起源 · 鲸落城与海神祭典（菲罗斯星 · 最早）":
        "组 5 · 利莫里亚 · 起源 · 鲸落城与海神祭典（菲罗斯星 · 最早）",
    "组 6 · IF 线（极少提及，压到最后）": "组 7 · IF 线（极少提及，压到最后）",
    "组 7 · 称呼（行为规则类，放在角色设定之后）": "组 8 · 称呼（行为规则类，放在角色设定之后）",
}

out = []
bad = 0


def p(*a):
    out.append(" ".join(str(x) for x in a))


def fail(m):
    global bad
    bad += 1
    p("  [FAIL] " + m)


def load(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def cmp_entries(bmap, nmap, allow, label, check_order=False):
    p("")
    p("【%s】" % label)
    if set(bmap) != set(nmap):
        fail("条目名集合不同：缺 %s / 多 %s"
             % (sorted(set(bmap) - set(nmap)), sorted(set(nmap) - set(bmap))))
        return
    p("  [OK] %d 条，名字集合一致" % len(nmap))

    diffs = []
    for name, n in nmap.items():
        b = bmap[name]
        for k in sorted(set(b) | set(n)):
            if k in allow:
                continue
            if b.get(k) != n.get(k):
                diffs.append("%s . %s\n      基准 %r\n      新源 %r" % (name, k, b.get(k), n.get(k)))
    if diffs:
        for d in diffs:
            fail(d)
    else:
        p("  [OK] 除 %s 外全部字段一致 —— **零变化**" % " / ".join(sorted(allow)))

    if check_order:
        od, changed = [], []
        for name, n in nmap.items():
            b = bmap[name]
            exp = b["order"] + DELTA.get(b["order"], 0)
            if n["order"] != exp:
                od.append("%s：%s → %s（期望 %s）" % (name, b["order"], n["order"], exp))
            elif b["order"] != n["order"]:
                changed.append("%s %s→%s" % (name, b["order"], n["order"]))
        if od:
            for d in od:
                fail(d)
        else:
            p("  [OK] order 符合预期；实际改动 %d 条：%s"
              % (len(changed), "，".join(changed) or "无"))


p("=" * 70)
p("世界书拆分 · JSON 保真校验")
p("=" * 70)

def v1_entries(d):
    """worldbook.json 可能是 {"entries": {...}}，也可能直接是 {"0": {...}}。"""
    return d["entries"] if (isinstance(d, dict) and "entries" in d) else d


b_wb = v1_entries(load(os.path.join(BASE, "worldbook.json")))
n_wb = v1_entries(load(os.path.join(CARD, "worldbook.json")))
cmp_entries({v["comment"]: v for v in b_wb.values()},
            {v["comment"]: v for v in n_wb.values()},
            ALLOW_V1, "V1 worldbook.json（独立世界书）", check_order=True)

b_card = load(os.path.join(BASE, "Rafayel.character.json"))
n_card = load(os.path.join(CARD, "Rafayel.character.json"))
if "character_book" not in n_card:
    fail("新卡里没有 character_book")
else:
    b_v2 = {e["name"]: e for e in b_card["character_book"]["entries"]}
    n_v2 = {e["name"]: e for e in n_card["character_book"]["entries"]}
    cmp_entries(b_v2, n_v2, ALLOW_V2, "V2 卡内 character_book.entries")

    p("")
    p("【V2 comment（组名）是否符合预期映射】")
    gd = []
    for name, n in n_v2.items():
        bc = b_v2[name].get("comment")
        exp = GROUP_MAP.get(bc, bc)
        if n.get("comment") != exp:
            gd.append("%s：%r → %r（期望 %r）" % (name, bc, n.get("comment"), exp))
    if gd:
        for d in gd:
            fail(d)
    else:
        p("  [OK] 44 条组名全部符合预期映射")

p("")
p("【卡内 character_book 顶层字段】")
for k in sorted(set(b_card["character_book"]) | set(n_card["character_book"])):
    if k == "entries":
        continue
    bv, nv = b_card["character_book"].get(k), n_card["character_book"].get(k)
    if bv == nv:
        p("  [OK] %s 相同" % k)
    else:
        p("  [变化] %s：%r → %r" % (k, bv, nv))

p("")
p("【角色卡其余字段】")
for k in sorted(set(b_card) | set(n_card)):
    if k == "character_book":
        continue
    if b_card.get(k) != n_card.get(k):
        fail("字段 %s 变化" % k)
if bad == 0:
    p("  [OK] 除 character_book 外全部一致")

p("")
p("=" * 70)
p("结论：%s" % ("全部通过 ✅" if bad == 0 else "有 %d 项失败 ❌" % bad))
p("=" * 70)

io.open(os.path.join(r"F:\workB\JOB", "_vj.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done bad=%d" % bad)
