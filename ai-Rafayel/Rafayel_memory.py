# -*- coding: utf-8 -*-
"""
祁煜（Rafayel）的**记忆与对话管理**（2026-09-17 从 `Rafayel_chat.py` 拆出）。

两块：
  ① 落盘 —— `save_memory` / `load_memory`（memory/{user_id}.json）
  ② `ConversationManager` —— 对话状态、历史截断、关键事实、定期摘要与画像补丁

⚠ 依赖方向：本模块 import config 与 profile，**不 import Rafayel_llm**。
  （get_reply 在 llm 层，反过来 import 本模块。若这里再 import llm 就成环了。）
"""

import json
import os
import re
import time

import requests

from Rafayel_config import (
    API_URL, AUTO_GREET_TZ_OFFSET, DAY_KEEP, DAY_ROLL, DAY_ROLL_MIN_GAP,
    DAY_SUMMARY_MAX_TOKENS, DIARY_ENABLE, DIARY_HARD_LEN, DIARY_MAX_ITEMS, DIARY_MAX_LEN,
    LLM_EXTRA, MAX_FACTS, MAX_HISTORY_TURNS,
    MEMORY_DIR, MODEL, NOW_GAP_HOURS, NOW_PROMPT, REPLY_SHAPE, REPLY_SHAPE_HINT,
    SUMMARY_INTERVAL, SUMMARY_MAX_TOKENS,
)
from Rafayel_daily import record as daily_record
# 💗 情绪（2026-10-01 新，主档 docs/情绪模块.md）：写日记时给一句「他现在的心情」。
#   ⚠ 只给**事实**、不加指令 —— 让他自己带着这个心情写，不替他规定写什么。
#   ⚠ 依赖方向合法：memory(2) → mood(1)；mood 只依赖 config，**不反向 import memory**。
#   ⚠ `MOOD_ENABLE=False` 或心情是「平静」⇒ `mood_current()` 返回 None ⇒ 零变化。
from Rafayel_mood import MOOD_TAGS as _MOOD_TAGS
from Rafayel_mood import current as mood_current
from Rafayel_profile import UserProfile

# ============================================================
#  🕐 当前时间（2026-09-19）
# ============================================================
# ⚠ 这条链上**只该有一个「现在几点」** —— 打招呼 / 发朋友圈 / 对话注入全用
#   `AUTO_GREET_TZ_OFFSET` 换算，别再自己新开一个偏移（换服务器时只改一处）。
_WEEKDAYS_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _now_bj():
    """按 `AUTO_GREET_TZ_OFFSET` 换算后的「现在」（北京时间）。"""
    return time.localtime(time.time() + AUTO_GREET_TZ_OFFSET * 3600)


def _period_cn(hour):
    """
    ⭐ 钟点 → **时段词**（2026-09-24 她提「时间提得太频繁了」⇒ 降精度）。

    为什么降：以前每轮都把 `04:08` 这种**精确到分钟**的钟点摆在整段 prompt 的最后一条
      （注意力最强的位置），模型就把它当成「要交代出去的信息」⇒ 开口就是
      「凌晨四点还没睡」「这个点还没睡」。
    ⇒ 平时只给「凌晨 / 清晨 / 上午…」这种**粗粒度**的词：他知道大概什么时候，
      但嘴里没有那个具体数字可念。精确钟点挪进括号里当**备注**，她问才说。
    """
    if 5 <= hour < 8:
        return "清晨"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 23:
        return "晚上"
    if hour >= 23 or hour < 2:
        return "深夜"
    return "凌晨"          # 2:00–5:00


def _season_cn(month):
    """
    ⭐ 月份 → **季节**（2026-09-24 加的日期感知）。

    ⚠ 只给四季，不给「初秋/深秋」这种 —— 月份和季节是**客观事实**，
      而「初秋」是我加的形容，容易跟他实际说的话对不上（也更容易变成他主动开口念叨）。
    """
    if 3 <= month <= 5:
        return "春天"
    if 6 <= month <= 8:
        return "夏天"
    if 9 <= month <= 11:
        return "秋天"
    return "冬天"


def now_prompt_text(gap_hours=None, fest=None, nudge=None):
    """
    拼「## 🕐 现在」那一段；`NOW_PROMPT` 关掉就返回空串。

    fest  = 今天是什么节日（None = 不是节日）；由调用方从 `Rafayel_event.fest_today` 取
            —— ⚠ 不在这里 import event：那会让 memory 反向依赖更高的一层。
    nudge = 今天的「突变关怀」指令（None = 没有/今天已经用过了）；
            由调用方从 `Rafayel_weather.nudge` 取，**只在主对话那条路传**
            （一次性 prompt 那种没历史的地方不该消耗这个「一天一次」的额度）。


    ⚠ 两件必须说清的事（都是她实测踩出来的）：
      ① **照实说**：模型没时间感知，不注入就会瞎编钟点 ⇒ 明确写「她问就照实说」。
      ② **隔了多久**：她睡一觉回来，模型看到的历史是**连续的**，
         不点明「隔了两三个小时」它就会接着上次的动作演（睡前洗虾、睡醒还在洗）。
         ⚠ 那个「睡」是**午睡 2~3 小时**（2026-09-20 她纠正），不是过夜 —— 别按更夸张的场景设计。
    ⚠ system_prompt 禁 `**` 与 ASCII 双引号 ⇒ 这段一律用「」或不用引号。
    """
    if not NOW_PROMPT:
        return ""
    n = _now_bj()
    # ⭐ 只写**时段**，精确钟点降级成括号里的备注（她问才说）—— 见 `_period_cn` 的注释。
    lines = ["## 🕐 现在（北京时间）",
             "%d年%d月%d日 %s %s %s（%02d:%02d）%s" % (
                 n.tm_year, n.tm_mon, n.tm_mday, _WEEKDAYS_CN[n.tm_wday],
                 _season_cn(n.tm_mon), _period_cn(n.tm_hour), n.tm_hour, n.tm_min,
                 "周末" if n.tm_wday >= 5 else "工作日")]

    # 🎉 今天是节日 ⇒ 让他心里有数（她提起来接得住；要不要开口归节日模块管）
    if fest:
        lines.append("今天是%s。" % fest)

    # 🌤 温度：只给数值，**不提城市**（她定的：她是深圳的，说「上海」会出戏）
    try:
        from Rafayel_weather import weather_line
        wl = weather_line()
    except Exception:
        wl = ""          # ⚠ 天气拿不到就当没这项功能，绝不能把对话搞崩
    if wl:
        lines.append(wl)
        lines.append(
            "（她问起才照实说。她没问就不要主动提温度、天气，"
            "也别拿天气做文章——「今天好热」「外面在下雨」这类话，她不问就别说。）"
        )
    # ⭐ 突变关怀：一天最多一次，是**允许的例外**（她定的）
    if nudge:
        lines.append("%s——说成关心一句就够，别重复说，也别像播天气。" % nudge)

    if gap_hours is not None and gap_hours >= NOW_GAP_HOURS:
        # ⚠ 分钟档**必须单独写**：Python 的 `round(0.5)` 是 **0**（银行家舍入），
        #    照旧写成「约 %d 小时前」会印出「约 0 小时前」。
        if gap_hours >= 24:
            when = "约 %d 天前" % int(round(gap_hours / 24.0))
        elif gap_hours >= 1:
            when = "约 %d 小时前" % int(round(gap_hours))
        else:
            when = "约 %d 分钟前" % int(round(gap_hours * 60))

        # ⚠ 这半句是重点：光给数字模型未必会用，得**翻译成演戏的指令**。
        #    两层意思，缺一不可（第二层是她 2026-09-19 追加的）：
        #      ① 上一回合已经结束了，别接着演；
        #      ② **他手上的事也该推进了** —— 隔了两三个小时还说在洗虾，
        #         做饭这种流程早该收尾（她说「正常做饭流程 1h，饭也该做完了」）。
        # ⚠ system_prompt 禁 `**` ⇒ 强调只能靠措辞，别用星号。
        if gap_hours >= 8:
            hint = "—— 隔了这么久，那是上一回事了：手上的事早该做完了，别接着上次的动作继续演。"
        elif gap_hours >= 2:
            hint = "—— 中间隔了这么久，别接成像刚聊到一半；手上的事也该推进到下一步了。"
        else:
            hint = "—— 中间过了这么久，你手上的事（做饭、洗澡、走路这类）也该有进展了，别还停在原地。"
        lines.append("她上一条消息是%s %s" % (when, hint))

    # ⚠⭐ 2026-09-24 她提「时间提得太频繁」⇒ 这里补上**反向那条禁令**。
    #     原来只写了「她问就照实说」，等于只管了一半：模型照样主动报时。
    lines.append(
        "（这是真实时间。她问起才照实说，别自己编一个钟点。"
        "她没问就不要主动报时，也别拿时间做文章——"
        "「这个点」「凌晨四点」「都这个时间了」这类话，她不问就别说。）"
    )
    return "\n".join(lines)


def _day_key(ts):
    """
    epoch 秒 → `"YYYY-MM-DD"`（按 `AUTO_GREET_TZ_OFFSET` 换算后的自然日）。

    ⚠ 跨天判定必须走这个函数，**别用 `time.localtime` 裸算** —— 否则换时区/换服务器时，
      这里的「今天」会和「现在几点」那段的「今天」对不上。
    """
    return time.strftime("%Y-%m-%d", time.localtime(float(ts) + AUTO_GREET_TZ_OFFSET * 3600))


def _day_label(day_key):
    """`"2026-09-23"` → `"9月23日 星期三"`；解析不了就原样返回。"""
    try:
        t = time.strptime(day_key, "%Y-%m-%d")
        return "%d月%d日 %s" % (t.tm_mon, t.tm_mday, _WEEKDAYS_CN[t.tm_wday])
    except Exception:
        return str(day_key or "")


def day_label(day_key):
    """
    `"2026-09-28"` → `"9月28日 星期一"` —— **只讲绝对日期，不带「前天 / 9天前」那层相对说法**。

    ⭐ 2026-09-30 新增（她截图：日记页那个下拉的选项在安卓上被折成两行）：
      原生下拉的可用宽度只有 200 出头（内边距 + 右边那个单选圈 + optgroup 缩进吃掉一大半），
      `前天（9月28日 星期一） · 9 条` 放不下 ⇒ **下拉里**改用这个短文案；
      **卡片标题**那行继续用 `_rel_day_label`（那边地方宽，「前天」也更有温度）。

    ⚠⭐ **为什么要开一个公共的，而不是让网页端直接用那个 `_day_label`**：
      下划线开头的是私有 helper，跨模块直接引是坏味道（改了签名谁都不知道）。
      这层壳就一句，但它把「**日期文案只有一份实现**」这条规矩落到实处 ——
      页面永远不需要自己拼日期（`docs/日记.md` 红线 6）。
    """
    return _day_label(day_key)


def _rel_day_label(day_key, today_key):
    """带上「昨天 / 前天」这种相对说法 —— 模型对「昨天」比对日期敏感得多。"""
    if day_key == today_key:
        return "今天（%s）" % _day_label(day_key)
    try:
        d = time.mktime(time.strptime(day_key, "%Y-%m-%d"))
        t = time.mktime(time.strptime(today_key, "%Y-%m-%d"))
        delta = int(round((t - d) / 86400.0))
        if delta == 1:
            return "昨天（%s）" % _day_label(day_key)
        if delta == 2:
            return "前天（%s）" % _day_label(day_key)
        if delta > 2:
            return "%d 天前（%s）" % (delta, _day_label(day_key))
    except Exception:
        pass
    return _day_label(day_key)


def _clean_day_text(text, limit=200):
    """
    小结文本**必须清洗**才能进 system：

    ⚠ 这段是**模型写的**，而 system 里禁 markdown 星号与 ASCII 双引号（锁定口径）
      ⇒ 不洗的话，模型随手一个 `**重点**` 就成了注入 system 的脏数据。
      跟节日/日常池子生成时的清洗是同一个道理。
    """
    s = str(text or "")
    s = re.sub(r"\*\*|\*|#+\s*", "", s)
    s = re.sub(r'"([^"\n]{0,80})"', "「\\1」", s)   # 成对的引号换中文引号
    s = s.replace('"', "").replace("`", "")        # 落单的引号/反引号直接去掉
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()[:limit]


# ============================================================
#  💾 记忆持久化
# ============================================================

def save_memory(user_id: str, cm):
    """把某个用户的记忆保存到 JSON 文件"""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    data = {
        "messages": cm.messages,
        "long_term_summary": cm.long_term_summary,
        # 待总结缓冲也要落盘：不加的话，重启会丢掉「上次满 SUMMARY_INTERVAL 轮之后、
        # 还没累够一轮」的那批素材，它们就永远进不了摘要（2026-09-22 修）。
        "pending_summary": cm.pending_summary,
        "key_facts": cm.key_facts,
        "turn_count": cm.turn_count,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        # 🕐 她最后一条消息的时间戳（用于重启后第一条也算得出「隔了多久」）
        "last_msg_at": cm.last_msg_at,
        # 🗓 跨天小结（2026-09-24）：[{date: "YYYY-MM-DD", text: "…"}]，最多 DAY_KEEP 条
        "day_summaries": cm.day_summaries,
    }
    path = os.path.join(MEMORY_DIR, f"{user_id}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 先写临时文件再替换，防止写一半崩了丢数据


def load_memory(user_id: str, cm) -> bool:
    """启动时读回记忆，文件不存在返回 False"""
    path = os.path.join(MEMORY_DIR, f"{user_id}.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cm.messages = data.get("messages", cm.messages)
        cm.long_term_summary = data.get("long_term_summary", cm.long_term_summary)
        # 待总结缓冲也要读回来（老文件没这个 key）。不是 list 就当没有 ——
        # 损坏的数据不该让整份记忆载入失败。
        _pend = data.get("pending_summary")
        if isinstance(_pend, list):
            cm.pending_summary = _pend
        # ⚠⚠ `key_facts` **必须是列表**（2026-09-30 加这道闸，跟上面 `_pend`、
        #    下面 `_days` 同一个 `isinstance` 口径 —— 三处原先就差这一个）。
        #    它是**字符串**时会怎样（文件名假设被人手改过）：
        #      · 加一条 ⇒ `cm.key_facts.append(...)` ⇒ AttributeError ⇒ 网页端 500
        #      · 改 / 删 ⇒ `[x for x in cm.key_facts ...]` 会去**遍历字符**，
        #        把 `"她对海鲜过敏"` 变成 `['她','对','海','鲜','过','敏']` 存回去
        #        ⇒ 她的「他记住的事」整组炸成一个字一个字，**而且什么都没报错**。
        #    ⇒ 不是列表就当没有（跟 `_pend` / `_days` 一致的收法）。
        _kf = data.get("key_facts")
        cm.key_facts = _kf if isinstance(_kf, list) else []
        cm.turn_count = data.get("turn_count", 0)
        # 🕐 读回「她最后一条消息」的时间戳。
        #    老文件没有 `last_msg_at` ⇒ 退回 `saved_at`（那是上次**存盘**的时刻，
        #    比她真正说话晚一轮回复，够用）；都读不到就 None ⇒ 这一轮不显示间隔。
        stamp = data.get("last_msg_at")
        if stamp is None:
            saved = data.get("saved_at")
            try:
                stamp = time.mktime(time.strptime(saved, "%Y-%m-%d %H:%M:%S"))
            except Exception:
                stamp = None
        cm.last_msg_at = stamp
        # 🗓 跨天小结：老文件没有这个 key（2026-09-24 之前）⇒ 当空表处理，
        #    ⚠ 不能因此报错 —— 读记忆失败会让整份记忆从头开始，代价太大。
        _days = data.get("day_summaries")
        if isinstance(_days, list):
            cm.day_summaries = [d for d in _days if isinstance(d, dict) and d.get("text")][-DAY_KEEP:]
        return True
    except Exception as e:
        print(f"⚠️ 读取记忆失败（{user_id}）：{e}，将从头开始")
        return False


def _read_key_facts(user_id: str):
    """
    只从 `memory/{uid}.json` 里取 `key_facts` 这一格（**纯读**：不建文件、不写一个字节）。

    ⚠⚠ 为什么单独有这么一个函数（2026-09-30，B 方案）：
      `ConversationManager.key_facts` 是**进程级缓存**（`load_memory()` 只在首次访问时读一次），
      而 `save_memory()` 写的是**整份缓存** ⇒ 她在网页上改的「他记住的事」
      会被下一轮对话整份盖回去。⇒ 每轮开头拿本函数把这一格换成磁盘上的**真值**。

    ⚠ 读不到 ⇒ 返回 `None`（**不是** `[]`）。「文件读坏了 / 拿不到」和「她就是空的」
      是两件事：前者若当空表用，下一轮 `save_memory()` 就把她记的东西全冲掉。
      `None` 的语义 = **这一轮不刷新**，退回改动前的行为（继续用缓存那份）。
    ⚠ 类型闸跟 `load_memory()` / `compute()` / `page/home.py` 三处保持一致
      （不是 list 就当没有 —— 见文件头「先看类型再看内容」那节）。
    """
    path = os.path.join(MEMORY_DIR, f"{user_id}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ 重读 key_facts 失败（{user_id}）：{e}，本轮不刷新")
        return None
    if not isinstance(data, dict):
        return None
    facts = data.get("key_facts")
    return facts if isinstance(facts, list) else []


def recent_context(user_id, n=6):
    """
    🎯 取「她最近在聊什么」的一段文本（给**发说说挑条**做相关性用）。

    内容 = 她最近 n 条消息 + 长期摘要尾部 + 最近几条关键事实。

    ⚠ 直接读 `memory/{uid}.json`，**不碰** `ConversationManager` ——
      发说说那条链（后台 task）不该依赖对话引擎的进程内状态，
      而且它跑在**另一个协程**里，去摸 `cm.messages` 既没必要也不安全。
    """
    path = os.path.join(MEMORY_DIR, f"{user_id}.json")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""

    parts = []
    for m in (data.get("messages") or []):
        if isinstance(m, dict) and m.get("role") == "user":
            parts.append(str(m.get("content") or ""))
    parts = parts[-n:]

    summary = (data.get("long_term_summary") or "").strip()
    if summary and summary != "（你们刚开始聊天，还没有值得记录的重要事件。）":
        parts.append(summary[-300:])          # 摘要可能很长，只取尾巴（越近越有用）

    # 🗓 跨天小结：跨天滚动之后 `messages` 会变空 ⇒ 只靠 messages 取材会「没话说」，
    #    把最近一天的小结也带上（挑说说时同样是「她最近在聊什么」）。
    for d in (data.get("day_summaries") or [])[-1:]:
        if isinstance(d, dict) and d.get("text"):
            parts.append(str(d["text"])[:200])

    for f in (data.get("key_facts") or [])[-5:]:
        parts.append(str(f))

    return "\n".join(parts)


# ============================================================
#  🗂 对话管理
# ============================================================

class ConversationManager:
    """对话管理器：维护每个用户的对话状态、记忆和用户画像"""

    def __init__(self, base_prompt, user_id="unknown"):
        self.base_prompt = base_prompt
        self.user_id = user_id
        self.messages = [{"role": "system", "content": base_prompt}]
        self.long_term_summary = "（你们刚开始聊天，还没有值得记录的重要事件。）"
        self.key_facts = []
        self.turn_count = 0
        self.pending_summary = []  # 待总结的对话片段

        # 上一轮请求的真实用量与结束原因（2026-09-15 加，查截断/排查用，不参与对话）
        self.last_finish_reason = None
        self.last_usage = None

        # 🕐 她**上一条消息**的时间戳（epoch 秒）—— 用来算「隔了多久」。
        #    ⚠ 只由 `add_user_message` 推进：发说说/打招呼那些**他说的话**不算她开口，
        #      否则他刚提醒过一句，下次就变成「隔了 0 小时」，间隔提示永远不出现。
        self.last_msg_at = None
        # ⚠ `gap_hours` 是**快照**（收她这条消息那一刻算的），不是实时算：
        #    `get_reply` 的顺序是先 add_user_message 再 update_system_message，
        #    等拼 system 时 `last_msg_at` 已经被推进到「现在」了 ⇒ 实时算永远是 0。
        self.gap_hours = None

        # 用户画像：独立文件，从对话里慢慢积累
        self.profile = UserProfile(user_id)

        # 🗓 跨天小结（2026-09-24）：[{"date": "YYYY-MM-DD", "text": "…"}]，最多 DAY_KEEP 条。
        #    为什么要有它：`messages` 里**没有时间戳** ⇒ 昨天的原话今天看着还是「当下」，
        #    模型就会把昨天的事当成今天。跨天时把昨天的原话摘出去、换成一条带日期的小结。
        self.day_summaries = []

    def reload_shared_from_disk(self):
        """
        🔄 把「网页端也能写」的两处从磁盘重读一次（**每轮对话开头**调）。

        ⚠⚠ 为什么必须有这一步（2026-09-30 查实，B 方案）：
          `Rafayel_llm._user_managers` 是**进程级缓存**，`load_memory()` 只在
          **首次**访问那个用户时读一次；之后 `save_memory()` / `self.profile.save()`
          写的都是**整份缓存副本** ⇒ 她在网页上改的
          ·「他记住的事」      → `{uid}.json` 的 `key_facts`
          ·「生日 / 称呼 / 他记住的你」 → `{uid}_profile.json`
          会被下一轮对话**整份盖回去**。两条来路都会中：
            · **跨进程**：QQ 端 `Rafayel_bot.py` vs `web/`
            · **同进程**：网页对话窗口（`page/chat.py` 直接调 `get_reply`）vs 主页写入
          ⇒ 每轮开头把这两格换成磁盘真值，本轮之后的新增就都作用在「刚读出来的那份」上。

        ⚠ **只刷这两处**，绝不动 `messages` / `long_term_summary` / `pending_summary` /
          `day_summaries` / `turn_count` —— 那些是对话连续性的根，重读了等于把本轮上下文抹掉
          （所以**不能**图省事直接调 `load_memory()`，那是整份覆盖）。

        ⚠ 调用点必须在 `_extract_facts()` / `extract_from_text()` **之前**：
          顺序反了，本轮刚提取到的事实会被这次重读一起冲掉。
        """
        # ① 画像：`UserProfile.load()` 本身就是幂等重载（文件不在就保持现状）
        self.profile.load()
        # ② 「他记住的事」：只换这一格（读不到 ⇒ None ⇒ 不动，见 `_read_key_facts()`）
        facts = _read_key_facts(self.user_id)
        if facts is not None:
            self.key_facts = facts

    def get_full_system_prompt(self):
        """构建完整的系统提示词，包含用户画像、记忆摘要和关键事实"""
        # 基础 prompt
        full_prompt = self.base_prompt

        # 追加用户画像（独立文件，从对话里积累；一条都没有就整段不注入）
        profile_text = self.profile.to_prompt_text()
        if profile_text:
            full_prompt += "\n\n" + profile_text

        # 追加长期记忆摘要
        full_prompt += f"\n\n## 📖 长期记忆摘要（请记住这些重要内容）\n{self.long_term_summary}"

        # 🗓 跨天小结（2026-09-24）：让他知道哪件事是哪天的，别把昨天当成今天。
        #    位置紧跟长期摘要 —— 两者都是「记忆」，挨着才读得顺。
        #    ⚠ 这节一天只变一次（跨天那轮），不会每轮破坏前缀缓存。
        _days = self._render_days()
        if _days:
            full_prompt += "\n\n" + _days

        # 追加关键事实
        if self.key_facts:
            facts_text = "\n".join([f"- {f}" for f in self.key_facts[-MAX_FACTS:]])
            full_prompt += f"\n\n## 📌 关键事实（用户让你记住的事）\n{facts_text}"

        # 🕐 「现在几点 + 隔了多久」**不在这里**了 —— 见下面 `now_hint_text()` 的注释。
        #    2026-09-20 挪走：这段每轮都变，留在 system 里会把 DeepSeek 的**前缀缓存**拦腰截断。

        # 💬 回复形状（2026-09-19 她挑的段内不拆行 + 2026-09-22 放宽成一到四段）。
        #    ⚠ 代码层还有一道保底（Rafayel_llm 发出前整形）—— 这里只是让模型少犯错。
        if REPLY_SHAPE and REPLY_SHAPE_HINT:
            full_prompt += "\n\n" + REPLY_SHAPE_HINT

        return full_prompt

    def now_hint_text(self, fest=None, nudge=None):
        """
        🕐 「现在几点 + 隔了多久」那一段 —— 由调用方**追加在聊天历史之后**送出去。

        ⭐ 2026-09-20 从 `get_full_system_prompt()` 末尾挪到这里，为的是**钱**：
          DeepSeek 的缓存是**前缀匹配** —— 只要前缀里有一处变了，从那一点往后
          **全部**按「未命中」计费。这段每轮都变（几点、隔了多久），它待在 system 里
          ⇒ system 每轮不同 ⇒ 它后面那**整段聊天历史**（12 轮 ≈ 2000+ tokens）
          永远吃不到缓存。挪到最后一条 ⇒ system + 历史整段稳定 ⇒ 命中率大涨。
          （9/19 实测：命中 404K / 未命中 142K，命中率 74% ⇒ 目标 90%+。）

        ⚠ 只进 request_messages，**绝不写回 cm.messages**（跟世界书/表情说明同一个口径）：
          否则会被 save_memory 落盘，每轮累积一份，还会变成常驻人设。
        ⚠ 注意力：原来靠「system 越靠后越受关注」，现在改成「整段 prompt 的最后一条」，
          位置同样是最靠后 ⇒ 真机 A/B 验过再定，不行就往回挪。
        """
        return now_prompt_text(self.gap_hours, fest=fest, nudge=nudge)

    def _compute_gap_hours(self):
        """
        距她上一条消息过了多少小时；没有基准（第一次聊 / 记录缺失）返回 None。
        """
        if not self.last_msg_at:
            return None
        try:
            return (time.time() - float(self.last_msg_at)) / 3600.0
        except (TypeError, ValueError):
            return None

    # ============================================================
    #  🗓 跨天滚动（2026-09-24）
    # ============================================================
    #  症状（她 2026-09-24 反馈）：他昨天说「今天去海边走了走」，今天早上又当成今天的事说。
    #  根因：messages 里消息**没有时间戳**，`truncate_history` 又只按条数裁
    #        ⇒ 跨天后昨天的原话原封不动留在历史里，模型自然当成刚说过。
    #  ⇒ 跨天时把「上一自然日」的原话**摘出历史** + 让模型压成一条带日期的小结，
    #    小结随 system 注入（长期摘要下面）⇒ 他知道那是哪天的事，也不再拿着旧原话当今天。

    def _summarize_day(self, day_key, msgs, api_key):
        """
        把某一天的原话压成 1~3 句带日期的小结。

        ⚠⭐ 失败一律返回空串 —— 调用方据此**不摘历史**：
          宁可他今天还把昨天当今天（能自愈，明天再滚一次），也**绝不能丢聊天记录**。
        ⚠ 生成的文本要过 `_clean_day_text`：它最终会进 system（禁星号 / ASCII 引号）。
        """
        lines = []
        for m in msgs[-40:]:
            if not isinstance(m, dict):
                continue
            role = "她" if m.get("role") == "user" else "你"
            c = str(m.get("content") or "").replace("\n", " ").strip()[:120]
            if c:
                lines.append("%s：%s" % (role, c))
        if not lines:
            return ""

        prompt = (
            "下面是祁煜和她那天的聊天记录（%s）。\n"
            "请用 1~3 句话写成一条给祁煜自己看的备忘，好让他以后不把那天的旧事当成今天发生的。\n"
            "要求：① 只写这段里真有的内容，不推测、不补细节；"
            "② 写清是谁说了或做了什么，不要写感想、不要抒情；"
            "③ 不许用星号、井号、英文引号；④ 直接给正文，不要任何标题或前缀。\n\n%s"
        ) % (_day_label(day_key), "\n".join(lines))

        data = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": DAY_SUMMARY_MAX_TOKENS,
            "temperature": 0.3,          # 这是备忘不是聊天，别让它发挥
        }
        if LLM_EXTRA:
            data.update(LLM_EXTRA)
        try:
            r = requests.post(API_URL,
                              headers={"Authorization": "Bearer %s" % api_key,
                                       "Content-Type": "application/json"},
                              json=data, timeout=15)
            out = r.json()["choices"][0]["message"]["content"]
        except Exception:
            return ""
        return _clean_day_text(out)

    def roll_days(self, api_key):
        """
        跨天就滚一次：写小结 ⇒ **成功之后**才把旧原话摘出历史。返回 True = 真滚了。

        ⚠ 顺序不能反：先摘历史再写小结的话，模型一挂那段对话就永久没了。
        ⚠ 同一天内怎么聊都只会走到 `prev == today` 那一支 ⇒ 不动历史、不花 token。
        """
        if not DAY_ROLL or not api_key:
            return False
        if not self.messages or len(self.messages) <= 1:
            return False                      # 没有历史可滚
        ts = self.last_msg_at
        if not ts:
            return False                      # 没有「她上次说话」的基准 ⇒ 不猜
        now = time.time()
        today = _day_key(now)
        prev = _day_key(ts)
        if prev == today:
            return False                      # 同一天：不滚
        try:
            gap = (now - float(ts)) / 3600.0
        except (TypeError, ValueError):
            gap = 0.0
        if gap < DAY_ROLL_MIN_GAP:
            # 零点前后紧接的几句仍算「连着聊」—— 刚聊完一分钟就被做成「昨天」，太怪
            return False

        old = [m for m in self.messages[1:]]
        text = self._summarize_day(prev, old, api_key)
        if not text:
            return False                      # ⚠ 写不出小结就不摘：绝不丢记录

        self.messages = [self.messages[0]]
        self.day_summaries.append({"date": prev, "text": text})
        # 只留最近 DAY_KEEP 天（老的自然掉队，文件也不会越长越大）
        self.day_summaries = self.day_summaries[-DAY_KEEP:]
        return True

    def _render_days(self):
        """
        「## 🗓 最近几天」那一段（进 system）。

        ⚠ 一天只变一次 ⇒ 不会像「现在几点」那样每轮把前缀缓存拦腰截断；
          没有小结时**整段不注入**（新用户不该看到空壳）。
        """
        if not DAY_ROLL or not self.day_summaries:
            return ""
        today = _day_key(time.time())
        rows = ["## 🗓 最近几天（过去的事别当成今天）",
                "今天是%s。" % _day_label(today)]
        for item in self.day_summaries[-DAY_KEEP:]:
            d = str(item.get("date") or "")
            txt = str(item.get("text") or "").strip()
            if not d or not txt or d == today:
                continue
            rows.append("%s：%s" % (_rel_day_label(d, today), txt))
        if len(rows) <= 2:
            return ""                          # 只有「今天是…」一行 ⇒ 不值得占位置
        rows.append(
            "（以上都是过去发生的事。她说「今天」时只指%s；"
            "这些旧事你可以记得、可以提，但别再当成今天发生的。）" % _day_label(today)
        )
        return "\n".join(rows)

    def update_system_message(self):
        """更新 messages 中的 system 消息"""
        self.messages[0]["content"] = self.get_full_system_prompt()

    def add_user_message(self, content, media=False):
        """
        添加用户消息。

        `media` = 她这条是不是图 / 表情（记进每日统计，好感度会用到）。
        """
        # 🔄 **最先做**：把网页端能改的两处从磁盘重读一遍。
        #    ⚠ 必须在下面 `_extract_facts()` / `extract_from_text()` **之前** ——
        #      顺序反了，本轮刚提取到的事实会被这次重读冲掉。
        self.reload_shared_from_disk()

        # 🕐 先算「隔了多久」再推进时间戳 —— 顺序反了就永远算成 0。
        self.gap_hours = self._compute_gap_hours()
        self.last_msg_at = time.time()

        # 📅 每日统计（好感度要用：哪天聊过 / 连续几天 / 谁先开口 / 发没发图）。
        #    ⚠ gap 必须传**刚算出来的快照**：函数里再算一次就已经被上面推进成 0 了。
        #    ⚠ daily_record 自己吞异常，这里不再包一层（写不进去最多少几个数，别拖累对话）。
        daily_record(self.user_id, gap_hours=self.gap_hours, media=media)

        self.messages.append({"role": "user", "content": content})
        self.turn_count += 1
        self.pending_summary.append({"role": "user", "content": content})

        # 检测关键词，提取关键事实
        self._extract_facts(content)

        # 从她的话里留意她的喜好（规则命中即存，命中就落盘）
        if self.profile.extract_from_text(content):
            self.profile.save()

    def add_assistant_message(self, content):
        """添加助手消息"""
        # 🔄 开场白 / 主动开口这类「**她那条消息不在前**」的轮次，也刷一次
        #    （正常一轮的最后一条是 user ⇒ 那次刷新已经在 `add_user_message` 里做过，
        #     这里再刷会把本轮刚提取到的事实冲掉）。
        #    判据就是「最后一条不是她说的」—— `system` / `assistant` / 空 都算。
        _last = self.messages[-1] if self.messages else None
        if not (isinstance(_last, dict) and _last.get("role") == "user"):
            self.reload_shared_from_disk()

        self.messages.append({"role": "assistant", "content": content})
        self.pending_summary.append({"role": "assistant", "content": content})

        # 从祁煜的回复中提取可能的重要信息（比如承诺、约定）
        self._extract_facts_from_reply(content)

    def _extract_facts(self, text):
        """从用户输入中提取关键事实"""
        # 检测用户是否在强调某件事
        patterns = [
            (r"记住[：:]\s*(.+)", "用户让你记住：{}"),
            (r"别忘了[：:]\s*(.+)", "用户提醒你：{}"),
            (r"答应我[：:]\s*(.+)", "你答应了用户：{}"),
            # 2026-09-15 修：原写法 (吗\?|吗|么|不)? 是**第 1 个捕获组**，
            # 用户输入「你还记得xxx」时 group(1)=None → len(None) TypeError 直接崩掉整轮回复。
            # 改成非捕获组 (?:…)，group(1) 才是真正要记的内容。
            #   ⚠ 顺带修掉原正则里的 `.{0,10}`：它是贪婪的，会把用户真正想说的内容吃掉，
            #     最后只捕获到末尾一两个字，再被 len>3 过滤掉 ⇒ 不崩了但也什么都记不到。
            (r"你还记得(?:吗\?|吗|么|不)?[，,。.、]?\s*(.+)", "用户提起过去的回忆：{}"),
            (r"以前[：:]\s*(.+)", "用户提到以前的事：{}"),
            (r"约定[：:]\s*(.+)", "你们之间的约定：{}"),
        ]

        for pattern, template in patterns:
            match = re.search(pattern, text)
            if match:
                # 2026-09-15 修：原写法 match.group(1) if match.groups() 有两个坑——
                #   ① groups() 判的是「元组非空」，group(1) 仍可能是 None（可选组没匹配上）
                #   ② None 传进 len() 直接 TypeError，而本函数由 add_user_message 调用且无 try 保护
                # 改用 lastindex 判断最后一个参与匹配的捕获组，并兜底取整句。
                fact = ""
                if match.lastindex:
                    fact = (match.group(match.lastindex) or "").strip()
                if not fact:
                    fact = text[:50]
                if len(fact) > 3:
                    self._add_fact(template.format(fact[:80]))
                    break

    def _extract_facts_from_reply(self, text):
        """从祁煜的回复中提取承诺、约定等"""
        patterns = [
            (r"我答应你[：:]\s*(.+)", "祁煜答应了：{}"),
            (r"我保证[：:]\s*(.+)", "祁煜保证了：{}"),
            # 2026-09-15 修：[记住|记得] 是**字符类**，匹配「记/住/|/得」任一单字，
            # 永远匹配不到「我会记住：xxx」。改成 (?:记住|记得)。
            (r"我会(?:记住|记得)[：:]\s*(.+)", "祁煜说会记住：{}"),
        ]
        for pattern, template in patterns:
            match = re.search(pattern, text)
            if match:
                fact = ""
                if match.lastindex:
                    fact = (match.group(match.lastindex) or "").strip()
                if not fact:
                    fact = text[:50]
                if len(fact) > 3:
                    self._add_fact(template.format(fact[:80]))
                    break

    def should_summarize(self):
        """是否需要触发摘要更新"""
        return self.turn_count > 0 and self.turn_count % SUMMARY_INTERVAL == 0

    def get_recent_messages(self):
        """获取最近 N 轮对话（不含 system）"""
        all_messages = self.messages[1:]  # 去掉 system
        if len(all_messages) > MAX_HISTORY_TURNS * 2:
            return all_messages[-(MAX_HISTORY_TURNS * 2):]
        return all_messages

    def truncate_history(self):
        """截断历史，保留最近 N 轮 + system"""
        all_messages = self.messages[1:]  # 去掉 system
        if len(all_messages) > MAX_HISTORY_TURNS * 2:
            recent = all_messages[-(MAX_HISTORY_TURNS * 2):]
            self.messages = [self.messages[0]] + recent

    def generate_summary(self, api_key):
        """调用 AI 生成对话摘要"""
        if not self.pending_summary:
            return

        # 取待总结的对话
        to_summarize = self.pending_summary.copy()
        self.pending_summary = []

        # 构造摘要 prompt
        # 🗓 2026-09-24：把「这段对话发生在哪一天」写进 prompt。
        #    原来不带日期 ⇒ 摘要是**无时间坐标**的一段话，昨天的和今天的混在一起，
        #    这正是「把昨天当成今天」的另一个来源（她反馈的那个症状）。
        _day_now = _day_key(time.time())
        # 💗 他**写这段日记时**的心情（2026-10-01，主档 2.7）
        #   ⚠ 取的是「此刻」，不是这 8 轮的平均 —— 谁写日记不是写当下那一刻的心情。
        #   ⚠ 情绪是后台线程判的 ⇒ 这里拿到的是**上一轮**判定的，差一轮，无感。
        #   ⚠ 开关关着 / 心情是平静 ⇒ None ⇒ prompt 不加、日记也不记（零变化）。
        _mood_now = None
        try:
            _mood_now = mood_current(self.user_id)
        except Exception as _me:
            print(f"⚠️ 取心情失败（日记照常写）：{_me}")
        _mood_line = ("6. ⭐ 你写这段日记时的心情是：%s（强度 %d）。"
                      "照着这个心情写，但**别把心情当题目** —— 该写那天那件事还是写那件事，"
                      "心情只是你落笔时的口气。\n"
                      % (_mood_now["mood"], _mood_now["level"])) if _mood_now else ""
        summary_prompt = f"""你正在为祁煜整理对话记忆。

【这段对话发生在】{_day_label(_day_now)}

【任务一】用一段话（不超过200字）总结这段对话的核心内容：
1. 她表现出了哪些情绪、需求或想法？
2. 祁煜做出了哪些重要的回应、承诺或行动？
3. 发生了什么可能影响后续对话的重要事件？
⚠ 必须写成**客观的第三人称**（「她说…」「祁煜…」）—— 这段是记忆提要，不是日记，
  写成「我」会污染他的长期记忆。
⚠ 写到具体事情时带上时间坐标（比如「{_day_label(_day_now)}她说…」），
  别把不同天的事并成一件；不确定是哪天就写「那天」。

【任务二】从对话里留意「关于她」的事实。只写**她自己明确说过**的，不要推测、不要脑补：
- name：她让祁煜怎么叫她（没说过就空字符串）
- likes：她喜欢什么（数组）
- dislikes：她讨厌 / 不吃 / 不喜欢什么（数组）
- traits：其他稳定特征，比如职业、习惯、作息（数组）
- birthday：她**自己**的生日，形如 "03-06"（公历，月-日两位）。
  ⚠ 只填她**明确说过**的（「我生日是3月6号」）；她没说过、或是**祁煜**的生日，一律空字符串。

【任务三】替祁煜写**这一段他自己的日记**（不超过200字，第一人称）：
1. ⚠⚠ **通篇不许出现「祁煜」这三个字** —— 谁写日记会管自己叫名字？
   一律用「我」。这是最容易写错的一条。
2. 她写成「她」，或者你平时叫她的那个称呼；别写「用户」「对方」「该用户」。
3. 味道是**写给自己看的**：可以承认当时没说出口的、心里拐过的念头、硬撑的地方。
   **不是汇报**，不用面面俱到 —— 挑这一天里最戳你的一件事写。
4. 用你自己（人设卡里那个祁煜）的口气，别写成台下旁观的观察记录。
5. ⚠ **只写这段对话里真实发生过的**，不编。
   真没什么值得写的，就只输出两个字：无
{_mood_line}

对话内容：
{json.dumps(to_summarize, ensure_ascii=False, indent=2)}

输出格式（严格照做，不要加「任务一」这种小标题，也不要加代码块围栏）：
先写任务一的摘要正文（第三人称），
然后换行，单独一行只写：
DIARY:
然后写任务三的日记正文（第一人称「我」），
然后换行，最后单独一行写：
PROFILE: {{"name": "", "likes": [], "dislikes": [], "traits": [], "birthday": ""}}
"""
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "model": MODEL,
                "messages": [{"role": "user", "content": summary_prompt}],
                "stream": False,
                "max_tokens": SUMMARY_MAX_TOKENS
            }
            if LLM_EXTRA:
                data.update(LLM_EXTRA)
            response = requests.post(API_URL, headers=headers, json=data, timeout=10)
            result = response.json()

            if "choices" in result:
                raw = result["choices"][0]["message"]["content"].strip()

                # ① 先切尾：任务二的画像补丁（模型没按格式输出就当没有，不影响摘要）
                m_prof = re.search(r"PROFILE:\s*(\{.*?\})\s*$", raw, re.S)
                body = raw[:m_prof.start()] if m_prof else raw
                if m_prof:
                    try:
                        if self.profile.merge(json.loads(m_prof.group(1))):
                            self.profile.save()
                    except Exception as pe:
                        print(f"⚠️ 画像补丁解析失败（忽略）：{pe}")

                # ② 再切中间那道 `DIARY:` 分界线 ⇒ 前=摘要（第三人称，喂 prompt），
                #    后=日记（第一人称，给他自己看）。⚠ 切不出来就是日记为空，见该函数说明。
                new_summary, diary_text = _split_diary_reply(body)

                if not new_summary:
                    new_summary = "（本轮无可摘要内容）"

                if self.long_term_summary == "（你们刚开始聊天，还没有值得记录的重要事件。）":
                    self.long_term_summary = new_summary
                else:
                    self.long_term_summary = self.long_term_summary + "\n\n" + new_summary
                if len(self.long_term_summary) > 1500:
                    self.long_term_summary = self.long_term_summary[-1500:]
                    self.long_term_summary = "...(较早记忆已压缩)...\n" + self.long_term_summary

                # 📔 日记（2026-09-30 · 她：「按天进行多次总结」）：
                #    同一次摘要，在日记里也留一条 ⇒ **一次摘要 = 一条**，
                #    一天聊得多就一天多条（她的例子：20 轮 ÷ 每 8 轮 = 2 条）。
                # ⚠⚠ **存的是 `diary_text`（任务三的第一人称那段），不是 `new_summary`** ——
                #    2026-09-30 她指出「这是祁煜的日记，应该以他的视角、第一人称写」，
                #    修之前这里传的是 `new_summary`（第三人称记忆提要），
                #    所以日记里全是「祁煜从调侃、退后到被…堵住」那种旁人视角的汇报。
                # ⚠⚠ 这段**必须自己 try**（不能靠外层那个）：
                #    外层 `except` 会把 `pending_summary` **重新排回队列**（:746，本意是
                #    「LLM 调用失败就下次再摘要」）⇒ 日记写盘一抛异常，这段对话会被
                #    **反复摘要**，日记里就冒出重复条目。
                # ⚠ 位置放在 `long_term_summary` 之后：日记写不成，也**不该影响**他的长期记忆。
                # ⚠ `DIARY_ENABLE=False` ⇒ 摘要照旧，日记一条不加（开关只影响这一处）。
                # ⚠ `diary_text` 可能是空的（模型没写 / 说了「无」）⇒ **这条不加**，
                #    不是错误、不用重试：摘要本身是好的，长期记忆一点没少。
                try:
                    _diary_text = _diary_text_from(diary_text)
                    if _diary_text:
                        # 💗 顺手把「写这条时他的心情」存进条目（主档 2.7）。
                        #    ⚠ 只存**标签**、不存强度 —— 强度是瞬时的，跨天没意义（会衰减）。
                        add_diary(self.user_id, _diary_text, src="he",
                                  mood=(_mood_now or {}).get("mood") or "")
                    else:
                        print("[📔] 这次没有可写的日记段（摘要照常更新）")
                except Exception as de:
                    print(f"⚠️ 日记写入失败（不影响对话）：{de}")
        except Exception as e:
            print(f"⚠️ 摘要生成失败：{e}，将继续正常对话")
            self.pending_summary = to_summarize + self.pending_summary

    def _add_fact(self, fact: str):
        """追加关键事实（去重 + 上限）。2026-09-15 新增：旧版无去重，同一句会被反复记进去。"""
        if fact in self.key_facts:
            return
        self.key_facts.append(fact)
        self.trim_facts()

    def trim_facts(self):
        """限制关键事实数量"""
        if len(self.key_facts) > MAX_FACTS:
            self.key_facts = self.key_facts[-MAX_FACTS:]


# ============================================================
#  🌐 她**自己在网页上**改「他记住的事」（2026-09-30 主页第 4 批）
# ============================================================
# ⚠ 这三个是**模块级**函数，给 `web/page/home.py` 用 ——
#    `Rafayel_memory` 在 `check_static.py` 的 `WRITER_MODULES` 里，
#    网页端 import 得先在 `WEB_WRITE_EXCEPTION` 开口（本批开的第 5 条）。
# ⭐ 一律 `load_memory()` → 改 → `save_memory()` ⇒ 跟 bot 走**同一套**落盘逻辑，
#    不会「网页存成一种格式、bot 又存成另一种」。
def _facts_edit(user_id: str, fn) -> bool:
    """
    「读 → 改 → 存」的壳。`fn(cm)` 返回 True 才落盘。

    ⚠⭐ `load_memory()` 返回 False ⇒ **直接放弃，绝不 save** ——
       那意味着文件不存在或读坏了；这时候拿一个空对象去存 = 把她整份记忆清空。
       **宁可这次改不成，也不能赌**（顺带也避免了「网页端凭空造出一份记忆」）。
    ⚠ 网页端**不许**自己 `json.load` / `json.dump` `memory/{uid}.json`：
       那份文件的字段清单归 `save_memory()` 管（它只写固定的那些），
       自己拼就会漏字段或写出多余的键。
    """
    cm = ConversationManager("", user_id)
    if not load_memory(user_id, cm):
        return False
    if not fn(cm):
        return False
    save_memory(user_id, cm)
    return True


def add_fact_by_her(user_id: str, text: str) -> bool:
    """
    🌐 她**自己在网页上加一条**「他记住的事」。返回是否有变化。

    ⭐ 去重口径跟 `_add_fact()`（规则轨）一致：**字面相等**。
       key_facts 存的是带模板前缀的整句（「用户让你记住：xxx」/「祁煜答应了：xxx」），
       语义去重会把两条不同来源的约定并成一条 —— 那种静默丢信息比多记一条更糟。
    ⚠ 长度上限 80 —— 跟 `_add_fact()` 里的 `fact[:80]` 对齐（那边存的时候也是这么截的）。
    """
    text = (text or "").strip()
    if not text or len(text) > 80:
        return False

    def _do(cm):
        if text in cm.key_facts:
            return False
        cm.key_facts.append(text)
        cm.trim_facts()                # 超 20 条丢最早的
        return True
    return _facts_edit(user_id, _do)


def edit_fact_by_her(user_id: str, old: str, new: str) -> bool:
    """
    🌐 她**自己在网页上改一条**。返回是否有变化。

    ⚠ **整条替换（含前缀）** —— 前缀（`用户让你记住：` 那些）本批不拆，
       显示什么样就编辑什么样；拆前缀要解析 6 种模板，拆错就把内容改坏了。
    """
    old = (old or "").strip()
    new = (new or "").strip()
    if not old or not new or len(new) > 80 or old == new:
        return False

    def _do(cm):
        if old not in cm.key_facts:
            return False
        cm.key_facts = [new if x == old else x for x in cm.key_facts]
        return True
    return _facts_edit(user_id, _do)


def delete_fact_by_her(user_id: str, text: str) -> bool:
    """
    🌐 她**自己在网页上删一条**。返回是否有变化。

    ⭐ **不留抑制名单** —— 跟第 3 批删画像标签**不一样**，是刻意的：
       key_facts 只有两种来源 —— 她说「记住：xxx」、祁煜说「我保证：xxx」。
       删掉之后要再生成，得**重新发生一次那样的发言**；那是一次**新事件**，不该被拦。
       ⇒ 留痕反而是错的：她改主意想记回来时，会被自己上次点的「删」挡住。
    ⚠ 必须 `save_memory()` —— 只改内存的话，页面看着删了，一刷新就回来
       （第 2 批踩过同款 P0：`suppress()` 只改内存）。
    """
    text = (text or "").strip()
    if not text:
        return False

    def _do(cm):
        rest = [x for x in cm.key_facts if x != text]
        if len(rest) == len(cm.key_facts):
            return False
        cm.key_facts = rest
        return True
    return _facts_edit(user_id, _do)


# ============================================================
#  📔 日记 `memory/{uid}_diary.json`（2026-09-30 · 目录页那格「纪念日」改成的功能页）
# ============================================================
# 她定的三条（原话：「LLM 每 8 轮总结出来的那段话，放在『纪念日』改成『日记』，
#   按天进行多次总结。同样支持增删修改」）：
#   · 内容 = 他每次摘要（每 8 轮一次）那一段 ⇒ **一次摘要 = 一条**，
#     一天聊得多就一天多条（她给的例子：一天 20 轮 ⇒ 2 条）
#   · 按天分组展示
#   · 她**能增删改**，而且**他写的那几条她也能改能删**（她选的「都能改都能删」）
#
# ⚠⚠ 三条最要紧的规矩（改这一段之前先读）：
#   1. **`long_term_summary` 一个字都不动** —— 那份是喂 prompt 的 1500 字滚动窗口。
#      日记是**旁路记录**，不是它的替代品；把它换成日记 = 改他的记忆行为 = 改人设。
#   2. **绝不写进 `{uid}.json`** —— `save_memory()` 只写固定 8 个字段，
#      塞进去 bot 一保存就被冲没。所以另开一个文件（跟 `{uid}_daily.json` 同一个做法）。
#   3. **bot 侧调用必须包在内层 try 里**（见 `generate_summary()` 末尾那段）——
#      外层那个 try 的 `except` 会把 `pending_summary` **重新排回队列**，
#      日记写盘一抛异常，同一段对话就会被**反复摘要**，日记里冒出重复条目。
#
# ⚠⚠⚠ 2026-09-30（她指出「这是祁煜的日记，应该以他的视角、第一人称写」）：
#   **日记的正文跟摘要的正文是两段东西，绝不能再共用一段文字。**
#   · 摘要（任务一）= **客观第三人称**的记忆提要，喂 prompt 用；
#   · 日记（任务三）= **第一人称「我」**、写给自己看的那一段。
#   两段在**同一次 LLM 调用**里产出（她选的 A 方案：不额外多调一次，不给她加延迟），
#   靠回复正文里单独一行的 `DIARY:` 分界 ⇒ 见 `_split_diary_reply()`。
#   🐛 修之前是 `add_diary(self.user_id, new_summary)` —— 直接把摘要当日记存，
#      所以她看到的每一段都是「祁煜从调侃、退后到被…堵住」这种**旁人视角的汇报**。
#
# 存储（`day` **平时不存**，展示时用 `_day_key(ts)` 推 ⇒ 以后改时区偏移，历史条目跟着换算；
#      只有老数据导入才显式写 `day` —— 那条得挂到一个跟 `ts` 不完全对应的日子上）：
#      ⚠ 2026-09-30 起：她在网页上**改了时间的**条目，那个 `day` 会被**清掉**
#        （见 `edit_diary_by_her`）—— 不清的话 `day` 会压过新 `ts`，条目不搬家。
#   {"entries": [{"id": "1790664027290", "ts": 1790664027.29, "src": "he", "text": "…"}],
#    "updated_at": "2026-09-30 05:20:01"}
#   ⚠ 可选字段还有 `legacy`（老数据导入标记）/ `rewritten`（已被改成第一人称的戳，
#     见 `rewrite_diary_text_by_her`，给一次性脚本当幂等判据）。
#     `save_memory()` 不管这个文件 ⇒ **加字段是安全的**（那条 8 字段的限制只针对 `{uid}.json`）。
# ⚠ **按 `id` 增删改，别拿文本当主键**：日记自带时间戳，而且同一段话可能重复出现
#   （他今天和明天都可能总结出相似的一句），拿文本定位会删错。
# ⚠ 这一套之所以放在 `Rafayel_memory` 而不是新开 `Rafayel_diary.py`：
#    写钩子本来就在这个文件的 `generate_summary()` 里，而且网页端**已经**为「他记住的事」
#    开过 `Rafayel_memory` 的口子 ⇒ 放这儿 = 那个新页面**零新增 ADR-22 开口**。
#    代价是这个文件更长，将来真嫌大再拆。


# 🔀 摘要那条回复里，「摘要正文」与「日记正文」之间的分界标记。
#    ⚠ 宽容到四种写法：`DIARY:` / `**DIARY:**` / `## DIARY:` / `> DIARY:`，
#      冒号全角半角都收；`re.M` ⇒ **必须行首**（正文里随口说一句「日记」不算分界）。
#    ⚠⚠ 尾随那两个 `*` **必须显式吃掉**（`(?:\*\*)?`）—— 光靠左边那个字符类不够：
#      左边只负责 `DIARY` **前面**的记号，`**DIARY:**` 的收尾 `**` 会落进日记正文里，
#      变成每条日记都顶着两个星号开头（自测 A3 抓到过）。
#      ⚠ 也不能写成尾随 `[ \t>*_#]*` —— 那个会把日记正文自己的开头 `*叹气*` 一起吃掉。
DIARY_MARK_RE = re.compile(r"^[ \t>*_#]*DIARY[：:][ \t]*(?:\*\*)?[ \t]*", re.M)

# 🈳 模型在【任务三】里说「今天没什么可写的」时给的那几种写法 ⇒ 不往日记里塞废话。
#    ⚠ 比的是**剥掉引号之后**的整串（见 `_diary_text_from`），不是「包含」。
_DIARY_EMPTY = ("", "无", "（无）", "(无)", "（今天没什么可记的）", "（今天没什么可记的。）")


def _split_diary_reply(body: str):
    """
    把摘要那条回复的**正文**切成 `(摘要, 日记)`。

    ⭐ 2026-09-30 加（她：「这是祁煜的日记，应该以他的视角、第一人称写」）。
      摘要（任务一）是**客观记忆提要**，日记（任务三）是**他写给自己看的第一人称那段**，
      两段在同一次调用里产出，靠行首的 `DIARY:` 分界。

    ⚠⚠ **切不出 `DIARY:` ⇒ 日记返回 `""`（这条不写）**。
      绝不能「拿摘要顶替」—— 那正是修之前的行为，写进去又成了第三人称。
      ⇒ 宁可少一条，也不要一条错的：错的那条她一眼就能看见，还得回来再提一次。
    """
    m = DIARY_MARK_RE.search(body or "")
    if not m:
        return (body or "").strip(), ""
    return body[:m.start()].strip(), body[m.end():].strip()


def _diary_text_from(raw_text: str) -> str:
    """
    把【任务三】那段打磨成**能存的一条日记**。返回 `""` ⇒ 这条不写。

    ⚠ 剥掉模型偶尔爱加的整段引号（`「…」` / `"…"`），否则列表里每条都带一对引号。
    ⚠⚠ **出现「祁煜」字样 ⇒ 说明模型没照做（他写日记不会管自己叫名字），但照样存**，
      只打一行日志。理由：**内容是真发生过的，丢了不可逆**；体裁她能在网页上改
      （日记本来就支持改，2026-09-30 还能改时间）。
      ⇒ 这一点上刻意跟「切不出 `DIARY:` 就丢弃」不同：那是**没有内容**，这是**体裁跑偏**。
    """
    t = (raw_text or "").strip()
    if len(t) >= 2 and t[0] in "「『\"'“”" and t[-1] in "」』\"'“”":
        t = t[1:-1].strip()
    if t in _DIARY_EMPTY:
        return ""
    if "祁煜" in t:
        print("[📔] 日记段里出现了「祁煜」⇒ 模型没写成第一人称（内容照存，她可在网页上改）")
    return t


def _diary_path(user_id: str) -> str:
    return os.path.join(MEMORY_DIR, f"{user_id}_diary.json")


def _diary_ok(e) -> bool:
    """一条日记是不是有效（`id` / `text` 齐不齐、正文非空）。"""
    return (isinstance(e, dict) and str(e.get("id") or "").strip()
            and isinstance(e.get("text"), str) and e["text"].strip())


def _diary_ts(e) -> float:
    try:
        return float(e.get("ts") or 0)
    except Exception:
        return 0.0


def _diary_id(ts: float) -> str:
    """毫秒时间戳当 id（够唯一；真撞了在 `add_diary()` 里挂个尾巴）。"""
    return str(int(float(ts) * 1000))


def _hm(ts) -> str:
    """epoch 秒 → `"HH:MM"`（按 `AUTO_GREET_TZ_OFFSET` 换算，跟 `_day_key` 同一套口径）。"""
    return time.strftime("%H:%M", time.localtime(float(ts) + AUTO_GREET_TZ_OFFSET * 3600))


def diary_ts_from(day: str, hm: str):
    """
    🌐 网页端专用：`"YYYY-MM-DD"` + `"HH:MM"` → epoch 秒。**解析不了 ⇒ `None`**。

    ⭐ 为什么放引擎里、不让页面自己 `strptime` + `mktime`：
       这函数必须是 `_day_key()` / `_hm()` 的**逆运算**，而那两个都按
       `AUTO_GREET_TZ_OFFSET` 换算过 ⇒ 逆过来就得**把偏移减掉**。
       页面自己写 `mktime()` 在这里恰好等价（现在偏移是 0），但哪天偏移一改，
       页面存进去的时间就会整体平移几个小时，而且**改的页面和读的页面一起平移**
       ⇒ 看上去「没问题」，只在她跟 QQ 端对时间的时候才暴露。
       ⇒ 时区口径只留一份，跟 `group_diary()` 的日期文案同一个道理。

    ⚠ 容错到「秒」：原生 `type=time` 默认给 `HH:MM`（`step=60`），
      但给她留余量，`HH:MM:SS` 也收（将来若有人手改表单不至于静默失败）。
    ⚠ **不抛异常**：脏输入返回 `None`，由调用方决定「这次不改时间」。
    """
    d = str(day or "").strip()
    t = str(hm or "").strip()
    if not d or not t:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return time.mktime(time.strptime("%s %s" % (d, t), fmt)) \
                - AUTO_GREET_TZ_OFFSET * 3600
        except Exception:
            continue
    return None


def load_diary(user_id: str):
    """
    读一个人的日记。**纯读**：没有 / 读坏 ⇒ 返回空结构，**绝不创建文件、绝不写**。

    ⚠ 这里把「读不出来」收敛成空列表，是因为**界面表现**本来就该一样
      （「他还没写过日记」和「日记文件坏了」都只能显示引导文案）。
    ⚠ 但**写盘**的时候不能这么宽松（见 `_diary_edit()`）：那时候
      「文件读坏了」和「文件不存在」是两件完全不同的事 —— 前者绝不能覆盖。
    """
    path = _diary_path(user_id)
    if not os.path.isfile(path):
        return {"entries": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[📔] 日记读失败（{os.path.basename(path)}）：{e}")
        return {"entries": []}
    if not isinstance(data, dict):
        return {"entries": []}
    return {"entries": [e for e in (data.get("entries") or []) if _diary_ok(e)]}


def _diary_trim(entries):
    """
    总量上限。⚠ **丢最早的时候绝不丢她自己写的** ——
    他写的可以按时间滚（跟 `long_term_summary` 一个道理），
    她自己手写一条是**有意为之**，被机器挤掉就是这个功能的失败。
    做法：从最早的 `he` 开始丢；万一全是 `her`，再退化成按时间丢。
    """
    if len(entries) <= DIARY_MAX_ITEMS:
        return
    order = sorted(range(len(entries)), key=lambda i: _diary_ts(entries[i]))
    drop = len(entries) - DIARY_MAX_ITEMS
    killed = set()
    for i in order:
        if drop <= 0:
            break
        if entries[i].get("src") == "he":
            killed.add(i)
            drop -= 1
    for i in order:
        if drop <= 0:
            break
        if i not in killed:
            killed.add(i)
            drop -= 1
    if killed:
        entries[:] = [e for i, e in enumerate(entries) if i not in killed]


def _diary_edit(user_id: str, fn) -> bool:
    """
    「读 → 改 → 原子落盘」的壳。`fn(entries)` 返回 True 才写。

    ⚠⚠ **文件存在但读坏了 ⇒ 直接放弃，绝不 save**：拿一个空列表去存 = 把她整本日记清空。
      宁可这次改不成。（跟 `_facts_edit()` 同一个道理；差别只是日记**允许首次创建**。）
    """
    path = _diary_path(user_id)
    data = {"entries": []}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[📔] 日记读失败，这次不改（{os.path.basename(path)}）：{e}")
            return False
        if not isinstance(data, dict):
            print(f"[📔] 日记格式不对，这次不改（{os.path.basename(path)}）")
            return False
        raw = data.get("entries")
        # ⚠⚠ `entries` **不是列表 ⇒ 一律当「坏文件」处理，放弃**。
        #    别小看这个分支：`{"entries": {"0": {...}}}`（对象而不是数组）时，
        #    直接 `(raw or [])` 会去**遍历它的 key**，key 是字符串 ⇒ `_diary_ok()` 全否
        #    ⇒ 过滤完变空列表 ⇒ 下面照常落盘 ⇒ **整本日记被静默清空**。
        #    同理 `"entries": "abc"` 会遍历字符。所以这里必须**先看类型再看内容**。
        if raw is None:
            raw = []
        elif not isinstance(raw, list):
            print(f"[📔] 日记 entries 不是列表，这次不改（{os.path.basename(path)}）")
            return False
    else:
        raw = []
    entries = [e for e in raw if _diary_ok(e)]
    if not fn(entries):
        return False
    try:
        os.makedirs(MEMORY_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"entries": entries,
                       "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[📔] 日记写失败（{os.path.basename(path)}）：{e}")
        return False
    return True


def add_diary(user_id: str, text: str, src: str = "he", ts=None,
              day: str = "", legacy: bool = False, mood: str = "") -> bool:
    """
    追加一条日记。返回是否写入。**bot 侧**走这个（`generate_summary()` 末尾）。

    💗 `mood`（2026-10-01）：写这条时他的心情**标签**（如「闷气」）。空 = 不记。
       ⚠ 只存**标签、不存强度** —— 强度是瞬时的，隔天就衰减了，存下来是假数据。
       ⚠ 老日记没有这个字段 ⇒ 读侧当「没记」，零迁移（`group_diary` 原样带出）。
       ⚠ 她手写的（`src="her"`）不传 ⇒ 没有这一项（她写的时候不需要他的心情标签）。

    ⚠ `DIARY_ENABLE=False` ⇒ 直接返回 False（要停就改 config 常量，**别去删调用点** ——
      那样开关就只剩半个，跟 AUTO_GREET 那批同一个口径）。
    ⚠ 长度只卡 `DIARY_HARD_LEN`（疯话闸），**不卡** `DIARY_MAX_LEN`（那是我给她手写定的额度）：
      摘要正常 ≤200 字，模型偶尔跑偏也不该**静默少一条**。
    ⚠ 调用方**仍然要包 try**（见上面规矩 3）：这里尽量不抛，但外层那个 except
      一旦接走异常就会重排 `pending_summary` ⇒ 重复摘要。
    """
    if not DIARY_ENABLE:
        return False
    text = (text or "").strip()
    if not text or len(text) > DIARY_HARD_LEN:
        return False
    ts = float(ts if ts is not None else time.time())
    ent = {"id": _diary_id(ts), "ts": ts,
           "src": src if src in ("he", "her") else "he", "text": text}
    if day:
        ent["day"] = str(day)
    if legacy:
        ent["legacy"] = True
    # 💗 心情标签：空 / 不是合法标签 ⇒ **不写这个字段**（老日记长什么样，新日记还是什么样）
    #    ⚠ 宁可这条没有心情，也不往里塞脏数据（跟 key_facts 那个「先看类型再看内容」同理）
    if mood and str(mood).strip() in _MOOD_TAGS:
        ent["mood"] = str(mood).strip()

    def _do(entries):
        while any(e["id"] == ent["id"] for e in entries):     # 同毫秒撞了 ⇒ 换个 id
            ent["id"] += "x"
        entries.append(ent)
        _diary_trim(entries)
        return True

    return _diary_edit(user_id, _do)


def add_diary_by_her(user_id: str, text: str) -> bool:
    """
    🌐 她**自己在网页上写一条**。⚠ 卡 `DIARY_MAX_LEN`（500）—— 她手写有额度，模型没有。

    ⚠ 这里**不查** `DIARY_ENABLE`：那个开关管的是「他会不会自动往日记里写」，
      不该顺手把她手写的入口也关掉（否则她只会在页面上看到「存不进去」而不知道为啥）。
    """
    text = (text or "").strip()
    if not text or len(text) > DIARY_MAX_LEN:
        return False
    return add_diary(user_id, text, src="her", ts=time.time())


def edit_diary_by_her(user_id: str, eid: str, text: str, ts=None) -> bool:
    """
    ✏️ 改一条的**正文**（顺带可改**时间**）。`src` 永远不动。

    `ts=None`（默认）⇒ **只动正文**，跟以前一模一样（向后兼容）。
    `ts=float` ⇒ 正文与时间**一次原子写**完成 —— 不做成两个函数，是为了不出现
    「正文改好了、时间没改」这种半截状态（两次写盘中间任何一次失败都会留下它）。

    ⚠ 她**能改他写的那几条**（2026-09-30 她选的「都能改都能删」），
      改完 `src` **仍保持 `he`**：那依然是他当时总结的那段，她只是顺手修个字。
      （跟画像那边「改一条自动项 ⇒ 撤下并转挂 `manual`」**不一样** ——
        那边涉及好感度计分，改过就不算他观察出来的了；这边没有分数，不用搞那套。）
    ⚠ **改时间不改 `src`** 也是同一条道理：改的是「发生在这天几点」，不是换人写。

    ⚠⭐ **`id` 绝不跟着时间走** —— `id` 是网页的主键（`/diary/e?id=…`），
      跟着 `ts` 一起变的话，保存后那次重定向**自己就找不着这条**了（表现是「跳回列表」）。
    ⚠⭐ **`ts` 真变了就必须 `day.pop()`** —— 分组时 `day` **优先于** `ts`
      （见 `group_diary()`）。老数据导入的那批每条都写死了 `day`，
      只改 `ts` 不清它 ⇒ **时间变了、条目却赖在原来那天不走**。
      清掉之后一律按新 `ts` 推 ⇒ 天之间的搬家、天内的先后，全都自动跟上。
    ⚠ 时间比较用**分钟精度**（`_day_key` + `_hm`）：原生日期控件本来就只有分钟，
      拿浮点秒硬比会把「同一分钟」判成改过 ⇒ 只修个错字也会顺手重写 `ts`。
    """
    eid = str(eid or "").strip()
    text = (text or "").strip()
    if not eid or not text or len(text) > DIARY_MAX_LEN:
        return False
    if ts is not None:
        try:
            ts = float(ts)
        except Exception:
            ts = None

    def _do(entries):
        for e in entries:
            if e.get("id") != eid:
                continue
            old_ts = _diary_ts(e)
            time_changed = False
            if ts is not None:
                time_changed = (_day_key(ts) != _day_key(old_ts)
                                or _hm(ts) != _hm(old_ts))
            text_changed = (e.get("text") != text)
            if not text_changed and not time_changed:
                return False          # 原样保存不算改动（别骗她「改好了」）
            if time_changed:
                e["ts"] = ts
                e.pop("day", None)    # ⚠ 见上面：不清它就不会搬家
            e["text"] = text
            return True
        return False

    return _diary_edit(user_id, _do)


def rewrite_diary_text_by_her(user_id: str, eid: str, text: str) -> bool:
    """
    🔁 **一次性改写脚本专用**（`tools/rewrite_diary.py`）：把一条**旧的第三人称摘要**
       改写成**第一人称日记**。

    ⚠ 为什么不复用 `edit_diary_by_her()` —— 差别有三条，而且每条都不能通融：
      ① **时间 / `day` 一律不动**：改写的是**体裁**，不是发生时间；
         走 `edit_diary_by_her` 就得算一遍时间比较，多一个会错的环节。
      ② 改完盖 **`rewritten: true`** ⇒ 脚本再跑一遍**自己跳过**（幂等）。
         ⚠ 不靠「正文里还有没有『祁煜』」这种猜的判据 —— 摘要未必每段都提名字。
      ③ **拒收 `src:"her"`**：她自己手写的那条**一个字都不许碰**（脚本再手滑也碰不到）。
    ⚠ 长度按 `DIARY_HARD_LEN`（4000）兜，**不按她手写的 500** —— 改写的产物是模型写的，
      跟 `add_diary()` 同一个口径（模型的额度由 prompt 控制，不用她的额度去卡它）；
      这里卡一个上限纯粹是防疯话。
    """
    eid = str(eid or "").strip()
    text = (text or "").strip()
    if not eid or not text or len(text) > DIARY_HARD_LEN:
        return False

    def _do(entries):
        for e in entries:
            if e.get("id") != eid:
                continue
            if e.get("src") == "her":
                return False              # ⚠ 她自己写的，谁都别动
            e["text"] = text
            e["rewritten"] = True
            return True
        return False

    return _diary_edit(user_id, _do)


def delete_diary_by_her(user_id: str, eid: str) -> bool:
    """
    🗑 删一条。

    ⭐ **不留抑制名单**（跟「他记住的事」一致、跟画像标签相反）：
      日记没有后台自动轨会把它「写回来」—— 下一次摘要总结的是**新的对话**、是新内容，
      留痕反而会把将来那条新日记误挡在门外。
    """
    eid = str(eid or "").strip()
    if not eid:
        return False

    def _do(entries):
        rest = [e for e in entries if e["id"] != eid]
        if len(rest) == len(entries):
            return False
        entries[:] = rest
        return True

    return _diary_edit(user_id, _do)


def group_diary(entries, today_key: str = ""):
    """
    按天分组，给页面用：**天与天倒序**（最新的一天在最上面）、**天内正序**（早上在前）。

    返回 `[(day_key, day_label, [(hm, entry), ...]), ...]`

    ⚠ 日期 label **只有这一份实现**（走现成的 `_rel_day_label`）—— 页面不许自己算，
      不然「今天 / 昨天」这种相对说法会在两处漂。
    ⚠ 分组用的「天」优先取条目里显式存的 `day`（老数据导入那种），否则用 `_day_key(ts)` 推。
    """
    today_key = today_key or _day_key(time.time())
    buckets = {}
    for e in entries:
        day = str(e.get("day") or "").strip() or _day_key(_diary_ts(e))
        buckets.setdefault(day, []).append(e)
    out = []
    for day in sorted(buckets, reverse=True):
        items = sorted(buckets[day], key=_diary_ts)
        out.append((day, _rel_day_label(day, today_key),
                    [(_hm(_diary_ts(e)), e) for e in items]))
    return out
