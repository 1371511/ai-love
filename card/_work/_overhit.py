# -*- coding: utf-8 -*-
"""检查过泛的单字触发词会造成哪些误命中。"""
import os, sys, json
ROOT = r"E:\ai-love"                        # 项目根（card\ memory\ .env 都在这）
CODE = os.path.join(ROOT, "ai-Rafayel")     # 祁煜代码层（2026-09-17 从项目根搬入）
sys.path.insert(0, CODE)
sys.path.insert(0, os.path.join(CODE, "worldbook"))   # 世界书代码在子目录，得单独挂
from Rafayel_worldbook import WorldBook
wb = WorldBook(os.path.join(ROOT, "card", "worldbook.json"))

# 找出所有长度 <= 2 的触发词（容易误命中）
d = json.loads(open(os.path.join(ROOT, "card", "worldbook.json"), "rb").read().decode("utf-8-sig"))
e = d["entries"]; items = list(e.values()) if isinstance(e, dict) else e
short = []
for x in items:
    for k in (x.get("key") or []):
        if len(k.strip()) <= 1:
            short.append((x.get("comment"), k))

TESTS = [
    "我想吃火锅", "今天天气真热", "我有点上火", "火车晚点了",
    "你今天精神不错", "这部电影挺神话的", "别神神秘秘的", "他的眼神很温柔",
    "花园里的花开了", "我花了两个小时", "这束花好香", "花钱如流水",
    "鲸鱼很大", "回家的路",
    # 2026-09-15 补：「第一次见面（有两次）」新增的双字/口语触发词的误命中检查
    "这个演员看着好眼熟", "这首歌很耳熟", "你救过那只猫吗",
    "我小时候在这儿住过", "那时候见过一面的人早忘光了",
    "你们俩认识多久了", "明天再来玩啊",
    # 2026-09-17 补：「他先把你推出危险」的双字/口语触发词
    "别靠近那条狗", "这儿危险别靠近", "他跟我说了句与我无关",
    "海里有很多鱼", "船在海上漂着",
    # 2026-09-17 补：「珊瑚石与那趟委托」
    "这块石头真好看", "找个人替我跑一趟",
]

out = ["【长度<=1 的触发词】"]
for c, k in short:
    out.append("  %s -> 「%s」" % (c, k))
out.append("")
out.append("【日常句的误命中检查】")
for t in TESTS:
    hits = wb.match([], current_text=t)
    names = [h.get("comment", "?") for h in hits] if hits else []
    out.append("  %-14s -> %s" % (t, "、".join(names) if names else "—"))

open(r"E:\ai-love\card\_work\_overhit_out.txt", "wb").write("\n".join(out).encode("utf-8"))
print("done")
