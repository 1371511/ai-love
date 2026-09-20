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

import json
import os
import time

from Rafayel_config import (
    AUTO_GREET_TZ_OFFSET, DAILY_STATS, MEMORY_DIR, SESSION_GAP_HOURS,
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
        _save(path, out)
        return out
    except Exception as e:
        print("⚠️ 每日统计落盘失败（不影响对话）：%s" % e)
        return None


if __name__ == "__main__":
    import sys
    uid = sys.argv[1] if len(sys.argv) > 1 else "cli"
    print(record(uid))
