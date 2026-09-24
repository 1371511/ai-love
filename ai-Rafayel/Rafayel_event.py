# -*- coding: utf-8 -*-
"""
「特殊事件（节日）」—— 日子到了，他主动开口说一句节日的原话（2026-09-24 接进来）。

她定的口径：
  ① 节日台词**以素材为底**，按来源分三类（决定权都在她那，见《祁煜节日-纳入清单.md》）：
     · **纯素材**：游戏里真有这个节日的语料 ⇒ **照原话说**（一个字不改）
     · **自写**：游戏里真没有（端午 / 圣诞）⇒ 按人设写，过生成器的自写闸
     · **原句 + 适当编写**：素材打底 + 补句末 / 拼接相邻原句 / 补一句落到她的收尾
       （2026-09-24 晚她定的，目前只有中秋用）⇒ 每一句原句都要能在素材里逐字搜到
  ② 是他**主动开口** —— 日子到了他就先说，不是她问才说
  ③ 排期与「主动打招呼」「主动发说说」**三条各自独立**，不共用闸

⚠ 生日**不在这个模块**：生日归发朋友圈那条通道（`Rafayel_qzone_auto.bday_due`），
   同一天既发说说又私聊太吵，所以这里只管**节日**。

分层：config ← profile ← memory ← daily ← dailyq ← **event** ← llm
  ⚠ 只依赖 config / qzone_auto 的**纯函数**，绝不 import llm（否则成环）。
  ⚠ 复用 `Rafayel_qzone_auto.render_text` 做「用户」→ 称呼的替换 —— 不另写第三份兜底值。

记录：`memory/{uid}_event.json`
  {"sent": {"2026": {"spring": "spring-1"}},   ← 某年某节日发过哪条（一年一条）
   "used":  {"spring": ["spring-1", ...]}}     ← 跨年累计发过的（多年不重复）
"""
import json
import os
import random
import time

from Rafayel_config import (
    AUTO_GREET_TZ_OFFSET, EVENT, EVENT_HOUR_END, EVENT_HOUR_START,
    EVENT_POOL, MEMORY_DIR,
)
from Rafayel_qzone_auto import render_text

# 池子进程内缓存（静态文件，没必要每次扫描都读盘）
_CACHE = None


def _load():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(EVENT_POOL, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception as e:
        print("⚠️ 节日池读取失败（特殊事件停用）：%s" % e)
        data = {}
    _CACHE = {
        "entries": [e for e in (data.get("entries") or []) if e.get("text")],
        "fixed": data.get("fixed") or {},
        "lunar": data.get("lunar") or {},
    }
    return _CACHE


def reload_pool():
    """测试用：强制重新读盘。"""
    global _CACHE
    _CACHE = None
    return _load()


def _now():
    """
    「现在几点 / 今天几号」——按 AUTO_GREET_TZ_OFFSET 换算后的北京时间。

    ⚠ 复用打招呼那个偏移，不另起一份 —— 两个偏移会漂（这边按 9 点算、那边按 17 点算）。
    """
    return time.localtime(time.time() + AUTO_GREET_TZ_OFFSET * 3600)


def date_of(key, year, pool=None):
    """
    某个节日在某年的 (MM, DD)。查不到返回 None。

    固定节日（晴空节 / 沐春节 / 新辰节）走 `fixed`；
    按农历走的（春节 / 繁灯节 / 兰夜节）查 `lunar[年份]`。
    ⚠ 表只到 2031 年 ⇒ 超出范围返回 None ⇒ **那年不触发**，绝不猜一个日期出来。
    """
    p = pool or _load()
    md = (p["fixed"] or {}).get(key)
    if not md:
        md = ((p["lunar"] or {}).get(str(year)) or {}).get(key)
    if not md or len(md) != 5 or md[2] != "-":
        return None
    try:
        return int(md[:2]), int(md[3:])
    except ValueError:
        return None


def fest_today(now=None, pool=None):
    """
    今天是什么节日。返回 (节日中文名, key)；不是任何节日返回 (None, None)。

    ⚠ 理论上一天最多一个（表里没有两个节日撞同一天），真撞了就取池子里排在前面的那个。
    """
    if not EVENT:
        return None, None
    p = pool or _load()
    now = now or _now()
    year, mm, dd = now.tm_year, now.tm_mon, now.tm_mday
    seen = {}
    for e in p["entries"]:
        seen.setdefault(e.get("key"), e.get("fest"))
    for key, name in seen.items():
        if date_of(key, year, p) == (mm, dd):
            return name, key
    return None, None


def _record_path(user_id):
    return os.path.join(MEMORY_DIR, "%s_event.json" % user_id)


def load_record(user_id):
    path = _record_path(user_id)
    if not os.path.exists(path):
        return {"sent": {}, "used": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
        if isinstance(rec, dict):
            rec.setdefault("sent", {})
            rec.setdefault("used", {})
            return rec
    except Exception:
        pass
    return {"sent": {}, "used": {}}


def save_record(user_id, rec):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    path = _record_path(user_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def pick(user_id, key, pool=None):
    """
    挑一条**她没听过的**节日原句。返回 entry；挑不出来返回 None。

    ⚠ 挑中**不立刻标记** —— 由调用方在**真的发出去之后**再 `mark`，
       否则发送失败也会把它算成「说过了」（跟打招呼、朋友圈同一个道理）。
    """
    p = pool or _load()
    lines = [e for e in p["entries"] if e.get("key") == key]
    if not lines:
        return None
    used = set((load_record(user_id).get("used") or {}).get(key) or [])
    fresh = [e for e in lines if e.get("id") not in used]
    if not fresh:                     # 全说过了 ⇒ 从头再来一轮
        fresh = list(lines)
    return random.choice(fresh)


def mark(user_id, entry, now=None):
    """把这条记成「今年这个节日说过了」+「她听过了」。发出去之后调用。"""
    if not entry:
        return False
    now = now or _now()
    year = str(now.tm_year)
    rec = load_record(user_id)
    # ⚠ 以原内容为基础再覆盖，绝不重建 dict（重建会漏搬字段，踩过两次）
    sent = dict(rec.get("sent") or {})
    y = dict(sent.get(year) or {})
    y[entry.get("key") or ""] = entry.get("id") or ""
    sent[year] = y
    used = dict(rec.get("used") or {})
    lst = list(used.get(entry.get("key")) or [])
    if entry.get("id") and entry["id"] not in lst:
        lst.append(entry["id"])
    used[entry.get("key") or ""] = lst
    rec["sent"] = sent
    rec["used"] = used
    save_record(user_id, rec)
    return True


def render(user_id, entry):
    """渲染成要发出去的文本。正文以**素材**为底（照原话 / 拼接 / 补收尾，见条目注释），
    这里只做「用户」→ 她的称呼的替换。"""
    if not entry:
        return ""
    return render_text(entry, user_id)


def bubbles_of(entry):
    """
    这条该分几条气泡发。返回 list[str]（至少一条）。

    ⭐ 2026-09-24 17:37 她定的：节日台词里「拼接 / 补收尾」那种**整条偏长**（最长 56 字），
    拆成两条气泡**连着发** —— 他的短信原话本来就是这样一句一条
    （素材里 `祁煜：A` / `祁煜：B` 就是两条消息）。切分在生成器里做好、写进 `bubbles`。

    ⚠ 安全网：`bubbles` 拼起来必须**正好等于**整条；不一致、或只有一条 ⇒ 退回单条。
      宁可发一条长的，也不发丢字 / 发乱的。
    """
    if not entry:
        return []
    text = entry.get("text") or ""
    b = entry.get("bubbles")
    if isinstance(b, list) and len(b) >= 2 and "".join(b) == text:
        return list(b)
    return [text] if text else []


def due(user_id, now=None, pool=None):
    """
    该不该给这个用户开口。返回 (entry, 原因)；不该发返回 (None, 原因字符串)。

    三个闸门：① 今天是节日 ② 今年这个节日还没说过 ③ 时段在 9:00–22:00
    """
    if not EVENT:
        return None, "功能已关闭"
    now = now or _now()
    name, key = fest_today(now, pool)
    if not key:
        return None, "今天不是节日"

    hour = now.tm_hour
    if not (EVENT_HOUR_START <= hour < EVENT_HOUR_END):
        return None, "现在（北京时间）%d 点，不在 %d–%d 点的时段内" % (
            hour, EVENT_HOUR_START, EVENT_HOUR_END)

    rec = load_record(user_id)
    already = ((rec.get("sent") or {}).get(str(now.tm_year)) or {}).get(key)
    if already:
        return None, "今年%s已经说过了（%s）" % (name, already)

    entry = pick(user_id, key, pool)
    if not entry:
        return None, "%s 池子空" % name
    return entry, name


def try_event(user_id, now=None):
    """一步到位：该发就返回 (文本, entry)，不该发返回 ("", None)。

    ⚠ 返回的**文本是整条**（进对话历史用整条）；要分气泡发的话，
      拿 entry 走 `bubbles_of(entry)`，两者拼起来一致。
    """
    entry, why = due(user_id, now)
    if not entry:
        return "", None
    return render(user_id, entry), entry
