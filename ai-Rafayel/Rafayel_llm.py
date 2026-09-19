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
from Rafayel_config import (
    API_URL, MAX_TOKENS, MODEL, QZONE_CMT_ENABLE, REPLY_ONE_LINE, TEMPERATURE,
    WB_MAX_CHARS, WB_MAX_ENTRIES, api_key,
)
from Rafayel_memory import ConversationManager, load_memory, save_memory
from Rafayel_sticker import apply_cooldown, sticker_instructions
from Rafayel_worldbook import get_worldbook


# 全局字典，按 user_id 存储每个用户的对话管理器
_user_managers = {}


def _to_one_line(text):
    """
    把回复**压成一行**（她 2026-09-19 挑的口径：像 QQ 随手打字，不分行）。

        （没躲，任你蹭过来，手落在你发顶）\n贴够了没。\n（嘴上这么说，另一只手却没拿开）
        ⇒ （没躲，任你蹭过来，手落在你发顶）贴够了没。（嘴上这么说，另一只手却没拿开）

    ⚠ 这是**保底**：prompt 里已经明说了「不要换行」（REPLY_ONE_LINE_HINT），
      但模型不一定每轮都听 —— 听话最好，不听话也**发不出多行**。
    ⚠ 只把换行拼掉，内容一字不动；空行直接丢。说说正文（render_text）不走这里。
    """
    if not REPLY_ONE_LINE or "\n" not in (text or ""):
        return text
    lines = [l.strip() for l in text.replace("\r\n", "\n").split("\n")]
    return "".join(l for l in lines if l)


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
                {"role": "system", "content": cm.get_full_system_prompt() + hint},
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
        return _to_one_line(text)
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


def get_reply(user_message: str, user_id: str, api_key_override: str = None) -> str:
    """
    供外部调用的入口函数

    参数：
        user_message: 用户发送的消息
        user_id: 用户的 QQ 号（用于区分不同用户，保持独立对话）
        api_key_override: 可选，手动传入 API Key（不传则使用环境变量或默认值）

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

    # 1. 添加用户消息
    cm.add_user_message(user_message)

    # 2. 更新 system 消息（加入最新的记忆）
    cm.update_system_message()

    # 3. 截断历史（保留最近 N 轮）
    cm.truncate_history()

    # 4. 构建请求的 messages
    request_messages = cm.messages.copy()

    # 4a. 世界书：命中关键词的条目才注入
    #     ⚠ system 那条是**每轮重算**的，不写回 cm.messages ——
    #        否则命中内容会被 save_memory 沉淀进 memory\*.json，越滚越大还会变成常驻人设。
    wb_before, wb_after = _build_worldbook(cm, user_message)
    if wb_before:
        request_messages[0] = {
            "role": "system",
            "content": cm.get_full_system_prompt() + "\n\n" + wb_before,
        }
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

            # 5.5 表情冷却闸：最近几条他已经发过表情 ⇒ 这一轮不再发（低频靠代码保证，
            #     prompt 只管「发得贴不贴切」）。
            # ⚠ 必须在 add_assistant_message **之前** —— 写进记忆的得是最终文本，
            #    否则下一轮看到的「他发过没有」是错的，闸就废了。
            _his_recent = [m.get("content") or ""
                           for m in cm.messages if m.get("role") == "assistant"]
            reply, _cooled = apply_cooldown(reply, _his_recent)
            if _cooled:
                print("[🖼️] 表情冷却：他最近几条已经发过表情 ⇒ 本条不再发")

            # 5.6 回复格式保底：压成一行（她挑的口径；prompt 里也说了，这里是兜底）
            reply = _to_one_line(reply)

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
