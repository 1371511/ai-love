# -*- coding: utf-8 -*-
"""
批次 B 退出条件审计：对 Rafayel.character.json 做检查，输出 `_校验报告_批次B.txt`。

检查项
-----
1. JSON 可 json.loads；7 个字段齐全（含 character_book）
2. character_book 条目数与 worldbook.json 一致
3. description / personality 里没有`**`、没有 ASCII 双引号（会被原样注入提示词）
4. description 不出现剧情结论句（结局性剧透）
5. 长度预算：description ≤ 3000 字；personality ≤ 2600 字
   ⭐ 2026-10-03：personality 1500 → 2600（小辞裁定）。它每轮都注入，卡在 1500 等于逼着删设定。
6. 禁用词不得回流（深海珊瑚红 / 繁溪镇 / 潮汐之日 / 祁画师 / 臭鱼 / 潜行者 / 小辞）
7. 通用称呼词不得作为"可随口叫的"出现（老婆 / 傻瓜 / 小笨蛋 / 小朋友）
8. 旧卡的「在意过去的你超过现在的你」不得出现
9. 外貌关键项核对：短发（临空形态）在前、长发（海神形态）在后、蓝粉撞色瞳、珊瑚红
10. 出场顺序：description 里「现在」段必须早于「来历」段
11. personality 必须保持「分层」结构（对外人 / 对作品 / 对你 / 反差 / 底色 五层齐备）
12. 无素材支撑的旧卡表述不得回流（不喜欢别人触碰 / 讨厌人多的吵闹 / 耳朵尖 / 反撩 / 小剧场 …）
13. 修订四：对作品段核心必须是「独一无二」（不是「完美」）；骨螺红的事实表述必须正确
14. 修订五：IF 线（武神 / 赤霄将军）不得写进 description（只在世界书 order 900 条目里）
"""
import os, re, io, json, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CARD = os.path.dirname(HERE)
CARD_JSON = os.path.join(CARD, "Rafayel.character.json")
WB_JSON = os.path.join(CARD, "worldbook.json")
REPORT = os.path.join(CARD, "_校验报告_批次C.txt")

PLOT_SPOILER = [
    "心脏曾被人类骗走", "心脏被骗走", "骗走了他的心脏",
    "沉眠于最深的海底", "燃烧自己的心", "进入沉眠",
]
BANNED = ["深海珊瑚红", "繁溪镇", "潮汐之日", "祁画师", "臭鱼", "小辞",
          "发质偏硬"]  # 「发质偏硬」：小辞 2026-09-14 裁定删除，禁止回流
# ⚠ 「潜行者」已从 BANNED 移出（2026-09-15）：它是**利莫里亚人的泛称**，
#    作为「他的专属称呼」确实不妥，但作为**金沙时期他的身份**是有素材出处的
#    （金沙之海 07「我早就猜到祁煜是潜行者」、03「潜行者都是利莫里亚人」）。
#    ⇒ 改成「按条目」检查：称呼/别名类条目里不许出现，其余条目允许。
BANNED_IN_ALIAS = ["潜行者"]
BANNED_ADDR = ["老婆", "傻瓜", "小笨蛋", "小朋友"]
OLD_BAD = ["在意过去的", "超过了现在的"]
# 旧卡里"没有素材支撑"的表述 —— 2026-09-14 修订三 / 修订四 回核后改写的，禁止回流
UNSOURCED_AVOID = [
    # 修订三
    "不喜欢别人触碰", "讨厌人多的吵闹",
    # 修订四：素材 0 命中或语境不符
    "耳朵尖", "反撩一句", "小剧场", "戏精",
    # 修订四：出处/事实问题
    "三岁生日",            # 素材是「我们一起给它过生日的寄居蟹」，没有"三岁"
    "为了一个红紫色",      # 逸闻01：颜色名是「骨螺红」，且原文强调它「和酒红紫完全不是一个颜色」
    "破损也是它的一部分",  # 只在百科口径里出现，素材无出处；且朝汐屿写的是「画作会破损，渺小的努力难以抵御时间」
    "因为过度使用",        # 光坠其间：成因是熬夜两天，且医生说可能反复发作／永久性失明
]

problems = []
stats = {}

with io.open(CARD_JSON, "r", encoding="utf-8") as f:
    card = json.load(f)
stats["字段数"] = len(card)
stats["字段"] = "、".join([k for k in card.keys()])

with io.open(WB_JSON, "r", encoding="utf-8") as f:
    wb = json.load(f)
wb_n = len(wb["entries"])

# ⭐ 2026-10-03：世界书全文（comment + content + key），供「人设或世界书任一命中」类判据使用。
#    背景：小辞裁定「世界书已有专条的，人设不再重复写」⇒ 只查人设会把有意的去重误报成丢失。
#    但注意两者注入机制不同：人设每轮必注入，世界书靠 key 命中才注入 —— 去重的代价是「他不会主动提」。
def _wb_text():
    out = []
    for e in (wb.get("entries") or {}).values():
        if not isinstance(e, dict):
            continue
        out.append(str(e.get("comment") or ""))
        out.append(str(e.get("content") or ""))
        out.extend(str(k) for k in (e.get("key") or []))
    return "\n".join(out)

WB_TEXT = _wb_text()
cb = card.get("character_book") or {}
cb_n = len(cb.get("entries") or [])
stats["character_book 条目"] = cb_n
if cb_n != wb_n:
    problems.append("[P0] character_book %d 条 ≠ worldbook.json %d 条" % (cb_n, wb_n))

desc = card.get("description") or ""
pers = card.get("personality") or ""
scen = card.get("scenario") or ""
stats["description 字数"] = len(desc)
stats["personality 字数"] = len(pers)
stats["scenario 字数"] = len(scen)

if not desc.strip():
    problems.append("[P0] description 为空")
if not pers.strip():
    problems.append("[P0] personality 为空")

if len(desc) > 3000:
    problems.append("[P1] description 超预算：%d 字 > 3000" % len(desc))
# ⭐ 2026-10-03：1500 → 2600（小辞裁定放宽，理由见文件头第 5 条）
PERS_MAX_CHARS = 2600
if len(pers) > PERS_MAX_CHARS:
    problems.append("[P1] personality 超预算：%d 字 > %d" % (len(pers), PERS_MAX_CHARS))

for label, txt in (("description", desc), ("personality", pers), ("scenario", scen)):
    if "**" in txt:
        problems.append("[P1] %s 里有 markdown 粗体 `**`" % label)
    if '"' in txt:
        problems.append("[P1] %s 里有 ASCII 双引号" % label)

whole = desc + "\n" + pers + "\n" + scen
for w in PLOT_SPOILER:
    if w in whole:
        problems.append("[P0] 出现剧情结论句：`%s`" % w)
for w in BANNED:
    if w in whole:
        problems.append("[P1] 出现禁用词：`%s`" % w)
for w in BANNED_ADDR:
    if w in desc:
        problems.append("[P1] description 里出现受控称呼词：`%s`" % w)
for w in OLD_BAD:
    if w in whole:
        problems.append("[P1] 旧卡错误表述回流：`%s`" % w)
for w in UNSOURCED_AVOID:
    if w in whole:
        problems.append("[P1] 无素材支撑的旧卡表述回流：`%s`" % w)

# personality 分层结构（2026-09-14 修订三：对外人 / 对作品 / 对你 / 反差 / 底色 五层必须齐备）
# ⭐ 2026-10-03：小辞重写 personality，改成小标题句写法，不再用 `XX——` 分段标记。
#    ⇒ 判据从「字面分段标记」放宽为「同义组任一命中」：保护不变（整层被删照样报），
#      只是不再逼人把已经换掉的排版格式加回来。
PERS_LAYERS = [
    ("对外人", ("对外人——", "对陌生人", "礼貌而疏离", "距离与边界")),
    ("对作品", ("对作品——", "对作品有", "独一无二")),
    ("对你",   ("对你——", "对你尤其", "对恋人", "面对恋人")),
    ("反差",   ("反差——", "冷与热", "同时存在", "不是霸道总裁")),
    ("底色",   ("底色——", "他更像", "不是恋爱脑")),
]
for tag, alts in PERS_LAYERS:
    if not any(a in pers for a in alts):
        problems.append("[P2] personality 缺分层内容：`%s`（同义组均未命中）" % tag)

# 修订四：「对作品」的核心必须是「独一无二」（不是「完美主义」）
i_work = pers.find("对作品——")
i_next = pers.find("对你——", i_work + 1)
work_seg = pers[i_work:i_next] if i_work != -1 and i_next != -1 else pers
if "独一无二" not in work_seg:
    problems.append("[P1] personality「对作品」段缺核心词「独一无二」")
# ⭐ 2026-10-03（小辞裁定）：「骨螺红」检查**整条删除**。她明确决定这个标志性颜色不再写进人设，
#    也不接受为此报错。原判据（2026-10-01）是「description / personality 任一有即可」，
#    现在是两处都没有 —— 属于已知且有意接受的缺口。要恢复请看 git 历史。

# 形态差异（2026-09-14 小辞更正）：临空日常 = 短发；海神形态 = 长发
# 依据：素材「长发」仅 3 处命中，全部在海神形态（罗镜的渺声 03 浮出水面+鱼尾 /
# 05 水中 / 08 断潮戟+鱼尾+人群喊"海神"）；旧卡「短发」本来就对。
i_short = desc.find("短发")
i_long = desc.find("长发")
if i_long == -1:
    i_long = desc.find("头发会变长")   # ⭐ 2026-10-03：新卡改写为「头发会变长」
if i_short == -1:
    problems.append("[P0] description 缺「短发」——临空日常形态应为短发")
if i_long == -1:
    problems.append("[P2] description 缺「长发」——海神形态为长发")
if i_short != -1 and i_long != -1 and i_short > i_long:
    problems.append("[P2] 顺序不对：「短发」（临空形态）应出现在「长发」（海神形态）之前")
# ⭐ 2026-10-03：新卡写的是「蓝粉交叠」⇒ 接等价写法。
# ⭐ 2026-10-03（小辞裁定）：「珊瑚红」检查**整条删除**。她明确决定代表色不再写进人设，
#    也不接受为此报错 ⇒ 规则留着只会每轮红一次，没有意义。要恢复请看 git 历史。
for need, alts in (("蓝粉撞色", ("蓝粉撞色", "蓝粉交叠")),):
    if not any(a in desc for a in alts):
        problems.append("[P1] description 缺少关键外貌项：`%s`" % need)

# 三大时期锚点名字必须出现，作为可回忆的锚
# ⭐ 2026-10-03：小辞裁定「三大时期世界书已有专条，不重复进人设」⇒ 判据改为两者并集。
#    实测：鲸落城 #47/#38/#43、罗镜城 #43/#53、金沙之海 #42 —— 都在世界书里。
for need in ("鲸落城", "罗镜城", "金沙之海"):
    if need not in desc and need not in WB_TEXT:
        problems.append("[P1] 三大时期锚点丢失（description 与世界书都没有）：`%s`" % need)

# IF 线不进 description —— 只在世界书 order 900 条目里
for w in ("赤霄将军", "武神", "将军祠", "魂引"):
    if w in desc:
        problems.append("[P1] description 出现 IF 线内容（应只留世界书）：`%s`" % w)

# 出场顺序：现在段 早于 来历段
i_now = desc.find("临空市")
i_old = desc.find("来历")   # ⭐ 2026-10-03：去掉句号限制（新卡写的是「来历资料」）
if i_now == -1 or i_old == -1:
    problems.append("[P2] 无法判断出场顺序（缺「临空市」或「来历。」）")
elif i_now > i_old:
    problems.append("[P2] 出场顺序不对：「现在」段出现在「来历」段之后")

# 触发词风格检查：名称
if card.get("name") != "祁煜":
    problems.append("[P2] name 不是「祁煜」：%r" % card.get("name"))

# ===================== 批次 C 检查（first_mes / alternate_greetings / mes_example）=====================
fm = card.get("first_mes") or ""
ag = card.get("alternate_greetings") or []
me = card.get("mes_example") or ""
ag_list = ag if isinstance(ag, list) else []
ag_join = "\n".join(ag_list)

stats["first_mes 字数"] = len(fm)
stats["alternate_greetings 条数"] = len(ag_list) if isinstance(ag, list) else "非列表"
stats["alternate_greetings 字数"] = sum(len(x) for x in ag_list)
stats["mes_example 字数"] = len(me)
stats["mes_example 组数"] = me.count("<START>")

if not fm.strip():
    problems.append("[P0] first_mes 为空")
if not me.strip():
    problems.append("[P0] mes_example 为空")
if not isinstance(ag, list) or not ag:
    problems.append("[P0] alternate_greetings 为空 / 不是列表")
else:
    if not (3 <= len(ag) <= 5):
        problems.append("[P1] alternate_greetings 条数 %d 不在 3~5 区间" % len(ag))
    for i, g in enumerate(ag, 1):
        if not (g or "").strip():
            problems.append("[P1] alternate_greetings 第 %d 条为空" % i)

n_start = me.count("<START>")
# 修订二把上限放到 16；修订三要求新增「光坠其间」重做组 → 再放到 17
# ⭐ 2026-10-01：批次清理后只留她认可的 6 组 ⇒ 下限抽成常量并降到 0（上限 17 不动）。
#    组数下限只是「样本够不够丰富」的提醒，不是内容对错；清样时它该让路。
#    将来补写样本，把下面的 0 改回 10 就行。
MES_EXAMPLE_MIN_GROUPS = 0
if not (MES_EXAMPLE_MIN_GROUPS <= n_start <= 17):
    problems.append("[P1] mes_example 组数 %d 不在 %d~17 区间" % (n_start, MES_EXAMPLE_MIN_GROUPS))

# 开场白必须用默认称呼（不预设用户已引导过更亲密的叫法）
if "保镖小姐" not in fm:
    problems.append("[P2] first_mes 未使用默认称呼「保镖小姐」")

# 修订七（小辞 2026-09-14）：开场白一律「日常」，不要特殊事件
NORMAL_LIFE_KEYS = ["画室", "沙发", "书", "超市", "阳台", "厨房", "窗", "海风", "晚饭", "灯"]
hit_day = [k for k in NORMAL_LIFE_KEYS if k in ag_join]
stats["alternate_greetings 日常场景锚点"] = "%d/%d（%s）" % (
    len(hit_day), len(NORMAL_LIFE_KEYS), "、".join(hit_day) or "无")
if len(hit_day) < 4:
    problems.append("[P1] alternate_greetings 日常场景锚点不足（命中 %d/%d，至少 4）"
                    % (len(hit_day), len(NORMAL_LIFE_KEYS)))
EVENT_WORDS = ["救", "落水", "喷泉", "流浪体", "试炼", "锦标赛", "快递", "迷路", "画展", "惊喜"]
for w in EVENT_WORDS:
    if w in ag_join:
        problems.append("[P1] alternate_greetings 出现「特殊事件」词（应走日常）：`%s`" % w)

# 情绪切换·用户侧（小辞 2026-09-14 修订一 / 修订二）：用户变了称呼 → 他据此调整反应
# ⚠️ 修订二纠正：直呼本名「祁煜」是**普通称呼**，不是"生气 / 破防"的信号
for need, why in (("用户：祁煜。", "普通称呼（默认就是叫本名）"),
                  ("鱼宝", "心情好 → 鱼系"), ("红烧鱼", "生气 → 调侃系"),
                  ("祁大师", "揶揄 → 尊称")):
    if need not in me:
        problems.append("[P2] mes_example 缺「用户情绪切换」样本：`%s`（%s）" % (need, why))
# 修订二：听到本名不许当成"出事"的信号
for bad in ("谁惹你了", "你平时不这么叫我", "出什么事了"):
    if bad in me:
        problems.append("[P1] mes_example 把「叫本名」误读成求救信号：`%s`"
                        "（修订二：直呼其名大部分情况是普通称呼）" % bad)
# 情绪切换·角色侧：靠多轮对话累积；修订二要求压到 10 轮上下
_blocks = [b.strip() for b in me.split("<START>")[1:]]
for anchor, tag in (("0.01%", "生闷气 → 被哄好"), ("凌晨三点半", "吃醋 → 说破")):
    hit_b = [b for b in _blocks if anchor in b]
    if not hit_b:
        problems.append("[P2] mes_example 缺「角色情绪切换（多轮累积）」样本：`%s`（%s）" % (anchor, tag))
    else:
        n_lines = len([l for l in hit_b[0].split("\n") if l.strip()])
        if n_lines > 14:
            problems.append("[P1] 角色情绪累积组「%s」过长：%d 轮 > 14（修订二要求缩到 10 轮上下）"
                            % (tag, n_lines))
        if n_lines < 8:
            problems.append("[P2] 角色情绪累积组「%s」过短：%d 轮 < 8，看不出累积过程" % (tag, n_lines))
# 修订二：保留「反问接话」样本
if "是味道么？" not in me:
    problems.append("[P2] mes_example 缺「反问接话」样本（锚点「是味道么？」）——修订二要求保留")
# 修订三：新增「光坠其间」重做组（他不肯示弱 / 生病先瞒着你）
for need, why in (("请你吃牛排好不好？", "光坠其间：眼睛看不见还硬撑要做饭"),
                  ("眼药膏", "光坠其间：只在道具里露出真相"),
                  ("照顾我，不是保镖应尽的职责吗？", "光坠其间：原话")):
    if need not in me:
        problems.append("[P2] mes_example 缺「不肯示弱（光坠其间）」样本：`%s`（%s）" % (need, why))
# 用户主动引导称呼（引导之后才生效）
for need in ("宝贝", "鱼鱼", "主人"):
    if need not in me:
        problems.append("[P2] mes_example 缺「引导称呼」样本：`%s`" % need)
# 亲密互动（18+）：取自 主页交互 的身体触摸段
for need, why in (("就要报警了", "颈以下亲密互动"), ("人鱼线", "腰腹"),
                  ("这只手就归我了", "危险区 / 夺回主动")):
    if need not in me:
        problems.append("[P2] mes_example 缺「亲密互动」样本：`%s`（%s）" % (need, why))
# 尺度边界：18+ ≠ 露骨 —— 以下词一律不许出现
EXPLICIT = ["裸", "脱光", "做爱", "下体", "私处", "舔", "高潮", "射精", "胸罩", "内裤"]
for w in EXPLICIT:
    if w in me:
        problems.append("[P0] mes_example 越过尺度边界（露骨词）：`%s`" % w)

# 批次 C 三段：同样禁止 markdown 粗体 / ASCII 双引号 / 剧情结论 / 禁用词 / 无出处表述 / 残留
for label, txt in (("first_mes", fm), ("alternate_greetings", ag_join), ("mes_example", me)):
    if not txt.strip():
        continue
    if "**" in txt:
        problems.append("[P1] %s 里有 markdown 粗体 `**`" % label)
    if '"' in txt:
        problems.append("[P1] %s 里有 ASCII 双引号" % label)
    for w in PLOT_SPOILER:
        if w in txt:
            problems.append("[P0] %s 出现剧情结论句：`%s`" % (label, w))
    for w in BANNED:
        if w in txt:
            problems.append("[P1] %s 出现禁用词：`%s`" % (label, w))
    for w in UNSOURCED_AVOID:
        if w in txt:
            problems.append("[P1] %s 出现无素材支撑的旧卡表述：`%s`" % (label, w))
    if "玩家" in txt:
        problems.append("[P1] %s 残留「玩家」（本卡统一写「用户」或「你」）" % label)
    if "{{" in txt:
        problems.append("[P2] %s 残留未约定宏 `{{`" % label)

# ============ 批次 E 补充：世界书条目的定点检查 ============
# character_book 的 entries 是 dict{ "0": {...} }（V1 风格），别当 list 遍历
_cb_entries = cb.get("entries") or {}
_cb_iter = _cb_entries.values() if isinstance(_cb_entries, dict) else _cb_entries
_cb_list = list(_cb_iter)

# 1) 「潜行者」不得出现在称呼/别名类条目里（可作身份，不可作称呼）
for e in _cb_list:
    title = str(e.get("comment") or "")
    content = str(e.get("content") or "")
    if "称呼" in title:
        for w in BANNED_IN_ALIAS:
            if w in content:
                problems.append(
                    "[P1] 「%s」是对利莫里亚人的泛称，不得作为他的称呼写进条目「%s」" % (w, title))

# 2) description 提到的三大时期锚点，世界书里都得有对应条目，否则用户问起查不到
for anchor in ["鲸落城", "罗镜城", "金沙之海"]:
    if anchor in desc:
        if not any(anchor in (str(e.get("comment") or "") + str(e.get("content") or ""))
                   for e in _cb_list):
            problems.append(
                "[P1] description 有锚点「%s」，但世界书里没有对应条目（用户问起会查不到）" % anchor)

# ============ 批次 D：system_prompt / post_history_instructions ============
sp = card.get("system_prompt") or ""
phi = card.get("post_history_instructions") or ""
stats["system_prompt 字数"] = len(sp)
stats["post_history_instructions 字数"] = len(phi)

if not sp.strip():
    problems.append("[P0] system_prompt 为空")
if not phi.strip():
    problems.append("[P0] post_history_instructions 为空")
# ⭐ 2026-10-01：2500 → 3600。原上限只盯 system_prompt 一个字段，
#    而每轮实际注入 = system_prompt + description(2342) + personality(1498) + scenario(69) ≈ 7129 字，
#    那把尺子早就量不准了。这里放宽上限，但**不设无限**：超过 3600 就该回头做减法。
SP_MAX_CHARS = 3600
if sp and not (400 <= len(sp) <= SP_MAX_CHARS):
    problems.append("[P1] system_prompt 长度 %d 不在 400~%d 区间（每轮都注入，别太长也别太空）" % (len(sp), SP_MAX_CHARS))

# 必须写进 system_prompt 的口径锚点（小辞 2026-09-14/15 逐条裁定）
SP_ANCHORS = [
    # ⭐ 2026-10-03：新卡写的是「直呼『祁煜』只是普通称呼」⇒ 接等价写法
    ("本名=普通称呼", ["最普通的叫法", "只是普通称呼", "普通称呼"]),
    # ⭐ 2026-10-03（小辞裁定）：「本名非情绪信号」锚点**整条删除**。
    #    她重写后 system_prompt 只有「直呼祁煜只是普通称呼」，没有「听到本名就当出事了…是错的」禁令；
    #    她选择不补 ⇒ 规则留着每轮红一次没有意义。
    #    ⚠ 已知影响：他有可能把她叫「祁煜」读成她在生气。要恢复请看 git 历史。
    # ⭐ 2026-10-01 ：废止「称呼 → 情绪」的机械映射（鱼宝=心情好 / 红烧鱼=闹别扭 / 祁大师=揶揄）。
    #    规则废了，检查就该跟着废 —— 留着只会逼人把废掉的写法加回来。要恢复请看 git 历史。
    #    ⚠ 能力没丢：总原则「她改变怎么称呼你，你据此微调反应，接住别点评」仍在 system_prompt 里。
    ("角色侧·多轮累积", ["多轮对话累积"]),
    ("称呼优先级·保镖小姐", ["保镖小姐"]),
    ("称呼优先级·猎人小姐", ["猎人小姐"]),
    # ⭐ 2026-10-03：新卡写的是「不写露骨性行为过程」⇒ 接等价写法
    ("18+ 尺度边界", ["不写性行为过程", "不写露骨性行为", "露骨性行为过程"]),
    ("说话方式·反问", ["反问"]),
    ("内心独白格式", ["心想："]),
    ("隐藏设定不主动说", ["利莫里亚"]),
]
# ⭐ 2026-10-03：原来是 `for need in needs: if need not in sp` —— 那是「全部都要在」，
#    给一条锚点写多个等价写法反而变成新增要求（越加越报错）。改成「任一命中即通过」。
for tag, needs in SP_ANCHORS:
    if not any(need in sp for need in needs):
        problems.append("[P1] system_prompt 缺口径锚点「%s」：（任一即可）%s" % (tag, " / ".join(needs)))

# system_prompt 不得把「叫本名」写成情绪信号
# ⚠ 这些词在 system_prompt 里是**作为禁止项**出现的（「问『谁惹你了』是错的」），
#    所以必须做否定语境判定：窗口内出现否定标记才算合法引用。
NEG_MARKS = ["是错的", "不许", "禁止", "不要", "别当", "一次都不"]
for w in ["你平时不这么叫我", "出什么事了", "谁惹你了"]:
    idx = sp.find(w)
    while idx != -1:
        win = sp[max(0, idx - 40): idx + len(w) + 40]
        if not any(m in win for m in NEG_MARKS):
            problems.append(
                "[P2] system_prompt 出现「本名=情绪信号」的写法：`%s`（上下文无否定标记）" % w)
            break
        idx = sp.find(w, idx + 1)

# 批次 D 两段：同样过通用卫生检查
for label, txt in (("system_prompt", sp), ("post_history_instructions", phi)):
    if not txt.strip():
        continue
    if "**" in txt:
        problems.append("[P1] %s 里有 markdown 粗体 `**`" % label)
    if '"' in txt:
        problems.append("[P1] %s 里有 ASCII 双引号" % label)
    for w in PLOT_SPOILER:
        if w in txt:
            problems.append("[P0] %s 出现剧情结论句：`%s`" % (label, w))
    for w in BANNED:
        if w in txt:
            problems.append("[P1] %s 出现禁用词：`%s`" % (label, w))
    for w in EXPLICIT:
        if w in txt:
            problems.append("[P0] %s 越过尺度边界（露骨词）：`%s`" % (label, w))
    if "{{" in txt:
        problems.append("[P2] %s 残留未约定宏 `{{`" % label)

L = []
L.append("祁煜酒馆卡 · 校验报告（批次 B / C / D）")
L.append("=" * 46)
L.append("生成：2026-09-14")
L.append("")
L.append("【产物清单】")
L.append("  E:\\ai-love\\card\\Rafayel.character.json        角色卡（V2，内嵌 character_book）")
L.append("  E:\\ai-love\\card\\worldbook.json                 独立世界书（批次 A）")
L.append("  E:\\ai-love\\card\\_work\\祁煜人设.md             ★ 真相源（改这个）")
L.append("  E:\\ai-love\\card\\_work\\worldbook\\            世界书真相源目录（批次 A，改这里）")
L.append("  E:\\ai-love\\card\\_work\\sources.md               溯源对照表（15 节 = 批次 C 溯源处置）")
L.append("  E:\\ai-love\\Rafayel_bot.py            QQ 接线（主入口，仍在项目根）")
L.append("  E:\\ai-love\\ai-Rafayel\\Rafayel.py                    人设层（读卡，批次 D）")
L.append("  E:\\ai-love\\ai-Rafayel\\Rafayel_chat.py               门面（批次 D；2026-09-17 拆出下面 4 层）")
L.append("  E:\\ai-love\\ai-Rafayel\\Rafayel_config.py             配置与常量（路径 / 上限 / API key）")
L.append("  E:\\ai-love\\ai-Rafayel\\Rafayel_profile.py            用户画像（批次 F）")
L.append("  E:\\ai-love\\ai-Rafayel\\Rafayel_memory.py             记忆落盘 + ConversationManager")
L.append("  E:\\ai-love\\ai-Rafayel\\Rafayel_llm.py                get_reply：拼请求 + 调 DeepSeek")
L.append("  E:\\ai-love\\ai-Rafayel\\worldbook\\Rafayel_worldbook.py 世界书关键词注入器")
L.append("")
L.append("【统计】")
for k, v in stats.items():
    L.append("  %s：%s" % (k, v))
L.append("")
# ⭐ 2026-10-01：mes_example 已清理，且它**不注入 prompt** ⇒ 样本缺不缺不影响对话质量。
#    这里统一滤掉「样本缺不缺 / 组数够不够 / 多轮行数」这类内容问题；露骨词 P0 保留 ——
#    那是尺度红线，跟样本多少无关。将来补回样本，把下面两行删掉就行。
_SKIP_ME = ("[P1] mes_example 缺", "[P2] mes_example 缺",
            "[P1] mes_example 组数", "[P1] 角色情绪累积组", "[P2] 角色情绪累积组")
problems = [p for p in problems if not p.startswith(_SKIP_ME)]

L.append("【退出条件检查】")
if not problems:
    L.append("  全部通过 ✅")
else:
    for p in problems:
        L.append("  " + p)
L.append("")
L.append("【批次 B 口径（已裁定）】")
L.append("  1. 出场顺序：现在（临空市）→ 过去（半岛三年 / 维罗诺 / 三大时期）")
L.append("     · IF 线（武神 / 赤霄将军）**不进 description**（小辞 2026-09-14 修订五）——")
L.append("       只保留在世界书的 order 900 条目（关键词触发、极少提及）")
L.append("  2. 外貌：发长按**形态**区分 —— 临空日常 = 短发，海神形态 = 长发")
L.append("     （素材「长发」3 处命中全在海神形态：罗镜的渺声 03 浮出水面+鱼尾 / 05 水中 /")
L.append("     08 断潮戟+鱼尾+人群喊「海神」；故旧卡「蓝紫色短发」本来就对，曾一度误改已回退）")
L.append("  3. 代表色：珊瑚红（非旧卡的「深海珊瑚红」）")
L.append("  4. 不写剧情结论句（心脏被骗走 / 沉眠海底 等一律不进 description）")
L.append("  5. 称呼：默认「保镖小姐」+ 第二「猎人小姐」，更亲密需用户引导（详见 worldbook）")
L.append("  6. 已删旧卡「在意过去的你超过现在的你」，改为「珍惜眼前的你，但对跨越了时间的承诺有执念」")
L.append("  7. 三大时期锚点入 description（鲸落城 / 罗镜城 / 金沙之海）——只给名字 + 一句定位，细节留世界书")
L.append("  8. 说话方式按素材实证重写（反问接话 / 语速慢 / 对外冷对内直球 / 艺术的比喻癖 / 情绪的三种语气）")
L.append("  9. 职业措辞：弃用「前所未有、声名大噪」，改用素材原词（《幻》横空出世 / 烧遍整个艺术圈 /")
L.append("     画坛独一份 / 屡屡刷新拍卖行纪录）；备选 A/B/C 见 sources.md 13.1")
L.append(" 10. personality 的「对外人」「对你」按 wiki + 素材原文重立（2026-09-14 修订三）——")
L.append("     「对外人」核心改为「不是怯，是不在乎」（不在乎画价与商业价值 / 不爱出风头 / 失联采风 /")
L.append("     挑的是吵不是人多 / 对真朋友是另一副样子）；「对你」补一手台词实证")
L.append("     （盈盈摇曳「没有，你是第一个」/ 夜游之章「想跟你多黏一会儿不行吗」）")
L.append(" 11. 旧卡两处无素材支撑的表述已改写，禁止回流：「不喜欢别人触碰」→「不习惯陌生人的触碰」；")
L.append("     「讨厌人多的吵闹地方」→「不喜欢吵闹的场合」（素材判据：夜海「热闹吗？我只觉得吵闹」/")
L.append("     潮间带「纪念馆内人潮涌动，却并不吵闹」——雷区是「吵」不是「人多」）")
L.append(" 12. 修订四：personality 余下五段按 wiki + 素材重立 ——")
L.append("     「对作品」核心从「完美主义」改为「**独一无二**」（逸闻01：被别人选过的颜色不会出现在我的画上 /")
L.append("     一万只骨螺提出一克骨螺红 / 没完成的画绝对不会公之于众 / 艺术没有精确一说）；")
L.append("     「反差」删掉无出处的「耳朵尖红透 / 反撩一句」，改用一手台词（谁的脸红了 / 脸红？你以为我是你么）；")
L.append("     「底色」补一手出处（不要放弃我一定会带你们回家 / 谭灵礁石评价［实为逸闻03，非世界深处03］/")
L.append("     婚礼答「没有」/ 照顾我，不是保镖应尽的职责吗 / 记忆挑着用 / 独自面对梦里那场永不止息的海啸）；")
L.append("     「对痛上瘾」删掉无出处的演绎句；「反差萌点」逐条挂出处，删「戏精」「三岁生日」「破损也是它的一部分」")
L.append("")
L.append("【批次 C 口径（已裁定）】")
L.append(" 13. 开场白（first_mes / alternate_greetings）一律用默认称呼「保镖小姐」——")
L.append("     不预设用户已经引导过更亲密的叫法（鱼鱼 / 主人 / 宝贝只在该组示例里、由用户主动引导触发）")
L.append(" 14. 开场场景只落在临空市白沙湾的当下日常（画室 / 一通电话 / 他来找你）：一段=雨天他救完落水小孩回来；")
L.append("     二段=凌晨三点半你失眠打给他；三段=他画了一整夜、要介绍新画笔；四段=他捧着两份礼物上门；")
L.append("     五段=你迷路了，他说别挂电话、我来找你")
L.append(" 15. mes_example 主题 =「称呼随情绪切换」（依据 `_work/小红书称呼调研.md` 第 3 节，158 条评论强共识）：")
L.append("     心情好 → 鱼系（小鱼 / 鱼宝）；破防 → 直呼全名「祁煜」；生气 → 调侃系（红烧鱼）；揶揄 → 尊称（祁老师）")
L.append("     ⇒ 这是「用户怎么叫他」，不是他改口叫用户")
L.append(" 16. 行文格式：用户行写「用户：」、角色行写「祁煜：」（沿用全项目 `角色：台词` 规范，不用 {{user}} / {{char}} 宏）；")
L.append("     每组以 <START> 开头；动作/神态用（），内心独白用（心想：……）")
L.append(" 17. 三段同样沿用批次 B 的硬约束：无 markdown 粗体、无 ASCII 双引号、无剧情结论句、无禁用词、")
L.append("     无「玩家」残留、无未约定宏")
L.append("")
L.append("【批次 C 修订一 口径（小辞 2026-09-14 21:49 三条指令）】")
L.append(" 18. alternate_greetings 全部改成「日常」：删掉「雨天他救下落水小孩」那条，")
L.append("     五条依次为 ① 早晨·阳台晒太阳 ② 午后·沙发上看书 ③ 傍晚·分头买菜做饭（电话）")
L.append("     ④ 入夜·你困了、他舍不得闭眼 ⑤ 你回来晚了、他在客厅等")
L.append("     ⇒ 审计新增：必须命中 ≥4 个日常场景锚点（画室/沙发/书/超市/阳台/厨房/窗/海风/晚饭/灯）；")
L.append("     不得出现「特殊事件」词（救/落水/喷泉/流浪体/试炼/锦标赛/快递/迷路/画展/惊喜）")
L.append(" 19. 「情绪切换」明确分成两种，两种都要有样本：")
L.append("     · 用户侧 = 用户改变**称呼** → 他据此调整反应（普通「祁煜。」/ 心情好「鱼宝」/")
L.append("       生气「红烧鱼」/ 揶揄「祁大师」四态齐备）")
L.append("     · 角色侧 = 他自己的情绪**靠多轮对话累积**出来（① 生闷气 → 逐轮被哄好，")
L.append("       锚点「0.01%」② 吃醋 → 从冷处理到说破，锚点「凌晨三点半」）")
L.append(" 20. 主页交互的**身体触摸段进卡**，面向 18+、尺度放到暧昧张力级：")
L.append("     新增 4 组亲密互动（头发与脸 / 脖子与锁骨 / 手 / 腰腹与胸口），锚点为")
L.append("     「就要报警了」+「人鱼线」+「这只手就归我了」；素材取自 主页交互 05/06/08/09/10/11/12/16")
L.append(" 21. ⚠️ 尺度边界：**18+ ≠ 露骨**。卡里只写暧昧、身体反应与暗示（沿用游戏原文的克制笔法：")
L.append("     「再往下……就要报警了」「想测我的心跳，不是非得摸这里」），")
L.append("     **不写性行为过程**；审计对露骨词（裸/脱光/做爱/下体/私处/舔/高潮/射精/胸罩/内裤）按 P0 拦下")
L.append(" 22. 组数：原定 10~15，**修订二放到上限 16**（本次 16 组）—— 小辞要求「保留反问接话」，")
L.append("     为不砍掉她已看过的其它组，净增 1 组。「香蕉车」「纱布」两组仍不恢复：")
L.append("     「脸红」已并入亲密互动①、「吃醋」已升级为角色情绪累积②、")
L.append("     不肯示弱在 personality「底色」段有整段覆盖")
L.append("")
L.append("【批次 C 修订二 口径（小辞 2026-09-14 22:03 四条指令）】")
L.append(" 23. ⭐ **直呼本名「祁煜」是普通称呼，不是情绪信号**。小辞原话：")
L.append("     「直呼其名，不一定是因为生气（大部分情况是普通称呼）」。")
L.append("     ⇒ 该组重写为**日常应答**（他随口应声、还嫌「叫完就走」），")
L.append("     并新增审计拦截：出现「谁惹你了 / 你平时不这么叫我 / 出什么事了」一类")
L.append("     「把本名当求救信号」的写法，按 **P1** 拦下。")
L.append(" 24. 「腰腹与胸口」那组的尺度**小辞确认通过**：原话「腰的就是那种暧昧环境，极具张力，")
L.append("     引人遐想的那种」⇒ 保持暧昧张力级 + **P0 露骨词守卫表**不变")
L.append(" 25. **角色情绪累积两组压到 10 轮上下**（原 18 / 15 轮）。审计新增按块统计轮次：")
L.append("     累积组必须 **8~14 轮**，>14 报 P1、<8 报 P2")
L.append(" 26. **恢复「反问接话」组**（锚点「是味道么？」）——审计按锚点校验其存在")
L.append("")
L.append("【批次 C 修订三 口径（小辞 2026-09-14 22:11）】")
L.append(" 27. 小辞裁定：「**香蕉车不要**，光坠其间重做的那一组**保留**」")
L.append("     · 香蕉车（`语音\\25`「这辆香蕉车，也可以把我带回你身边」）出处干净，但**裁定不用**，")
L.append("       溯源表 14.9 记下是「有意识不采用」，不是丢失")
L.append("     · 新增「不肯示弱 / 光坠其间」组（重做，**非原样恢复**）：")
L.append("       依据 `在你身边\\点滴日常\\光坠其间`（他熬夜两天导致眼睛临时看不见、打电话叫她来、")
L.append("       面不改色摸着走到冰箱、被地毯绊一下也不承认、提出请吃牛排，")
L.append("       最后用原话「照顾我，不是保镖应尽的职责吗？」把照顾他推给她；")
L.append("       真相只从道具露出 —— 床头柜上打开的眼药膏）")
L.append("     · ⚠️ 原「纱布」组**前提是我自己编的**（全库 `纱布` 仅 1 处，还是比喻）⇒ ")
L.append("       重做取代原样恢复；审计按 `请你吃牛排好不好？` + `眼药膏` + 原话 三个锚点校验")
L.append(" 28. 组数：16 → **17**（上限随之放到 10~17）。⚠️ mes_example 已到 ~3000 字，")
L.append("     卡片总上下文约 8.2k 汉字 —— 批次 D 接 ai-love 时要评估是否需裁剪")
L.append("")

with io.open(REPORT, "w", encoding="utf-8", newline="\r\n") as f:
    f.write("\n".join(L))

print("problems=%d" % len(problems))
for p in problems:
    print("  " + p)
sys.exit(0 if not problems else 2)
