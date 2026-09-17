# -*- coding: utf-8 -*-
r"""
祁煜（Rafayel）的**人设层**。

这一层只做一件事：把酒馆卡读进来，导出人设常量。

对话引擎在 2026-09-17 拆成了多层，本层不掺和任何运行时逻辑：
  Rafayel_config  配置与常量（含 MEMORY_DIR / 各项上限 / API key）
  Rafayel_profile 用户画像
  Rafayel_memory  记忆落盘 + ConversationManager
  Rafayel_llm     get_reply（拼请求 + 调 DeepSeek）
  Rafayel_chat    门面 + CLI（`from Rafayel_chat import …` 旧写法仍可用）
人设常量（CARD_* / name / system_prompt）由上面各层从本模块取。

唯一真相源 = card\_work\祁煜人设.md
  （改人设改它，然后跑 card\_work\md2card.py --only card）
"""
import os
import json

# ============================================================
#  🎭 角色配置区（改这里就能换人设！）
# ============================================================

# 2026-09-15 批次 D：人设改由酒馆卡提供，旧版硬编码人设**全部停用**。
#   · 唯一真相源 = card\_work\祁煜人设.md（改人设去改它，再跑 md2card.py --only card）
#   · 旧版备份 = Rafayel.py.bak-before-card-20260915
#   · 保留两套人设会互相打架（旧版称呼阶段递进 vs 新版三层优先级），所以是替换不是叠加。

# 2026-09-17 搬家：代码移入 ai-Rafayel\，而 card\ / memory\ 仍留在项目根 ——
# 所以不能再用「本文件同层」定位，必须先上跳一层拿到项目根。
_HERE = os.path.dirname(os.path.abspath(__file__))          # E:\ai-love\ai-Rafayel
ROOT = os.path.dirname(_HERE)                               # E:\ai-love
CARD_JSON = os.path.join(ROOT, "card", "Rafayel.character.json")

def load_card():
    """读取酒馆卡 JSON。文件缺失/损坏就明确报错，绝不静默降级成空人设。"""
    with open(CARD_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

_CARD = load_card()

name = _CARD.get("name") or "祁煜"

CARD_DESCRIPTION = _CARD.get("description") or ""
CARD_PERSONALITY = _CARD.get("personality") or ""
CARD_SCENARIO = _CARD.get("scenario") or ""
CARD_SYSTEM_PROMPT = _CARD.get("system_prompt") or ""
CARD_POST_HISTORY = _CARD.get("post_history_instructions") or ""
CARD_FIRST_MES = _CARD.get("first_mes") or ""
CARD_ALT_GREETINGS = _CARD.get("alternate_greetings") or []

# 喂给模型的 system 提示词：行为规则在最前，人设事实在后
system_prompt = "\n\n".join([x for x in [
    CARD_SYSTEM_PROMPT,
    "## 角色\n" + CARD_DESCRIPTION,
    "## 性格\n" + CARD_PERSONALITY,
    "## 当前情境\n" + CARD_SCENARIO,
] if x])

