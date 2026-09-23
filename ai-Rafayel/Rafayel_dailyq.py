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
            _SNIPS = [x.get("text") for x in (json.load(f) or {}).get("snips") or []]
            _SNIPS = [x for x in _SNIPS if x]
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
    snip = random.choice(_SNIPS) if _SNIPS else None
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
        lines.append("然后接这样一句，把话头递给她（也是照原话说）：")
        lines.append(snip_txt)
    lines.append(
        "规矩：不许改字、不许加细节、不许在结尾升华讲道理；"
        "日常那句是你的原话，直接说就行，别解释它。"
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
