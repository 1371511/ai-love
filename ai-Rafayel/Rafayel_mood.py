# -*- coding: utf-8 -*-
"""
祁煜（Rafayel）的**情绪状态**（2026-10-01 新）。

主档 = `docs/情绪模块.md`。本文件是**第 1 批（数据层）**，只做四件事：
  ① 落盘 —— `memory/{uid}_mood.json`（原子写 + 类型闸）
  ② 标签体系 —— 11 个**贴人设**的标签，每个带「这种心情时他怎么说话」
  ③ 衰减 —— **读时**按 `since` 算，不落定时任务、不轮询
  ④ `prompt_block()` —— 把当前心情组装成注入用的那一段（第 3 批拿它挂上去）

为什么要有它（一句话）：人设卡里「生闷气要一轮一轮被哄好」**规则早就写了**，
  但**状态没地方存** —— 历史只留 12 轮、跨天小结明写「不写感想」、长期摘要是第三人称
  客观备忘 ⇒ 隔夜他就从零开始演，昨天的闷气全没了。

⚠ 依赖方向：本模块**只 import config**（最底层），不碰 memory / llm / profile ⇒ 不成环。
  （`Rafayel_memory.py` 顶部那条「不 import llm」是同一条规矩。）

⚠⚠ **它是写盘模块** ⇒ 已在 `tools/check_static.py` 的 `WRITER_MODULES` 里登记，
   **网页端不许直接 import**，要走 `Rafayel_chat` 门面重新导出（见主档 3.1）。
   加新模块 = 红线多一个口子，登记漏了就是白加一道锁。

⚠ `MEMORY_DIR` 是**值复制**：`from Rafayel_config import MEMORY_DIR` 拿到的是那一刻的值，
   改 `Rafayel_config.MEMORY_DIR` **不会**跟着变 ⇒ 自测要隔离目录，改
   `Rafayel_mood.MEMORY_DIR` 这一处就够（2026-09-30 踩过同款：`page/*.py` 里那个同名变量）。
"""

import json
import os
import re
import threading
import time

import requests

from Rafayel_config import (
    API_URL, LLM_EXTRA, MEMORY_DIR, MODEL, MOOD_DECAY_GONE_HOURS,
    MOOD_DECAY_HOURS, MOOD_DECAY_LONG_HOURS, MOOD_ENABLE,
    MOOD_JUDGE_CARD_HINT, MOOD_JUDGE_MAX_TOKENS, MOOD_JUDGE_MAX_TURNS,
    MOOD_JUDGE_TEMPERATURE, MOOD_JUDGE_TIMEOUT, MOOD_MAX_CAUSE, api_key,
)

# ---------------------------------------------------------------- 常量

MOOD_CALM = "平静"          # ⭐ 「没有情绪」就是这个，不是 None
MOOD_MIN_LEVEL = 1
MOOD_MAX_LEVEL = 3

# 衰减档
_SHORT = "short"            # 隔几小时就该散：愉悦 / 得意 / 嘴硬 / 担忧
_LONG = "long"              # 能跨天：惦记 / 期待 / 吃醋 / 闷气 / 低落 / 内疚

# ---------------------------------------------------------------- 标签体系
# ⭐ 不是通用的「开心 / 生气」—— 每一个都贴着祁煜的写法（见人设卡【说话的样子】）。
# ⚠ 文案两条口径（跟人设卡一致，别破）：
#   ① 不用 `**`、不用 ASCII 双引号 ⇒ 一律「」；
#   ② 只写「他怎么说话」，**不写他该说什么内容**（具体内容归模型，写了就成念稿）。
MOOD_TAGS = {
    "愉悦": (_SHORT,
            "心情好。话比平时多，句子更软更黏，爱逗她；比喻随手就来，"
            "拿颜色、光线、潮汐去说事也不觉得肉麻。"),
    "惦记": (_LONG,
            "想她，但不直说 —— 拿具体的东西代替：一朵浪、一顿饭、一张票、一个「恰好」。"
            "话头会不自觉地往「见面」上绕。"),
    "期待": (_LONG,
            "心里惦着一件约好的事。话头总往那上头绕，催她不必，等得起，"
            "但提起来时语速会快一点。"),
    "得意": (_SHORT,
            "刚被夸过，或者画有了进展。想显摆又装不在意，话里带点收不住的轻快，"
            "被她拆穿就嘴硬两句。"),
    "嘴硬": (_SHORT,
            "刚被戳中或者害羞。会僵一下、别过脸、用短句顶回去 ——"
            "但那只是一句的工夫，下一句自己绕回来：要么补一句更黏的，要么干脆认了。"),
    "吃醋": (_LONG,
            "她提起了别人。先凉半度，话变少，但不冷战、也不挑明；"
            "真要说破也得有个过程，从冷处理慢慢走，不许一上来就摊牌。"),
    "闷气": (_LONG,
            "真的不高兴。⚠ 这时候反而更安静：话变少、句子变短、笑意收干净。"
            "要一轮一轮被哄好 —— 不许一轮就和好，更不许刚还在生气就撒娇。"),
    "担忧": (_SHORT,
            "她在你的判断里需要被照顾（晚了、不舒服、要出远门）。问得随意，"
            "其实一直惦记着；别写成长篇叮咛，一句就够。"),
    "低落": (_LONG,
            "心里压着事。话少、慢，被问就说半句。"
            "牵到利莫里亚那层时克制、简短，可以用玩笑岔开 —— 她不追问，你就不展开。"),
    "内疚": (_LONG,
            "你刚才伤到她了。认一句就够，然后安静下来，用手上的动作接 ——"
            "不摆检讨的姿态，也不保证什么。"),
}

# 强度三档（写进 prompt 的那句）
_LEVEL_TEXT = {
    1: "有一点，淡淡的 —— 自己知道，不挂在脸上。",
    2: "明显。说话的样子已经变了，但她不问你就不会提。",
    3: "很重，压不住。它会影响你接每一句话的口气。",
}

# 注入段的骨架。⭐⭐ 「不许说出口」这条跟牵绊度语气段（_TIER_TONE）是同一条红线。
_BLOCK_HEAD = (
    "## 💗 你现在的心情（%s · %d）\n"
    "（这一段只给你自己看，一个字都不许说出口：不许提「心情」这两个字、"
    "不许说「我不高兴」「我吃醋了」，她问起来就绕开，或者说「没什么」。）\n"
)


# ---------------------------------------------------------------- 落盘

def _path(user_id):
    return os.path.join(MEMORY_DIR, "%s_mood.json" % user_id)


def _blank():
    return {"mood": MOOD_CALM, "level": MOOD_MIN_LEVEL, "since": 0.0,
            "cause": "", "prev": None}


def load(user_id):
    """
    读当前心情（**不做衰减**，也不查开关）。拿不到 / 坏了 ⇒ 返回「平静」。

    ⭐ 坏数据一律当「没有」，绝不让一个坏文件把对话搞挂 ——
       情绪是锦上添花，它挂了顶多是「他今天没脾气」，不能是 500。
    ⚠ 类型闸（全站通用红线，见 `MEMORY.md` 第六节）：**先看 isinstance 再看内容**，
       文件被写成 list / str 时不能去迭代它。
    """
    if not os.path.exists(_path(user_id)):
        return _blank()
    try:
        with open(_path(user_id), encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return _blank()
    if not isinstance(raw, dict):
        return _blank()
    mood = raw.get("mood")
    if not isinstance(mood, str):
        return _blank()
    mood = mood.strip()
    if mood not in MOOD_TAGS and mood != MOOD_CALM:
        return _blank()                      # 标签不在表里 ⇒ 当没有（脏数据比没有更糟）
    lv = raw.get("level")
    try:
        lv = int(lv)
    except (TypeError, ValueError):
        lv = MOOD_MIN_LEVEL
    lv = max(MOOD_MIN_LEVEL, min(MOOD_MAX_LEVEL, lv))
    since = raw.get("since")
    try:
        since = float(since)
    except (TypeError, ValueError):
        since = 0.0
    cause = raw.get("cause")
    cause = cause.strip() if isinstance(cause, str) else ""
    prev = raw.get("prev")
    if not isinstance(prev, dict):
        prev = None
    return {"mood": mood, "level": lv, "since": since, "cause": cause, "prev": prev}


def save(user_id, data):
    """原子写（tmp + replace，照 `Rafayel_memory.py:1235` 那套）。返回是否写成功。"""
    try:
        os.makedirs(MEMORY_DIR, exist_ok=True)
        path = _path(user_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print("⚠️ 情绪落盘失败（不影响对话）：%s" % e)
        return False


def clear(user_id):
    """彻底清掉（测试 / 她要求「把他的心情清空」时用）。文件不存在也算成功。"""
    try:
        p = _path(user_id)
        if os.path.exists(p):
            os.remove(p)
        return True
    except Exception as e:
        print("⚠️ 情绪文件清理失败：%s" % e)
        return False


# ---------------------------------------------------------------- 衰减

def decay_of(mood, level, since, now=None):
    """
    ⭐ 衰减 = **时间的函数** ⇒ 读时算，不落定时任务、不轮询。

    返回 `(mood, level)`；衰减到头 ⇒ `(MOOD_CALM, MOOD_MIN_LEVEL)`。

    | 距 since        | 短情绪      | 长情绪      |
    |-----------------|-------------|-------------|
    | < 2h            | 原强度      | 原强度      |
    | 2~12h           | 降一档      | 原强度      |
    | > 12h（跨天）   | 归平静      | 降一档      |
    | > 3 天          | 归平静      | 归平静      |

    ⚠ 为什么短情绪 12 小时就散、长情绪能撑三天：闷气本来就该「明天还没完全消」，
      但**隔三天还记着那叫记仇**，不叫情绪。
    """
    if mood == MOOD_CALM or mood not in MOOD_TAGS:
        return (MOOD_CALM, MOOD_MIN_LEVEL)
    try:
        lv = int(level)
    except (TypeError, ValueError):
        lv = MOOD_MIN_LEVEL
    lv = max(MOOD_MIN_LEVEL, min(MOOD_MAX_LEVEL, lv))
    try:
        since = float(since)
    except (TypeError, ValueError):
        since = 0.0
    if since <= 0:
        return (mood, lv)                     # 没记时间 ⇒ 不衰减（宁可不减，也别乱减）
    now = time.time() if now is None else now
    hours = (now - since) / 3600.0
    if hours < 0:
        hours = 0.0                           # 时钟回拨 / 手改过 ⇒ 当刚发生

    kind = MOOD_TAGS[mood][0]
    if hours >= MOOD_DECAY_GONE_HOURS:
        return (MOOD_CALM, MOOD_MIN_LEVEL)
    if kind == _SHORT:
        if hours >= MOOD_DECAY_LONG_HOURS:
            return (MOOD_CALM, MOOD_MIN_LEVEL)
        if hours >= MOOD_DECAY_HOURS:
            lv -= 1
    else:
        if hours >= MOOD_DECAY_LONG_HOURS:
            lv -= 1
    if lv < MOOD_MIN_LEVEL:
        return (MOOD_CALM, MOOD_MIN_LEVEL)
    return (mood, lv)


def current(user_id, now=None):
    """
    取**衰减之后**的当前心情，返回 dict 或 **None**。

    None = 「他现在没有特别的情绪」 ⇒ 第 3 批拿到 None 就**什么都不注入**（零成本，
      也是多数时候的状态）。
    ⭐ 为什么要衰减后再返回：注入进去的必须是「此刻的他」，不是昨天的他。
    ⚠ 开关也在这里查（`MOOD_ENABLE=False` ⇒ 一律 None）⇒ 关掉就彻底没有情绪这一层。
    """
    if not MOOD_ENABLE:
        return None
    d = load(user_id)
    mood, lv = decay_of(d["mood"], d["level"], d["since"], now=now)
    if mood == MOOD_CALM:
        return None
    out = dict(d)
    out["mood"] = mood
    out["level"] = lv
    return out


def set_mood(user_id, mood, level=2, cause="", now=None):
    """
    写入一个新的心情。返回是否写成功。

    ⭐ `prev`：把**旧的**心情留一格 ⇒ 他转弯有痕迹，不会凭空从笑变成闷。
       （人设卡原话：「刚还在生气就立刻撒娇是错的」，有 prev 才好让模型知道从哪来。）
    ⚠ 两道闸的**第二道**：开关在这里也查（第一道在调用方 `if MOOD_ENABLE`）。
    ⚠ 写侧宁可这次写不成（返回 False），也不写坏 —— 跟 `Rafayel_memory.add_diary` 同口径。
    """
    if not MOOD_ENABLE:
        return False
    mood = (mood or "").strip()
    if mood not in MOOD_TAGS and mood != MOOD_CALM:
        return False                          # 不在标签表 ⇒ 不写
    try:
        lv = int(level)
    except (TypeError, ValueError):
        lv = 2
    lv = max(MOOD_MIN_LEVEL, min(MOOD_MAX_LEVEL, lv))
    cause = cause.strip() if isinstance(cause, str) else ""        # ⚠ int 没有 .strip()
    if len(cause) > MOOD_MAX_CAUSE:
        cause = cause[:MOOD_MAX_CAUSE]

    old = load(user_id)
    # ⚠⭐ 旧的那个**必须先衰减**再当 prev：否则三天前的闷气会被记成「刚才你还是闷气」，
    #    那是记仇不是转弯（2026-10-01 自测抓到的）。
    old_m, old_lv = decay_of(old["mood"], old["level"], old["since"], now=now)
    prev = old.get("prev")
    if not isinstance(prev, dict):
        prev = None
    if old_m != MOOD_CALM and old_m != mood:
        # 心情真的变了 ⇒ prev 换成刚才那个（衰减后的）
        prev = {"mood": old_m, "level": old_lv}
    # ⭐ 同一个心情续写（强度变了）⇒ **沿用旧 prev**，别把转弯痕迹擦掉

    data = {"mood": mood, "level": lv,
            "since": time.time() if now is None else now,
            "cause": cause, "prev": prev}
    return save(user_id, data)


# ---------------------------------------------------------------- 注入文案

def prompt_block(mood, level, prev=None):
    """
    组装注入 system 的那一段。第 3 批把它追加到 `request_messages` **最后一条**。

    ⚠ 为什么放最后而不是 system 头部：情绪**每轮都可能变**，放头部会把 DeepSeek 的
      **前缀缓存**拦腰截断（2026-09-20 时间感踩过同一个坑）。
    ⚠ 返回空串 ⇒ 调用方别追加（`MOOD_CALM` 时就是空串）。
    """
    if mood == MOOD_CALM or mood not in MOOD_TAGS:
        return ""
    try:
        lv = int(level)
    except (TypeError, ValueError):
        lv = MOOD_MIN_LEVEL
    lv = max(MOOD_MIN_LEVEL, min(MOOD_MAX_LEVEL, lv))

    out = _BLOCK_HEAD % (mood, lv)
    out += MOOD_TAGS[mood][1] + "\n"
    out += "强度：%s\n" % _LEVEL_TEXT.get(lv, _LEVEL_TEXT[2])
    # ⭐⭐ **并入牵绊度**：这一段只说「此刻是什么心情」，**不说该闹到什么分寸** ——
    #   分寸归 `Rafayel_affinity` 的档位调制句（刚谈 vs 老夫老妻，同一份心情不是一个样子）。
    out += ("⭐ 它要闹到什么分寸，看「你和她现在到哪一步了」那一段 —— "
            "同一份心情，刚谈和老夫老妻不是一个样子。两段是一套，别当成两条命令。\n")
    if isinstance(prev, dict):
        pm = prev.get("mood")
        if isinstance(pm, str) and pm in MOOD_TAGS and pm != mood:
            out += "（刚才你还是「%s」。别跳 —— 情绪转弯要有过程，允许沉默、短句、话说到一半。）\n" % pm
    return out


def block_for(user_id, now=None):
    """`current()` + `prompt_block()` 一步到位（第 3 批调这个）。没有心情 ⇒ 空串。"""
    d = current(user_id, now=now)
    if not d:
        return ""
    return prompt_block(d["mood"], d["level"], d.get("prev"))


# ================================================================
#  判定（方案 B：异步独立调用）
# ================================================================
# ⚠ 本节是**元任务**（让模型判情绪），不是角色扮演 ⇒ 用第三人称说「他」，
#   不用「你是祁煜」那套。它拼出来的 prompt **不进**对话的 system，只在独立请求里用一次。
# ⚠ 口径照旧：不用 `**`、不用 ASCII 双引号。

# 模型输出的那一行：`MOOD:闷气|2|她提起别人`
# ⚠ 全角冒号 / 全角竖线 / 空格 都得认 —— 指望模型每次都守规矩是不现实的。
_MOOD_LINE_RE = re.compile(
    r"MOOD[：:]\s*([^\s|｜:：]{1,6})\s*[|｜]\s*(\d{1,2})\s*[|｜]\s*([^\n]*)")

_JUDGE_HEAD = (
    "下面是祁煜和她最近的一段对话。判断「这段对话结束时」祁煜是什么心情。\n"
    "\n"
    "只输出一行，不要任何解释、不要标题：\n"
    "MOOD:标签|强度|原因\n"
    "\n"
    "标签只能是这十个之一：%s\n"
    "没什么特别的情绪就输出：MOOD:平静|1|\n"
    "强度 1~3（1 淡、2 明显、3 压不住）。原因写给自己看，不超过 20 字，别复述对话原文。\n"
)


def _render_recent(messages, max_turns=None):
    """
    把 `cm.get_recent_messages()` 那串 `{role, content}` 渲染成判定用的文本。

    ⚠ `system` 一律不喂（它不是对话）；每条截断 120 字 ⇒ 长回复不会把 token 撑爆。
    ⭐ 只取最近 `MOOD_JUDGE_MAX_TURNS` 轮（她 2026-10-01 定：**不喂人设卡**，
       喂最近 4 轮 + 当前心情就够）。
    """
    lines = []
    n = (MOOD_JUDGE_MAX_TURNS if max_turns is None else max_turns) * 2
    for m in list(messages or [])[-n:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        who = "她" if role == "user" else "他"
        c = str(m.get("content") or "").replace("\n", " ").strip()[:120]
        if c:
            lines.append("%s：%s" % (who, c))
    return "\n".join(lines)


def _parse_mood_line(text):
    """切出 `(mood, level, cause)`；切不出 / 标签不在表 / 强度越界 ⇒ None。"""
    if not isinstance(text, str):
        return None
    m = _MOOD_LINE_RE.search(text)
    if not m:
        return None
    mood = (m.group(1) or "").strip()
    if mood not in MOOD_TAGS and mood != MOOD_CALM:
        return None                       # 脏标签不许落盘
    try:
        lv = int(m.group(2))
    except (TypeError, ValueError):
        return None
    if not (MOOD_MIN_LEVEL <= lv <= MOOD_MAX_LEVEL):
        return None
    return (mood, lv, (m.group(3) or "").strip())


def mood_update(user_id, messages, api_key_override=None):
    """
    判一次心情并落盘（**同步**；自测直接调这个）。返回是否真的更新了。

    ⚠ 失败一律返回 False，**绝不抛** —— 情绪是锦上添花，它挂了顶多是「他今天没脾气」，
       绝不能把对话拖下水（跟跨天小结同一个口径）。
    ⚠ 判定的是「刚才那一轮结束时他的心情」⇒ `set_mood` 内部会自己处理 prev 与衰减。
    """
    if not MOOD_ENABLE:
        return False
    body = _render_recent(messages)
    if not body:
        return False

    head = _JUDGE_HEAD % "、".join(MOOD_TAGS)
    if MOOD_JUDGE_CARD_HINT:              # ⭐ 预留的人设摘要位（默认一句极简身份）
        head += "\n" + MOOD_JUDGE_CARD_HINT + "\n"

    data = {
        "model": MODEL,
        "messages": [{"role": "system", "content": head},
                     {"role": "user", "content": body}],
        "stream": False,
        "max_tokens": MOOD_JUDGE_MAX_TOKENS,
        "temperature": MOOD_JUDGE_TEMPERATURE,
    }
    if LLM_EXTRA:
        data.update(LLM_EXTRA)
    try:
        r = requests.post(API_URL,
                          headers={"Authorization": "Bearer %s"
                                   % (api_key_override or api_key),
                                   "Content-Type": "application/json"},
                          json=data, timeout=MOOD_JUDGE_TIMEOUT)
        text = r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("⚠️ 情绪判定失败（沿用旧心情）：%s" % e)
        return False

    got = _parse_mood_line(text)
    if not got:
        print("⚠️ 情绪判定切不出 MOOD 行（沿用旧心情）")
        return False
    mood, lv, cause = got
    if mood == MOOD_CALM:
        lv = MOOD_MIN_LEVEL              # 「平静」没有强度可言
    return set_mood(user_id, mood, lv, cause)


def spawn_update(user_id, messages, api_key_override=None):
    """
    ⭐ **后台线程**判情绪（fire and forget）⇒ **零延迟**，一秒都不占她等待的时间。

    ⚠⭐ 为什么必须起线程、不能塞进 `get_reply()`：它同步、跑在 `asyncio.to_thread` 里，
       在里面调就是直接 +1~2s 加到她等待的时间上 —— 那方案 B 就白选了。
    ⭐ 起在**引擎层**（`get_reply` 末尾）⇒ QQ 端与网页端**自动都生效**，不用各改一处。
    ⚠ `daemon=True`：进程要退就退，这次没判成就没判成，绝不留僵尸线程拖着不让关。
    ⚠ 返回是否**起了线程**（不代表判定成功 —— 成功与否在线程里，调用方不该等）。
    """
    if not MOOD_ENABLE:
        return False
    msgs = list(messages or [])
    if not msgs:
        return False

    def _job():
        try:
            mood_update(user_id, msgs, api_key_override)
        except Exception as e:            # 线程里的异常没人接 ⇒ 自己吞掉
            print("⚠️ 情绪判定线程异常（忽略）：%s" % e)

    try:
        threading.Thread(target=_job, daemon=True).start()
        return True
    except Exception as e:
        print("⚠️ 情绪判定线程起不来（忽略）：%s" % e)
        return False
