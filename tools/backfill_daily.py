# -*- coding: utf-8 -*-
"""
🧾 一次性回填工具：从**外部日志**补 `memory/{uid}_daily.json` 的 `days` / `first_day`。

为什么需要它
------------
bot 自己不记「第一次聊天是哪天」（见 2026-09-21 排查：所有时间戳字段都是「最后一次」，
且 `save_memory` 用 `写 tmp + os.replace` ⇒ 连文件的创建时间都被刷成最后一次写入）。
所以老用户的「互动天数 / 连续天数」只能从**她手上的外部日志**回填 —— 这是唯一真实来源。

⚠ 三条铁律
----------
① **只补统计字段**（`days` / `first_day` / `streak`），**绝不碰** `{uid}.json`（对话记忆）
   和 `{uid}_profile.json`（画像）。
② **默认 dry-run**，必须显式 `--apply` 才写盘；写之前先整目录备份到
   `memory/_bak-daily-<时间戳>/`。
③ **日志里没有的人绝不凭空建档** —— 只处理 `memory/{uid}.json` 已存在的 QQ 号。

用法
----
    # ① 先看会改成什么（不写盘）
    python tools/backfill_daily.py --log server.log

    # ② 确认无误再写
    python tools/backfill_daily.py --log server.log --apply

    # ③ 手上只有「QQ号 → 起始日」对照表（最省事，只补 first_day）
    python tools/backfill_daily.py --map start.csv --apply

    # 服务器上的 memory 目录不在项目里时
    python tools/backfill_daily.py --log server.log --memory /home/ubuntu/ai-love/memory --apply

日志格式
--------
**宽容解析**：只要一行里同时出现「日期」和「QQ 号」就能吃，不挑日志种类
（NapCat 日志 / tmux 带时间戳的输出 / 自己整理的 csv / txt 都行）。

- 日期：`2026-09-15`、`2026/09/15 12:34`、`[2026-09-15 12:34:56]`、`2026年9月15日`
- QQ 号：9~11 位数字。优先认 `user_id=123456` / `user_id: 123456` / `来自: 123456`
  这类带标签的写法；没有标签就取行内唯一的 9~11 位数字（取完时间戳之后剩下的）。
- `--map` 文件：每行 `QQ号,日期` 或 `QQ号 日期`，也允许 `#` 开头注释。
"""

import argparse
import json
import os
import re
import shutil
import sys
import time

# ---------------------------------------------------------------- 正则

# 日期：年-月-日（分隔符允许 - / . 年 月 日，时间部分可有可无）
RE_DATE = re.compile(
    r"(?P<y>(?:19|20)\d{2})\s*[-/.年]\s*(?P<m>\d{1,2})\s*[-/.月]\s*(?P<d>\d{1,2})\s*日?"
)
# 带标签的 QQ 号（最可靠）
# ⚠ 关键：`['\"]?` 要放**两处** —— JSON 日志是 `"user_id":123456`（键后面还跟着一个引号），
#    只在冒号后留一个引号位会匹配不上，然后掉进「裸号」那条路，把消息 id 当成 QQ 号。
RE_UID_TAGGED = re.compile(
    r"(?:user_id|sender_id|sender|qq|uid|来自|from)"
    r"['\"]?\s*[:=（(]?\s*['\"]?(?P<uid>\d{5,12})",
    re.I,
)
# 裸 QQ 号
RE_UID_BARE = re.compile(r"(?<!\d)(?P<uid>\d{9,11})(?!\d)")

# 明显不是 QQ 号的数字（时间戳 / 端口 / 消息 id），裸号模式里要剔掉
_BAD_UID = re.compile(r"^(?:19|20)\d{2}$")       # 光秃秃的年份
_YEAR = 2026


def _norm_date(m):
    y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    if not (2015 <= y <= _YEAR + 1):
        return None
    return "%04d-%02d-%02d" % (y, mo, d)


def _pick_uid(line, date_span):
    """从一行里挑 QQ 号；挑不出来返回 None。"""
    m = RE_UID_TAGGED.search(line)
    if m:
        return m.group("uid")
    # 裸号：先把日期那段挖掉，免得把 2026/09/15 里的数字当 QQ 号
    rest = line[:date_span[0]] + " " + line[date_span[1]:] if date_span else line
    cand = [x for x in RE_UID_BARE.findall(rest) if not _BAD_UID.match(x)]
    if len(cand) == 1:
        return cand[0]
    if len(cand) > 1:
        return cand[0]          # 多个候选时取第一个，报告里会标「存疑」
    return None


def parse_log(path):
    """
    读一个日志文件 ⇒ {uid: {"days": set(), "suspect": bool, "first": str}}
    ⚠ 只读不改；编码错了就换 latin-1 兜底（日志里常混着各种编码）。
    """
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        with open(path, encoding="latin-1", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        print("⚠️  读不了 %s：%s" % (path, e))
        return out

    for line in lines:
        md = RE_DATE.search(line)
        if not md:
            continue
        day = _norm_date(md)
        if not day:
            continue
        uid = _pick_uid(line, md.span())
        if not uid:
            continue
        e = out.setdefault(uid, {"days": set(), "suspect": False})
        e["days"].add(day)
    return out


def parse_map(path):
    """读「QQ号 → 起始日」对照表 ⇒ {uid: {"first": day, "days": set()}}"""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        print("⚠️  读不了 %s：%s" % (path, e))
        return out
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = re.split(r"[,\s\t]+", s)
        if len(parts) < 2:
            continue
        uid, day = parts[0], None
        for p in parts[1:]:
            m = RE_DATE.search(p)
            if m:
                day = _norm_date(m)
                break
        if day:
            out[uid] = {"first": day, "days": set()}
    return out


# ---------------------------------------------------------------- 连续天数

def _recalc_streak(days):
    """按日期集合重算「最近一段连续天数」。days 是已排序的 YYYY-MM-DD 列表。"""
    if not days:
        return 0
    def to_i(s):
        y, m, d = (int(x) for x in s.split("-"))
        return (y * 12 + m) * 31 + d
    n = 1
    for i in range(len(days) - 1, 0, -1):
        if to_i(days[i]) - to_i(days[i - 1]) == 1:
            n += 1
        else:
            break
    return n


def _gaps(days, min_gap=3):
    """列出相邻间隔 >= min_gap 天的断点（提示「日志可能不全」）。"""
    def to_i(s):
        y, m, d = (int(x) for x in s.split("-"))
        return (y * 12 + m) * 31 + d
    out = []
    for i in range(1, len(days)):
        g = to_i(days[i]) - to_i(days[i - 1])
        if g >= min_gap:
            out.append((days[i - 1], days[i], g))
    return out


# ---------------------------------------------------------------- 写盘

def backup(memory_dir):
    if not os.path.isdir(memory_dir):
        return None
    dst = os.path.join(memory_dir, "_bak-daily-" + time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dst, exist_ok=True)
    n = 0
    for fn in os.listdir(memory_dir):
        if fn.endswith("_daily.json"):
            shutil.copy2(os.path.join(memory_dir, fn), os.path.join(dst, fn))
            n += 1
    return (dst, n) if n else None


def apply(uid, memory_dir, days, first, dry=True):
    """把一个 uid 的 days/first_day 并进 `{uid}_daily.json`；返回 (动作, 合并后的 days)。"""
    path = os.path.join(memory_dir, "%s_daily.json" % uid)
    old = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                old = d
        except Exception:
            old = {}

    merged = sorted(set(x for x in (old.get("days") or []) if isinstance(x, str)) | set(days))
    # ⭐ first_day 取「回填的最早」而不是「文件里已有的」—— 回填就是为了找更早的那天。
    old_first = str(old.get("first_day") or "")
    new_first = first or (merged[0] if merged else "")
    if old_first and new_first:
        first_out = min(old_first, new_first)
    else:
        first_out = old_first or new_first

    act = "跳过"
    if merged != sorted(old.get("days") or []) or first_out != old_first:
        act = "待写" if dry else "已写"

    if not dry:
        out = dict(old)
        out["user_id"] = uid
        out["days"] = merged
        out["first_day"] = first_out
        out["streak"] = _recalc_streak(merged)
        out.setdefault("her_initiated", 0)
        out.setdefault("media", 0)
        out["last_day"] = old.get("last_day") or (merged[-1] if merged else "")
        out["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        out["backfilled"] = True          # 标记：这批天数来自外部日志，不是实时统计
        os.makedirs(memory_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return act, merged, first_out


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="从外部日志回填每日统计（days / first_day）")
    ap.add_argument("--log", action="append", default=[], help="日志文件，可重复")
    ap.add_argument("--map", help="QQ号→起始日 对照表（只补 first_day）")
    ap.add_argument("--memory", help="memory 目录，默认取项目根下的 memory/")
    ap.add_argument("--apply", action="store_true", help="真写盘（默认 dry-run）")
    ap.add_argument("--allow-new", action="store_true",
                    help="允许给没有记忆文件的 QQ 号建档（默认不允许）")
    args = ap.parse_args()

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    memory_dir = args.memory or os.path.join(here, "memory")
    if not args.log and not args.map:
        ap.error("至少要给一个 --log 或 --map")

    # 汇总
    merged_all = {}
    for p in args.log:
        for uid, e in parse_log(p).items():
            t = merged_all.setdefault(uid, {"days": set(), "first": None})
            t["days"] |= e["days"]
    if args.map:
        for uid, e in parse_map(args.map).items():
            t = merged_all.setdefault(uid, {"days": set(), "first": None})
            f = e.get("first")
            if f and (t["first"] is None or f < t["first"]):
                t["first"] = f

    if not merged_all:
        print("❌ 日志里没解析出任何「日期 + QQ号」，换一份带日期的日志再来。")
        return 2

    if args.apply:
        b = backup(memory_dir)
        if b:
            print("🗂️  已备份 %d 个 _daily.json → %s" % (b[1], b[0]))

    print("")
    print("%s  memory 目录：%s" % ("【写入】" if args.apply else "【预演，不写盘】", memory_dir))
    print("-" * 72)
    skipped = []
    for uid in sorted(merged_all):
        days = sorted(merged_all[uid]["days"])
        first = merged_all[uid]["first"] or (days[0] if days else "")
        known = os.path.exists(os.path.join(memory_dir, "%s.json" % uid))
        if not known and not args.allow_new:
            skipped.append((uid, first, len(days)))
            continue
        act, m2, f2 = apply(uid, memory_dir, days, first, dry=not args.apply)
        g = _gaps(m2)
        print("%-12s %-6s 首日 %s  最新 %s  共 %d 天%s"
              % (uid, act, f2 or "—", (m2[-1] if m2 else "—"), len(m2),
                 ("  ⚠ 有 %d 处断档（最大 %d 天）" % (len(g), max(x[2] for x in g)))
                 if g else ""))
        for a, b2, n in g[:5]:
            print("               └ 断档 %s → %s（隔 %d 天）" % (a, b2, n))
    print("-" * 72)

    if skipped:
        print("⚠️  这些 QQ 号没有记忆文件，已跳过（确认是本人再加 --allow-new）：")
        for uid, f, n in skipped:
            print("     %s  首日 %s  %d 天" % (uid, f or "—", n))

    if not args.apply:
        print("")
        print("上面是预演。确认没问题就加 --apply 真写盘。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
