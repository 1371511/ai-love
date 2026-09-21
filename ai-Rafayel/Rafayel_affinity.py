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
import re
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
#    例：30→31 花 8（心动价），31→32 起才花 15（倾情价）⇒ 到 246 级累计 6140 分
#      （⚠ 246 只是**表里最后一行**，之后每级还是 30 分，**不封顶**）。
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
# ⚠⚠ `MAX_LEVEL` 是「**官方表里最后一个有定义的等级**（246）」，**不是上限**！
#    ⭐ 246 之后照样升级，每级仍按最后一档的价（`LAST_RATE` = 30 分）——
#      md 里写的 `101~246+` 那个「+」就是这个意思（2026-09-21 她指出：
#      「其实是没有限制的，不止是 6140」）。
#    ⇒ 凡是拿它当**上限/满分**用的（网页端分母、`CUM[MAX_LEVEL]` 下标）都是错的。
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
LAST_RATE = CURVE[-1][3]            # 最后一档每级多少分（246 级之后一直用它）


def cum_at(level):
    """
    升到**任意** level 级要多少累计分 —— **level 允许超过 `MAX_LEVEL`**。

    ⭐ 表外按最后一档的价线性外推：`CUM[246] + (L-246) * 30`。
    ⚠ 网页端算进度一律用这个，**别直接 `CUM[a["level"]]`** —— 过了 246 级就 IndexError。
    """
    if level <= MAX_LEVEL:
        return CUM[level]
    return CUM[MAX_LEVEL] + (level - MAX_LEVEL) * LAST_RATE


def tier_of(level):
    """级别 → 档位名（心动 / 倾情 / 眷恋 / 情衷）"""
    for name, lo, hi, _r in CURVE:
        if lo <= level <= hi:
            return name
    return CURVE[-1][0]


def level_of(score):
    """
    分数 → (级别, 档位名, 升到下一级要多少累计分)。

    ⭐⭐ **没有等级上限**：官方表只列到 `MAX_LEVEL`（246 级 = 6140 分），
       246 之后继续升、每级仍花 `LAST_RATE` ⇒ `next_at` **永远算得出来**，
       不存在「已满级」（md 里 `101~246+` 的「+」就是这个意思）。
    ⚠ 别把 `MAX_LEVEL` 当上限用来截断（2026-09-21 修：原来到 246 就停、`next_at=None`）。
    """
    lo, hi = 1, MAX_LEVEL
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if CUM[mid] <= score:
            lo = mid
        else:
            hi = mid - 1
    L = lo
    extra = score - CUM[MAX_LEVEL]
    if extra >= 0:                      # 表外：每 LAST_RATE 分再升一级（整除，不四舍五入）
        L = MAX_LEVEL + extra // LAST_RATE
    return L, tier_of(L), cum_at(L + 1)


# ---------------------------------------------------------------- 官方素材（跨级触发）
# ⭐ 清单在 `card/affinity.md`（`EGG_FILE=` / `EGG_AT=` / `SMS_DIR=` / `SMS=` 四行），
#    正文在 `card/affinity/`。**要改一律改 md**，代码不写死任何等级。
# ⚠ 本模块**只读**：这里只负责「读素材 + 算该发哪一条」，写盘一律交给 `Rafayel_daily`。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # E:\ai-love
_DEFAULT_NAME = "保镖小姐"          # 画像里没记称呼时的兜底（跟主动打招呼同一个口径）
MILESTONE_MAX = 5                   # 网页端「他说过的那句话」最多显示几条（她 2026-09-21 定成 5）

# 素材里的表情标记是全角 + 二级名：`[表情：涂鸦叽：生气]`
_STICKER_RE = re.compile(r"^\[表情[：:]([^\]：:]+)[：:]([^\]：:]+)\]$")
_LINK_RE = re.compile(r"^\[链接[：:]")


def _md_text():
    try:
        with open(MD_PATH, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _md_lines(prefix):
    """取 md 里所有以 prefix 开头的行的**后半段**（已 strip）。"""
    out = []
    for line in _md_text().splitlines():
        s = line.strip()
        if s.startswith(prefix):
            out.append(s[len(prefix):].strip())
    return out


def _path_from_md(prefix, rel_default):
    for s in _md_lines(prefix):
        if s:
            return os.path.join(_ROOT, s.replace("/", os.sep))
    return os.path.join(_ROOT, rel_default)


EGG_FILE = _path_from_md("EGG_FILE=", os.path.join("card", "affinity", "牵绊彩蛋.txt"))
SMS_DIR = _path_from_md("SMS_DIR=", os.path.join("card", "affinity", "牵绊短信"))


def load_sms_nodes():
    """45 个短信节点 ⇒ [(等级, 档位, 标题, 文件名), …]（按等级升序）。"""
    out = []
    for s in _md_lines("SMS="):
        p = [x.strip() for x in s.split("|")]
        if len(p) == 4:
            try:
                out.append((int(p[0]), p[1], p[2], p[3]))
            except ValueError:
                pass
    out.sort()
    return out


def load_egg_levels():
    """86 条彩蛋各自绑的等级（**顺序 = 彩蛋行号**，取自 md 的 `EGG_AT=`）。"""
    for s in _md_lines("EGG_AT="):
        vals = [int(x.strip()) for x in s.split(",") if x.strip().isdigit()]
        if vals:
            return vals
    return []


_EGG_CACHE = None
_SMS_CACHE = {}
_SMS_FULL_CACHE = {}
_TAG_CACHE = None


def _sticker_tags():
    """真实标签名（`card/stickers.md`）。拿不到就返回空集合 ⇒ 表情标记一律剥掉。"""
    global _TAG_CACHE
    if _TAG_CACHE is None:
        tags = set()
        try:
            with open(os.path.join(_ROOT, "card", "stickers.md"), encoding="utf-8") as f:
                for line in f:
                    m = re.match(r"^\|\s*\d+\s*\|\s*`[^`]+\.gif`\s*\|\s*([^|]+?)\s*\|", line)
                    if m:
                        tags.add(m.group(1).strip())
        except Exception:
            pass
        _TAG_CACHE = tags
    return _TAG_CACHE


def egg_texts():
    """86 条彩蛋正文（剥 `祁煜：`）。读不到返回 [] ⇒ 调用方按「没素材」处理。"""
    global _EGG_CACHE
    if _EGG_CACHE is None:
        out = []
        try:
            with open(EGG_FILE, encoding="utf-8") as f:
                for raw in f:
                    t = raw.strip()
                    if t.startswith("祁煜："):
                        out.append(t[3:].strip())
        except Exception as e:
            print("⚠️ 牵绊彩蛋读取失败（跨级彩蛋关闭）：%s" % e)
        _EGG_CACHE = out
    return _EGG_CACHE


def sms_opening(filename):
    """
    一条牵绊短信的**开头句** = 他开口的第一句。读不到返回 ""。

    ⚠ 三条取值规矩（2026-09-21 对着 45 条全量核过）：
      ① 纯表情行 `[表情：涂鸦叽：生气]` ⇒ 归一成 `[表情:生气]`
         （素材是全角冒号 + 二级名，跟 `card/stickers.md` 的标签对不上）；
         标签不在标签表里就往后找下一句 —— **绝不把标记原样发出去**。
      ② `[链接：…]` 行是链接占位，跳过取下一句（166 级就是这么办的）。
      ③ 短信是**带 A/B/C 分支的多轮对话**，整条发到 QQ 会散架 ⇒ **只发第一句**；
         她回什么，交给模型接。
    """
    if filename in _SMS_CACHE:
        return _SMS_CACHE[filename]
    text = ""
    try:
        with open(os.path.join(SMS_DIR, filename), encoding="utf-8") as f:
            lines = [raw.strip()[3:].strip() for raw in f
                     if raw.strip().startswith("祁煜：")]
        tags = _sticker_tags()
        for one in lines:
            m = _STICKER_RE.match(one)
            if m:
                tag = m.group(2).strip()
                if tag in tags:
                    text = "[表情:%s]" % tag
                    break
                continue
            if _LINK_RE.match(one):
                continue
            text = one
            break
    except Exception as e:
        print("⚠️ 牵绊短信读取失败（这条不发）：%s" % e)
    _SMS_CACHE[filename] = text
    return text


def _fix_markup(t):
    """把素材里的全角表情标记归一成 `[表情:标签]`（网页端再画成小圆片）。"""
    m = _STICKER_RE.match(t)
    if m:
        return "[表情:%s]" % m.group(2).strip()
    return t


def sms_full(filename):
    """
    一条牵绊短信的**完整结构**（网页端「牵绊提升彩蛋」用；QQ 端用不到）。

    返回 {"opening": str, "blocks": [block, …]} —— **按素材里的先后顺序**排好的块
      block = {"kind": "branch", "n": 1, "options": [opt, …]}        一段分支
            | {"kind": "line", "who": "他"/"她", "text": str}        分支外的散句
      opt   = {"key": "A", "title": str, "her": [str, …], "him": [str, …]}

    ⭐ 为什么是「有序的块」而不是「opening + branches」：33 / 45 条的**分支外还夹着散句**
      （一段分支结束后他又补一句之类），按原顺序排才不会把对话接错位。

    ⚠ 素材格式（45 条里 42 条一致）：第一行 `祁煜：…` 是开场句；之后每个 `◇分支N` 块里是
      `  A｜选项名` / `   用户：…` / `   祁煜：…`。
      ⭐ **但结构并不统一，必须容错**（2026-09-21 全量核过）：
        · `11雨棍（53 级）` **一段分支都没有**，就两句话（只有 opening + extra）；
        · `12颜料雨伞（56 级）` 有 **4 段**分支；`21微缩景观（下）86 级` 只有 **2 段**。
      ⇒ 所以**一个「几段分支」的假设都不能有**；一行格式歪了只是少一段，
        **绝不抛异常** —— 不能让一条脏数据把整页搞成 500。
    """
    if filename in _SMS_FULL_CACHE:
        return _SMS_FULL_CACHE[filename]
    out = {"opening": "", "blocks": []}
    try:
        with open(os.path.join(SMS_DIR, filename), encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        print("⚠️ 牵绊短信读取失败（这条不上网页）：%s" % e)
        _SMS_FULL_CACHE[filename] = out
        return out

    cur_br = None
    cur_op = None
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("◇分支"):
            n = sum(1 for b in out["blocks"] if b["kind"] == "branch") + 1
            cur_br = {"kind": "branch", "n": n, "options": []}
            out["blocks"].append(cur_br)
            cur_op = None
            continue
        m = re.match(r"^([A-Za-z])\s*[｜|]\s*(.*)$", s)
        if m and cur_br is not None:
            cur_op = {"key": m.group(1).upper(), "title": m.group(2).strip(),
                      "her": [], "him": []}
            cur_br["options"].append(cur_op)
            continue
        if s.startswith("用户："):
            txt = _fix_markup(s[3:].strip())
            if cur_op is not None:
                cur_op["her"].append(txt)
            else:
                out["blocks"].append({"kind": "line", "who": "她", "text": txt})
            continue
        if s.startswith("祁煜："):
            txt = _fix_markup(s[3:].strip())
            if cur_op is not None:
                cur_op["him"].append(txt)
            elif not out["opening"]:
                out["opening"] = txt
            else:
                out["blocks"].append({"kind": "line", "who": "他", "text": txt})
            continue
    _SMS_FULL_CACHE[filename] = out
    return out


def _her_name(user_id, memory_dir):
    prof = _read_json(os.path.join(memory_dir, "%s_profile.json" % user_id)) or {}
    return (prof.get("name") or "").strip() or _DEFAULT_NAME


def init_unlocked(level):
    """
    第一次接入时造一份初始记录：**当前等级以下的素材全标成「已解锁」，但不补发**。

    ⭐ 为什么必须这样：老用户一上来可能就 100 级，否则会一口气把二十多条历史素材灌给她。
    ⚠ `sms` / `eggs` 两列的语义是「**已解锁**」而不是「已发送」。`sms` 只喂网页端 `/messages`；
      `eggs` 还兼作「发过哪些」的去重表（`pending_unlock` 的 `sent_eggs`）——
      ⚠ 所以它是「**真发过 ∪ 首连时到级的**」，网页端「他说过的那句话」读的就是它
      （这个「混着」的缺口在 `compute()` 里记着，等她定要不要拆出 `said`）。
    """
    lv = int(level or 1)
    return {"level": lv,
            "sms": [n[0] for n in load_sms_nodes() if n[0] <= lv],
            "eggs": [i for i, x in enumerate(load_egg_levels()) if x <= lv]}


def current_level(user_id, memory_dir):
    """这个用户现在几级（算不出来返回 0）。"""
    try:
        return compute(user_id, memory_dir)["level"]
    except Exception:
        return 0


def pending_unlock(user_id, memory_dir):
    """
    跨级了没有？该发哪一条？**只读**，返回 dict 或 None。

    dict = {level_now, mark_sms, send}
      level_now  现在的等级 ⇒ 调用方拿它推进 `unlocked.level`
      mark_sms   这一跳新到达的**短信节点等级**列表 ⇒ **只记「已解锁」，不发**
      send       要发的那一条 `{"kind","level","text","key"}`；没有就是 None

    ⭐⭐ **牵绊短信不进 QQ**（2026-09-21 她定：「既然写进了网页端，就不放在 QQ 对话端里了」）
       ⇒ 短信节点照样**标记解锁**（网页端读 `unlocked.sms` 去显示），但 **QQ 端一条都不发**；
         QQ 端从此**只发彩蛋**。
       ⇒ 连带变化：以前「短信优先、把撞车的彩蛋挤到下次」那套**没了** ——
         彩蛋不再被挤，按 `EGG_AT` 该发就发（7/13/33/53… 那 18 个撞车等级不再顺延）。
    ⚠ 一级最多发一条；没升级就返回 None（**绝不补发历史**）。
    ⚠ 没发出去的彩蛋**不丢**：`eggs` 只在真发出去后才记 ⇒ 下次升级接着补。
    """
    lv_now = current_level(user_id, memory_dir)
    if not lv_now:
        return None
    daily = _read_json(os.path.join(memory_dir, "%s_daily.json" % user_id)) or {}
    u = daily.get("unlocked")
    if not isinstance(u, dict):
        return None                      # 还没初始化 ⇒ 调用方先 init_unlocked
    if lv_now <= int(u.get("level") or 0):
        return None                      # 没升级

    def _ints(seq):
        out = set()
        for x in (seq or []):
            try:
                out.add(int(x))
            except (TypeError, ValueError):
                pass
        return out

    name = _her_name(user_id, memory_dir)

    # ① 短信：这一跳新到达的节点**只标解锁**，不回 QQ
    #    （开头句由网页端取；真要发也轮不到 QQ —— 那是「他愿意主动说的」，属于恋爱线的活儿）
    sent_sms = _ints(u.get("sms"))
    mark_sms = sorted(lv for lv, _t, _ti, _fn in load_sms_nodes()
                      if lv <= lv_now and lv not in sent_sms)

    # ② 彩蛋：现在**唯一**会真的发到 QQ 的东西（一级最多一条，剩下的下次补）
    sent_eggs = _ints(u.get("eggs"))
    eggs = egg_texts()
    send = None
    for i, lv in enumerate(load_egg_levels()):
        if lv <= lv_now and i not in sent_eggs and i < len(eggs):
            send = {"kind": "egg", "level": lv, "key": i,
                    "text": re.sub(r"@?用户", name, eggs[i])}
            break

    return {"level_now": lv_now, "mark_sms": mark_sms, "send": send}


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

    # 🎁 他 QQ 里说过的彩蛋 ⇒ 网页端「他说过的那句话」卡片。
    #    ⭐ 卡片是「有内容才渲染」，给空列表它就自动隐藏 —— **页面代码不用改**。
    #    ⭐⭐ 2026-09-21 她定死（原话：「『他说过的那句话』保留，而且主页显示的话，就显示最新的 5 条，
    #       不要全部显示。内容是 …\彩蛋里的 01牵绊度提升 的语句。只显示他在QQ聊天时提到过的语句」）：
    #      ① **内容 = 彩蛋** —— 就是素材 `彩蛋\01牵绊度提升.txt` 那 86 句
    #         （项目内副本 `card/affinity/牵绊彩蛋.txt`，已核 MD5 与桌面源一致）。
    #      ② **只显示最新 MILESTONE_MAX 条**（按绑定等级倒序取前 5 = 他最近说的那几句），不铺开。
    #    ⚠⚠ 一个诚实的边界（**别对外说满**）：`unlocked["eggs"]` 混了**两类** ——
    #       · 正常流程：`pending_unlock()` 里**真发出去才记**（那个 `sent_eggs`）
    #       · 首次接入：`init_unlocked()` 把**到级的全标上**（老用户不补发历史）
    #       ⇒ 严格讲这张卡是「**说过 or 到级**」，不是 100%「说过」。
    #       想一分不差地只算「真说过」，得另开一个 `said` 列表（首连留空、发送时才追加）。
    #       ⭐⭐ **2026-09-21 她拍板：走 B —— 不加 `said`**，就照现在这样，
    #          「**到级就算说过了**」（理由：老用户逻辑上早该说过；测试期新用户影响也小）。
    #       ⇒ **以后别把这里当 bug 去修**。真要改口径，先回去看 §19.7。
    #    ⚠ 反向别再犯：短信（`unlocked["sms"]`）**只标解锁、QQ 端一句都没说过**
    #      ⇒ 按她的口径本就不该进这张卡（它们有自己的页面 `/messages`）。
    #    ⚠ 纯表情行不上卡片（当前 86 条彩蛋里没有，这条只是保险）。
    milestones = []
    u = daily.get("unlocked") if daily else None
    if isinstance(u, dict):
        eggs, egg_lv = egg_texts(), load_egg_levels()
        items = []
        for x in (u.get("eggs") or []):          # ⚠ 混着哪两类，见上面那段注释
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(eggs) and i < len(egg_lv):
                items.append((egg_lv[i], eggs[i]))
        items.sort(key=lambda p: p[0], reverse=True)       # 等级大的 = 最近说的
        milestones = [t for _lv, t in items
                      if t and not t.startswith("[表情:")][:MILESTONE_MAX]

    return {
        "user_id": user_id,
        "name": prof.get("name") or "",
        "score": score,
        "level": level,                 # 官方级别（1 起，246 之后继续涨，**无上限**）
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
        "milestones": milestones,  # ⭐ 官方素材：跨级解锁的那些（网页端「他说过的那句话」）
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
                if a["next_at"] else ""))
    # ⚠ 别说「满级」—— 等级没有上限，只有「官方表到哪」。
    t_span = ("%d~%d 级" % (a["tier_lo"], a["tier_hi"])) if a["level"] <= a["tier_hi"] \
        else ("%d 级起" % a["tier_lo"])
    L.append("  %s 第 %d 级（本档 %s；官方表到 %d 级 = %d 分，之后每级 %d 分、不封顶）"
             % (a["tier"], a["level"] - a["tier_lo"] + 1, t_span,
                MAX_LEVEL, CUM[MAX_LEVEL], LAST_RATE))
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
