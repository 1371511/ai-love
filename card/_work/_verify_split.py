# -*- coding: utf-8 -*-
"""校验「世界书拆分」是否保真（2026-09-17）。

判据：
  ① 条目集合一致（44 条，名字集合相同）
  ② 每条 content / keys / constant / selective / position / secondary_keys **完全相同**
  ③ order 按预期映射（地球流浪组 +400，其余不变）
  ④ 拼接后的解析顺序 = order 递增（审计的单调性不变式）
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import md2card as M          # noqa: E402
import _wb_io                # noqa: E402

BASELINE = os.path.join(HERE, "_baseline", "worldbook.md")
TMP = os.path.join(HERE, "_tmp_concat.md")

# 允许变化的字段（并给出预期规则）
DELTA = {200: 400, 205: 400, 210: 400, 215: 400}

out = []
bad = 0


def p(*a):
    out.append(" ".join(str(x) for x in a))


def fail(msg):
    global bad
    bad += 1
    p("  [FAIL] " + msg)


p("=" * 70)
p("世界书拆分保真校验")
p("  基准：%s" % BASELINE)
p("  新源：%s（%d 个文件）" % (_wb_io.WB_DIR, len(_wb_io.list_files())))
p("=" * 70)

# 拼接新目录
concat = _wb_io.read_text()
with io.open(TMP, "w", encoding="utf-8", newline="\n") as f:
    f.write(concat)

try:
    base = M.parse_worldbook(BASELINE)
    new = M.parse_worldbook(TMP)
finally:
    if os.path.exists(TMP):
        os.remove(TMP)

p("")
p("【1】条目集合")
if len(base) == len(new) == 44:
    p("  [OK] 基准 44 条 / 新源 44 条")
else:
    fail("基准 %d 条 / 新源 %d 条（期望都是 44）" % (len(base), len(new)))

bn = [e["_name"] for e in base]
nn = [e["_name"] for e in new]
if set(bn) != set(nn):
    fail("名字集合不同：缺 %s / 多 %s" % (sorted(set(bn) - set(nn)), sorted(set(nn) - set(bn))))
else:
    p("  [OK] 44 个条目名集合完全一致")

bmap = {e["_name"]: e for e in base}

p("")
p("【2】逐条内容比对（content / keys / constant / selective / position / secondary_keys）")
diff = []
for e in new:
    b = bmap.get(e["_name"])
    if b is None:
        continue
    if b["_content_text"] != e["_content_text"]:
        diff.append("%s：正文不同（基准 %d 字 / 新 %d 字）"
                    % (e["_name"], len(b["_content_text"]), len(e["_content_text"])))
    if b["keys"] != e["keys"]:
        diff.append("%s：keys 不同\n      基准 %s\n      新源 %s" % (e["_name"], b["keys"], e["keys"]))
    if b["secondary_keys"] != e["secondary_keys"]:
        diff.append("%s：secondary_keys 不同" % e["_name"])
    for f in ("constant", "selective", "position"):
        if b[f] != e[f]:
            diff.append("%s：%s 不同（%s → %s）" % (e["_name"], f, b[f], e[f]))
if diff:
    for d in diff:
        fail(d)
else:
    p("  [OK] 44 条全部一致，**内容零变化**")

p("")
p("【3】order 映射（允许变化，需符合预期）")
od = []
for e in new:
    b = bmap.get(e["_name"])
    if b is None:
        continue
    exp = b["order"] + DELTA.get(b["order"], 0)
    if e["order"] != exp:
        od.append("%s：order %d → %d（期望 %d）" % (e["_name"], b["order"], e["order"], exp))
if od:
    for d in od:
        fail(d)
else:
    changed = [(bmap[e["_name"]]["order"], e["order"]) for e in new
               if bmap.get(e["_name"]) and bmap[e["_name"]]["order"] != e["order"]]
    p("  [OK] order 全部符合预期；实际改动 %d 条：%s"
      % (len(changed), ", ".join("%d→%d" % c for c in changed) or "无"))

p("")
p("【4】拼接顺序单调性（审计不变式）")
seq = [(e["_name"], e["order"]) for e in new]
bad_seq = [seq[i][0] for i in range(1, len(seq)) if seq[i][1] < seq[i - 1][1]]
if bad_seq:
    fail("以下条目排在前一条之后却 order 更小：%s" % "、".join(bad_seq))
else:
    p("  [OK] %d 条严格按 order 递增排列（%d → %d）" % (len(seq), seq[0][1], seq[-1][1]))

p("")
p("【5】group 字段变化（预期内，只报告不判错）")
gmap = {}
for e in new:
    b = bmap.get(e["_name"])
    if b and b.get("_group") != e.get("_group"):
        gmap.setdefault("%s → %s" % (b.get("_group"), e.get("_group")), 0)
        gmap["%s → %s" % (b.get("_group"), e.get("_group"))] += 1
for k, v in sorted(gmap.items()):
    p("  · %s  (%d 条)" % (k, v))
if not gmap:
    p("  · 无变化")

p("")
p("=" * 70)
p("结论：%s" % ("全部通过 ✅" if bad == 0 else "有 %d 项失败 ❌" % bad))
p("=" * 70)

io.open(os.path.join(r"F:\workB\JOB", "_vs.txt"), "w", encoding="utf-8").write("\n".join(out))
print("done bad=%d" % bad)
