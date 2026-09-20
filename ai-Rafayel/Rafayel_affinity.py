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

# ---------------------------------------------------------------- 等级（官方四档）
# ⭐⭐ 真相源是 `card/affinity.md` 的「机器可读」段（`CURVE=`），这里**不写死数字**。
#    规则（从官方累计分 232 / 525 / 1765 / 6140 反推，四档全对得上）：
#      **升到 L 级，花的是「L-1 所在档位」的每级分** —— 跨档那一步仍按上一档的价。
#    例：30→31 花 8（心动价），31→32 起才花 15（倾情价）⇒ 满级 246 累计 6140 分。
MD_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "card", "affinity.md")

FALLBACK_CURVE = [("心动", 1, 30, 8), ("倾情", 31, 50, 15),
                  ("眷恋", 51, 100, 25), ("情衷", 101, 246, 30)]


def _parse_curve(text):
    """从 md 的 ``` 块里抠 `CURVE=` 那一行。"""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("CURVE="):
            out = []
            for item in s[len("CURVE="):].split(";"):
                p = [x.strip() for x in item.split(",")]
                if len(p) == 4:
                    out.append((p[0], int(p[1]), int(p[2]), int(p[3])))
            if out:
                return out
    return None


def load_curve():
    """读真相源 md；读不到才退回内置副本（数字与 md 一致，但改了 md 就不跟着变）。"""
    try:
        with open(MD_PATH, encoding="utf-8") as f:
            c = _parse_curve(f.read())
        if c:
            return c, "md"
    except Exception:
        pass
    return list(FALLBACK_CURVE), "fallback"


CURVE, CURVE_SRC = load_curve()
MAX_LEVEL = max(hi for _n, _lo, hi, _r in CURVE)


def _build(curve):
    rate = {}
    for name, lo, hi, r in curve:
        for L in range(lo, hi + 1):
            rate[L] = r
    last = curve[-1][3]
    cum = [0, 0]                       # cum[L] = 升到 L 级所需累计分
    for L in range(2, MAX_LEVEL + 1):
        cum.append(cum[L - 1] + rate.get(L - 1, last))
    return cum, rate


CUM, RATE_OF = _build(CURVE)


def tier_of(level):
    """级别 → 档位名（心动 / 倾情 / 眷恋 / 情衷）"""
    for name, lo, hi, _r in CURVE:
        if lo <= level <= hi:
            return name
    return CURVE[-1][0]


def level_of(score):
    """分数 → (级别 1~246, 档位名, 下一级阈值 or None)"""
    lo, hi = 1, MAX_LEVEL
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if CUM[mid] <= score:
            lo = mid
        else:
            hi = mid - 1
    L = lo
    return L, tier_of(L), (CUM[L + 1] if L < MAX_LEVEL else None)


# ---------------------------------------------------------------- 等级 → 语气
# ⭐ **绝不许他说出口**：等级/分数/档位名是后台数据，一出口就破「不露机器人那一面」。
# ⚠ system_prompt 禁 `**` 与 ASCII 双引号 ⇒ 下面一律用「」或不用引号。
_TIER_TONE = {
    "心动": (
        "还不算熟。你会留意她，但不会主动凑上去。\n"
        "  - 嘴上不饶人，答话带点刺；被夸会别扭，会岔开话题。\n"
        "  - 关心藏在行动里（顺手替她把事办了），嘴上不承认是为她。\n"
        "  - 不主动说想她、不吃醋、不做亲昵动作；她凑太近你会退半步。"
    ),
    "倾情": (
        "已经熟了，会开始找话说，也会记得她提过的小事。\n"
        "  - 会主动起话题、会拿她打趣，玩笑里带一点真心。\n"
        "  - 她跟别人亲近时会有点不自在，但不挑明，顶多语气凉半度。\n"
        "  - 可以有近距离的动作（碰一下、揉一下头发），做完自己先装没事。"
    ),
    "眷恋": (
        "她在你心里已经占位置了，你自己也知道。\n"
        "  - 会主动找她、会担心她（问得随意，其实是一直惦记着）。\n"
        "  - 想她的话说得绕，别直给（比如今天画到一半想起个事）。\n"
        "  - 动作可以更近：靠过去、把她揽过来、摸头，停留得久一点。\n"
        "  - 被她依赖时嘴硬心软；她真要走你会留，但不说「别走」这两个字。"
    ),
    "情衷": (
        "认定了。在她面前你不用绷着。\n"
        "  - 可以直白地说在意、说想见她，只是说一半留一半，还是你那个口气。\n"
        "  - 会为她破例、会把她排在工作前面，也会承认（语气平常，不像告白）。\n"
        "  - 亲密是自然的：抱、靠在她肩上、牵手，不用找理由。\n"
        "  - 她难受时你先稳住她再说别的；她撒娇你接得住，还回一句带刺的温柔。"
    ),
}


def level_prompt(level, tier):
    """
    给 system 用的「你和她现在到哪一步了」。等级越高，他越亲。

    ⚠ 两条硬规矩（写进提示里，让模型照做）：
      ① **一个字都不许说出口** —— 不许报等级、分数、档位名；
         她问起来就绕开，或者说「你想知道的话，自己感觉」。
      ② 只调**相处方式**，不改人设 —— 他还是他，只是对她更近一点。
    """
    tone = _TIER_TONE.get(tier) or _TIER_TONE["心动"]
    return (
        "## 💞 你和她现在到哪一步了（%s %d 级）\n"
        "（这一段只给你自己看，一个字都不许说出口：不许报等级、分数、档位名，\n"
        " 她问起来就绕开，或者说「你想知道的话，自己感觉」。）\n"
        "%s\n"
        "⭐ 这是渐变的：别因为一句话就从冷淡跳到黏人，跟着你们聊了多久慢慢来。\n"
        "⭐ 尺度照旧：18+ 暧昧张力级，不写性行为过程。"
    ) % (tier, level, tone)


def tone_for(user_id, memory_dir):
    """算这个用户当前的等级，返回对应的语气提示（算不出来返回空串）。"""
    try:
        a = compute(user_id, memory_dir)
        return level_prompt(a["level"], a["tier"])
    except Exception:
        return ""


def _today():
    """今天（服务器时区；整条链都按服务器本地时间算，别再新开偏移）。"""
    return time.strftime("%Y-%m-%d", time.localtime())


def days_since(day):
    """`YYYY-MM-DD` 距今几天（含首尾 ⇒ 同一天返回 1）。解析不了返回 0。"""
    try:
        y, m, d = (int(x) for x in str(day).split("-")[:3])
        n = time.localtime()
        a = (n.tm_year * 12 + n.tm_mon) * 31 + n.tm_mday
        b = (y * 12 + m) * 31 + d
        return max(0, a - b) + 1
    except Exception:
        return 0


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
    usage = _read_json(os.path.join(memory_dir, "%s_usage.json" % user_id))

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
        # ⚠ `missing` 是**给后台看的**（日志/CLI），**绝不能直接显示给用户** ——
        #   「bot 侧落盘 memory/xxx_daily.json」这种话一上页面就破「不露机器人那一面」。
        #   网页端要显示的是 `has_daily=False` 那套温和文案。
        missing.append("每日统计（哪天聊过 / 连续几天 / 谁先开口 / 发图数）"
                       "—— 需要 bot 侧落盘 memory/%s_daily.json" % user_id)

    if not mem:
        missing.append("主记忆 memory/%s.json（这个人还没跟他聊过）" % user_id)
    if not prof:
        missing.append("画像 memory/%s_profile.json" % user_id)

    # 💰 token 消耗（memory/{uid}_usage.json，bot 侧落盘；老用户从接入那天才开始有）
    tokens = calls = t_in = t_out = cache_hit = 0
    cache_rate = 0.0
    if usage:
        calls = int(usage.get("calls") or 0)
        t_in = int(usage.get("prompt") or 0)
        t_out = int(usage.get("completion") or 0)
        cache_hit = int(usage.get("cache_hit") or 0)
        tokens = int(usage.get("total") or (t_in + t_out))
        # 命中率只看**输入**那半边（输出不进缓存）
        cache_rate = (cache_hit / t_in) if t_in else 0.0

    level, tier, nxt = level_of(score)
    # ⚠ 只回填了「第一次是哪天」（对照表模式）时 days 是空的 ⇒ 仍然算**没有**每日统计，
    #    网页端该显示「—」而不是 0（她：登进来看到一串 0 很打击积极性）。
    has_daily = bool(daily) and days > 0
    t_lo = t_hi = 0
    for name, lo, hi, _r in CURVE:
        if name == tier:
            t_lo, t_hi = lo, hi
            break
    saved_at = mem.get("saved_at") or ""
    # ⭐ `first_day` **只能来自外部日志回填**（`{uid}_daily.json` 的 `first_day`）：
    #    bot 自己不记「第一次是哪天」（所有时间戳字段都是「最后一次」，
    #    且 save_memory 用 tmp+replace ⇒ 连文件创建时间都被刷成最后一次写入）。
    #    ⚠ 千万别拿 `saved_at` 当首次 —— 那是**最后一次存盘**的日子（2026-09-21 修）。
    first_day = ""
    if daily:
        first_day = str(daily.get("first_day") or "").strip()
        if not first_day:
            ds = [x for x in (daily.get("days") or []) if isinstance(x, str)]
            first_day = min(ds) if ds else ""
    known_days = days_since(first_day) if first_day else 0

    return {
        "user_id": user_id,
        "name": prof.get("name") or "",
        "score": score,
        "level": level,                 # 官方级别 1~246
        "tier": tier,                   # 心动 / 倾情 / 眷恋 / 情衷
        "level_name": tier,             # 旧字段，留着兼容，值同 tier
        "tier_lo": t_lo,
        "tier_hi": t_hi,
        "next_at": nxt,
        "to_next": (nxt - score) if nxt else 0,
        "turns": turns,
        "facts": len(facts),
        "profile_items": prof_n,
        "days": days,
        "streak": streak,
        "she_initiated": she_initiated,
        "has_daily": has_daily,          # ⭐ 网页端按这个决定显示数字还是「—」
        "has_usage": bool(usage),        # 有没有落过 token 用量
        "calls": calls,                  # 累计请求次数
        "tokens": tokens,                # 累计 token（输入+输出）
        "tokens_in": t_in,
        "tokens_out": t_out,
        "cache_hit": cache_hit,
        "cache_rate": cache_rate,        # 输入侧的缓存命中率 0~1
        "last_active": saved_at,
        "first_day": first_day,          # ⭐ 只有外部日志回填过才有；没有就是 ""
        "known_days": known_days,        # 认识第 N 天（0 = 不知道，网页端显示「—」）
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
    L.append("  好感度 %d  → %s %d 级%s"
             % (a["score"], a["tier"], a["level"],
                ("，距 %d 级还差 %d 分" % (a["level"] + 1, a["to_next"]))
                if a["next_at"] else "（已满级）"))
    L.append("  %s 第 %d 级（本档 %d~%d 级，满级 %d 需 %d 分）"
             % (a["tier"], a["level"] - a["tier_lo"] + 1,
                a["tier_lo"], a["tier_hi"], MAX_LEVEL, CUM[MAX_LEVEL]))
    L.append("  对话 %d 轮 · 记住的事 %d 条 · 画像 %d 条"
             % (a["turns"], a["facts"], a["profile_items"]))
    if a["has_usage"]:
        L.append("  已用 %d token（入 %d / 出 %d）· 缓存命中 %.0f%% · 请求 %d 次"
                 % (a["tokens"], a["tokens_in"], a["tokens_out"],
                    a["cache_rate"] * 100, a["calls"]))
    else:
        L.append("  ⚠ 还没落过 token 用量（memory/%s_usage.json）" % uid)
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
