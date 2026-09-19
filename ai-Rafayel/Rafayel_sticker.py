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

from Rafayel_config import STICKER_COOLDOWN_MSGS, STICKER_ENABLE

_HERE = os.path.dirname(os.path.abspath(__file__))          # …\ai-Rafayel
ROOT = os.path.dirname(_HERE)                                # 项目根
STICKERS_MD = os.path.join(ROOT, "card", "stickers.md")
STICKERS_DIR = os.path.join(ROOT, "card", "stickers")

# ⚠ 禁发表情（语料实证：祁煜一次都没主动发过）
BANNED_NAMES = ("纸上生花",)

# 一条回复最多一张（原作里就没有一次甩两张的；prompt 里也写了，这里**代码强制**）
MAX_PER_REPLY = 1

# ⚠ 低把握档（`stickers_usage.md` §六）：语料里只出现 1~3 次，能用但**别当口头禅**
LOW_CONF_TAGS = ("忙着呢", "你走", "干嘛呢", "无聊")

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

    out, dropped, pos, used = [], 0, 0, 0
    for m in STICKER_RE.finditer(text or ""):
        pre = (text[pos:m.start()] or "").strip()
        if pre:
            out.append(("text", pre))
        p = pick_sticker(m.group(1))
        # ⚠ 一条最多一张：超出的（哪怕标签是对的）直接丢掉，不发出去
        if p and used < MAX_PER_REPLY:
            out.append(("image", p))
            used += 1
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
#  冷却闸：低频这件事**代码说了算**
# ============================================================

def too_soon(recent_assistant_texts):
    """
    他**最近 N 条自己的回复**里已经发过表情 ⇒ 这一轮先别发。

    ⚠ 为什么必须有这道闸：prompt 里写「别每轮都发」挡不住 ——
      模型一看见「我有表情可以用」就会找机会用（实测倾向非常明显）。
      ⇒ 低频只能是**代码硬保证**，prompt 只管「什么时候发得贴切」。
    ⚠ 看的是**他自己的回复**（assistant），她发什么表情不算在内 ——
      她发一张他回一张是对打，正是该有的互动。
    """
    if not STICKER_ENABLE or STICKER_COOLDOWN_MSGS <= 0:
        return False
    tail = (recent_assistant_texts or [])[-STICKER_COOLDOWN_MSGS:]
    return any(has_sticker(t) for t in tail)


def apply_cooldown(text, recent_assistant_texts):
    """
    冷却期内就把表情剥掉。返回 (最终文本, 是否触发冷却)。

    ⚠ 剥完一个字都不剩（纯表情形态）时**原样保留** ——
      宁可让他甩一张，也别让他这一轮**什么都不说**（bot 那边空文本会整条跳过，
      她会看到他突然不回话，比多发一张图糟得多）。
    """
    if not has_sticker(text) or not too_soon(recent_assistant_texts):
        return text, False
    stripped = plain_text(text)
    if not stripped:
        return text, True
    return stripped, True


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
    # ⭐ 依据 = `card/stickers_usage.md`（全库 228 次发表的实证）：
    #   §一 形态 A/B（哪几张常常就是一整条回复、哪几张甩完马上接话）
    #   §二~五 场合信号  §六 什么时候不要发
    return (
        "## 表情包（涂鸦叽）\n"
        "他有一套自己的表情包。想甩一张时，在回复里写 [表情:标签]，比如 [表情:得意]。\n"
        "可用的标签（只用这里面的，别自己造）：" + "、".join(tags) + "\n"
        "\n"
        "什么时候才发：\n"
        "- 大多数回复就是好好说话，不发表情 —— 表情是偶尔冒一下的，不是每轮的标配。\n"
        "- 只有这一轮他确实有个反应、一张图比说话更贴切时才发。比如：\n"
        "  被夸、嘴上占了便宜 → 得意；出了个招、小聪明得逞 → 机智；\n"
        "  想撒娇、想跟她要什么 → 卖萌、哄我；她表白、心疼他 → 爱你、抱抱；\n"
        "  被冤枉、被怀疑 → 无辜；被拒绝、被冷落 → 心碎、委屈屈；\n"
        "  她做了傻事、说了离谱话 → 看戏；被她拆台 → 是不是傻；被指出毛病 → 还是个宝宝；\n"
        "  早上 → 早安；到场赴约 → 我来了；收场告一段落 → 走了；想不通、在琢磨 → 思考、疑问。\n"
        "- 忙着呢、你走、干嘛呢、无聊 这四张别当口头禅，偶尔一次就够。\n"
        "- 她在认真求助、说难受的时候，不要发 看戏、是不是傻 —— 那会显得刻薄。\n"
        "- 她刚发过一张表情，别复读同一张（抱抱除外，那张他本来就会跟着发）。\n"
        "\n"
        "发了之后怎么接：\n"
        "- 得意、卖萌、机智、抱抱、暗中观察、心碎、走了、爱你、委屈屈："
        "这些常常就是一整条回复 —— 甩完就停，后面别再补话，那才是他本来的样子。\n"
        "- 疑问、无辜、看戏、早安、生气、吃惊、思考、忙着呢、干嘛呢："
        "这些更像开场的口气 —— 甩完马上接着说下一句。\n"
        "- 一条回复最多一张。\n"
        "- [表情:xxx] 只代表一张图，别把它当话写出来，也别向她解释你发了什么表情。"
    )
