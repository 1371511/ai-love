# -*- coding: utf-8 -*-
"""
📅 每日统计落盘 —— `memory/{uid}_daily.json`，好感度要用的那几个数。

为什么单独一个文件：
  - `Rafayel_affinity.py` 是**只读**的（只算好感度），它不能写盘 ⇒ 写盘放这里。
  - 只有 bot 侧会写（她说话那一刻）；网页端永远只读。

⚠ 三条铁律
  ① **绝不因为写统计把对话搞挂** —— 异常一律吞掉，只打一行日志。
  ② 只统计**她说的话**：他主动打招呼 / 发说说那些不算她开口，
     否则「她主动」永远虚高（跟 `last_msg_at` 同一个口径）。
  ③ 日期用**同一个偏移**（`AUTO_GREET_TZ_OFFSET`）—— 整条链只该有一个「今天」。
"""

import glob
import json
import os
import time

from Rafayel_config import (
    AUTO_GREET_TZ_OFFSET, DAILY_STATS, MEMORY_DIR, SESSION_GAP_HOURS, USAGE_STATS,
)

_DAY_CAP = 400          # 最多留这么多天，别让文件无限长大


def _today():
    return time.strftime("%Y-%m-%d", time.localtime(time.time() + AUTO_GREET_TZ_OFFSET * 3600))


def _path(user_id):
    return os.path.join(MEMORY_DIR, "%s_daily.json" % user_id)


def _load(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)          # 先写临时文件再替换，防止写一半崩了丢数据


def record(user_id, gap_hours=None, media=False):
    """
    她说了句话 ⇒ 记一笔。返回更新后的 dict；关掉开关或失败则返回 None。

    字段：
      days           她开过口的日期（YYYY-MM-DD，去重排序）
      first_day      ⭐ 第一次聊天是哪天（**外部日志回填**才有，见 tools/backfill_daily.py）
                     ⚠ 一旦写进来就**永远保留**，别用 days[0] 覆盖它（回填的白做了）
      last_day       最近一次是哪天
      streak         连续天数（含今天；断了一天就归 1）
      her_initiated  她隔了一阵主动来找他的次数（gap >= SESSION_GAP_HOURS）
      media          她发过图 / 表情的条数
    """
    if not DAILY_STATS:
        return None
    try:
        path = _path(user_id)
        d = _load(path) or {}
        today = _today()

        days = [x for x in (d.get("days") or []) if isinstance(x, str)]
        if today not in days:
            days.append(today)
            days = sorted(days)[-_DAY_CAP:]

        # ⭐ `first_day` 是**外部日志回填**进来的（tools/backfill_daily.py），
        #    它比 days 里最早的那天还早 —— 一旦被覆盖，回填就白做了。
        #    ⇒ 有就原样留着；没有才退回「days 里最早那天」，再没有才用今天。
        first = str(d.get("first_day") or "").strip()
        if not first:
            first = days[0] if days else today

        # 连续天数：昨天也在 ⇒ +1；否则从今天重新起算。
        # ⚠ 判据是「上一条记录的 last_day」，不是「days 里有没有昨天」——
        #    days 里可能有上周的同一天，那是另一段连续。
        ystr = time.strftime("%Y-%m-%d",
                             time.localtime(time.time() + AUTO_GREET_TZ_OFFSET * 3600 - 86400))
        prev = int(d.get("streak") or 0)
        last = str(d.get("last_day") or "")
        if last == today:
            streak = max(1, prev)
        elif last == ystr:
            streak = max(1, prev) + 1
        else:
            streak = 1

        initiated = int(d.get("her_initiated") or 0)
        # gap 为 None = 她第一次开口（没有基准）⇒ 也算她主动
        if gap_hours is None or gap_hours >= SESSION_GAP_HOURS:
            initiated += 1

        m = int(d.get("media") or 0) + (1 if media else 0)

        out = {
            "user_id": user_id,
            "days": days,
            "first_day": first,
            "last_day": today,
            "streak": streak,
            "her_initiated": initiated,
            "media": m,
            "backfilled": bool(d.get("backfilled")),   # 这批天数里含外部日志回填的部分
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        # ⭐ 跨级解锁记录（牵绊度官方素材，见 Rafayel_affinity.init_unlocked）必须**原样带过去** ——
        #    这个 dict 每次都是重建的，漏了它 ⇒ 下一句对话就把解锁进度抹掉、素材会重复发。
        if isinstance(d.get("unlocked"), dict):
            out["unlocked"] = d["unlocked"]
        _save(path, out)
        return out
    except Exception as e:
        print("⚠️ 每日统计落盘失败（不影响对话）：%s" % e)
        return None


def load_unlocked(user_id):
    """读跨级解锁记录（`{uid}_daily.json` 的 `unlocked`）。没记过返回 None。"""
    d = _load(_path(user_id)) or {}
    u = d.get("unlocked")
    return u if isinstance(u, dict) else None


def save_unlocked(user_id, data):
    """
    写跨级解锁记录（**本文件的职责**：整条链上只有这里写 `{uid}_daily.json`）。

    ⚠ 跟 `record()` 一个脾气：异常一律吞掉，**绝不能因为记解锁把对话搞挂**。
    """
    try:
        path = _path(user_id)
        d = _load(path) or {}
        d["unlocked"] = data
        d.setdefault("user_id", user_id)
        d["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save(path, d)
        return data
    except Exception as e:
        print("⚠️ 跨级解锁记录落盘失败（不影响对话）：%s" % e)
        return None


# ---------------------------------------------------------------- 启动补底
# ⭐⭐ 2026-09-21 她定（起因：她 15 级的号**看不到**网页端「他说过的那句话」）：
#    `unlocked` **只由 QQ 端写**，而它只在**收到私聊**时才跑 ⇒ 两类用户会永远没有这条记录：
#      ① 功能上线**之前**就聊过的老用户；② 换机器 / 重装后还没私聊过的号。
#    而网页端那张卡是「**有内容才渲染**」⇒ 记录缺失 = 面板整张不出现（看着像坏了，其实没坏）。
#    ⇒ bot **启动时**扫一遍 memory，给「没有 unlocked」的用户补一次底。

def memory_uids():
    """memory/ 里「聊过」的纯数字 uid。

    ⚠ 只靠 `isdigit()` 就够：`{uid}_daily.json` / `{uid}_usage.json` / `{uid}_profile.json`
      取到的主干都是 `123_daily` 这种带下划线的，`isdigit()` 天然为 False ⇒ 顺带排掉。
      （跟 `auto_greet_scan` 一个口径，别再另立一套。）
    """
    out = []
    try:
        for p in glob.glob(os.path.join(MEMORY_DIR, "*.json")):
            uid = os.path.splitext(os.path.basename(p))[0]
            if uid.isdigit() and uid not in out:
                out.append(uid)
    except Exception:
        pass
    return sorted(out)


def backfill_unlocked(init_fn, level_fn, dry_run=False):
    """
    给「聊过、但还没有 `unlocked` 记录」的用户补一次底。返回 `(补了几个, 跳过几个, 名单)`。

    `init_fn(level)` = `Rafayel_affinity.init_unlocked`；`level_fn(uid, dir)` = `current_level`。
    （用传参而不是 import：`Rafayel_affinity` 只读、不反过来依赖本模块，别把这条单向搞反。）

    ⚠ 四条硬规矩：
      ① **只补缺** —— 已有 `unlocked` 的**一个字节都不碰**（否则会抹掉「真发过哪些」，
         素材会重发一遍）。判据用 `load_unlocked(uid) is not None`。
      ② **不补发历史** —— 只写记录、不推消息（跟「老用户首次接入」同一个口径，§19.7）。
      ③ **算不出等级（0）就跳过** —— 宁可不补，也不写一份 `level=0` 的错误底：
         那会让 `pending_unlock` 把历史素材当成新的，一级一条连着补发。
      ④ **异常一律吞掉** —— 补底失败绝不能拖住 bot 启动。
    """
    filled, skipped, names = 0, 0, []
    for uid in memory_uids():
        try:
            if load_unlocked(uid) is not None:      # ① 只补缺
                skipped += 1
                continue
            lv = int(level_fn(uid, MEMORY_DIR) or 0)
            if lv <= 0:                             # ③ 算不出来就不动
                skipped += 1
                continue
            if not dry_run:
                save_unlocked(uid, init_fn(lv))     # ② 只写记录，不发消息
            filled += 1
            names.append("%s(%d级)" % (uid, lv))
        except Exception:                           # ④
            skipped += 1
    return filled, skipped, names


def _usage_path(user_id):
    return os.path.join(MEMORY_DIR, "%s_usage.json" % user_id)


def _split_usage(usage):
    """
    把 DeepSeek 回包的 `usage` 拆成我们要的六个字段。

    DeepSeek 的结构（2026-09 实测）：
        {"prompt_tokens": N, "completion_tokens": M, "total_tokens": T,
         "prompt_tokens_details": {"cached_tokens": C}}
    ⭐ `cached_tokens` 就是**缓存命中**的部分 —— 它便宜得多，是省钱的关键指标
       （前缀缓存，见 2026-09-20 那批）。缓存未命中 = prompt_tokens - cached_tokens。
    """
    if not isinstance(usage, dict):
        return None
    p = int(usage.get("prompt_tokens") or 0)
    c = int(usage.get("completion_tokens") or 0)
    det = usage.get("prompt_tokens_details") or {}
    hit = int(det.get("cached_tokens") or 0) if isinstance(det, dict) else 0
    if hit > p:                       # 脏数据兜底，别让命中数超过输入数
        hit = p
    return {"prompt": p, "completion": c, "total": int(usage.get("total_tokens") or (p + c)),
            "cache_hit": hit, "cache_miss": p - hit}


def record_usage(user_id, usage):
    """
    记一笔 token 消耗 ⇒ `memory/{uid}_usage.json`。返回更新后的 dict。

    ⚠ 跟 `record()` 同一个脾气：异常一律吞掉，**绝不能因为记用量把对话搞挂**。
    """
    if not USAGE_STATS:
        return None
    part = _split_usage(usage)
    if not part:
        return None
    try:
        path = _usage_path(user_id)
        d = _load(path) or {}
        today = _today()

        def add(a, b):
            return int(a or 0) + int(b or 0)

        out = {
            "user_id": user_id,
            "calls": add(d.get("calls"), 1),
            "prompt": add(d.get("prompt"), part["prompt"]),
            "completion": add(d.get("completion"), part["completion"]),
            "total": add(d.get("total"), part["total"]),
            "cache_hit": add(d.get("cache_hit"), part["cache_hit"]),
            "cache_miss": add(d.get("cache_miss"), part["cache_miss"]),
        }

        # 按天明细：以后要做「每人每天上限」「哪天最费」就靠这个
        days = d.get("days") if isinstance(d.get("days"), dict) else {}
        t = days.get(today) or {}
        days[today] = {
            "calls": add(t.get("calls"), 1),
            "prompt": add(t.get("prompt"), part["prompt"]),
            "completion": add(t.get("completion"), part["completion"]),
            "total": add(t.get("total"), part["total"]),
            "cache_hit": add(t.get("cache_hit"), part["cache_hit"]),
            "cache_miss": add(t.get("cache_miss"), part["cache_miss"]),
        }
        if len(days) > _DAY_CAP:
            for k in sorted(days)[:-_DAY_CAP]:
                days.pop(k, None)
        out["days"] = days
        out["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save(path, out)
        return out
    except Exception as e:
        print("⚠️ token 用量落盘失败（不影响对话）：%s" % e)
        return None


if __name__ == "__main__":
    import sys
    uid = sys.argv[1] if len(sys.argv) > 1 else "cli"
    print(record(uid))
