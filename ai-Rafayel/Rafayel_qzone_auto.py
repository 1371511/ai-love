# -*- coding: utf-8 -*-
"""
祁煜「主动发朋友圈」S2 —— 选条 + 排期（对标 `Rafayel_greet.py`）。

只做**四件事**（发送不在这里做）：
  ① 读产物 `card\\qzone_pool.json`（语料池；由 `card\\_work\\md2qzone.py` 本机生成）
  ② `should_post(uid)` —— 现在该不该给这个人发（时段 / 当天条数 / **排期**）
  ③ `pick_post(uid)`   —— 从池子里挑一条（去重**按用户独立**、图文篇目缺图就跳过）
  ④ `mark_posted(...)` —— 记一笔：发过什么、`cycle`、以及**下次最早什么时候能发**

⚠⚠ 与「主动打招呼」的关系：**两条独立排期，绝不共用闸。**
   共用会让两条互相抢当天名额（她天天在聊 ⇒ 打招呼本来就不触发；加个共享闸，
   说说还会被顶掉），和「主动打招呼必然不触发」是同一类问题。
   ⇒ 本模块**不 import** `Rafayel_greet`；自己的记录写 `memory\\{uid}_qzone.json`，
     跟 greet 的 `{uid}_greet.json` 完全分开。代价是**同一天可能既打招呼又发说说**，
    靠两条**各自独立的每日上限**兜，而不是互相顶。

⚠ 分层（单向，不许反向）：只许 import `Rafayel_config` / `Rafayel_qzone` / `Rafayel_profile`。
   **websocket 只在 `Rafayel_bot.py` 里碰** —— 发送、私聊提醒都由调用方做。
⚠ 本模块**不碰** `get_reply`、不写 `cm.messages`。提醒语发出去以后，由调用方
   `record_proactive(uid, text)` 补写一条 assistant —— 不写的话她问「什么说说？」
   模型根本不知道上一句是他说的，会出现接不住的回复。
"""

import json
import os
import random
import re
import time
from datetime import datetime, timedelta

from Rafayel_config import (
    AUTO_GREET_TZ_OFFSET,               # ⏱ 时区沿用打招呼那一份：整条链上只该有一个「现在几点」
    MEMORY_DIR, QZONE_AUTO, QZONE_AUTO_GAP_DAYS_MAX, QZONE_AUTO_GAP_DAYS_MIN,
    QZONE_AUTO_HOUR_END, QZONE_AUTO_HOUR_START, QZONE_AUTO_MAX_PER_DAY,
)
from Rafayel_profile import get_user_profile

_HERE = os.path.dirname(os.path.abspath(__file__))      # …\ai-Rafayel
ROOT = os.path.dirname(_HERE)                            # 项目根
POOL_JSON = os.path.join(ROOT, "card", "qzone_pool.json")
REMINDS_MD = os.path.join(ROOT, "card", "qzone_reminds.md")

_DEFAULT_NAME = "保镖小姐"

# 池子只在进程内缓存一次（30 KB，读一次就够；生成器改动需重启进程才生效）
_POOL_CACHE = None


# ============================================================
#  语料池
# ============================================================

def load_pool(force=False):
    """
    读 `card\\qzone_pool.json` ⇒ entries 列表。

    ⚠ 读不到**不抛异常**（返回空列表）—— 发朋友圈是锦上添花，不许因为它把 bot 搞崩。
      但会在日志里说清楚，否则「一条都发不出去」会查不出原因。
    """
    global _POOL_CACHE
    if _POOL_CACHE is not None and not force:
        return _POOL_CACHE
    try:
        with open(POOL_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 容错：产物可能是 {"entries": [...]}，也可能直接是列表
        entries = data.get("entries") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            raise ValueError("qzone_pool.json 结构不对")
        _POOL_CACHE = [e for e in entries if isinstance(e, dict) and e.get("id")]
    except Exception as e:
        print("⚠️ 朋友圈语料池读取失败（发说说功能自动降级为空）：%s" % e)
        _POOL_CACHE = []
    return _POOL_CACHE


def pool_stats():
    """启动时打一行状态用。"""
    pool = load_pool()
    held = [e for e in pool if e.get("hold")]
    after = [e for e in pool if not e.get("hold")]
    sendable = [e for e in after if not e.get("extra")]
    return {
        "total": len(pool), "held": len(held),
        "after_hold": len(after), "sendable": len(sendable),
        "with_image": len([e for e in sendable if e.get("images")]),
    }


def image_paths(entry):
    """池子里的 `card/qzone_images/X` 相对路径 ⇒ 本机绝对路径。"""
    return [os.path.join(ROOT, rel.replace("/", os.sep))
            for rel in (entry.get("images") or []) if rel]


def _images_ok(entry):
    """
    ⚠ 带图篇目**必须带图发**（她 2026-09-19 明确否掉「直链取不到就降级纯文字」）：
      图文件缺失 ⇒ **这条不发**（评审时也提示：绝不发「只有文字的那一版」，她会当场看出来）。
    """
    for p in image_paths(entry):
        if not os.path.isfile(p):
            return False
    return True


# ============================================================
#  记录（每人一份，已 gitignore）
# ============================================================

def _record_path(user_id):
    return os.path.join(MEMORY_DIR, "%s_qzone.json" % user_id)


def _blank_record():
    return {"cycle": 0, "sent": [], "recent_cats": [],
            "date": "", "count": 0, "last": "", "next_at": "",
            "remind_recent": []}


def has_record(user_id):
    return os.path.exists(_record_path(user_id))


def load_record(user_id):
    path = _record_path(user_id)
    if not os.path.exists(path):
        return _blank_record()
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
        if not isinstance(rec, dict):
            return _blank_record()
        base = _blank_record()
        base.update(rec)                 # 老记录缺字段时自动补默认值
        return base
    except Exception:
        return _blank_record()


def save_record(user_id, rec):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    path = _record_path(user_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ============================================================
#  时间（口径与 Rafayel_greet 完全一致）
# ============================================================

def _now_bj():
    """
    「现在几点 / 今天几号」—— 按 `AUTO_GREET_TZ_OFFSET` 换算后的北京时间。
    ⚠ 只用于判断时段和当天日期。**绝不能拿它写 `rec["last"]`** ——
      那个字段要跟时间戳格式对齐（`_hours_since` 用 mktime 反解），写偏移值会让间隔差 8 小时。
    """
    return time.localtime(time.time() + AUTO_GREET_TZ_OFFSET * 3600)


def _hours_since(stamp):
    if not stamp:
        return None
    try:
        return (time.time() - time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))) / 3600.0
    except Exception:
        return None


def _valid_stamp(text):
    """`next_at` 形如 '2026-09-20 14:37'。长度和分隔符对就认，杜绝脏数据把比较搞歪。"""
    return bool(text) and len(text) == 16 and text[4] == "-" and text[7] == "-" and text[10] == " "


def _rand_clock():
    hour = random.randint(QZONE_AUTO_HOUR_START, QZONE_AUTO_HOUR_END - 1)   # 8..22 点
    return hour, random.randint(0, 59)


def _next_at_text(now=None):
    """
    随机排下一次：`GAP_DAYS_MIN`~`GAP_DAYS_MAX` 个自然日后的 8:00~22:59 之间某个随机钟点。

    ⚠ 光按「日历 +N 天」不够：会出现「17:15 发完、后天 14:30 发」= 实际只隔 45 小时，
      用户要的「至少 2 天」就不成立 ⇒ **不足 GAP_DAYS_MIN 天就顺延一天**。
    ⚠ 钟点**独立随机抽**，别绑死在「上次同一钟点」（那样每隔 2~3 天都在 13:00 冒出来，很机器）。
    ⚠ 结果只跟 `_now_bj()` 比，两端同一个钟，TZ 怎么改都不会算歪。
    """
    now = now or _now_bj()
    base_ts = time.mktime(tuple(now))
    days = random.randint(QZONE_AUTO_GAP_DAYS_MIN, QZONE_AUTO_GAP_DAYS_MAX)
    hour, minute = _rand_clock()
    day = datetime(now.tm_year, now.tm_mon, now.tm_mday) + timedelta(days=days)
    cand = datetime(day.year, day.month, day.day, hour, minute)
    if time.mktime(cand.timetuple()) - base_ts < QZONE_AUTO_GAP_DAYS_MIN * 86400:
        cand += timedelta(days=1)
    return "%04d-%02d-%02d %02d:%02d" % (cand.year, cand.month, cand.day, cand.hour, cand.minute)


def _first_at_text(now=None):
    """
    新用户的**第一条**：她 2026-09-19 定「**最早第二天才发**」
    （不是「发现这个人的当天」）⇒ 明天 8:00~22:59 之间随机一分钟。
    """
    now = now or _now_bj()
    hour, minute = _rand_clock()
    day = datetime(now.tm_year, now.tm_mon, now.tm_mday) + timedelta(days=1)
    return "%04d-%02d-%02d %02d:%02d" % (day.year, day.month, day.day, hour, minute)


# ============================================================
#  排期判断
# ============================================================

def should_post(user_id, now=None):
    """
    判断现在该不该给这个用户发一条说说。返回 (True, 说明) 或 (False, 原因)。
    原因只用于日志。

    闸门（三条 + 一个初始化）：
      ① 新用户 ⇒ **不发**，只把 `next_at` 初始化成「明天随机钟点」（第一条最早第二天）
      ② 时段：8:00–23:00
      ③ 每人每天 ≤ `QZONE_AUTO_MAX_PER_DAY` 条
      ④ `next_at` 排期到（距上次实际 ≥2 天）
    """
    if not QZONE_AUTO:
        return False, "功能已关闭"

    now = now or _now_bj()

    # ① 新用户：先初始化排期，今天不发
    if not has_record(user_id):
        rec = _blank_record()
        rec["next_at"] = _first_at_text(now)
        save_record(user_id, rec)
        return False, "新用户首次扫到 —— 只初始化排期（第一条最早 %s）" % rec["next_at"]

    # ② 时段
    hour = now.tm_hour
    if not (QZONE_AUTO_HOUR_START <= hour < QZONE_AUTO_HOUR_END):
        return False, "现在（北京时间）%d 点，不在 %d–%d 点" % (
            hour, QZONE_AUTO_HOUR_START, QZONE_AUTO_HOUR_END)

    rec = load_record(user_id)

    # ③ 当天条数
    today = time.strftime("%Y-%m-%d", now)
    if rec.get("date") == today and int(rec.get("count", 0)) >= QZONE_AUTO_MAX_PER_DAY:
        return False, "今天已经发过 %d 条（上限 %d）" % (
            rec.get("count"), QZONE_AUTO_MAX_PER_DAY)

    # ④ 排期（字符串比较：'2026-09-20 14:37' 定宽可直接比大小，两端都出自 _now_bj）
    nxt = (rec.get("next_at") or "").strip()
    if _valid_stamp(nxt) and time.strftime("%Y-%m-%d %H:%M", now) < nxt:
        return False, "排期未到（下次最早 %s）" % nxt

    return True, "可以发"


# ============================================================
#  选条
# ============================================================

def candidates(user_id, record=None):
    """
    这个人现在能发的候选。

    ⚠ **去重按用户独立**（她 2026-09-19 纠正，推翻先前「全局去重」）：
      只认**自己这份** `sent` ⇒ 给 A 发过**不影响** B 的池子。
    ⚠ 过滤顺序：hold → extra（链接/视频，S2 不发）→ 已发 → **配图缺失**。
    ⚠ 「最近 3 条用过同一 `cat`」是**软**多样性：命中就避开，全避开了就不避（别把自己饿死）。
    """
    rec = record if record is not None else load_record(user_id)
    sent = set(rec.get("sent") or [])
    recent = list(rec.get("recent_cats") or [])[-3:]

    out = []
    for e in load_pool():
        if e.get("hold"):                    # ① 暂缓名单，候选阶段直接滤掉
            continue
        if e.get("extra"):                   # ② 链接 / 视频，S2 不发（别发半截的 @ 或 BV 号）
            continue
        if e.get("id") in sent:              # ③ 这个人已经发过
            continue
        if not _images_ok(e):                # ④ 带图但图缺失 ⇒ 不发（不降级纯文字）
            continue
        out.append(e)

    fresh = [e for e in out if (e.get("cat") or "") not in recent]
    return fresh or out                      # 软多样性：能避就避，避不开就用全量


def pick_post(user_id, now=None):
    """
    挑一条（**不落盘**）。返回 (entry, 说明)；挑不出返回 (None, 原因)。

    ⚠ 池子耗尽 ⇒ `cycle + 1`、`sent` 清空、从头再来（202 条 ÷ 2.5 天 ≈ 16 个月，实际碰不到）。
    ⚠ 若重置后仍然挑不出（比如剩余的**全缺图**）⇒ 返回 None，**宁可今天不发，也不挑没图的发**。
    """
    now = now or _now_bj()
    pool = load_pool()
    if not pool:
        return None, "语料池为空（card/qzone_pool.json 没生成？）"

    for attempt in range(2):
        cand = candidates(user_id)
        if cand:
            e = random.choice(cand)
            note = "选中 %s%s" % (e["id"], "" if attempt == 0 else "（池子已翻新到第 %d 轮）"
                                  % (int(load_record(user_id).get("cycle", 0)) + 1))
            return e, note
        # 池子耗尽 ⇒ 翻新一轮
        rec = load_record(user_id)
        rec["cycle"] = int(rec.get("cycle", 0)) + 1
        rec["sent"] = []
        rec["recent_cats"] = []
        save_record(user_id, rec)

    return None, "池子耗尽且翻新后仍无可发（很可能是剩下的篇目配图全缺）"


def mark_posted(user_id, entry, delivered=True, now=None):
    """
    记一笔。**调用方必须在 `pick_post` 之后调它**（不管发没发出去）。

    delivered=True  ⇒ 追加进 `sent`（这条对这个人作废）并更新 `recent_cats`
    delivered=False ⇒ **仍推进排期与当天计数**，但不进 `sent`
      —— 发送失败时这样处理：既不会每 15 分钟重试同一条，也不会白白耗掉一条语料。

    ⚠ 计数顺序照 greet 的坑：**先判「是不是同一天」再覆盖 `date`**。
      反过来写的话比较永远为真，`count` 只涨不归零，每天上限就废了。
    """
    now = now or _now_bj()
    rec = load_record(user_id)
    today = time.strftime("%Y-%m-%d", now)

    same_day = (rec.get("date") == today)
    rec["date"] = today
    rec["count"] = (int(rec.get("count", 0)) + 1) if same_day else 1

    if delivered and entry:
        sent = list(rec.get("sent") or [])
        if entry.get("id") and entry["id"] not in sent:
            sent.append(entry["id"])
        rec["sent"] = sent
        cats = list(rec.get("recent_cats") or [])
        cats.append(entry.get("cat") or "")
        rec["recent_cats"] = cats[-3:]

    # ⚠ 必须是**服务器真实本地时间**，不能写偏移后的时间
    #   （_hours_since 用 mktime 反解它，写偏移值会让「距上次多久」差 8 小时）
    rec["last"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rec["next_at"] = _next_at_text(now)
    save_record(user_id, rec)
    return rec


# ============================================================
#  正文渲染（每人不同 ⇒ 只能留到运行时）
# ============================================================

def display_name(user_id):
    """她的称呼：画像里记着的；没引导过就是「保镖小姐」。⚠ `get_user_profile` 返回 **dict**。"""
    try:
        prof = get_user_profile(user_id) or {}
        return (prof.get("name") or "").strip() or _DEFAULT_NAME
    except Exception:
        return _DEFAULT_NAME


def render_text(entry, user_id):
    """
    发送前最后一站：正文里的「用户」/`@用户` → **她的称呼**。

    ⚠ 这一步**绝不能烘进 `qzone_pool.json`** —— 池子是所有人共用的，而每人称呼不同。
      （`【黄豆豆：X】`→emoji 和「只取首行」是全局的，那两步在生成器里做了。）
    """
    name = display_name(user_id)
    return re.sub(r"@?用户", lambda m: name, entry.get("text") or "")


# ============================================================
#  私聊提醒语（发送成功后发；入口太深，没人刷 QQ 空间）
# ============================================================

def load_reminds():
    lines = []
    try:
        with open(REMINDS_MD, "r", encoding="utf-8") as f:
            for raw in f:
                s = raw.strip()
                if s.startswith("- "):
                    lines.append(s[2:].strip())
    except Exception as e:
        print("⚠️ 朋友圈提醒语料读取失败（用兜底句）：%s" % e)
    return lines


def reminder_text(user_id, record=None):
    """
    取一句提醒（**发出去以后调用方必须 `record_proactive`**，否则她回「什么说说？」模型接不住）。
    ⚠ 提醒**不计入**「每天 1 条」的账 —— 它是发送动作的回执，不是主动搭话。
    """
    rec = record if record is not None else load_record(user_id)
    lines = load_reminds()
    if not lines:
        return "刚在空间发了条说说。你来看看？"

    # ⚠ 轮换的账要记**原文**、别记替换过占位符的那版 ——
    #   否则 `l not in recent` 永远为真（`（她的名字）` 对不上 `（保镖小姐）`），
    #   避重就完全失效，同一句会连着抽出来。（greet 那边原来也是这个坑，已同修。）
    recent = list(rec.get("remind_recent") or [])
    fresh = [l for l in lines if l not in recent]
    raw = random.choice(fresh or lines)

    recent.append(raw)
    rec["remind_recent"] = recent[-4:]
    save_record(user_id, rec)

    return raw.replace("她的名字", display_name(user_id))
