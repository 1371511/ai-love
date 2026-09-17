# -*- coding: utf-8 -*-
"""
批次 A 退出条件审计：对 worldbook.json + worldbook/ 目录做全量检查，输出 `_校验报告.txt`。

检查项（全部必须为 0 / PASS）
---------------------------
1. worldbook.json 能被 json.loads 解析
2. 条目数与 worldbook.md 的 `### ` 数量一致
3. 触发词无重复（大小写不敏感）
4. 每个条目 keys 非空、content 非空
5. content 里无 wiki 残留：{{ }} [[ ]] <tag> &nbsp; 等
6. content 里无 `source:` 之类只给人看的元数据泄漏
7. 未采用词（素材 0 次或已判错）不得出现在 keys 里
8. 每条的 uid / disable / position 字段符合 ST 规范

运行：python _audit_worldbook.py
"""
import os
import re
import sys
import json
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CARD_DIR = os.path.dirname(HERE)
WB_JSON = os.path.join(CARD_DIR, "worldbook.json")
WB_DIR = os.path.join(HERE, "worldbook")          # 世界书目录（多文件，按文件名排序拼接）
REPORT = os.path.join(CARD_DIR, "_校验报告.txt")

if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _wb_io                                      # noqa: E402

# 不得作为触发词的词（素材 0 次命中、或已判定方向错误）
BANNED_KEYS = [
    "祁画师", "臭鱼", "繁溪镇", "潮汐之日", "深海珊瑚红",
    "小辞",            # 素材 0 次
    "小画家",          # 素材里是"他对用户的称呼"，不是他的别名
]

WIKI_RESIDUE = [
    (r"\{\{", "{{ 模板残留"),
    (r"\}\}", "}} 模板残留"),
    (r"\[\[", "[[ 链接残留"),
    (r"\]\]", "]] 链接残留"),
    (r"<[a-zA-Z/][^>]{0,40}>", "<tag> HTML 残留"),
    (r"&nbsp;|&amp;|&lt;|&gt;", "HTML 实体残留"),
    (r"\|\s*$", "行尾管道符"),
    (r"\*\*", "markdown 粗体残留（会被原样注入提示词）"),
    (r'"', "ASCII 引号残留（正文应使用「」）"),
]
# 说明：正文里的 `- ` 项目符号是允许的（注入给模型就是普通列表），因此不做检查。

META_LEAK = re.compile(r"^\s*-?\s*(keys|secondary_keys|constant|selective|order|position|source)\s*:", re.M)

# 内容硬约束：description 类字段不应出现的剧情结论句（本批次先预埋，供 B 批复用）
PLOT_SPOILERS = ["心脏被骗走", "心脏被人类骗走", "沉眠于最深的海底"]


def main():
    problems = []
    stats = {}

    # --- 1. JSON 可解析 ---
    try:
        with open(WB_JSON, "r", encoding="utf-8") as f:
            raw = f.read()
        data = json.loads(raw)
        entries = data["entries"]
    except Exception as e:
        problems.append(f"[P0] worldbook.json 无法解析：{e}")
        write_report(problems, {})
        return 2

    stats["条目数（JSON）"] = len(entries)

    # --- 2. 与 md 条目数一致 ---
    md_text = _wb_io.read_text()
    md_entries = re.findall(r"^###\s+(.*)$", md_text, flags=re.M)
    stats["条目数（md）"] = len(md_entries)
    if len(md_entries) != len(entries):
        problems.append(
            f"[P0] 条目数不一致：md {len(md_entries)} 条 vs JSON {len(entries)} 条"
        )

    # --- 3~8 逐条检查 ---
    seen_keys = {}
    total_keys = 0
    const_n = 0
    after_n = 0
    disabled_n = 0
    empty_content = []
    no_keys = []
    dup_keys = []
    residue_hits = []
    leak_hits = []
    banned_hits = []
    uid_bad = []
    position_bad = []

    for uid, e in entries.items():
        name = e.get("comment", f"uid={uid}")
        keys = e.get("key", [])
        content = e.get("content", "")
        total_keys += len(keys)

        if e.get("constant"):
            const_n += 1
        if e.get("position") == 1:
            after_n += 1
        if e.get("disable"):
            disabled_n += 1

        if not keys:
            no_keys.append(name)
        if not content.strip():
            empty_content.append(name)

        for k in keys:
            kl = k.lower()
            if kl in seen_keys:
                dup_keys.append(f"`{k}`（{seen_keys[kl]} ↔ {name}）")
            else:
                seen_keys[kl] = name
            if k in BANNED_KEYS:
                banned_hits.append(f"`{k}` 出现在「{name}」")

        for pat, desc in WIKI_RESIDUE:
            if re.search(pat, content):
                residue_hits.append(f"「{name}」命中 {desc}")

        if META_LEAK.search(content):
            leak_hits.append(f"「{name}」正文里疑似混入元数据行")

        if e.get("uid") != int(uid):
            uid_bad.append(f"uid={uid} 的 uid 字段是 {e.get('uid')}")
        if e.get("position") not in (0, 1):
            position_bad.append(f"「{name}」position={e.get('position')}")

    stats["触发词总数"] = total_keys
    stats["常驻条目（constant）"] = const_n
    stats["after_char 条目"] = after_n
    stats["被禁用条目（x- 前缀）"] = disabled_n

    if no_keys:
        problems.append(f"[P0] 以下条目没有 keys：{'、'.join(no_keys)}")
    if empty_content:
        problems.append(f"[P0] 以下条目正文为空：{'、'.join(empty_content)}")
    if dup_keys:
        problems.append(f"[P0] 触发词重复：{'；'.join(dup_keys)}")
    if residue_hits:
        problems.append(f"[P1] wiki 残留：{'；'.join(residue_hits)}")
    if leak_hits:
        problems.append(f"[P1] 元数据泄漏进正文：{'；'.join(leak_hits)}")
    if banned_hits:
        problems.append(f"[P1] 使用了禁用词：{'；'.join(banned_hits)}")
    if uid_bad:
        problems.append(f"[P2] uid 字段与键名不一致：{'；'.join(uid_bad)}")
    if position_bad:
        problems.append(f"[P2] position 值非法：{'；'.join(position_bad)}")

    # 排序单调性：md 里条目出现的先后应严格对应 order 递增（时间线倒叙 = 越靠前 order 越小）
    idx_sorted = sorted(entries.keys(), key=lambda k: int(k))
    seq = [(k, entries[k].get("order", 100)) for k in idx_sorted]
    bad_seq = [
        f"「{seq[i][0]}:{entries[seq[i][0]]['comment']}(order={seq[i][1]})」排在此前条目之后却 order 更小"
        for i in range(1, len(seq)) if seq[i][1] < seq[i - 1][1]
    ]
    if bad_seq:
        problems.append("[P2] 条目顺序与 order 不一致：" + "；".join(bad_seq))
    stats["order 区间"] = f"{seq[0][1]} → {seq[-1][1]}" if seq else "-"

    # 长度体检
    lengths = sorted(((len(e.get("content", "")), e.get("comment", "")) for e in entries.values()), reverse=True)
    stats["最长条目"] = f"{lengths[0][1]}（{lengths[0][0]} 字）" if lengths else "-"
    stats["总内容字数"] = sum(l for l, _ in lengths)

    # 备注：本批次是"资料条目"，出现剧情细节是设计使然；此检查供 B 批的 description 使用
    stats["（预埋）剧情结论句检查"] = "本批次不适用（世界书条目本就是资料卡）"

    write_report(problems, stats)
    return 0 if not problems else 2


def write_report(problems, stats):
    lines = []
    lines.append("祁煜酒馆卡 · 校验报告")
    lines.append("=" * 46)
    lines.append("批次：A（世界书 character_book）")
    lines.append("生成：2026-09-14")
    lines.append("")
    lines.append("【产物清单】")
    lines.append("  E:\\ai-love\\card\\worldbook.json          世界书（SillyTavern 独立格式）")
    lines.append("  E:\\ai-love\\card\\_work\\worldbook\\        世界书目录（唯一真相源，改这里）")
    lines.append("  E:\\ai-love\\card\\_work\\md2card.py         md → JSON 生成器")
    lines.append("  E:\\ai-love\\card\\_work\\sources.md         溯源对照表")
    lines.append("  E:\\ai-love\\card\\_work\\称呼词频报告.md     全库词频实证")
    lines.append("")
    lines.append("【统计】")
    for k, v in stats.items():
        lines.append(f"  {k}：{v}")
    lines.append("")
    lines.append("【排序约定】时间线倒叙：越靠近现在的排越前（order 越小）")
    lines.append("  ⚠ 数字前缀即 order 上界；order 与 md 物理顺序必须一致（否则 P2 报错）")
    lines.append("  文件（_work/worldbook/ 目录）      内容                          order")
    lines.append("  10_present-places-people.md      现在·地点与人          组 0  100–130")
    lines.append("  12_present-objects-toys.md       现在·物件与周边        组 0  135–199")
    lines.append("  14_present-worldview.md          现在·世界观（官方词典） 组 0  165–199")
    lines.append("  20_encounters-with-user.md       与用户的相遇与事件      组 1  200–299")
    lines.append("  30_lemuria-fall.md               利莫里亚·覆灭           组 2  300–399")
    lines.append("  40_lemuria-golden-sea.md         利莫里亚·金沙时期       组 3  400–449")
    lines.append("  50_lemuria-mirror-city.md        利莫里亚·罗镜城时期     组 4  450–499")
    lines.append("  60_lemuria-whalefall.md          利莫里亚·起源（鲸落城） 组 5  500–599")
    lines.append("  70_earth-wanderer.md             地球·流浪与假身份       组 6  600–699")
    lines.append("  90_if-line.md                    IF 线（武神，极少提及） 组 7  900–989")
    lines.append("  99_forms-of-address.md           称呼（after_char）      组 8  990–999")
    lines.append("  依据：wiki 年表/祁煜线 —— 2047 教授 → 2044 维罗诺/回国 → 覆灭 → 金沙(3万年后)")
    lines.append("        → 罗镜城(万年后) → 鲸落城(起源)。武神线 wiki 未收年表，按 IF 线处理。")
    lines.append("  说明：利莫里亚三大时期（覆灭/金沙/罗镜城/鲸落城）是他的**核心过往**，")
    lines.append("        地球流浪假身份（维罗诺/半岛/莫亚/塞壬）只在逸闻带过，故压在组 6。")
    lines.append("")
    lines.append("【退出条件检查】")
    if not problems:
        lines.append("  全部通过 ✅")
    else:
        for p in problems:
            lines.append(f"  {p}")
    lines.append("")
    lines.append("【本批次已发现并修正的旧卡错误】")
    lines.append("  1. 「莫亚 / Mo」有两个来源 — Mo = 利莫里亚语 Motherland（工作室 Mo Art Studio 的命名由来）+ 歌剧《塞壬之歌》的「莫亚」一角")
    lines.append("     （旧卡只说「源于歌剧角色」，不完整，已补正）")
    lines.append("  2. 「潮汐之日」查无此词 — 素材里是「潮汐逆流之日」")
    lines.append("  3. 「小画家」方向反了 — 是他对用户的称呼，不是他的别名")
    lines.append("  4. 「深海珊瑚红」（官方口径实为「珊瑚红」）、「繁溪镇」、「祁画师」、「臭鱼」素材 0 次命中")
    lines.append("")
    lines.append("【已裁定不采信的可疑设定（戏中戏 / IF 线）】")
    lines.append("  · 思念 28-宴神曲 把「莫亚」写成母星，并出现帝国/圣潮之廷/女王陛下 → 属 IF 线，不采信")
    lines.append("  · 思念 18-狂热剂量 的「出生地：维罗诺 / 身份职业（前）：歌剧演员」档案 → 档案式文本，不采信")
    lines.append("  · 逸闻 03 中路易斯「续写」的利莫里亚起源 → 剧中人虚构，只采信他引用的史实部分")
    lines.append("")
    lines.append("【本次修订（2026-09-14 · 排序 + IF 线）】")
    lines.append("  1. 全部条目按「时间线倒叙」重排 order 与 md 内顺序（组 0 → 组 7）")
    lines.append("  2. 新增「鲸落城的由来」：合并鲸落城 / 海神祭典 / 神庙 / 海神庆典")
    lines.append("  3. 新增「赤霄将军 · 武神」：IF 线，order 900，正文首句即标明「另一条线」")
    lines.append("  4. 「海月仪式」的触发词「鲸落城」移交新条目，避免触发词重复")
    lines.append("  5. 补回重排时漏掉的「焰尾鱼」（order 325）")
    lines.append("  6. 「祁煜的称呼」补入「祁大师」（民间尊称，素材 18+ 处实证）")
    lines.append("")
    lines.append("【第二轮修订（2026-09-14 19:07 · 按小辞裁定）】")
    lines.append("  1. 「赤霄将军 · 武神」压缩（352 → 约 165 字），IF 线保持精简")
    lines.append("  2. 新增「半岛的三年」（order 205）：他来临空前在半岛艺术之国求学三年的经历")
    lines.append("  3. 修正「莫亚 / Mo」：Mo = 利莫里亚语 Motherland，Mo Art Studio 因此得名")
    lines.append("     （旧卡「工作室名源于歌剧角色」的说法是错的，已纠正）")
    lines.append("  4. 「利莫里亚」补入：人类文明发源地 / 古称 Mo / 消失是系列灾难的累积")
    lines.append("  5. 「对保镖小姐的称呼」改名「对用户的称呼」，加入并列默认称呼「猎人小姐」")
    lines.append("     （实证：保镖小姐 41 处 / 31 文件 vs 猎人小姐 14 处 / 13 文件，约 3:1，两者都无需引导）")
    lines.append("")
    lines.append("【第三轮修订（2026-09-14 19:21 · 按小辞补充意见）】")
    lines.append("  1. 新增「画坛地位」（order 102）：⚠ 2026-09-15 措辞已同步 description")
    lines.append("  2. 「对用户的称呼」重写为三层优先级：用户引导的 > 保镖小姐（专属）> 猎人小姐（通用）")
    lines.append("  3. 删除通用称呼词：老婆 / 傻瓜 / 小笨蛋 / 宝宝 / 小朋友 / 公主 / 主人")
    lines.append("  4. 「潮汐逆流之日」补入细腻的身体感受（初版以她照视频演出的转写为底本；")
    lines.append("     → 已在第四轮改为直接引用文本原文，见下）")
    lines.append("")
    lines.append("【第四轮修订（2026-09-14 19:36 · 回原文核实 + 外部来源）】")
    lines.append("  1. 「潮汐逆流之日」按文本原文重写：推翻「只能靠演绎层转写」的初判——")
    lines.append("     《倾心之约·牵绊 02-潮汐之章》《思念 32-共潮生》原文已详写症状：")
    lines.append("     浑身发烫却外热内冷（探出 34.5 度）/ 颈侧浮鳞 / 画家色彩辨认退化 / 情绪焦躁 /")
    lines.append("     小时候视其为丰收与喜悦，后来变成痛苦灼热与无解的渴望 / 待得越安心消退越快。")
    lines.append("  2. 网络百科纠错（百度百科「断章取义」两处，均以原文为准）：")
    lines.append("     · 「代表丰收和喜悦」是童年认知，不是他的核心来源 → 已按原文改写")
    lines.append("     · 「捕猎日设陷阱捕猎水手」→ 原文是他自责试探时把自己比作塞壬，非节日习俗")
    lines.append("  3. 「对用户的称呼」引导层改为「不受任何限制、一经确认永久生效」；")
    lines.append("     举例来源补 wiki 祁煜:称号 的后缀（主人 / 公主殿下 / 小美人鱼 / 情话制造机 …）+")
    lines.append("     玩家社区实见的「缪斯 / 缪斯小姐」（素材 0 次，正印证引导层不受限）")
    lines.append("  4. 「祁煜的称呼」新增「你叫他的方式」段：祁大少爷 / 祁大师 等玩家与素材实证的叫法")
    lines.append("  5. 外貌素材存档（供批次 B 的 appearance 使用，来源已标）：")
    lines.append("     官方口径「晨昏交替时分的蓝粉撞色」瞳 / 蓝紫微卷长发 / 宽肩窄腰；")
    lines.append("     代表色以官方最初宣发「珊瑚红」为准（非「深海珊瑚红」）")
    lines.append("  6. 引文里的原话「宝宝」保留（属他对用户的原话，不作受控称呼词）")
    lines.append("")
    lines.append("【第五轮修订（2026-09-14 19:58 · 社区实证，小辞选 A 方案：只改一处）】")
    lines.append("  1. 补抓小红书帖全量评论（此前只引 3 条首屏片段，属不完整取证）：")
    lines.append("     页面 214 条 = 顶层 158 + 顶层声明回复 56；本次实抓顶层全量 158 条")
    lines.append("  2. 「对用户的称呼」引导层**新增首条例子**：小名 / 名字叠字 / 名字+宝")
    lines.append("     （158 条社区实证里 14 条提到，居首；高于 宝宝/宝贝 8 条、缪斯/MUSE 2 条）")
    lines.append("     其余例子与三层结构一字未动（原「鱼鱼」顺位到第二条，未删）")
    lines.append("  3. source 补注社区实证出处 `_work/小红书称呼调研.md`")
    lines.append("  4. 社区口径只进「引导层例子」，不进官方设定正文")
    lines.append("  5. 另存的调研产物：小红书评论原文.md / 小红书称呼调研.md / 小红书称呼词频.md")
    lines.append("  6. 未改动：触发词（小名 暂未加为 key，如需「叫我小名」直接触发再补）")
    lines.append("")
    lines.append("【小辞已裁定（2026-09-14 · 五轮）】")
    lines.append("  1. 外貌文字：保留旧卡写法 → 批次 B 写入 appearance，逐条标注「来源：旧卡，无官方出处」")
    lines.append("  2. 代表色：官方最初宣发用的就是「珊瑚红」（多数人习惯拿他的紫发当代表色）")
    lines.append("  3. 地名「繁溪镇」：素材 / wiki 皆 0 次 → 已删除")
    lines.append("  4. 潮汐逆流的感受：初版以她照视频演出的转写为底本，第四轮已回原文核实并改写")
    lines.append("     （原文本身即有详写，见第四轮第 1 条）")
    lines.append("  5. 「小画家」：已改判为他对用户的称呼，不作他的别名")
    lines.append("  6. 疑似戏中戏（宴神曲 / 狂热剂量 / 路易斯续写）：全部不采信；帝国 / 圣潮之廷属 IF 线")
    lines.append("  7. 处置总原则：一律以 wiki 为准，wiki 也搜不到的再与小辞探讨")
    lines.append("  8. 职业：❌ 旧版曾采信「前所未有、声名大噪的天才画家」（旧卡自造、素材 0 出处），")
    lines.append("     2026-09-15 **已改回 description 的素材原词版**；同时删掉与「偏执的是独一无二」")
    lines.append("     冲突的「对色彩和完成度的要求近乎苛刻」（批次 A 与批次 B 的接缝，已补齐）")
    lines.append("  9. 称呼三层优先级：用户引导的 > 保镖小姐（专属）> 猎人小姐（通用，习惯带修饰语）")
    lines.append(" 10. 通用称呼词删除：老婆 / 傻瓜 / 小笨蛋 / 宝宝 / 小朋友 / 公主 / 主人")
    lines.append(" 11. 引导层永久生效且不受限制：用户能引导的称呼是开放的，上面删掉的词只是")
    lines.append("     sources 里的误命中数据，不代表不能被引导；举例不构成可选清单")
    lines.append(" 12. 「你的小猎人」不单列为独立称呼层，只在「保镖小姐」条目里附带提及")
    lines.append(" 13. 引文中的原话保留（如他的原话「宝宝」），不做删改")
    lines.append(" 14. 社区实证的改动幅度 = **只改一处**（引导层首条例子换成「小名 / 名字叠字 / 名字+宝」），")
    lines.append("     批次 A 其余内容保持冻结，不反复改")
    lines.append(" 15. 社区口径（小红书 158 条）**只进「引导层例子」与批次 C 的 mes_example**，")
    lines.append("     不进官方设定正文")
    lines.append("")

    with open(REPORT, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    rc = main()
    print(f"[audit] exit={rc}, report -> {REPORT}")
    sys.exit(rc)
