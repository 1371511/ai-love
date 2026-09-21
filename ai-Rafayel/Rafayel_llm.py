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

import requests

# 2026-09-17 搬家：世界书代码在 ai-Rafayel\世界书\ 子目录里，**不在本文件同一层**。
# Python 只会把「本文件所在目录」自动加进 sys.path，子目录里的模块默认搜不到，
# 所以这里手动补一段。放在 import Rafayel_worldbook 之前 —— 顺序不能挪到后面。
_WB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "世界书")
if _WB_DIR not in sys.path:
    sys.path.insert(0, _WB_DIR)

from Rafayel import (
    CARD_ALT_GREETINGS, CARD_FIRST_MES, CARD_POST_HISTORY, system_prompt,
)
from Rafayel_affinity import tone_for
from Rafayel_daily import record_usage as usage_record
from Rafayel_config import (
    AFFINITY_TONE, API_URL, MAX_TOKENS, MEMORY_DIR, MODEL, QZONE_CMT_ENABLE,
    REPLY_MAX_LINES, REPLY_SHAPE, REPLY_SPLIT_FALLBACK, REPLY_SPLIT_MAX_LINES,
    REPLY_SPLIT_MIN_CHARS, TEMPERATURE, WB_MAX_CHARS, WB_MAX_ENTRIES,
    api_key,
)
from Rafayel_memory import ConversationManager, load_memory, save_memory
from Rafayel_sticker import apply_cooldown, sticker_instructions
from Rafayel_worldbook import get_worldbook


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


def _build_worldbook(cm, user_message):
    """
    返回 (before_text, after_text)：本轮命中的世界书条目渲染结果。

    设计要点：
    - 注入是**每轮重算**的，不写入 cm.messages（避免沉淀进记忆文件）。
    - 这里允许失败降级：世界书只是"参考资料"，读不到最多是少一点上下文，
      不该把整个聊天搞挂。人设卡（load_card）则相反 —— 读不到必须报错。
    """
    try:
        wb = get_worldbook()
        return wb.build(
            cm.get_recent_messages(),
            user_message,
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
        return tone_for(cm.user_id, MEMORY_DIR)
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
            "temperature": TEMPERATURE,
        }
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
            "temperature": TEMPERATURE,
        }
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


def get_reply(user_message: str, user_id: str, api_key_override: str = None,
              media: bool = False) -> str:
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

    # 1. 添加用户消息（media 只用于每日统计，不参与对话内容）
    cm.add_user_message(user_message, media=media)

    # 2. 更新 system 消息（加入最新的记忆）
    cm.update_system_message()

    # 3. 截断历史（保留最近 N 轮）
    cm.truncate_history()

    # 4. 构建请求的 messages
    request_messages = cm.messages.copy()

    # 4a. 世界书：命中关键词的条目才注入
    #     ⚠ system 那条是**每轮重算**的，不写回 cm.messages ——
    #        否则命中内容会被 save_memory 沉淀进 memory\*.json，越滚越大还会变成常驻人设。
    # 4a-0. 💞 牵绊度语气（等级几天才动一次，放 system 里不影响前缀缓存）
    wb_before, wb_after = _build_worldbook(cm, user_message)
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

    # 4d. 🕐 时间感（2026-09-20 从 system 末尾挪到这里，见 `now_hint_text` 的注释）。
    #     ⭐ 为了**钱**：这段每轮都变，留在 system 里会把 DeepSeek 的前缀缓存拦腰截断，
    #        后面的整段聊天历史就永远按「未命中」计费。挪到最后 ⇒ system + 历史可缓存。
    #     ⚠ 同样只进 request_messages，绝不写回 cm.messages。
    #     ⚠ 位置 = 整段 prompt 的最后一条（原来靠「system 越靠后越受关注」，
    #        现在换成「全局最靠后」，注意力不比原来差；真机 A/B 再定）。
    _now = cm.now_hint_text()
    if _now:
        request_messages.append({"role": "system", "content": _now})

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
        "temperature": TEMPERATURE,
    }

    try:
        response = requests.post(API_URL, headers=headers, json=data, timeout=30)
        result = response.json()

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

            # 6. 添加助手消息到对话管理器
            cm.add_assistant_message(reply)

            # 7. 触发摘要更新（如果到了总结间隔）
            if cm.should_summarize():
                cm.generate_summary(effective_api_key)
                cm.update_system_message()
                cm.trim_facts()

            save_memory(user_id, cm)   # 每次对话后保存
            return reply
        else:
            error_msg = result.get("error", {}).get("message", str(result))
            return f"（AI 接口出错：{error_msg}）"
    except requests.exceptions.Timeout:
        return "（请求超时，请稍后再试 💙）"
    except Exception as e:
        return f"（发生错误：{e} 💙）"
