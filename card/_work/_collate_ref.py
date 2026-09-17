# -*- coding: utf-8 -*-
"""
世界书 ↔ 深空百科（官方设定词典）对照。

与 `_collate.py` 的区别：
  `_collate.py` 对的是**剧情素材**（有对话/旁白/男主标注，找「漏收录」和「记错出处」）
  本脚本对的是**官方设定词典**（找「我们自己写的口径和官方不一致」）

深空百科的性质：游戏内百科词条，条目短、定义性强、含确切年份与数字，
是核对世界书里世界观陈述（Evol / 芯核 / EVER / 猎人协会 / 临空市 …）的一手依据。

用法：直接跑，产出 F:\\workB\\JOB\\lysk\\_collate_ref_out.txt
"""
import os, re, io, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wb_io

BK = r"C:\Users\lin\Desktop\祁煜剧情\5深空百科"
# 2026-09-17 起：世界书已拆成目录，读目录（_wb_io 按文件名排序拼接，排除 _ 前缀）
WB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worldbook")
OUT = r"F:\workB\JOB\lysk\_collate_ref_out.txt"

PUNCT = (r"[，。！？、；：""''"
         "\u201c\u201d\u2018\u2019"
         r"（）()《》「」〈〉\[\]<>·—…\-~,.\!?;:'\"\s]")


def norm(s):
    if not s:
        return ""
    s = s.replace("\u00a0", " ")
    out = []
    for c in s:
        o = ord(c)
        if 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        elif c == "\u3000":
            continue
        else:
            out.append(c)
    return re.sub(PUNCT, "", "".join(out))


# ---------------- 读百科 ----------------
bk = []          # (分类, 词条名, 全文, 全文norm)
for dp, dn, fns in os.walk(BK):
    for fn in sorted(fns):
        if not fn.lower().endswith(".txt"):
            continue
        fp = os.path.join(dp, fn)
        rel = os.path.relpath(fp, BK)
        cat = rel.split(os.sep)[0] if os.sep in rel else ""
        raw = open(fp, "rb").read().decode("utf-8-sig", errors="replace").strip()
        name = re.sub(r"^\d+", "", fn[:-4]).strip()
        bk.append((cat, name, raw, norm(raw)))

# ---------------- 读世界书 ----------------
wb_raw = _wb_io.read_text(WB_DIR)
entries = []
for part in re.split(r"(?m)^### ", wb_raw)[1:]:
    lines = part.split("\n")
    name = lines[0].strip()
    if name.startswith("x-"):
        continue
    keys, src, body = [], "", []
    for ln in lines[1:]:
        s = ln.strip()
        low = s.lower()
        if low.startswith("keys:"):
            keys = [k.strip() for k in s[5:].split(",") if k.strip()]
        elif low.startswith("source:"):
            src = s[7:].strip()
        elif low.startswith(("order:", "secondary_keys:", "constant:",
                             "selective:", "position:")):
            continue
        else:
            body.append(ln)
    btxt = "\n".join(body).strip()
    entries.append({"name": name, "keys": keys, "source": src,
                    "body": btxt, "all": name + " " + " ".join(keys) + " " + btxt})

wb_all = norm("\n".join(e["all"] for e in entries))

out = []
out.append("世界书 ↔ 深空百科（官方设定词典）对照")
out.append("百科：%s" % BK)
out.append("百科词条 %d 个 / 世界书条目 %d 条" % (len(bk), len(entries)))
out.append("")

# =========================================================
# 1. 每个百科词条 → 世界书里有没有对应条目
# =========================================================
out.append("=" * 74)
out.append("【1】百科词条 → 世界书对应情况")
out.append("  ✅ 世界书里有 / ❗ 世界书完全没有")
out.append("")

hit, miss = [], []
for cat, name, raw, nraw in bk:
    matched = [e["name"] for e in entries
               if norm(name) and norm(name) in norm(e["all"])]
    (hit if matched else miss).append((cat, name, raw, matched))

for cat, name, raw, matched in hit:
    out.append("  ✅ [%s] %-16s → 世界书：%s" % (cat, name, "、".join(matched[:3])))
out.append("")
out.append("  --- 世界书完全没有的词条（%d 个）---" % len(miss))
for cat, name, raw, _ in miss:
    out.append("  ❗ [%s] %s" % (cat, name))

out.append("")
out.append("  百科 %d 条：有对应 %d / 无对应 %d" % (len(bk), len(hit), len(miss)))

# =========================================================
# 2. 有对应的词条：两边并排，人工比对口径
# =========================================================
out.append("")
out.append("=" * 74)
out.append("【2】有对应的词条：百科原文 vs 世界书正文（人工比对口径用）")
out.append("")
for cat, name, raw, matched in hit:
    out.append("")
    out.append("▸ %s / %s      → 世界书「%s」" % (cat, name, matched[0]))
    out.append("  【百科】")
    for ln in [x.strip() for x in raw.split("\n") if x.strip()]:
        out.append("      %s" % ln)
    e = next(e for e in entries if e["name"] == matched[0])
    out.append("  【世界书】")
    for ln in [x.strip() for x in e["body"].split("\n") if x.strip()]:
        out.append("      %s" % (ln[:100] + ("…" if len(ln) > 100 else "")))

# =========================================================
# 3. 反向：百科里的关键设定，世界书有没有（按「百科有、世界书搜不到」的子串）
# =========================================================
out.append("")
out.append("=" * 74)
out.append("【3】百科里的定义性短句，世界书里搜不到的（可能漏收录 / 口径不同）")
out.append("")

def terms(raw, name):
    """百科格式是「标题行 + 说明段」，短行即官方术语名。加上引号/书名号内的词。"""
    res = [name]
    for ln in raw.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        # 子标题：短、不以句号结尾
        if len(ln) <= 14 and not ln.endswith(("。", "！", "？", ".")):
            res.append(ln.strip("《》"))
        # 引号 / 书名号内的词
        for a, b in (("《", "》"), ("“", "”"), ("「", "」")):
            for w in re.findall(re.escape(a) + r"([^" + re.escape(a + b) + r"]{1,20})" + re.escape(b), ln):
                res.append(w.strip())
    return [x for x in res if len(x) >= 2]


gaps = Counter()
where = {}
for cat, name, raw, nraw in bk:
    for t in terms(raw, name):
        nt = norm(t)
        if not nt or nt in wb_all:
            continue
        gaps[t] += 1
        where.setdefault(t, "%s/%s" % (cat, name))

out.append("  --- 百科官方术语，世界书里搜不到的（漏收录候选）---")
for t, c in gaps.most_common(60):
    out.append("  %-30s [%s]" % (t, where[t]))
out.append("")
out.append("  百科术语 %d 个 / 世界书里搜不到 %d 个" % (len(gaps), sum(gaps.values())))

# =========================================================
# 4. 世界书里的年份数字，百科里有没有（防编造数字）
# =========================================================
out.append("")
out.append("=" * 74)
out.append("【4】世界书里出现的年份 / 数量，回百科核对")
out.append("")

bk_n = norm("\n".join(x[2] for x in bk))
years = Counter()
for e in entries:
    for m in re.finditer(r"(19|20)\d{2}", e["all"]):
        years[(e["name"], m.group(0))] += 1
for (nm, y), c in sorted(years.items()):
    out.append("  %-6s [%s]  百科里出现：%s" % (y, nm, "是" if y in bk_n else "否"))

io.open(OUT, "w", encoding="utf-8").write("\n".join(out))
print("done -> %s (%d 行)" % (OUT, len(out)))
