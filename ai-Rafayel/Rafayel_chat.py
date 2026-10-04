# -*- coding: utf-8 -*-
"""
祁煜（Rafayel）的**对话引擎入口**（对外薄壳）。

2026-09-17 按小辞的意见拆分：单个 620 行的文件既影响阅读也影响查阅，
现按职责切成 4 层 + 本入口。本文件只做三件事：

  ① 重新导出各子模块的公开名字 —— 保持 `from Rafayel_chat import …` 的旧写法可用，
     `Rafayel_bot.py` 与全部回归脚本**无需改动**（除 `_verify_e2.py` 的桩点，见 sources）。
  ② 提供命令行调试模式 `run_cli_mode`（直接运行本文件时进入）。
  ③ 作为「祁煜对话引擎」这一整块的门面。

层次（依赖单向，无循环）：
    Rafayel_config       路径 / 数值 / API key（最底层，不依赖本项目）
        ↑
    Rafayel_profile      用户画像（正则三道闸 + 落盘）
        ↑
    Rafayel_memory       记忆落盘 + ConversationManager（摘要 / 关键事实）
        ↑
    Rafayel_llm          get_reply（拼请求 + 调 DeepSeek）
        ↑
    Rafayel_chat         本文件（门面 + CLI）

旁支：`Rafayel.py` 读人设卡，`Rafayel_worldbook.py` 做关键词注入，两者被上面各层引用。

⚠ 这里必须显式 `import requests`：回归脚本（`_smoke_real.py`）会打
  `Rafayel_chat.requests.post` 的桩来抓真实 payload。`requests` 是模块对象，
  各层拿到的是同一个，所以从这里改一样能拦住 Rafayel_llm / Rafayel_memory 的发包。
"""

import os
import random

import requests  # noqa: F401  # 见上方注释：供回归脚本打桩，勿删

# —— 人设层（卡字段常量） ——
from Rafayel import (
    CARD_ALT_GREETINGS, CARD_DESCRIPTION, CARD_FIRST_MES, CARD_PERSONALITY,
    CARD_POST_HISTORY, CARD_SCENARIO, CARD_SYSTEM_PROMPT, name, system_prompt,
)

# —— 第 1 层：配置与常量 ——
from Rafayel_config import (
    API_URL, DEFAULT_API_KEY, MAX_FACTS, MAX_HISTORY_TURNS, MAX_PROFILE_ITEMS,
    MAX_TOKENS, MEMORY_DIR, MODEL, SUMMARY_INTERVAL, SUMMARY_MAX_TOKENS,
    TEMPERATURE, WB_MAX_CHARS, WB_MAX_ENTRIES, api_key,
)

# —— 第 2 层：用户画像 ——
from Rafayel_profile import (
    UserProfile, clear_user_profile, get_user_profile, set_user_profile,
)

# —— 第 3 层：记忆与对话管理 ——
from Rafayel_memory import (ConversationManager, load_memory, recent_context,
                            save_memory)

# —— 第 4 层：请求组装与 API 调用 ——
from Rafayel_llm import (
    _build_worldbook, _user_managers, comment_opening, comment_reply,
    get_reply, record_proactive, summarize_call, take_opening,
)

# —— 旁支：主动打招呼（素材原文直发，不经过模型） ——
from Rafayel_greet import load_pools, try_greet
from Rafayel_mood import mood_hint   # 💗 顶栏心情（2026-10-01）：门面转出，网页端不许直接 import mood

# —— 旁支：表情包（涂鸦叽标签 → 磁盘上的 gif） ——
# ⭐ 2026-10-01：网页端要**显示真图**了（原先是灰圆片占位）。
#   ⚠ 走门面转出，不让 `web/` 直接 import `Rafayel_sticker` —— 理由两条：
#     ① 跟 `mood_hint` 一个口径，网页端只认 `Rafayel_chat` 这一个门面；
#     ② `Rafayel_sticker` 自己要读 `card/stickers.md`、还带模块级缓存（`_TABLE_CACHE`），
#        这类「有状态的模块」经门面进出，将来改缓存策略只动一个地方。
#   ⚠ 它是**只读**模块（只读 md + 只 stat 文件，一个字节都不写盘），
#     所以**不登记进 `WRITER_MODULES`** —— 那名单是给写盘模块用的。
from Rafayel_sticker import pick_sticker

# —— 旁支：跨级解锁记账（2026-10-02 从网页端 `page/chat.py` 的 `_unlock_tick()` 挪进引擎） ——
#   ⭐ 为什么经门面转出、不让 `web/` 直接 import `Rafayel_daily`：
#     要记这笔账得同时动 `Rafayel_daily`（写盘）+ `Rafayel_affinity`（算级数）
#     两个模块 ⇒ 网页端直接引就是**一条 ADR-22 口子**。而现在
#     **两个页面**（`/chat` 与 `/call`）都要用它 ⇒ 放门面里，两边只认这一个名字。
#     ⚠ `web/base.py` 更不能承接它 —— 那会让**所有页面**跟着背这条口子。
#   ⚠ 它是 `Rafayel_bot.maybe_send_unlock` 去掉「发送」那一半：只记账、一个字都不发。
from Rafayel_daily import tick_unlock


__all__ = [
    # 对外调用
    "get_reply", "take_opening", "record_proactive", "_user_managers",
    # 她在朋友圈留话 ⇒ 他跑来私聊（2026-09-20）
    "comment_opening",
    # 她评论了 ⇒ 他直接在空间回复那条评论（事件带内容）
    "comment_reply",
    # 主动打招呼
    "try_greet", "load_pools",
    # 表情包（标签 ⇒ 磁盘绝对路径；`None` = 没这张 / 功能关着）
    "pick_sticker",
    # 💞 跨级解锁记账（引擎侧实现 = `Rafayel_daily.tick_unlock`）
    "tick_unlock",
    # ☎️📔 单通电话摘要（引擎侧实现 = `Rafayel_memory.summarize_call`，
    #   取 `cm` 那半在 `Rafayel_llm.summarize_call`）。
    #   ⭐ 为什么不走「网页端直接 import `Rafayel_memory`」：那个模块是**写盘模块**，
    #     网页端引它 = 又开一条 ADR-22 口子。走门面 = 复用 `call.py` **已经开着的**那条。
    #   ⚠ 调用方要自己包 `base._chat_lock(uid)`（它读改写 `memory/{uid}.json`）。
    "summarize_call",
    # 对话与记忆
    "ConversationManager", "save_memory", "load_memory", "recent_context",
    # 用户画像
    "UserProfile", "get_user_profile", "set_user_profile", "clear_user_profile",
    # 人设
    "name", "system_prompt", "CARD_DESCRIPTION", "CARD_PERSONALITY",
    "CARD_SCENARIO", "CARD_SYSTEM_PROMPT", "CARD_POST_HISTORY",
    "CARD_FIRST_MES", "CARD_ALT_GREETINGS",
    # 配置
    # ⚠ `MEMORY_DIR` 是**给老代码留的兼容出口**（`card/_work/_verify_profile.py` 在引它）。
    #    ⚠ 它是**值复制**，别再拿它拼路径 —— 新代码一律用 `Rafayel_config.mem_dir()` /
    #      `mem_path()`（Step 0：落盘目录的唯一出口，调用时才解析）。
    "MEMORY_DIR", "MAX_TOKENS", "TEMPERATURE", "SUMMARY_MAX_TOKENS", "SUMMARY_INTERVAL",
    "MAX_HISTORY_TURNS", "MAX_FACTS", "MAX_PROFILE_ITEMS",
    "WB_MAX_CHARS", "WB_MAX_ENTRIES", "API_URL", "MODEL",
    "DEFAULT_API_KEY", "api_key",
    # CLI
    "run_cli_mode",
]


# ============================================================
#  命令行交互模式（仅当直接运行此文件时生效）
# ============================================================

def run_cli_mode():
    # 这个模式用于在终端直接测试 AI 人设，不影响 QQ 机器人模式

    # 初始化一个临时的对话管理器（使用固定 user_id = "cli"）
    cli_manager = ConversationManager(system_prompt, user_id="cli")

    # 使用全局字典存储，方便后续 get_reply 调用
    _user_managers["cli"] = cli_manager

    # 2026-09-18 修：旧版**从没调过 load_memory** —— 因为 get_reply 里的 load_memory
    # 只在 `if user_id not in _user_managers` 分支里，而 CLI 一上来就把 "cli" 注册好了，
    # 该分支永远不成立 ⇒ 每次启动都是全新会话，上一轮的记忆还在文件里却读不回来，
    # 而且第一轮 save_memory 就把它覆盖掉了。这里显式读一次。
    load_memory("cli", cli_manager)

    # 开场白取自酒馆卡（alternate_greetings 随机一条，没有就退回 first_mes），
    # 与 QQ 端共用 `take_opening`：只有真的第一次聊才给，且会写进历史 + 落盘。
    _greeting = take_opening("cli")
    if _greeting:
        print("\n🌊 海浪轻轻拍打着沙滩，你推开了 Mo Art Studio 的门……\n")
        print(f"💙 {name}：{_greeting}\n")
    else:
        print("\n（读到了上次的记忆，接着上次继续；删掉 memory\\cli.json 可从头开始）\n")
    # 2026-09-15：删除开局的五问表单（名字/爱好/食物/技能/补充）。
    #   称呼与喜好改由对话中自然引导 + 自动提取，见 UserProfile。

    print(f'💙 {name}已上线，来和他聊聊天吧！')
    print("   （输入 '再见' 即可结束对话）")
    print('   （系统会自动记住重要的事情，无需担心上下文丢失）\n')

    while True:
        user_input = input('你：')

        if user_input.strip() == '再见':
            farewells = [
                f'💙 {name}：唉……真不想参加那个海外巡回的特展。明明我的画到场就够了，为什么人也要去……好吧，不会让你等太久的。',
                f'💙 {name}：你对待我就像对待自己家的门，想来就来，想走就走。……算了，门给你留着，记得回来。',
                f'💙 {name}：我数着每天的潮涨潮落，日升月起……终于，要等到和你见面的日子了。……虽然这次是我要走了。',
                f'💙 {name}：（看着你，停顿了一下）……看到了一个，让我爱上这片陆地的人呗。所以，别让我等太久。',
            ]
            print(random.choice(farewells))
            break

        # 使用外部接口函数处理
        reply = get_reply(user_input, user_id="cli", api_key_override=api_key)
        print(f'💙 {name}说：{reply}\n')


if __name__ == "__main__":
    run_cli_mode()
