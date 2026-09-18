# -*- coding: utf-8 -*-
"""
祁煜（Rafayel）的**主动打招呼**（2026-09-18 新功能）。

游戏里他会在你打开主页时主动开口；QQ 私聊拿不到「对方上线」事件，
所以这里把「上线问候」映射成：**你冷场够久，他就忍不住先说一句**。

只做三件事：
  ① 读语料 `card\greetings.md`（素材原文，一句未改）
  ② 判断某个 user_id 此刻该不该主动发（冷场时长 / 时段 / 当天条数 / **排期**）
  ③ 记住发过什么、以及**下次最早什么时候能发**（避免短期内重复，也避免太频繁）

🎲 排期（2026-09-18 加）：每次发完就随机约好下次——
  从本次开口时刻起隔 48~72 小时（2~3 天，按**实际时长**算，不是日历天数），
  时刻落在 8:00~22:59 随机一分钟。写在记录的 `next_at` 字段里，到点之前一律不开口。
  ⚠ 冷场门槛（IDLE_HOURS=16）与排期是**两个独立的闸门**：冷场管「她是不是真的走了」，
    排期管「我上次说过话到现在够不够久」，两个都满足才开口。

⚠ 与对话引擎的关系：本模块**自己不碰** `get_reply`、也不写 `cm.messages`（保持旁支、单向依赖）。
   但**发出去的那句话必须进对话历史** —— 由调用方（`Rafayel_bot`）在发送成功后调
   `record_proactive(uid, text)` 补写。理由：不写的话她回话时模型根本不知道上一句是他说的，
   会出现完全"接不住"的回复（2026-09-18 她实测反馈的口径问题）。
"""

import json
import os
import random
import time
from datetime import datetime, timedelta

from Rafayel_config import (
    AUTO_GREET, AUTO_GREET_GAP_DAYS_MAX, AUTO_GREET_GAP_DAYS_MIN,
    AUTO_GREET_HOUR_END, AUTO_GREET_HOUR_START,
    AUTO_GREET_IDLE_HOURS, AUTO_GREET_MAX_PER_DAY, AUTO_GREET_MIN_GAP_HOURS,
    AUTO_GREET_TZ_OFFSET, MEMORY_DIR,
)
from Rafayel_profile import get_user_profile

_HERE = os.path.dirname(os.path.abspath(__file__))          # E:\ai-love\ai-Rafayel
ROOT = os.path.dirname(_HERE)                               # E:\ai-love
GREETINGS_MD = os.path.join(ROOT, "card", "greetings.md")

# 池子名 ↔ md 里的 `## ` 小节标题
# ⚠ 这两个字符串必须与 card\greetings.md 里的 `## ` 小节标题**逐字一致**，
#   改标题就要同步改这里，否则 load_pools 拿到的池名对不上，那个池直接空掉。
POOL_REUNION = "重逢（冷场超过 3 天才用）"
POOL_DAY = "白天（8:00–17:00）"
POOL_EVENING = "傍晚与夜里（17:00–21:00）"
POOL_NIGHT = "深夜（21:00–23:00）"

REUNION_IDLE_HOURS = 72     # 冷场超过这么久才算「重逢」（3 天）
# 两档语义（配合 IDLE_HOURS=16 的门槛）：
#   16h ~ 3 天没说话 → 按发送当时的钟点走「白天 / 傍晚与夜里 / 深夜」池；
#   超过 3 天        → 走「重逢」池（那批语料是「你终于回来了 / 好久不见」，本来就是给久别用的）。
# ⚠ 别把这个值降到 16h 门槛附近：那样冷场一满足就直接进重逢档，时段池会全废。
#   也别再回到「6 小时门槛」那种配置 —— 门槛比一晚睡眠短，等于每天早上都触发。

_DEFAULT_NAME = "保镖小姐"


def load_pools():
    """
    读 greetings.md，返回 {小节标题: [句子...]}。
    文件读不到就返回空 dict —— 主动打招呼是可有可无的锦上添花，不许因为它把 bot 搞崩。
    """
    pools = {}
    try:
        with open(GREETINGS_MD, "r", encoding="utf-8") as f:
            cur = None
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("# "):
                    continue
                if line.startswith("## "):
                    cur = line[3:].strip()
                    pools[cur] = []
                elif line.startswith("- ") and cur:
                    pools[cur].append(line[2:].strip())
    except Exception as e:
        print("⚠️ 主动打招呼语料读取失败（功能关闭）：%s" % e)
        return {}
    return {k: v for k, v in pools.items() if v}


def _pool_for(idle_hours, hour):
    """按冷场时长 + 当前小时选池。时段不对返回 None（不打扰）。"""
    if idle_hours >= REUNION_IDLE_HOURS:
        return POOL_REUNION
    if 8 <= hour < 17:
        return POOL_DAY
    if 17 <= hour < 21:
        return POOL_EVENING
    if 21 <= hour < 23:
        return POOL_NIGHT
    return None


def _record_path(user_id):
    return os.path.join(MEMORY_DIR, "%s_greet.json" % user_id)


def load_record(user_id):
    path = _record_path(user_id)
    if not os.path.exists(path):
        return {"date": "", "count": 0, "last": "", "recent": [], "next_at": ""}
    try:
        with open(path, "r", encoding="utf-8") as f:
            rec = json.load(f)
        if isinstance(rec, dict):
            rec.setdefault("next_at", "")
            return rec
        return {"date": "", "count": 0, "last": "", "recent": [], "next_at": ""}
    except Exception:
        return {"date": "", "count": 0, "last": "", "recent": [], "next_at": ""}


def save_record(user_id, rec):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    path = _record_path(user_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _now_bj():
    """
    「现在几点 / 今天几号」——按 AUTO_GREET_TZ_OFFSET 换算后的北京时间。

    ⚠ 只用于判断时段和记当天日期。**绝不能拿它写 `rec["last"]`** ——
      那个字段要和时间戳格式对齐（`_hours_since` 用 mktime 反解），写偏移后的时间会让间隔算错 8 小时。
    """
    return time.localtime(time.time() + AUTO_GREET_TZ_OFFSET * 3600)


def _today_bj():
    return time.strftime("%Y-%m-%d", _now_bj())


def _hours_since(stamp):
    """stamp 形如 '2026-09-18 12:00:00'（save_memory 写进去的格式）。解析不了返回 None。"""
    if not stamp:
        return None
    try:
        return (time.time() - time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))) / 3600.0
    except Exception:
        return None


def _valid_stamp(text):
    """next_at 形如 '2026-09-20 14:37'。长度和分隔符对就认，杜绝脏数据把比较搞歪。"""
    return bool(text) and len(text) == 16 and text[4] == "-" and text[7] == "-" and text[10] == " "


def _next_at_text(now=None):
    """
    随机排下一次：GAP_DAYS_MIN~MAX 个自然日后的 8:00~22:59 之间某个随机钟点。

    ⚠ 光按「日历 +N 天」算不够：会出现「17:15 发完、后天 14:30 发」= 实际只隔 45 小时，
      用户要的「至少 2 天」就不成立。所以**不足 GAP_DAYS_MIN 天就顺延一天**。
    ⚠ 也不要把钟点绑死在「上次同一钟点」（那样每隔 2~3 天都在 13:00 冒出来，很机器），
      所以钟点是独立随机抽的。
    ⚠ 结果字符串只跟 _now_bj() 比，两端同一个钟，AUTO_GREET_TZ_OFFSET 怎么改都不会算歪。
    """
    now = now or _now_bj()
    base_ts = time.mktime(tuple(now))
    days = random.randint(AUTO_GREET_GAP_DAYS_MIN, AUTO_GREET_GAP_DAYS_MAX)
    hour = random.randint(AUTO_GREET_HOUR_START, AUTO_GREET_HOUR_END - 1)   # 8..22 点
    minute = random.randint(0, 59)
    day = datetime(now.tm_year, now.tm_mon, now.tm_mday) + timedelta(days=days)
    cand = datetime(day.year, day.month, day.day, hour, minute)
    if time.mktime(cand.timetuple()) - base_ts < AUTO_GREET_GAP_DAYS_MIN * 86400:
        cand += timedelta(days=1)
    return "%04d-%02d-%02d %02d:%02d" % (cand.year, cand.month, cand.day, cand.hour, cand.minute)


def should_greet(user_id, last_active, now=None):
    """
    判断现在该不该给这个用户主动发一句。

    last_active：最后一次对话落盘的时间字符串（memory\{uid}.json 的 saved_at）。
    返回 (True, 池名) 或 (False, 原因字符串) —— 原因只用于日志。
    """
    if not AUTO_GREET:
        return False, "功能已关闭"

    idle = _hours_since(last_active)
    if idle is None:
        return False, "没有可用的最后活跃时间"
    if idle < AUTO_GREET_IDLE_HOURS:
        return False, "才冷场 %.1f 小时（阈值 %s 小时）" % (idle, AUTO_GREET_IDLE_HOURS)

    now = now or _now_bj()
    hour = now.tm_hour
    if not (AUTO_GREET_HOUR_START <= hour < AUTO_GREET_HOUR_END):
        return False, "现在（北京时间）%d 点，不在 %d–%d 点的时段内" % (
            hour, AUTO_GREET_HOUR_START, AUTO_GREET_HOUR_END)

    rec = load_record(user_id)
    today = time.strftime("%Y-%m-%d", now)
    if rec.get("date") == today and rec.get("count", 0) >= AUTO_GREET_MAX_PER_DAY:
        return False, "今天已经发过 %d 条（上限 %d）" % (rec["count"], AUTO_GREET_MAX_PER_DAY)

    # 🎲 排期：上次发完就约好了下次最早什么时候，没到点一律不开口。
    # ⚠ 字符串比较（'2026-09-20 14:37' 定宽可直接比大小），两端都出自 _now_bj()。
    nxt = (rec.get("next_at") or "").strip()
    if _valid_stamp(nxt):
        if time.strftime("%Y-%m-%d %H:%M", now) < nxt:
            return False, "排期未到（下次最早 %s）" % nxt
    else:
        # 兼容旧记录（那时候还没有 next_at 字段）→ 退回「距上次满 N 小时」
        gap = _hours_since(rec.get("last", ""))
        if gap is not None and gap < AUTO_GREET_MIN_GAP_HOURS:
            return False, "距上次主动发才 %.1f 小时（兜底下限 %s 小时）" % (
                gap, AUTO_GREET_MIN_GAP_HOURS)

    pool = _pool_for(idle, hour)
    if not pool:
        return False, "当前时段没有对应的池"

    return True, pool


def pick_greeting(user_id, pool, record=None, now=None):
    """
    从指定池里挑一句，避开最近发过的。
    返回文本；池子为空或无语料返回 ""。
    """
    pools = load_pools()
    lines = pools.get(pool) or []
    if not lines:
        return ""

    rec = record if record is not None else load_record(user_id)
    recent = rec.get("recent") or []
    fresh = [l for l in lines if l not in recent]
    if not fresh:                    # 全发过了就重置，从头再来
        fresh = lines
        recent = []

    # 占位符要换成她真正的称呼（画像里记着的；没引导过就是「保镖小姐」）
    name = ""
    try:
        # ⚠ get_user_profile 返回的是 dict 不是 UserProfile 对象
        prof = get_user_profile(user_id) or {}
        name = (prof.get("name") or "").strip()
    except Exception:
        name = ""

    raw = random.choice(fresh)

    # ⚠ 2026-09-19 修：避重的账要记**原文**（带「她的名字」占位符那版），
    #   别记替换过的那版 —— 否则 `l not in recent` 永远为真
    #   （`（她的名字）？…` 对不上 `（保镖小姐）？…`），避重彻底失效，
    #   带占位符的那几句会连着被抽到。
    recent.append(raw)
    rec["recent"] = recent[-10:]
    today = time.strftime("%Y-%m-%d", now or _now_bj())
    # ⚠ 先判「是不是同一天」再覆盖 date —— 反过来写的话比较永远为真，
    #   count 会一直涨、从不归零，每天上限就废了（原版就是这个顺序，2026-09-18 顺手修）。
    same_day = (rec.get("date") == today)
    rec["date"] = today
    rec["count"] = (rec.get("count", 0) + 1) if same_day else 1
    # ⚠ 这里必须是**服务器真实本地时间**，不能写偏移后的时间
    #   —— _hours_since 用 mktime 反解它，写偏移值会让「距上次多久」差 8 小时。
    rec["last"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # 🎲 顺手把下次也约好：隔 2~3 个自然日的随机时刻
    rec["next_at"] = _next_at_text(now)
    save_record(user_id, rec)

    return raw.replace("她的名字", name or _DEFAULT_NAME)


def try_greet(user_id, last_active, now=None):
    """一步到位：该发就返回文本，不该发返回 ""。"""
    ok, pool = should_greet(user_id, last_active, now)
    if not ok:
        return ""
    return pick_greeting(user_id, pool)
