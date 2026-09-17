# -*- coding: utf-8 -*-
"""全库取证：祁煜「画家身份/天才程度/画坛地位」的一手表述都在哪些原文里。"""
import os, re

ROOT = r"E:\JOB"
OUT = os.path.join(r"E:\ai-love\card\_work", "_talent_out.txt")

WORDS = [
    "名声大噪", "声名大噪", "大噪", "前所未有", "天才", "才华", "天赋",
    "拍卖", "利兹", "纪录", "记录", "天价", "一画难求", "炙手可热",
    "轰动", "横空出世", "一夜成名", "一夜之间", "画坛", "艺术圈", "画界",
    "蜚声", "闻名", "举世", "知名", "登顶", "巅峰", "最年轻", "洛阳纸贵",
    "《幻》", "幻》", "成名", "走红", "爆红", "声名鹊起", "声名",
    "画家", "大师", "神作", "旷世", "惊世", "绝伦", "无价",
]

def walk():
    for dp, dn, fn in os.walk(ROOT):
        for f in fn:
            if f.lower().endswith(".txt"):
                yield os.path.join(dp, f)

def main():
    files = list(walk())
    hits = {w: [] for w in WORDS}
    for p in files:
        try:
            b = open(p, "rb").read()
        except Exception:
            continue
        try:
            t = b.decode("utf-8", errors="replace")
        except Exception:
            continue
        rel = os.path.relpath(p, ROOT)
        for w in WORDS:
            c = t.count(w)
            if c:
                for m in re.finditer(re.escape(w), t):
                    s = max(0, m.start() - 60)
                    e = min(len(t), m.end() + 60)
                    ctx = t[s:e].replace("\r", "").replace("\n", " / ")
                    hits[w].append((rel, ctx))
    out = []
    out.append("扫描文件数：%d" % len(files))
    out.append("")
    for w in WORDS:
        lst = hits[w]
        if not lst:
            out.append("【%s】 0 命中" % w)
            continue
        fileset = set(x[0] for x in lst)
        out.append("【%s】 %d 次 / %d 文件" % (w, len(lst), len(fileset)))
        for rel, ctx in lst[:8]:
            out.append("    (%s) ...%s..." % (rel, ctx))
        out.append("")
    open(OUT, "wb").write("\n".join(out).encode("utf-8"))
    print("done -> %s" % OUT)

main()
