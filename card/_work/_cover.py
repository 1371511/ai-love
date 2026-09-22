# -*- coding: utf-8 -*-
"""世界书覆盖度抽查：模拟用户真实问法，看命中情况。"""
import os, sys

ROOT = r"E:\ai-love"                        # 项目根（card\ memory\ .env 都在这）
CODE = os.path.join(ROOT, "ai-Rafayel")     # 祁煜代码层（2026-09-17 从项目根搬入）
sys.path.insert(0, CODE)
sys.path.insert(0, os.path.join(CODE, "worldbook"))   # 世界书代码在子目录，得单独挂
from Rafayel_worldbook import WorldBook

wb = WorldBook(os.path.join(ROOT, "card", "worldbook.json"))

CASES = [
    # (说法, 期望是否该命中)
    ("我该怎么叫你比较好", 1),
    ("你平时都怎么称呼我", 1),
    ("你怕猫这件事是真的吗", 1),
    ("你为什么那么怕猫", 1),
    ("你的画到底值多少钱", 1),
    ("利兹拍卖行", 1),
    ("你是什么时候成名的", 1),
    ("讲讲你的家乡", 1),
    ("利莫里亚是什么", 1),
    ("你是海神吗", 1),
    ("金沙之海是什么地方", 1),
    ("菲罗斯星在哪", 1),
    ("我们去猫咖吧", 1),
    ("喵呜徽章怎么抽", 1),
    ("唐知理是谁", 1),
    ("谭灵", 1),
    ("涂鸦叽", 1),
    ("你的 Evol 是什么", 1),
    ("芯核", 1),
    ("你在临空大学教什么", 1),
    ("你的生日", 0),          # 客观数值在卡里，世界书无条目属正常
    ("今天天气不错", 0),
    ("我想吃火锅", 0),
]

out = []
miss_expect = []
miss_none = []
for text, expect in CASES:
    hits = wb.match([], current_text=text)
    names = [h.get("comment", "?") for h in hits] if hits else []
    tag = "HIT " if names else "MISS"
    out.append("%-4s %-22s -> %s" % (tag, text, "、".join(names) if names else "—"))
    if expect and not names:
        miss_expect.append(text)
    if not expect and names:
        miss_none.append(text)

out.append("")
out.append("【该命中却没命中】%d 条：%s" % (len(miss_expect), " / ".join(miss_expect) or "无"))
out.append("【不该命中却命中】%d 条：%s" % (len(miss_none), " / ".join(miss_none) or "无"))

open(r"E:\ai-love\card\_work\_cover_out.txt", "wb").write("\n".join(out).encode("utf-8"))
print("done")
