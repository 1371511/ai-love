# -*- coding: utf-8 -*-
"""
「她问起你今天怎么过的」—— 日常问答（2026-09-24 接进来）。

她定的三条口径（2026-09-24）：
  ① 互动句 = **从真语料里摘的现成短句**，不是我写、也不是模型现编 ⇒ 零编造
  ② **不带图**（问答场景发图会打断节奏）
  ③ **同一天她问第二次 ⇒ 换新一条**（不是复用同一条）
     ⇒ 合起来其实就是：**每条只发一次，发完为止**，跟"一天一条"无关

⭐⭐ 最根本的一条：日常原句**照原话说，一个字都不改**
   （02:22 她否掉「改口」时定的 —— 加任何细节都是我的话，越加越不像他）

分层：config ← profile ← memory ← daily ← **dailyq** ← llm ← chat
  ⚠ 只依赖 config / daily / qzone_auto 的**纯函数**，绝不 import llm（否则成环）。
  ⚠ 复用 `Rafayel_qzone_auto.render_text` 做「用户」→ 称呼的替换 —— 不另写第三份兜底值。

⚠ 两条铁律（跟世界书同一个口径）：
  ① 注入文本**只进 request_messages**，绝不写回 cm.messages（落盘就会变成常驻人设）
  ② 池子读不到 ⇒ **降级返回空串**，绝不让日常问答把对话搞挂（人设卡才需要报错）
"""
import json
import os
import random

from Rafayel_config import DAILY_POOL, DAILY_QA, DAILY_SNIPS
from Rafayel_daily import daily_seen, daily_seen_add
from Rafayel_qzone_auto import render_text

# ⭐ 互动短句只从**「抛回给她」**这一类挑（2026-09-24 她指出「你嫌弃？」接不上之后定的）。
#   理由：这类**不依赖前文**（你呢 / 你说呢 / 所以你今天过得怎么样），接在任何日常后面都成立；
#   而「试探 / 邀请 / 开涮」三类都藏着前提（得他先展示什么、得她先表现出兴趣），
#   **随机配必然时不时接不上**。它们仍留在 JSON 里，等以后能做「按内容配」再放出来。
SNIP_GROUPS = ("A 抛回给她",)

# 触发词：她**主动问**才走（她说日常事件不主动推，只由她提起才触发）
TRIGGERS = [
    "今天怎么过", "今天过得", "怎么过的", "过得怎么样",
    "今天做了什么", "今天干什么", "今天干吗", "今天怎么样",
    "在干嘛", "在干什么", "在忙什么", "忙什么呢", "忙什么",
    "最近怎么样", "最近在忙", "今天一天",
]

# 池子进程内缓存（静态文件，没必要每轮读盘）
_POOL = None
_SNIPS = None
_LOADED = False


def _load():
    global _POOL, _SNIPS, _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        with open(DAILY_POOL, encoding="utf-8") as f:
            _POOL = (json.load(f) or {}).get("entries") or []
    except Exception as e:
        print("⚠️ 日常池读取失败（日常问答停用）：%s" % e)
        _POOL = []
    try:
        with open(DAILY_SNIPS, encoding="utf-8") as f:
            _SNIPS = [(x.get("group"), x.get("text"))
                      for x in (json.load(f) or {}).get("snips") or []]
            _SNIPS = [(g, t) for g, t in _SNIPS if t]
    except Exception as e:
        print("⚠️ 互动短句池读取失败（只说日常原句）：%s" % e)
        _SNIPS = []


def is_daily_question(user_message):
    """她是不是在问「你今天怎么过的」。⚠ 空消息不算。"""
    if not DAILY_QA:
        return False
    m = (user_message or "").strip()
    if not m:
        return False
    return any(t in m for t in TRIGGERS)


def pick(user_id):
    """
    挑一条**她没听过的**日常。

    返回 (entry, snip)；挑不出来（池子空）返回 (None, None)。
    ⚠ 挑中**不立刻标记** —— 由调用方在**真的发出去之后**再 `mark`，
       否则发送失败也会把它算成"说过了"（跟 record_proactive 同一个道理）。
    """
    _load()
    if not _POOL:
        return None, None
    seen = set(daily_seen(user_id))
    fresh = [e for e in _POOL if e.get("id") not in seen]
    if not fresh:
        fresh = list(_POOL)          # 都听过了 ⇒ 从头再来一轮
    e = random.choice(fresh)
    # 只从启用中的类别挑；万一那类被清空了，退回全部（不至于一句话都接不上）
    enabled = [t for g, t in _SNIPS if g in SNIP_GROUPS] or [t for _, t in _SNIPS]
    snip = random.choice(enabled) if enabled else None
    return e, snip


def mark(user_id, entry):
    """把这条记成"说过了"。由调用方在发出之后调用。"""
    if not entry:
        return False
    return daily_seen_add(user_id, entry.get("id") or "")


def render(user_id, entry, snip):
    """
    渲染注入文本。**照原话说**，一个字都不改。

    ⚠ 禁 ASCII 双引号与 markdown 加粗（跟 system_prompt 同一个约定，会被原样注入）。
    ⚠ 「用户」占位符在这里换成她的称呼（复用 qzone_auto 的 render_text）。
    """
    if not entry:
        return ""
    text = render_text(entry, user_id)
    snip_txt = render_text({"text": snip}, user_id) if snip else ""

    lines = ["【她问起你今天做了什么】",
             "你今天其实是这样过的（照原话说，一个字都别改）：",
             text]
    if snip_txt:
        # ⭐ 2026-09-24 她指出随机配的短句会接不上（「你嫌弃？」前面是句感慨 ⇒ 断了）
        #   ⇒ 参考句只给**口气**，接得上就照说，接不上让他自己接一句。
        lines.append("说完把话头递给她，一句就够，要短。下面这句是口气参考，接得上就照着说：")
        lines.append(snip_txt)
        lines.append("接不上就自己接一句——反问她、问她今天过得怎么样都行。")
    lines.append(
        "规矩：日常那句不许改字、不许加细节、也别解释它；"
        "你接的那一句不许升华讲道理、不许报时间。"
    )
    return "\n".join(lines)


def hint_for(user_id, user_message, on_pick=None):
    """
    主入口：命中 ⇒ 返回 (注入文本, entry)；没命中 ⇒ ("", None)。

    on_pick：可选回调（给测试注入用的，生产不用传）—— 拿到刚挑中的条目。
    ⚠ **不在这里 mark**：调用方要在消息真的发出去之后再 `mark`。
    """
    if not is_daily_question(user_message):
        return "", None
    entry, snip = pick(user_id)
    if not entry:
        return "", None
    if on_pick:
        on_pick(entry)
    return render(user_id, entry, snip), entry
