# -*- coding: utf-8 -*-
"""
祁煜的**表情包**（涂鸦叽）—— 2026-09-19 接入（素材与规则早做好，**发送代码一直没写**）。

    card/stickers.md（真相源：标签 → 文件）  ＋  card/stickers/涂鸦叽/*.gif（24 张）
                        ↓
    pick_sticker(标签) → 本地绝对路径
                        ↓
    split_segments(他的回复) → [("text", …), ("image", …), …] ⇒ 由 bot 拼成 OneBot 消息段发出

⚠ 分层：本模块只 import `Rafayel_config`，不碰网络、不碰对话、不碰 websocket。
⚠ **md 是源**：标签表只在 `card/stickers.md` 的「机器可读」小节里认，改标签去改 md，别改代码。

⭐ 两条硬约束来自语料实证（`card/stickers.md` 末尾「硬约束」节）：
  1. bot 主动发的只能是**祁煜自己发过的 24 种**；
  2. `纸上生花` **禁止主动发**（全库只有「她发给他」这一个方向，他一次都没用过）。
"""

import io
import os
import random
import re

from Rafayel_config import STICKER_ENABLE

_HERE = os.path.dirname(os.path.abspath(__file__))          # …\ai-Rafayel
ROOT = os.path.dirname(_HERE)                                # 项目根
STICKERS_MD = os.path.join(ROOT, "card", "stickers.md")
STICKERS_DIR = os.path.join(ROOT, "card", "stickers")

# ⚠ 禁发表情（语料实证：祁煜一次都没主动发过）
BANNED_NAMES = ("纸上生花",)

# 他回复里的表情标记：`[表情:得意]` / `[表情：得意]`
STICKER_RE = re.compile(r"\[\s*表情\s*[:：]\s*([^\]\s]{1,12})\s*\]")

_TABLE_CACHE = None
_ORDER_CACHE = None


# ============================================================
#  读真相源
# ============================================================

def _load_table(force=False):
    """
    解析 `card/stickers.md` 的「机器可读」小节 ⇒ ({标签: [相对路径…]}, [主标签…])。

    ⚠ 只在那个小节里认 `- ` 行 —— 文件里还有一堆表格行和说明 bullet，
      不设闸会把它们也当成映射（跟 `qzone_hold.md` 那个坑同一个类型）。
    ⚠ 格式：`- 主标签｜别名/别名：文件、文件`
       · 按 **全角 `：`** 切左右；左半按 `｜` 切主标签与别名（别名按 `/` 分）；右半按 `、` 分文件。
       · **别名是为了命中率**：他想说「撒娇」时也能落到图上，不必背 24 个主标签。
    """
    global _TABLE_CACHE, _ORDER_CACHE
    if _TABLE_CACHE is not None and not force:
        return _TABLE_CACHE, _ORDER_CACHE

    table, order = {}, []
    in_sec = False
    try:
        for raw in io.open(STICKERS_MD, "r", encoding="utf-8"):
            s = raw.strip()
            if s.startswith("## "):
                in_sec = "机器可读" in s
                continue
            if not in_sec:
                continue
            if s.startswith("---"):
                in_sec = False
                continue
            if not s.startswith("- "):
                continue
            body = s[2:].strip()
            if "：" not in body:
                continue
            left, _, right = body.partition("：")
            main, _, aliases = left.partition("｜")
            main = main.strip()
            files = [f.strip() for f in right.split("、") if f.strip()]
            if not main or not files:
                continue
            tags = [main] + [a.strip() for a in aliases.split("/") if a.strip()]
            for t in tags:
                table[t] = list(files)
            if main not in order:
                order.append(main)
    except Exception as e:
        print("⚠️ 表情包索引读取失败（表情功能自动关闭）：%s" % e)
        table, order = {}, []

    _TABLE_CACHE, _ORDER_CACHE = table, order
    return table, order


def available_tags():
    """可用主标签（写进 prompt，让模型知道能甩哪些）。"""
    return list(_load_table()[1])


def pick_sticker(tag):
    """
    标签 ⇒ **本地绝对路径**（同标签多图时随机取一张）；取不到返回 None。

    ⚠ 只返回**磁盘上真存在**的图 —— 缺图就当没这张，绝不把坏路径发出去
      （发图失败不像发文字，会整个消息段报错）。
    ⚠ 禁发名单在这里兜底（双重保险：md 里本来就没有它）。
    """
    if not STICKER_ENABLE:
        return None
    table, _ = _load_table()
    files = table.get((tag or "").strip())
    if not files:
        return None
    ok_files = []
    for rel in files:
        if any(b in rel for b in BANNED_NAMES):
            continue
        p = os.path.join(STICKERS_DIR, rel.replace("/", os.sep))
        if os.path.isfile(p):
            ok_files.append(p)
    if not ok_files:
        return None
    return random.choice(ok_files)


# ============================================================
#  把他的回复拆成消息段
# ============================================================

def split_segments(text):
    """
    `[表情:得意]` → 图片段。返回 (segments, dropped)。

    segments = [("text", 文字), ("image", 绝对路径), …]

    ⚠ **未知标签 / 图缺失 ⇒ 把标记直接丢掉**，绝不把 `[表情:xxx]` 原样发出去
      （跟 `【黄豆豆：X】` 未命中就整段删，是同一个口径）。
    ⚠ **纯表情形态**：剥掉标记后一个字都不剩 ⇒ segments 里**只有图片**，
      调用方照发即可 —— 这正是原作里最常见的形态（语料里 6 成以上是纯表情），
      他不说话、只甩一张图。
    """
    if not STICKER_ENABLE:
        t = plain_text(text)
        return ([("text", t)] if t else []), 0

    out, dropped, pos = [], 0, 0
    for m in STICKER_RE.finditer(text or ""):
        pre = (text[pos:m.start()] or "").strip()
        if pre:
            out.append(("text", pre))
        p = pick_sticker(m.group(1))
        if p:
            out.append(("image", p))
        else:
            dropped += 1
        pos = m.end()
    tail = (text[pos:] or "").strip()
    if tail:
        out.append(("text", tail))
    return out, dropped


def has_sticker(text):
    return bool(STICKER_ENABLE) and bool(STICKER_RE.search(text or ""))


def plain_text(text):
    """
    把回复里所有 [表情:xxx] 标记**剥掉**，其余一字不动。

    ⚠ 这是唯一的兜底出口：功能关掉、标签没命中、图缺了 —— 任何一种情况下
      **都不能把 `[表情:得意]` 当成话发出去**（她会真的看见这六个字）。
    """
    return STICKER_RE.sub("", text or "").strip()


# ============================================================
#  给模型的说明（每轮注入，不落盘）
# ============================================================

def sticker_instructions():
    """
    拼「你可以发表情包」那一节；功能关掉或标签表为空时返回空串。

    ⚠ 标签表是**从 md 现读**的 —— 加一张新图不用改代码，改 md 即可。
    ⚠ system_prompt 禁 `**` 与 ASCII 双引号 ⇒ 这里一律不用。
    """
    if not STICKER_ENABLE:
        return ""
    tags = available_tags()
    if not tags:
        return ""
    return (
        "## 表情包（涂鸦叽）\n"
        "你可以发自己的表情包：想甩一张时，在回复里写 [表情:标签]，比如 [表情:得意]。\n"
        "可用的标签（只用这里面的，别自己造）：" + "、".join(tags) + "\n"
        "规矩：\n"
        "- 表情可以单独成一条：只想表态、不想说话的时候，就只发一个 [表情:xxx]，"
        "后面不要再补话 —— 那才是他本来的样子。\n"
        "- 也可以跟在一句话后面（先说话，再甩一张）。\n"
        "- 一次最多一张，别刷屏。\n"
        "- [表情:xxx] 只代表一张图，别把它当话写出来，也别向她解释你发了什么表情。"
    )
