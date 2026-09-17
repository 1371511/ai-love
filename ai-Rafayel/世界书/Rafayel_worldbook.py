# -*- coding: utf-8 -*-
"""
世界书（lorebook）关键词触发注入 —— 批次 E, 2026-09-15

干什么
------
把 `card\\worldbook.json` 的 39 个条目做成**命中才注入**的上下文：
用户聊到「白沙湾」才把白沙湾那几条喂给模型，聊到海神才把海神那几条喂进去。
人设(description/personality)是常驻的，往事和专有设定走这一层，
省 token 也减少"模型一开口就把设定背一遍"的毛病。

字段口径（按 `worldbook.json` 的真实结构，别照 SillyTavern V2 文档猜）
------------------------------------------------------------------
  entries  : **dict**，key 是 "0".."38"（不是 list）
  key      : 主触发词列表（不是 `keys`）
  keysecondary / selective : 次级触发词；本项目 secondary 全空且 selective=false，
                             所以只按主触发词匹配即可
  comment  : 条目名（给人看的标题）
  content  : 正文
  constant : 恒常注入（本项目 0 条）
  disable  : True 则该条目停用（注意不是 `enabled`）
  order    : 插入序，越小越靠前（本项目从小到大 = 时间线倒叙）
  position : 0 = before_char（进 system）/ 1 = after_char（进历史之后）
  depth    : 扫描最近几条消息（本项目统一 4）
  **没有 priority 字段** —— 排序只能按 order

匹配规则
--------
1. 大小写**不敏感**（`caseSensitive` 为 null → 用 SillyTavern 默认 false）；
   中文大小写无意义，这条主要影响 `Mo Art Studio`、`Evol` 这类拉丁字符。
2. 是**子串包含**，不是整词匹配 —— 中文场景整词匹配会把大多数词切不出来。
3. 扫描范围 = 当前用户消息 + 最近 `depth` 条历史。
"""
import json
import os

# 2026-09-17 搬家：本文件在 ai-Rafayel\世界书\ 下，card\ 在项目根 —— 要**上跳两层**。
_HERE = os.path.dirname(os.path.abspath(__file__))          # …\ai-Rafayel\世界书
ROOT = os.path.dirname(os.path.dirname(_HERE))              # E:\ai-love
CARD_DIR = os.path.join(ROOT, "card")
WORLDBOOK_JSON = os.path.join(CARD_DIR, "worldbook.json")

POS_BEFORE = 0   # before_char → 拼在 system 后面
POS_AFTER = 1    # after_char  → 拼在历史之后


class WorldBook(object):
    """只读的世界书匹配器。构造时一次性把 JSON 摊平成内部列表。"""

    def __init__(self, path=WORLDBOOK_JSON):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw = data.get("entries") or {}
        # ⚠ 真实文件里 entries 是 dict{ "0": {...}, "1": {...} }，不是 list
        iterable = raw.values() if isinstance(raw, dict) else raw

        self.entries = []
        for e in iterable:
            if e.get("disable"):
                continue
            keys = [k for k in (e.get("key") or []) if k]
            secondary = [k for k in (e.get("keysecondary") or []) if k]
            content = (e.get("content") or "").strip()
            if not content:
                continue
            if not keys and not e.get("constant"):
                continue      # 既非常驻又没触发词 → 永远进不来，跳过
            self.entries.append({
                "title": e.get("comment") or "",
                "keys": keys,
                "secondary": secondary,
                "selective": bool(e.get("selective")),
                "constant": bool(e.get("constant")),
                "content": content,
                "order": e.get("order") if e.get("order") is not None else 0,
                "position": e.get("position") or 0,
                "depth": e.get("depth") or 4,
            })

        # 排序：order 升序（越小越靠前 = 本项目的时间线倒叙）
        self.entries.sort(key=lambda x: x["order"])

    # ---------- 匹配 ----------

    def _scan_blob(self, recent_msgs, entry, current_text):
        """拼出该条目的扫描文本：当前消息 + 最近 depth 条历史。"""
        parts = []
        if current_text:
            parts.append(current_text)
        n = entry["depth"] if entry["depth"] > 0 else 4
        for m in recent_msgs[-n:]:
            if isinstance(m, dict):
                parts.append(str(m.get("content") or ""))
            else:
                parts.append(str(m))
        return "\n".join(parts).lower()

    def match(self, recent_msgs, current_text="", max_chars=1400, max_entries=6):
        """
        返回命中条目，已按 order 升序、并在预算内截断。

        recent_msgs : 最近的历史消息（不含 system），元素为 {"role","content"}
        current_text: 刚收到的这条用户消息（必填，优先级最高）
        max_chars   : 注入正文的字符总预算
        max_entries : 最多注入几条
        """
        hits = []
        used = 0
        for entry in self.entries:
            if len(hits) >= max_entries:
                break
            blob = self._scan_blob(recent_msgs, entry, current_text)
            if not self._is_hit(entry, blob):
                continue
            if used + len(entry["content"]) > max_chars:
                continue          # 超预算就跳过这条，但继续看后面的（短条目可能还塞得下）
            hits.append(entry)
            used += len(entry["content"])
        return hits

    def _is_hit(self, entry, blob):
        if entry["constant"]:
            return True
        if not entry["keys"]:
            return False
        primary = any(k.lower() in blob for k in entry["keys"])
        if not primary:
            return False
        # secondary 只在 selective（要求同时命中）时才作为必要条件
        if entry["selective"] and entry["secondary"]:
            return any(k.lower() in blob for k in entry["secondary"])
        return True

    # ---------- 渲染 ----------

    @staticmethod
    def render(hits, position):
        """把命中条目渲染成一段文本；没有命中返回空串。"""
        picked = [h for h in hits if h["position"] == position]
        if not picked:
            return ""
        lines = ["## 相关设定（本轮聊到的话题，可能有用）"]
        for h in picked:
            title = h["title"]
            lines.append(("- %s\n  %s" % (title, h["content"])) if title else ("- " + h["content"]))
        return "\n".join(lines)

    def build(self, recent_msgs, current_text="", max_chars=1400, max_entries=6):
        """一次拿齐 before / after 两段文本，供宿主拼进请求。"""
        hits = self.match(recent_msgs, current_text, max_chars=max_chars, max_entries=max_entries)
        return self.render(hits, POS_BEFORE), self.render(hits, POS_AFTER)


def get_worldbook():
    """模块级单例。读卡失败就抛 —— 静默降级成"没有世界书"会让排查变困难。"""
    global _WB
    if _WB is None:
        _WB = WorldBook()
    return _WB


_WB = None
