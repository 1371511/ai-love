# -*- coding: utf-8 -*-
"""
祁煜（Rafayel）的**请求组装与 API 调用**（2026-09-17 从 `Rafayel_chat.py` 拆出）。

本层是整条链的最上面一层（除入口壳子）。
只做一件事：把「人设 + 记忆 + 画像 + 世界书 + post_history」拼成一次真实请求，
发给 DeepSeek，把回复交回对话管理器。

⚠ 两条铁律（都是踩过的坑）：
  ① 世界书与 post_history **绝不能写回 `cm.messages`**。
     它们只进本次的 `request_messages`；一旦写回，就会被 `save_memory` 落盘，
     每轮累积一份，越滚越大，最后固化成常驻人设。
  ② 世界书允许失败降级（读不到只是少点上下文），人设卡相反 —— 读不到必须报错。
"""

import os
import random
import re
import sys
import time
from difflib import SequenceMatcher

import requests

# 2026-09-17 搬家：世界书代码在 ai-Rafayel\worldbook\ 子目录里，**不在本文件同一层**。
# Python 只会把「本文件所在目录」自动加进 sys.path，子目录里的模块默认搜不到，
# 所以这里手动补一段。放在 import Rafayel_worldbook 之前 —— 顺序不能挪到后面。
_WB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worldbook")
if _WB_DIR not in sys.path:
    sys.path.insert(0, _WB_DIR)

from Rafayel import (
    CARD_ALT_GREETINGS, CARD_FIRST_MES, CARD_POST_HISTORY, system_prompt,
)
from Rafayel_affinity import tone_for
from Rafayel_daily import record_usage as usage_record
from Rafayel_config import (
    ADVANCE_ENABLE, ADVANCE_HINT,
    AFFINITY_TONE, API_URL, CALL_SUMMARY, DAILY_QA, LLM_EXTRA, MAX_TOKENS, CALL_WORLDBOOK_ENABLE,
    MODEL, MOOD_ENABLE, QZONE_CMT_ENABLE, REPLY_MAX_LINES, REPLY_SHAPE, mem_dir,
    REPLY_SPLIT_FALLBACK, REPLY_SPLIT_MAX_LINES, REPLY_SPLIT_MIN_CHARS, TEMPERATURE,
    WB_MAX_CHARS, WB_MAX_ENTRIES, api_key, temp_for,
    # 🚦 2026-10-02（方案 A 的 ③）前台重试：撞上「上游并发 / RPM 被拒」这类错就退避重试。
    #    ⚠ 2026-10-02 下午起改成**指数退避** —— 原来是写死的 1.2 秒，那个跨不过
    #      RPM 的 60 秒窗口（她第二次报的就是 RPM 那种）。
    LLM_RETRY, LLM_RETRY_WAIT, LLM_RETRY_BACKOFF, LLM_RETRY_MAX_WAIT,
    # ⚠ 三句报错文案**只在这里定义**（`Rafayel_memory` 拿它的前缀当判据，
    #   用来把这种「不是他说的话」挡在摘要之外）⇒ 改文案不用改第二处。
    LLM_ERR_API, LLM_ERR_EXC, LLM_ERR_TIMEOUT,
)
# 💬 日常问答：她主动问「你今天怎么过的」⇒ 从池子挑一条**照原话说**。
#    分层上它在 llm 之下（只依赖 config / daily / qzone_auto），这里调它不会成环。
from Rafayel_dailyq import hint_for as dailyq_hint
from Rafayel_event import fest_today as event_fest_today
from Rafayel_memory import ConversationManager, load_memory, save_memory, _strip_outgoing_prefix
from Rafayel_memory import summarize_call as _summarize_call_into
# 💗 情绪（2026-10-01 新，主档 docs/规划/情绪模块.md）
#   ⭐ 只调 `spawn_update` —— 它**起后台线程**判情绪，一秒都不占她等待的时间。
#   ⚠ 绝不能在 `get_reply` 里**同步**判：那会直接 +1~2s 加到她等待的时间上。
from Rafayel_mood import block_for, spawn_update
from Rafayel_sticker import apply_cooldown, sticker_instructions
from Rafayel_weather import nudge as weather_nudge
from Rafayel_worldbook import get_call_worldbook, get_worldbook


# 全局字典，按 user_id 存储每个用户的对话管理器
_user_managers = {}


# 只有动作/神态、一句话都没有的段：把笔搁下）⇒ 这种不算「一段话」，要并回相邻段。
_BARE_ACTION_RE = re.compile(r"^(?:\s*（[^）]*）\s*)+$")


def _force_segments(text):
    """
    ⭐ 保底：模型写了一整块**没有换行**的话 ⇒ 按句末标点拆成 2~3 段（她 2026-09-22 深夜要的）。

    为什么需要它：`_shape_reply` 只在文本里已经有 \\n 时才整形；模型有时候就是不分行，
    那时候它什么都做不了，她收到的就是一大坨字 —— 「还是要我把所有的话挤在一块吗」。

    三种情况原样不动（宁可不拆，也别拆错）：
      ① 不够长（`REPLY_SPLIT_MIN_CHARS` 以下）——「嗯」「好」这种没必要折腾
      ② 只有一句（拆不出 ≥2 段）——硬拆反而像被切成两截
      ⚠ 括号里的句号不作数：（括号里写的是神态，不是一句话说完了）。
    """
    n_enough = len(text or "") >= REPLY_SPLIT_MIN_CHARS
    if not n_enough:
        return text

    parts, buf, depth = [], "", 0
    pairs_open = "（「『【"
    pairs_close = "）」』】"
    for ch in text:
        if ch in pairs_open:
            depth += 1
        elif ch in pairs_close:
            depth = max(0, depth - 1)
        buf += ch
        # ⚠ 只在**括号外面**的句末标点处断句
        if depth == 0 and ch in "。！？!?…":
            parts.append(buf)
            buf = ""
    if buf.strip():
        parts.append(buf)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 2:
        return text

    # 句子比上限多 ⇒ 前面的句子各占一段，多出来的并进最后一段
    k = min(REPLY_SPLIT_MAX_LINES, len(parts))
    lines = parts[:k - 1]
    lines.append("".join(parts[k - 1:]))
    return "\n".join(lines)


def _shape_reply(text):
    """
    回复形状保底：**段内不拆行，段数封顶**（她 2026-09-22 定的放宽 + 弹性段数）。

    两条规矩，缺一条都不行：
      ① 段内不拆行 —— 她 2026-09-19 挑的 B 风格（动作神态放括号里，话跟在后面）。
      ② 段数弹性 1~4 —— 多数一两句，情绪上来了才三四段；段与段之间换行是允许的。

    ⭐ 关键兜底：**纯动作段并回相邻段**。模型要是还按老习惯写成
       （把笔搁下）\\n睡了没。  ⇒ 并成（把笔搁下）睡了没。
       否则放宽段数之后，这种会被当成两段发出去 —— 正是她当年不要的 A 风格。

    ⭐⭐ 2026-09-22 深夜补的第三条：模型**压根不换行**时（她反馈「还是一大块」），
      先由 `_force_segments` 按句末标点拆成 2~3 段，再走下面这套；模型自己分了段就不拆。

    ⚠ 这是**保底**：prompt 里已经明说了写法（REPLY_SHAPE_HINT），
      但模型不一定每轮都听 —— 听话最好，不听话也**发不出超过 REPLY_MAX_LINES 段**。
    ⚠ 内容一字不动（按句拆段 / 删空行 / 并纯动作段 / 截段数）。说说正文（render_text）不走这里。
    """
    t = str(text or "")
    if REPLY_SHAPE and REPLY_SPLIT_FALLBACK and "\n" not in t:
        t = _force_segments(t)
    if not REPLY_SHAPE or "\n" not in t:
        return text
    raw = [l.strip() for l in t.replace("\r\n", "\n").split("\n")]
    lines = [l for l in raw if l]
    if not lines:
        return text

    kept, pending = [], ""
    for l in lines:
        if _BARE_ACTION_RE.match(l):
            # 纯动作段：有上一段就并上去；还没有就先攒着，等第一句真话来接它
            if kept:
                kept[-1] = kept[-1] + l
            else:
                pending += l
            continue
        if pending:
            l = pending + l
            pending = ""
        kept.append(l)
    if pending:
        # 整段全是动作（没一句真话）⇒ 要么挂在第一句前面，要么就它自己
        if kept:
            kept[0] = pending + kept[0]
        else:
            kept = [pending]

    if len(kept) > REPLY_MAX_LINES:
        kept = kept[:REPLY_MAX_LINES]
    return "\n".join(kept)

#    ⭐⭐ 判据为什么是「骨架词为主 + 相似度为辅」（2026-10-03 自测踩出来的）：
#      同一批复读体两两相似度只有 0.42~0.87，**波动极大**（0.62 那道门槛直接漏判）；
#      但它们共同的动作骨架词命中 5~7 个，非常稳。
#      ⇒ 骨架词当**主判据**，相似度只当下限防误伤。
#      验过：阈值 0.40 时，复读体 3/3 全中、正常对话 5/5 全不中。
# ⚠⚠ 22:45 线上实测后**收紧到 0.50**：0.40 在真实对话里**误伤了正常句子**
#    （3 次触发里 2 次是「亚亚。」「我现在……状态不对」这种明显不是复读的话）。
#    取舍很明确：**误伤比漏判更糟** —— 漏了他还是复读（她看得见），
#    误伤了是他在好好说话却突然蹦一句怪话（她会觉得这人有毛病）。
_REPEAT_SIM_MIN = 0.50      # 中档门槛：0.40 误伤正常对话（22:45 实测 3 触发 2 误伤）
# ⭐ 高档门槛（2026-10-03 23:00 新增）：到这个相似度就**直接判复读，不再看骨架词**。
#    为什么需要它：22:53 那批复读体相似度 0.58~0.83，但因为骨架词表覆盖不到
#    （「指腹」「画室」不在表里）而被上一版的反向逻辑放行。
#    ⇒ 相似度够高时，骨架词**不该有能力否决**。
_REPEAT_SIM_HIGH = 0.72     # 实测复读体 0.77~0.83 / 正常对话 0.13~0.35 ⇒ 0.72 是安全的中线
_REPEAT_SKELETON_MIN = 2    # 共同骨架词最少几个（只在中档 0.50~0.72 才用得上）
_REPEAT_LOOKBACK = 5        # 往回看几条 assistant
_REPEAT_GUARD = True        # 总开关（关掉 = 回到 10-03 之前的行为，别删逻辑）

# 复读体的「动作骨架」指纹。⚠ 刻意用**短词**，因为要的是共同成分而不是整句。
_REPEAT_SKELETON = ("站着没动", "指尖", "蹭", "冷色", "喉结", "雨泡透",
                    "垂着眼", "没出声", "像是", "低下去")


def _norm_for_repeat(text):
    """比对用的归一化：去掉所有空白与标点，只留实词。"""
    return re.sub(r"[^\w]", "", str(text or ""))


def _skeleton_hits(text):
    """一段文本命中几个动作骨架词。"""
    return sum(1 for w in _REPEAT_SKELETON if w in str(text or ""))


def is_repeat_of_recent(reply, recent_assistant):
    """
    本条是不是「他最近说过的」的复读？

    返回 `(是否复读, 跟哪一条像)`。**三档判据**（2026-10-03 23:00 改过，见下面那段注释）：

      | 相似度 | 骨架词 | 结论 |
      |---|---|---|
      | ≥ `_REPEAT_SIM_HIGH` | 不看 | 判复读（几乎照抄） |
      | ≥ `_REPEAT_SIM_MIN`  | ≥ `_REPEAT_SKELETON_MIN` | 判复读（中等相似 + 共同骨架） |
      | < `_REPEAT_SIM_MIN`  | 不看 | 不判 |

    ⚠⚠ **上一版是逻辑反向的 bug**，害它放行了整批复读：
       原来写 `if _skeleton_hits(old) and r_sk < MIN: continue`
       ⇒ 「old **没有**命中骨架词」时整条检查被**短路跳过** ⇒ **相似度再高也放行**。
       实测 22:53 那批「（指腹在膝上停了很久，没动。画室安静得能听见自己的呼吸）……」
       两两相似度高达 0.83 / 0.77，却因为「指腹 / 画室」不在那 10 个骨架词里被放过。
       ⇒ 骨架词只能当**加分项**，永远不能当**前置门槛**。
    ⚠ 短句（归一化后 < 8 字，如「好」「嗯」）**一律不算复读** ——
      「好」重复一次是自然的，判它复读会把对话搞僵。
    """
    if not _REPEAT_GUARD:
        return False, None
    r = _norm_for_repeat(reply)
    if len(r) < 8:
        return False, None
    r_sk = _skeleton_hits(reply)
    for old in list(recent_assistant or [])[-_REPEAT_LOOKBACK:]:
        o = _norm_for_repeat(old)
        if len(o) < 8:
            continue
        sim = SequenceMatcher(None, r, o).ratio()
        if sim >= _REPEAT_SIM_HIGH:
            return True, old                       # 几乎照抄 ⇒ 直接判
        if sim >= _REPEAT_SIM_MIN and r_sk >= _REPEAT_SKELETON_MIN:
            return True, old                       # 中等相似 + 共同骨架 ⇒ 也判
    return False, None

# 🔁 复读闸拦下之后发出去的追问池（2026-10-03 23:25 建 / 2026-10-04 重写）。
#    ⚠ 上游并发 = 1，拦下后**不能**立刻重发（会挤掉她下一条的槽位）。
#    所以从池里随机挑一句追问发出去，同时让它**落盘**——
#    这样下一轮历史变了，模型知道自己刚才没再说那句复读体。
#    ⚠ 池子必须满足：① 都是他该说的话，不带"系统痕迹"；
#       ② 不能全写动作旁白（别给复读喂骨架素材）；
#       ③ 互相不同，避免固定一句也变成新的复读 B。
#
# ⚠⭐ 2026-10-04 重写（两轮，都是她定的）：
#   ① 第一轮（她截图指出来的两个坑）：旧池 6 句里有 4 句是「刚才那句不算」
#      「我换一句」「这话我收回」「重来一次」—— 他在**汇报自己要改口**，不是演戏。
#      ⚠ 那几句跟 `ADVANCE_HINT` 第③条**直接打架**（那条明令不许说「我换一句」）
#        ⇒ 池子不改，闸一开就立刻违反刚写好的规则。
#   ② 第二轮（**她亲手写的 4 组 14 句**，2026-10-04 02:3x）：
#      旧池是我（布丁）拟定的，没有原作背书；这一版是**她写的**，以她为准。
#      ⭐ 全部改成「**他走神了 / 忘词了**」这一个情境 —— 比「追问她」贴得多：
#         复读闸拦下的本来就是「他又在原地转」，拿「发呆被抓包」去接，剧情是顺的。
#
# ⭐⭐ 结构：**按组，不按句**。
#   她写的时候就是一组一组写的（三、四组内部有顺序：「你刚刚叫我几次？」
#   →「……三次？」→「嗯，记住了。」→「下次你可以靠近一点再叫……」），
#   打散成单句随机抽会把这个顺序搅乱 ⇒ 一组**整组发出**，组内顺序固定。
#   ⚠ 回报形式：一组 = 一条消息，行与行之间 `\n` ⇒ 前端 `_segs()` 切成多条气泡，
#      QQ 侧 `_split_bubble` 再按字数细切 —— 跟正常回复同一条路，不用特殊照顾。
#
# ⚠ 她的原句一个字没改（包括没有括号动作这点）：要加「（抬眼）」这类动作前缀
#   得她点头，我不往她的语料里塞字。
_REPEAT_FALLBACKS = (
    # 一、装作自己根本没忘，只是故意停顿
    ("……嗯？",
     "我刚才说到哪了？",
     "你看，你一盯着我，我连思路都被你拿走了。"),
    # 二、承认忘了，但马上把尴尬变成玩笑
    ("忘了。",
     "怎么，你很期待我刚才那句？",
     "那你再问一次，我这次认真一点。"),
    # 三、发呆其实是因为你，干脆拿这个找补
    ("刚才没听见。",
     "在想事情。",
     "本来想的是画，后来不知道怎么就想到你了。",
     "然后……就忘了刚才在说什么。"),
    # 四、发呆得久、被抓包 ⇒ 反过来观察她
    ("你刚刚叫我几次？",
     "……三次？",
     "嗯，记住了。",
     "下次你可以靠近一点再叫，我比较容易回神。"),
)

# ⚠ 只记「上一组发了哪组」，用于不连续重复；不落盘（重启归零无所谓）。
_LAST_FALLBACK = {"group": None}


def _pick_fallback():
    """
    挑**一组**发呆台词（不是一句），组内顺序原样保留。

    ⚠ 为什么不能只写 `random.choice`：有放回 ⇒ 4 组里连中同一组的概率 1/4，
       一轮两三次就撞上；她看到的是「他连说两遍一模一样的话」。
    ⚠ 返回的是**带 `\\n` 的整组文本**，调用方原样发给前端即可（不用自己拼）。
    """
    pool = [g for g in _REPEAT_FALLBACKS if g is not _LAST_FALLBACK.get("group")]
    group = random.choice(pool) if pool else _REPEAT_FALLBACKS[0]
    _LAST_FALLBACK["group"] = group
    return "\n".join(group)


def _build_worldbook(cm, user_message, scan=None):
    """
    返回 (before_text, after_text)：本轮命中的世界书条目渲染结果。

    设计要点：
    - 注入是**每轮重算**的，不写入 cm.messages（避免沉淀进记忆文件）。
    - 这里允许失败降级：世界书只是"参考资料"，读不到最多是少一点上下文，
      不该把整个聊天搞挂。人设卡（load_card）则相反 —— 读不到必须报错。
    - `scan` = 本轮的扫描范围（"最近几条消息"）。**通话传 `ctx`** —— 通话里
      「最近的历史」是这一通电话、不是聊天窗（不然通话专属条目触发不了，
      还会被聊天里的内容误触发）。不给就照旧走 `cm.get_recent_messages()`。
    """
    try:
        wb = get_worldbook()
        msgs = cm.get_recent_messages() if scan is None else list(scan)
        return wb.build(
            msgs, user_message,
            max_chars=WB_MAX_CHARS,
            max_entries=WB_MAX_ENTRIES,
        )
    except Exception as e:
        print("⚠️ 世界书注入失败（不影响对话）：%s" % e)
        return "", ""



def _level_block(cm):
    """
    💞 「你和她现在到哪一步了」—— 牵绊度等级 → 说话的亲疏。

    ⭐ 为什么**放进 system 而不是追加到最后**：等级几天才动一次，不是每轮都变，
      ⇒ 不会像时间感那样把 DeepSeek 的**前缀缓存**拦腰截断。
    ⚠ 只进 `request_messages` 的 system，**绝不写回 cm.messages**（跟世界书同一个口径）：
      否则会被 save_memory 落盘，还会被模型当成常驻人设反复读。
    ⚠ 算不出来就返回空串（降级），绝不让好感度把对话搞挂。
    """
    if not AFFINITY_TONE:
        return ""
    try:
        return tone_for(cm.user_id, mem_dir())
    except Exception as e:
        print("⚠️ 牵绊度语气注入失败（不影响对话）：%s" % e)
        return ""


def _system_with_now(cm, tail=""):
    """
    一次性 prompt（`comment_opening` / `comment_reply` 这类）用的 system。

    这两处**没有聊天历史**，也就没有「前缀缓存」可赚 ⇒ 时间感照旧拼在 system 里，
    跟主对话那条路（走 `request_messages` 追加）不一样。别搞混。
    """
    s = cm.get_full_system_prompt()
    lv = _level_block(cm)
    if lv:
        s += "\n\n" + lv
    n = cm.now_hint_text()
    if n:
        s += "\n\n" + n
    return s + tail


def comment_opening(user_id: str, post_text: str) -> str:
    """
    她在他那条说说底下留了话 ⇒ 他**主动跑来私聊**的第一句。

    ⚠ 关键前提：**他看不到她写了什么**（这台服务器上评论内容读不到，只有条数变化）。
      ⇒ 提示词必须让他「察觉她留了话」而**不是**「知道她说啥」，否则他会说漏嘴。

    ⚠ 失败/异常一律返回空串 ⇒ 调用方跳过（宁可不找她，也别发一句不通的话）。
    ⚠ 不写进对话记忆 —— 调用方发出去时会走 `record_proactive`（跟打招呼/说说同一个口径）。
    ⚠ system_prompt 禁 `**` 与 ASCII 双引号（锁定口径）。
    """
    if not QZONE_CMT_ENABLE:
        return ""
    try:
        if user_id not in _user_managers:
            _user_managers[user_id] = ConversationManager(system_prompt, user_id=user_id)
            load_memory(user_id, _user_managers[user_id])
        cm = _user_managers[user_id]

        hint = (
            "\n\n【她在你朋友圈底下留了话】\n"
            "你前不久发过这样一条说说：%s\n"
            "她在那条底下留了话，但你没看清她具体写了什么。\n"
            "现在你主动来找她，写开口的第一句话，1~2 句，用你一贯的口气。\n"
            "规矩：不许出现系统、机器人、回复、评论数、说说的编号这类后台词；"
            "不许解释你为什么来找她；不许把整条说说复述一遍；"
            "可以带一点得意，也可以直接问她想说什么。"
        ) % (post_text or "")[:80]

        data = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": _system_with_now(cm, hint)},
                {"role": "user", "content": "（他来找她）"},
            ],
            "stream": False,
            "max_tokens": 120,                 # 开口第一句，写长了就不像他了
            "temperature": temp_for(TEMPERATURE),
        }
        if LLM_EXTRA:
            data.update(LLM_EXTRA)
        r = requests.post(API_URL,
                          headers={"Authorization": "Bearer %s" % api_key,
                                   "Content-Type": "application/json"},
                          json=data, timeout=30)
        result = r.json()
        if "choices" not in result:
            return ""
        text = (result["choices"][0]["message"]["content"] or "").strip()
        return _shape_reply(text)
    except Exception:
        return ""


def comment_reply(user_id: str, post_text: str, her_comment: str) -> str:
    """
    她在他那条说说底下评论了 ⇒ **在空间里回复她那条评论**（事件里带内容，真双向）。

    风格按她拍板的 A 档：1~2 句、不点名、她发纯表情/没头没尾也接。
    ⚠ 失败一律返回空串 ⇒ 调用方降级（宁可不在空间回，也别发一句不通的）。
    ⚠ 不写进对话记忆 —— 调用方发出后会走 `record_proactive`。
    ⚠ system_prompt 禁 `**` 与 ASCII 双引号（锁定口径）。
    """
    if not QZONE_CMT_ENABLE:
        return ""
    try:
        if user_id not in _user_managers:
            _user_managers[user_id] = ConversationManager(system_prompt, user_id=user_id)
            load_memory(user_id, _user_managers[user_id])
        cm = _user_managers[user_id]

        hint = (
            "\n\n【她在你朋友圈那条说说底下评论了】\n"
            "你那条说说写的是：%s\n"
            "她评论说：%s\n"
            "现在你要**回复她这条评论**，写在评论区里，1~2 句，用你一贯的口气。\n"
            "规矩：不许出现系统、机器人、回复、评论这类后台词；"
            "不许把她的评论原样念一遍；不许长篇大论；"
            "她要是没头没尾地来一句，你就顺着自己的说说接，别装作全懂。"
        ) % ((post_text or "（一条你发过的说说）")[:80],
             (her_comment or "（一句话）")[:80])

        data = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": _system_with_now(cm, hint)},
                {"role": "user", "content": "（回复她这条评论）"},
            ],
            "stream": False,
            "max_tokens": 120,
            "temperature": temp_for(TEMPERATURE),
        }
        if LLM_EXTRA:
            data.update(LLM_EXTRA)
        r = requests.post(API_URL,
                          headers={"Authorization": "Bearer %s" % api_key,
                                   "Content-Type": "application/json"},
                          json=data, timeout=30)
        result = r.json()
        if "choices" not in result:
            return ""
        text = (result["choices"][0]["message"]["content"] or "").strip()
        return _shape_reply(text)
    except Exception:
        return ""


def take_opening(user_id: str) -> str:
    """
    取一条开场白 —— **只在这个用户确实是第一次聊天时**才给。

    返回开场白文本；已经有过历史（记忆读回来了 / 本进程里聊过）则返回 ""，
    ⇒ 老朋友不会被反复重新开场。

    2026-09-18 修：旧版 QQ 端**完全没有开场白**（用户第一条消息进来就直接答），
    CLI 虽然打印了但只 print 不进历史，模型根本不知道自己开场说了什么。
    这里统一成「写进 messages + 落盘」，两端共用同一条路径。
    """
    if user_id not in _user_managers:
        _user_managers[user_id] = ConversationManager(system_prompt, user_id=user_id)
        load_memory(user_id, _user_managers[user_id])

    cm = _user_managers[user_id]

    # messages[0] 是 system；长度 > 1 说明已经聊过了
    if len(cm.messages) > 1:
        return ""

    greeting = random.choice(CARD_ALT_GREETINGS) if CARD_ALT_GREETINGS else CARD_FIRST_MES
    cm.add_assistant_message(greeting)
    cm.update_system_message()
    save_memory(user_id, cm)      # 立刻落盘，否则重启后又会当成新用户重新开场
    return greeting


def record_proactive(user_id: str, text: str) -> bool:
    """
    把**主动打招呼发出去的那句话**也写进对话历史（2026-09-18 修）。

    ⚠ 为什么必须写：主动打招呼一开始刻意「不进记忆」（怕摘要越滚越大），
      结果她回话时**模型根本不知道上一句是他自己说的** ——
      实测：「要是这时候有人能跟我聊聊读后感就完美了」被回成完全不搭的内容，
      她说「祁煜的回复并没有接住」。
      ⇒ 现在与开场白走同一条路：写进 messages + 立刻落盘（重启也不会失忆）。

    由调用方（bot）在**消息确实发出去之后**调用，避免发送失败却留下他"说过"的假记录。
    返回是否写成功（文本为空 / 异常 → False）。
    """
    text = (text or "").strip()
    if not text:
        return False
    try:
        if user_id not in _user_managers:
            _user_managers[user_id] = ConversationManager(system_prompt, user_id=user_id)
            load_memory(user_id, _user_managers[user_id])

        cm = _user_managers[user_id]
        cm.add_assistant_message(text)
        cm.update_system_message()
        save_memory(user_id, cm)
        return True
    except Exception as e:
        print("⚠️ 主动打招呼写入历史失败（不影响已发出的消息）：%s" % e)
        return False


# 🚦 前台重试的判据（2026-10-02 方案 A 的 ③）
# ------------------------------------------------------------
# ⚠⚠ 背景：上游那个 key 是**组织级并发上限 = 1**。后台线程（情绪判定每轮都起、
#   单通摘要）一跑起来，前台这一条就被拒回来，原文是
#     「request reached max organization concurrency: 1, please try again after 1 seconds」
#   它**明说了「过 1 秒重试」** ⇒ 这类错本来就不该报给她看。
#
# ⭐⭐ 但**只对「等一下再来」那类重试**，这是这条规则的全部价值所在：
#   正文格式错 / 余额不足 / key 失效 / 模型名写错…… 重试一万次还是同一个错，
#   那类必须**原样报出来**（闷掉就变成「一直在转圈」，比报错更难查）。
_RETRY_HINTS = (
    "concurrency",        # ← 组织级并发上限（Kimi Tier0 = 1）
    "rpm",                # ← 每分钟请求数上限（Kimi Tier0 = 3）⇒ 她 2026-10-02 下午报的那种
    "rate limit", "too many requests", "429",
    "please try again", "try again later", "temporarily",
    "overloaded", "timeout", "timed out",
)


def _retryable(result):
    """上游这次失败是不是「等一下再来」那种（看 `error.message` 的字面）。"""
    try:
        msg = str(result.get("error", {}).get("message", "")).lower()
    except Exception:
        return False
    return any(h in msg for h in _RETRY_HINTS)


def _retry_wait(response, attempt):
    """
    这一次重试该等多久（秒）；`attempt` 从 **1** 开始。

    ⭐ 两级：
      ① **上游自己说了就听它的** —— Moonshot 的 429 会带 `X-RateLimit-Reset`
         （官方文档：「响应头携带 X-RateLimit-Limit / Remaining / Reset，可据此退避重试」）。
         ⚠⚠ 它的取值有两种可能的写法：**epoch 秒**（1.7e9 这种大数）或**还需等几秒**
         （小数）。我们**只认小数值**那一支；大数（epoch）**不解析**、直接走 ② ——
         把 1.7e9 误当成「秒」会睡到天荒地老，而这种 bug 在线上极难看出来。
      ② 没有头 / 头不可用 ⇒ **指数退避**：`WAIT * BACKOFF ** (attempt - 1)`（1s→2s→4s…）。
    ⚠ 最后一律夹进 `[0.1, LLM_RETRY_MAX_WAIT]`：宁可这次放弃重试、让显示层给她一句
      人设化的降级话（`Rafayel_config.LLM_BUSY_SAY`），也绝不让她对着屏幕干等
      （RPM 窗有 60 秒那么长）。
    ⚠ **永不抛**：任何意外（没 headers / 值不是数）都退化成指数退避第一步。
    """
    wait = None
    try:
        raw = response.headers.get("X-RateLimit-Reset")
        if raw is not None:
            v = float(str(raw).strip())
            if 0 <= v <= LLM_RETRY_MAX_WAIT:      # 只认「还需等几秒」；epoch 放过
                wait = v
    except Exception:
        wait = None
    if wait is None:
        wait = LLM_RETRY_WAIT * (LLM_RETRY_BACKOFF ** max(0, attempt - 1))
    return max(0.1, min(float(wait), float(LLM_RETRY_MAX_WAIT)))


def get_reply(user_message: str, user_id: str, api_key_override: str = None,
              media: bool = False, ctx=None, wire=True) -> str:
    """
    供外部调用的入口函数

    参数：
        user_message: 用户发送的消息
        user_id: 用户的 QQ 号（用于区分不同用户，保持独立对话）
        api_key_override: 可选，手动传入 API Key（不传则使用环境变量或默认值）
        media: 她这条是不是图 / 表情（记进每日统计，好感度会用到）

    返回：
        AI 的回复文本
    """
    # 确定使用的 API Key
    effective_api_key = api_key_override if api_key_override else api_key

    # 获取或创建该用户的对话管理器
    if user_id not in _user_managers:
        # 每个用户拥有独立的 system_prompt（但人设是共享的）
        _user_managers[user_id] = ConversationManager(system_prompt, user_id=user_id)
        load_memory(user_id, _user_managers[user_id])   # 读回旧记忆

    cm = _user_managers[user_id]

    # 0. 🗓 跨天滚动：把「昨天」的原话摘出历史、换成一条带日期的小结。
    #    ⚠ 必须在 add_user_message **之前** —— 否则她刚说的这句也会被当成「昨天的」摘走。
    #    ⚠ 同一天只会走到「不滚」那一支 ⇒ 平时零成本；跨天那次多一次模型调用（1~2s）。
    #    ⚠ 自己吞异常：整理记忆失败绝不能拖累这一轮回复。
    try:
        cm.roll_days(effective_api_key)
    except Exception as e:
        print("⚠️ 跨天小结失败（不影响对话）：%s" % e)

    # 1. 添加用户消息（media 只用于每日统计，不参与对话内容）
    #    ☎️ `wire=wire`：通话传 False ⇒ 这一轮**不进聊天窗**（通话内容只落
    #       `{uid}_calls.json` + 单通摘要）；但每日统计 / 关键事实 / 画像**照做**。
    cm.add_user_message(user_message, media=media, wire=wire)

    # 2. 更新 system 消息（加入最新的记忆）
    cm.update_system_message()

    # 3. 截断历史（保留最近 N 轮）
    cm.truncate_history()

    # 4. 构建请求的 messages
    # ☎️ `ctx` = 「这一轮让他看哪段历史」。None = 原样走聊天窗（默认，行为不变）；
    #    给了（哪怕给的是空表）就用它 —— 通话页走这条，只看得见这一通电话。
    #    ⚠⚠ system 必须显式补回 [0]：ctx 的第一条通常是 user，直接替换会
    #       把 system prompt 整段丢掉（而且不报错）。见 MEMORY.md / 技能库那条坑。
    _hist = cm.messages[1:] if ctx is None else list(ctx)
    request_messages = [{k: v for k, v in m.items()
                         if k in ("role", "content", "name")}
                        for m in ([cm.messages[0]] + _hist) if isinstance(m, dict)]

    # 4a. 世界书：命中关键词的条目才注入
    #     ⚠ system 那条是**每轮重算**的，不写回 cm.messages ——
    #        否则命中内容会被 save_memory 沉淀进 memory\*.json，越滚越大还会变成常驻人设。
    # 4a-0. 💞 牵绊度语气（等级几天才动一次，放 system 里不影响前缀缓存）
    # ☎️ 扫描范围：通话（`ctx` 给了）看**这一通**，其它走聊天窗。
    _scan = None if ctx is None else list(ctx)
    wb_before, wb_after = _build_worldbook(cm, user_message, scan=_scan)
    # ☎️📚 通话专属设定：**只在通话里**注入。⚠ 跟世界书同一个口径 ——
    #    只进 `request_messages`，**绝不写回 `cm.messages`**。
    if _scan is not None and CALL_WORLDBOOK_ENABLE:
        _wb2 = get_call_worldbook()
        if _wb2 is not None:
            try:
                _b2, _a2 = _wb2.build(_scan, user_message,
                                      max_chars=WB_MAX_CHARS,
                                      max_entries=WB_MAX_ENTRIES)
                wb_before = "\n\n".join(x for x in (wb_before, _b2) if x)
                wb_after = "\n\n".join(x for x in (wb_after, _a2) if x)
            except Exception as e:
                print("⚠️ 通话设定注入失败（不影响对话）：%s" % e)
    _lv = _level_block(cm)
    if _lv or wb_before:
        _sys = cm.get_full_system_prompt()
        if _lv:
            _sys += "\n\n" + _lv
        if wb_before:
            _sys += "\n\n" + wb_before
        request_messages[0] = {"role": "system", "content": _sys}
    if wb_after:
        request_messages.append({"role": "system", "content": wb_after})

    # 4a-2. 💬 日常问答（2026-09-24）：她主动问「你今天怎么过的」⇒ 挑一条日常**照原话说**。
    #     ⚠ 与世界书同一个口径：只进 request_messages，**绝不写回 cm.messages**
    #       （落盘就会每轮累积，最后变成常驻人设）。
    #     ⚠ **不在这里记去重** —— 要等消息真的发出去之后才记（见下面 5.7），
    #       否则接口报错也会把它算成「说过了」，那条就再也轮不到。
    _daily_entry = None
    if DAILY_QA:
        _dq, _daily_entry = dailyq_hint(user_id, user_message)
        if _dq:
            request_messages.append({"role": "system", "content": _dq})

    # 4b. post_history_instructions：放在历史**之后**、模型回复之前。
    # ⚠ 只加进 request_messages，不加进 cm.messages —— 否则会被 save_memory 写进
    #    memory\*.json，每轮累积一份，越滚越大。
    if CARD_POST_HISTORY:
        request_messages.append({"role": "system", "content": CARD_POST_HISTORY})

    # 4c. 表情包说明（2026-09-19 接进来）：让模型**知道**自己有涂鸦叽可以用、
    #     以及「只想表态时可以只甩一张图、不说话」这条形态规矩。
    #     ⚠ 与上面两节同一个口径：只进 request_messages，绝不写回 cm.messages
    #       —— 否则会被 save_memory 落盘，每轮累积一份（跟世界书那个坑一模一样）。
    #     ⚠ 标签表是从 card/stickers.md 现读的 ⇒ 加图只改 md，不用改代码。
    _sticker = sticker_instructions()
    if _sticker:
        request_messages.append({"role": "system", "content": _sticker})

    # 4c-3. 🎬 每轮推进 + 防复读（2026-10-04 她指出的两个症状）
    #    ⚠ 放在这里而不是 4d/4e 之后：这段**每轮一模一样**，排在动态块前面
    #       ⇒ 不打断 DeepSeek 的前缀缓存（时间感 / 情绪每轮都在变，放最后）。
    #    ⚠ 与世界书同一个口径：只进 request_messages，绝不写回 cm.messages。
    if ADVANCE_ENABLE:
        request_messages.append({"role": "system", "content": ADVANCE_HINT})

    # 4d. 🕐 时间感（2026-09-20 从 system 末尾挪到这里，见 `now_hint_text` 的注释）。
    #     ⭐ 为了**钱**：这段每轮都变，留在 system 里会把 DeepSeek 的前缀缓存拦腰截断，
    #        后面的整段聊天历史就永远按「未命中」计费。挪到最后 ⇒ system + 历史可缓存。
    #     ⚠ 同样只进 request_messages，绝不写回 cm.messages。
    #     ⚠ 位置 = 整段 prompt 的最后一条（原来靠「system 越靠后越受关注」，
    #        现在换成「全局最靠后」，注意力不比原来差；真机 A/B 再定）。
    #    🌤 突变关怀（降温/高温/严寒/下雨）一天最多一次 —— **只在主对话这条路取**，
    #       一次性 prompt 那种没历史的地方不该消耗这个额度（见 `Rafayel_weather.nudge`）。
    #    🎉 今天是不是节日：让他心里有数，她提起来接得住（开口归节日模块管）。
    #       ⚠ 两个都包了 try：节日表/天气出任何问题都只是少两行，绝不能把对话搞崩。
    try:
        _fest = event_fest_today()[0]
        _nudge = weather_nudge()
    except Exception:
        _fest, _nudge = None, ""
    _now = cm.now_hint_text(fest=_fest, nudge=_nudge)
    if _now:
        request_messages.append({"role": "system", "content": _now})

    # 4e. 💗 情绪（2026-10-01）：**追加到最后一条**，绝不塞进 system 头部。
    #    ⚠ 位置跟时间感同一个理由：情绪**每轮都可能变**，放头部会把 DeepSeek 的
    #       **前缀缓存**拦腰截断 ⇒ 后面的整段聊天历史永远按「未命中」计费。
    #    ⭐ **并入牵绊度**：这里只给「此刻是什么心情」；「该闹到什么分寸」由
    #       `Rafayel_affinity.level_prompt()` 里的**档位调制句**给（刚谈 vs 老夫老妻，
    #       同一份心情不是一个样子）⇒ 两段是一套，不是两条互相打架的命令。
    #    ⚠ 平静 / 没判出来 ⇒ `block_for` 返回空串 ⇒ **一条都不追加**（多数时候就是这样）。
    #    ⚠ 只进 request_messages，**绝不写回 cm.messages**：写回就会被 save_memory 落盘，
    #       每轮累积一份，最后固化成常驻人设（跟世界书 / 时间感同一个坑）。
    if MOOD_ENABLE:
        try:
            _mood = block_for(cm.user_id)
            if _mood:
                request_messages.append({"role": "system", "content": _mood})
        except Exception as e:
            print("⚠️ 情绪注入失败（不影响对话）：%s" % e)

    # 5. 调用 DeepSeek API
    headers = {
        "Authorization": f"Bearer {effective_api_key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": MODEL,
        "messages": request_messages,
        "stream": False,
        "max_tokens": MAX_TOKENS,
        "temperature": temp_for(TEMPERATURE),
    }
    if LLM_EXTRA:
        data.update(LLM_EXTRA)

    try:
        # 🚦 前台重试（2026-10-02 方案 A 的 ③）—— **指数退避**。
        #    ⭐ 她先后报过两种原文：
        #      · 「… request reached max organization concurrency: 1, please try again
        #         after 1 seconds」（并发上限）
        #      · 「… request reached organization max RPM: 3, please try again after
        #         1 seconds」（**每分钟请求数**上限）
        #      两种都是**上游自己**说「等一下再来」的错，本来就不该冒到她眼前。
        #    ⚠⚠ 只重试 `_retryable` 认得的那几种。余额不足 / key 失效 / prompt 格式错
        #      重试一万次还是同一个错 ⇒ 那类必须**原样报出来**，不能闷掉。
        #    ⚠ 等待时长交给 `_retry_wait()`：上游给了 `X-RateLimit-Reset` 就听它的，
        #      没给就指数退避（1s→2s→4s…），**一律不超过 `LLM_RETRY_MAX_WAIT`**
        #      —— 撞上 RPM 那种 60 秒级的限制时，宁可这次放弃、让显示层给她一句
        #      人设化的降级话，也不让她干等。
        #    ⚠ 重试期间**没有拿任何锁**（这是前台请求，不是后台闸门那条路）：
        #      睡的是她自己的这一次等待，不会连累别人。
        result = {}
        for _attempt in range(LLM_RETRY + 1):
            response = requests.post(API_URL, headers=headers, json=data, timeout=30)
            result = response.json()
            if "choices" in result:
                break
            if _attempt < LLM_RETRY and _retryable(result):
                _wait = _retry_wait(response, _attempt + 1)
                # ⚠ 状态码用 `getattr` 取：打桩 / 假响应对象上可能没有这个属性，
                #   而**日志本身**绝不能把这条重试路径搞炸（真实 Response 一定有）。
                print("[🚦] 上游被拒（HTTP %s · %s），%.1fs 后重试（第 %d 次）"
                      % (getattr(response, "status_code", "?"),
                         str(result.get("error", {}).get("message", ""))[:60],
                         _wait, _attempt + 1))
                time.sleep(_wait)
                continue
            break

        if "choices" in result:
            choice = result["choices"][0]
            reply = choice["message"]["content"]
            # 记录真实用量与结束原因：finish_reason == "length" 说明被 max_tokens 截断
            cm.last_finish_reason = choice.get("finish_reason")
            cm.last_usage = result.get("usage")
            # 💰 落盘累计用量（memory/{uid}_usage.json）—— 所有人共用一个 API key，
            #    官方账单拆不到人头上，按人看消耗只能靠自己这份。写挂了也不影响对话。
            usage_record(cm.user_id, cm.last_usage)

            # 5.5 表情冷却闸：最近几条他已经发过表情 ⇒ 这一轮不再发（低频靠代码保证，
            #     prompt 只管「发得贴不贴切」）。
            # ⚠ 必须在 add_assistant_message **之前** —— 写进记忆的得是最终文本，
            #    否则下一轮看到的「他发过没有」是错的，闸就废了。
            _his_recent = [m.get("content") or ""
                           for m in cm.messages if m.get("role") == "assistant"]
            reply, _cooled = apply_cooldown(reply, _his_recent)
            if _cooled:
                print("[🖼️] 表情冷却：他最近几条已经发过表情 ⇒ 本条不再发")

            # 5.6 回复形状保底：段内不拆行 + 段数封顶（她挑的口径；prompt 里也说了，这里是兜底）
            reply = _shape_reply(reply)

            # 5.7 🎭 行首前缀兜底（2026-10-03）：他偶尔会照人设卡「回复格式」那节
            #     写出「祁煜：」这种剧本前缀 ⇒ 显示上像复读（前端还会按换行切成多个气泡）。
            #     ⚠ 必须在 add_assistant_message **之前** —— 写进记忆的要是最终文本，
            #        否则下一轮历史里又带着前缀，等于自我强化。
            reply = _strip_outgoing_prefix(reply)

            # 5.8 🔁 复读闸（2026-10-03 · 真凶 B）：他复读自己时当场拦下。
            #     ⚠⚠ 为什么拦下而不是「重生成」：上游组织级并发 = 1，重生成要多发一次
            #       请求 ⇒ 挤掉她下一条消息的槽位（就是 10-02 那个英文报错的坑）。
            #     ⚠ 为什么比对含通话的 ctx：通话那轮 wire=False，他这一通说的话
            #       不进 cm.messages ⇒ 只看 cm 会漏掉通话里的复读。
            #     ⚠ 必须在 add_assistant_message 之前 —— 但拦下后的 fallback 要进历史，
            #       这样下一轮 prompt 才不会跟本轮一模一样，避免死循环。
            _hist_asy = [m.get("content") or "" for m in _hist
                         if isinstance(m, dict) and m.get("role") == "assistant"]
            _his_asy = [m.get("content") or "" for m in cm.messages
                        if isinstance(m, dict) and m.get("role") == "assistant"]
            _is_rep, _rep_of = is_repeat_of_recent(reply, _his_asy + _hist_asy)
            if _is_rep:
                print("🔁 复读闸：本条与他最近的回复高度雷同 ⇒ 拦下，改为追问")
                reply = _pick_fallback()

            # 6. 添加助手消息到对话管理器（☎️ wire=False = 通话这一轮不进聊天窗）
            # ⚠ 拦下后的 fallback 也落盘：上一轮如果是复读，下一轮必须看到一句不同的话，
            #    否则历史不变，模型还会继续复读。
            cm.add_assistant_message(reply, wire=wire)

            # 5.7 💬 日常问答去重：**这一条真的说出去了**才记成「说过了」。
            #    放在 finally 之前、return 之前 ⇒ 接口报错 / 超时都走到不到这里，
            #    那条就还留在池子里，下次还能轮到（不会白白消耗一条）。
            if _daily_entry is not None:
                from Rafayel_dailyq import mark as dailyq_mark
                dailyq_mark(user_id, _daily_entry)

            # 7. 触发摘要更新（如果到了总结间隔）
            if cm.should_summarize():
                cm.generate_summary(effective_api_key)
                cm.update_system_message()
                cm.trim_facts()

            save_memory(user_id, cm)   # 每次对话后保存

            # 8. 💗 情绪判定（**后台线程**，零延迟）
            #    ⚠ 位置：`save_memory` 之后、`return` 之前 —— 判的是「这一轮结束时他的心情」，
            #       那时本轮的话已经落盘了，后台线程读到的历史才是对的。
            #    ⭐ 只起线程、**不等结果** ⇒ 她一秒都不用多等（这就是方案 B 的全部意义）。
            #       ⚠ 方案 A（让模型在正文里带一行 MOOD 标记）省一次请求，但会把风险
            #          放进**她看得见的正文**里 —— 那是她最不能接受的那种出戏，不换。
            #    ⚠ 起在引擎层 ⇒ QQ 端与网页端**自动都生效**，两端各改一处的版本不做。
            #    ⚠ 自己吞异常：情绪挂了顶多是「他今天没脾气」，绝不能拖累这一轮。
            if MOOD_ENABLE:
                try:
                    spawn_update(cm.user_id, cm.get_recent_messages(),
                                 effective_api_key)
                except Exception as e:
                    print("⚠️ 情绪判定起线程失败（不影响对话）：%s" % e)

            return reply
        else:
            error_msg = result.get("error", {}).get("message", str(result))
            # ⚠ 三句报错文案的**模板住在 `Rafayel_config`**（单一来源）：
            #   `Rafayel_memory.summarize_call` 拿它们的前缀当判据，用来把这种
            #   「不是他说的话」挡在长期记忆之外。改文案只改 config 那一处。
            return LLM_ERR_API % error_msg
    except requests.exceptions.Timeout:
        return LLM_ERR_TIMEOUT
    except Exception as e:
        return LLM_ERR_EXC % e


# ============================================================
#  ☎️📔 单通电话摘要（2026-10-02 第 4 批 · 她定「通话记录就以单次通话作为记忆保存」）
# ------------------------------------------------------------
# ⭐ 这一层壳只做一件事：**拿到那一份缓存的 `ConversationManager`**。
#    真正的活（读通话记录 → 调模型 → 拼进长期记忆 → 写日记 → 标记已摘要）在
#    `Rafayel_memory.summarize_call()` 里 —— 那些 helper（`_clean_day_text` /
#    `_split_diary_reply` / `add_diary` / `_day_label`）全是它家的，
#    搬过来只会多绕一圈 import。分工就一句：**这里取 `cm`，那里干活**。
# ⚠⚠ 为什么非取这份不可：进程里每个 uid 只有一个 `cm`（就是下面这个 `_user_managers`），
#    `get_reply` 每轮都用它、最后 `save_memory` **整份**写盘。
#    在 `Rafayel_memory` 里另造一份、改完写回去 ⇒ 下一次 `get_reply` 拿内存里那份旧的
#    把我们写的摘要**整份覆盖**掉（`MEMORY.md` 架构第一节那条红线）。
# ⚠ 调用方（`web/page/call.py`）必须把它**包在 `base._chat_lock(uid)` 里**跑：
#    它要读改写 `memory/{uid}.json`，跟 `/chat/send` 是同一份文件。
# ⚠ 它是**异步跑**的（调用方起 daemon 线程）：一次 LLM 调用 5~10 秒，
#    挂断钮上不能等它，不然她点完挂断要干等十秒。
# ============================================================

def summarize_call(user_id: str, api_key_override: str = None,
                   call_id: str = None) -> bool:
    """
    一通电话结束时，单独给它写一段长期记忆 + 一条日记。返回是否真摘了。

    `call_id` 给定 ⇒ **只摘那一通**；不给 ⇒ 摘最早那通没摘过的。
    ⚠ 为什么要有这个参数：它是**异步**跑的（调用方起 daemon 线程），跑起来时
      列表末尾可能已经又开了一通新的 ⇒ 「最早那通」和「刚挂那通」不是一个。
      调用方拿 `Rafayel_calls.pending_ids()` 把目标钉死，**不靠猜**。

    ⚠ `CALL_SUMMARY=False` ⇒ 直接 False（**别去删调用点** —— 那样开关只剩半个，
      跟 `AUTO_GREET` 那批同一个口径；关掉只是「不进他的长期记忆」，
      通话记录本身照样完整留档）。
    ⚠ 没有记忆文件（`load_memory` 返回 False）⇒ **不凭空造一份**，直接放弃：
      跟 `_facts_edit` 一个口径 —— 拿一个空对象去 `save_memory` 会把她的记忆清空。
    """
    if not CALL_SUMMARY:
        return False
    cm = _user_managers.get(user_id)
    if cm is None:
        cm = ConversationManager(system_prompt, user_id=user_id)
        if not load_memory(user_id, cm):
            return False
        _user_managers[user_id] = cm
    key = api_key_override if api_key_override else api_key
    return _summarize_call_into(cm, key, call_id)
