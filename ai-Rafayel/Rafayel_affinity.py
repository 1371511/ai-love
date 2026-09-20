# -*- coding: utf-8 -*-
"""
🌱 好感度（affinity）—— 纯计算模块，只读不写。

设计取舍
--------
- **只依赖标准库**：不 import config / profile，方便网页端和 CLI 都能直接调。
  参数集中在本文件顶部的常量里（以后要挪进 `Rafayel_config.py` 也随时可以）。
- **只读**：本模块**绝不写任何文件**。写盘由调用方决定（以后是 bot 侧落盘
  `memory/{uid}_affinity.json`，网页端永远只读）。
- ⭐ **好感度是系统数据，绝不进 QQ 对话**（一进聊天就破「不露机器人那一面」），
  只走网页端 —— 这条别破。

⚠ 数据缺口（2026-09-21 写实）
  目前 `memory/{uid}.json` 里**没有**每日统计（哪天聊过、连续几天、谁先开口），
  所以 days / streak / her_initiated 现在只能算成 0。
  ⇒ 用 `missing` 字段**明说缺什么**，而不是悄悄给个假数字。
  等 bot 侧补了 `memory/{uid}_daily.json`，这里自动就能算准（读得到就合并）。
"""

import json
import os
import time

# ---------------------------------------------------------------- 评分参数
# ⚠ 这些数字是初版，**等真机跑一段时间再调**。改这里就够了。
PT_PER_TURN = 1          # 每一轮对话
PT_PER_FACT = 10         # 她让他记住一件事（进 key_facts）—— 信任，最值钱
PT_PER_PROFILE = 3       # 画像里每多一条（喜欢/讨厌/特质）
PT_NEW_DAY = 5           # 新的一天首次互动
PT_STREAK_PER_DAY = 3    # 连续第 N 天，额外 +3×N
PT_STREAK_CAP = 15       # 连续加分封顶
PT_SHE_INITIATED = 8     # 她隔了很久主动来找他
PT_MEDIA = 2             # 她发图/表情（暂未落盘，预留）

# 等级：⭐ **名字是角色内容，等她定稿**。现在这套是占位。
LEVELS = [
    (0,   "初识"),
    (30,  "有点在意"),
    (80,  "在意"),
    (150, "喜欢"),
    (250, "很在意"),
    (400, "心上人"),
]


def level_of(score):
    """分数 → (第几级从1开始, 等级名, 下一级阈值 or None)"""
    idx, name, nxt = 1, LEVELS[0][1], LEVELS[1][0] if len(LEVELS) > 1 else None
    for i, (threshold, nm) in enumerate(LEVELS):
        if score >= threshold:
            idx = i + 1
            name = nm
            nxt = LEVELS[i + 1][0] if i + 1 < len(LEVELS) else None
    return idx, name, nxt


def _read_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def compute(user_id, memory_dir):
    """
    算出某个用户的好感度。缺的数据返回 0 并记进 `missing`，**不编造**。
    """
    mem = _read_json(os.path.join(memory_dir, "%s.json" % user_id)) or {}
    prof = _read_json(os.path.join(memory_dir, "%s_profile.json" % user_id))
    if prof is None:
        # 兼容：画像可能躺在备份目录里（本机 cli 就是这样）
        prof = _read_json(os.path.join(memory_dir, "_bak-20260918-cli",
                                       "%s_profile.json" % user_id)) or {}
    daily = _read_json(os.path.join(memory_dir, "%s_daily.json" % user_id))

    missing = []

    turns = int(mem.get("turn_count") or 0)
    facts = mem.get("key_facts") or []
    prof_n = sum(len(prof.get(k) or []) for k in ("likes", "dislikes", "traits"))

    score = turns * PT_PER_TURN + len(facts) * PT_PER_FACT + prof_n * PT_PER_PROFILE

    days = streak = she_initiated = media = 0
    if daily:
        days = len(daily.get("days") or [])
        streak = int(daily.get("streak") or 0)
        she_initiated = int(daily.get("her_initiated") or 0)
        media = int(daily.get("media") or 0)
        score += days * PT_NEW_DAY
        score += min(streak * PT_STREAK_PER_DAY, PT_STREAK_CAP)
        score += she_initiated * PT_SHE_INITIATED
        score += media * PT_MEDIA
    else:
        missing.append("每日统计（哪天聊过 / 连续几天 / 谁先开口 / 发图数）"
                       "—— 需要 bot 侧落盘 memory/%s_daily.json" % user_id)

    if not mem:
        missing.append("主记忆 memory/%s.json（这个人还没跟他聊过）" % user_id)
    if not prof:
        missing.append("画像 memory/%s_profile.json" % user_id)

    idx, name, nxt = level_of(score)
    saved_at = mem.get("saved_at") or ""
    first_day = saved_at.split(" ")[0] if saved_at else ""

    return {
        "user_id": user_id,
        "name": prof.get("name") or "",
        "score": score,
        "level": idx,
        "level_name": name,
        "next_at": nxt,
        "to_next": (nxt - score) if nxt else 0,
        "turns": turns,
        "facts": len(facts),
        "profile_items": prof_n,
        "days": days,
        "streak": streak,
        "she_initiated": she_initiated,
        "last_active": saved_at,
        "first_day": first_day,
        "likes": prof.get("likes") or [],
        "dislikes": prof.get("dislikes") or [],
        "traits": prof.get("traits") or [],
        "milestones": [],          # 批 2 再填（跨级时 LLM 写一句）
        "topics": [],              # 批 2 再填（近期话题，不存原文）
        "missing": missing,
    }


def format_report(uid, memory_dir):
    """CLI 用：打印一份人能读的摘要。"""
    a = compute(uid, memory_dir)
    L = []
    L.append("%s（%s）" % (a["name"] or "未填称呼", uid))
    L.append("  好感度 %d  → 第 %d 级「%s」%s"
             % (a["score"], a["level"], a["level_name"],
                ("，距下一级还差 %d" % a["to_next"]) if a["next_at"] else "（已封顶）"))
    L.append("  对话 %d 轮 · 记住的事 %d 条 · 画像 %d 条"
             % (a["turns"], a["facts"], a["profile_items"]))
    L.append("  互动 %d 天 · 连续 %d 天 · 她主动 %d 次" % (a["days"], a["streak"], a["she_initiated"]))
    if a["missing"]:
        L.append("  ⚠ 缺数据（目前算不出来的）：")
        for m in a["missing"]:
            L.append("     - " + m)
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    MEM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory")
    uid = sys.argv[1] if len(sys.argv) > 1 else "cli"
    print(format_report(uid, MEM_DIR))
