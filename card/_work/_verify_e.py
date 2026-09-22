# -*- coding: utf-8 -*-
"""批次 E 验证：世界书关键词注入是否按预期命中 / 不命中。"""
import sys, io, os

ROOT = r"E:\ai-love"                        # 项目根（card\ memory\ .env 都在这）
CODE = os.path.join(ROOT, "ai-Rafayel")     # 祁煜代码层（2026-09-17 从项目根搬入）
sys.path.insert(0, CODE)
sys.path.insert(0, os.path.join(CODE, "worldbook"))   # 世界书代码在子目录，得单独挂
import Rafayel_worldbook as WI

out = []
def p(*a):
    out.append(" ".join(str(x) for x in a))

wb = WI.WorldBook()
p("条目数（启用中）：", len(wb.entries))
p("order 范围：", wb.entries[0]["order"], "~", wb.entries[-1]["order"])
p("before/after 分布：",
  sum(1 for e in wb.entries if e["position"] == 0), "/",
  sum(1 for e in wb.entries if e["position"] == 1))
p(""

)

p("=== 1. 命中测试（应该命中）===")
CASES = [
    ("今天去白沙湾的画室找你", ["白沙湾"]),
    ("你的眼神颜色好特别", None),
    ("Mo Art Studio 在哪里啊", ["Mo Art Studio"]),
    ("你以前在那座城的事，能说说吗", None),
    ("听说你会吹口琴？", None),
    ("你怕猫这件事是真的吗", None),
    ("我该怎么叫你比较好", None),
    ("你的 Evol 是什么", ["Evol"]),
    ("嘉兰百合开了", None),
    ("你酒量好像不太好", None),
    # 批次 E 补录后应命中的（2026-09-15）
    ("金沙之海是什么地方", None),
    ("菲罗斯星上是什么样的", None),
    ("听说你当过潜行者", None),
    ("去猫咖打喵喵牌吗", None),
    ("喵喵币能抽什么", None),
    ("海洋干涸三万年后你还记得吗", None),
]
for text, expect_key in CASES:
    before, after = wb.build([], text)
    titles = [l.strip("- ").strip() for l in before.split("\n") if l.startswith("- ")]
    p("  输入：%s" % text)
    p("    命中：%s" % (titles if titles else "（无）"))
    if expect_key:
        keys = []
        for t in titles:
            keys.extend(WI.WorldBook.render and [])
    p("")

p("=== 2. 不该命中的输入（聊日常不该触发往事）===")
for text in ["今天吃什么呀", "我在加班好累", "天气不错", "晚安"]:
    before, after = wb.build([], text)
    titles = [l.strip("- ").strip() for l in before.split("\n") if l.startswith("- ")]
    p("  %-10s -> %s" % (text, titles if titles else "（无命中）"))

p("")
p("=== 3. 预算与条数上限 ===")
b1, _ = wb.build([], "白沙湾 画室 oyer", max_entries=1)
p("  max_entries=1 时命中条数：", b1.count("\n- "))
b2, _ = wb.build([], "白沙湾 画室 画家 眼神 法庭", max_chars=200)
p("  max_chars=200 时注入正文长度约：", len(b2))

p("")
p("=== 4. after_char 是否能单独取出 ===")
_, after = wb.build([], "以后我叫你什么比较好")
p("  after 段：", (after[:120] + "…") if after else "（无命中）")

p("")
p("=== 5. 大小写不敏感（拉丁触发词）===")
for t in ["mo art studio", "MO ART STUDIO", "Mo Art Studio"]:
    before, _ = wb.build([], t)
    titles = [l.strip("- ").strip() for l in before.split("\n") if l.startswith("- ")]
    p("  %-18s -> %s" % (t, titles[:1]))

p("")
p("=== 6. 历史也在扫描范围内（depth）===")
msgs = [{"role": "user", "content": "昨天我们说的那个地方"},
        {"role": "assistant", "content": "嗯。"},
        {"role": "user", "content": "我还是想再去一次白沙湾"}]
before, _ = wb.build(msgs, "你还记得吗")
titles = [l.strip("- ").strip() for l in before.split("\n") if l.startswith("- ")]
p("  历史含「白沙湾」-> ", titles)

io.open(r"F:\workB\JOB\lysk\_e2.txt", "w", encoding="utf-8").write("\n".join(out))
print("done")
