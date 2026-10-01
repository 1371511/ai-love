# -*- coding: utf-8 -*-
"""
🗄 原话留档（2026-10-01 · 她定：「原话不删，供用户查看，不调用」）

`roll_days()` 每天会把「上一自然日」的原话**从 `messages` 里物理摘走**，
`truncate_history()` 超过 12 轮再从头部裁 —— 两处都是**真删**，
她因此反馈「三天前的记录不见了」。

⇒ 本模块在**删之前**把原话另存一份到 `memory/{uid}_archive.json`，
  供她在网页上翻看（`/chat/history?day=…`）。

⭐⭐ 三条红线（她定的口径，别越线）
------------------------------------------------------------
1. **只存档，不调用**：本模块**没有**任何给 prompt 用的函数，
   引擎层也不会读它。归档一旦进 prompt，`DAY_ROLL` 就白做了 ——
   旧原话回到上下文，他照样把昨天当今天。
2. **独立文件**：⚠⚠ 绝不写进 `memory/{uid}.json` —— 那份被 `cm` 缓存
   **整份覆盖**（谁后写谁赢），塞进去会被静默抹掉。跟日记 / 情绪一个道理。
3. **只 import config**：不引 `Rafayel_memory`，避免循环依赖；
   网页端**不 import 本模块**（ADR-22），它自己 `json.load` 那份文件。

⚠ 写入点只有两处，都在 `Rafayel_memory` 里、都在**真的要删之前**：
   · `roll_days()`      —— 摘走一整天原话前
   · `truncate_history()` —— 从头部裁掉超 12 轮的那些前
   ⚠ 顺序不能反：先存后删。存失败 ⇒ **宁可不删也不丢**（见调用处的处理）。
"""
import json
import os
import time

from Rafayel_config import ARCHIVE_KEEP, ARCHIVE_MAX_DAYS, ARCHIVE_MAX_MSGS, MEMORY_DIR


def _path(user_id):
    return os.path.join(MEMORY_DIR, "%s_archive.json" % str(user_id))


def _blank():
    return {"days": []}


def load(user_id):
    """
    读整份归档，返回 `{"days": [{"date":…, "messages":[...]}, …]}`。

    ⚠ 类型闸（全站红线，见 `MEMORY.md` 第六节）：**先看 isinstance 再看内容** ——
       文件被写成 list / str 时不能去迭代它。
    ⚠ 读坏了返回空档（**绝不抛**）：归档是给她翻着看的，挂了顶多是「那天没存档」，
       绝不能拖垮对话本身。
    """
    p = _path(user_id)
    if not os.path.exists(p):
        return _blank()
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print("⚠️ 归档读取失败（当空档处理）：%s" % e)
        return _blank()
    if not isinstance(raw, dict):
        return _blank()
    days = raw.get("days")
    if not isinstance(days, list):
        return _blank()
    out = []
    for d in days:
        if not isinstance(d, dict):
            continue
        date = str(d.get("date") or "").strip()
        if not date:
            continue
        msgs = d.get("messages")
        msgs = msgs if isinstance(msgs, list) else []
        keep = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                keep.append({"role": role, "content": content})
        out.append({"date": date, "saved_at": d.get("saved_at") or 0.0,
                    "messages": keep})
    out.sort(key=lambda x: x["date"])
    return {"days": out}


def save(user_id, data):
    """原子写（tmp + replace，跟 `Rafayel_mood.save` 同一套）。返回是否成功。"""
    try:
        os.makedirs(MEMORY_DIR, exist_ok=True)
        p = _path(user_id)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception as e:
        print("⚠️ 归档落盘失败（不影响对话）：%s" % e)
        return False


def day_of(user_id, date):
    """某一天的 `{"date":…, "messages":[...]}`，没有就 `None`。给网页端翻原话用。"""
    for d in load(user_id)["days"]:
        if d["date"] == date:
            return d
    return None


def dates(user_id):
    """有存档的日期列表（升序）。"""
    return [d["date"] for d in load(user_id)["days"]]


def append(user_id, date, messages):
    """
    把一批原话**追加**到 `date` 那天的存档里。返回存了几条（0 = 没存）。

    ⭐⭐ 为什么是「追加」而不是「覆盖」：同一天可能被**两个删源**各写一次 ——
      `truncate_history()` 先裁掉更早的那些、`roll_days()` 再摘走剩下的一整天。
      覆盖 ⇒ 前一批被后一批抹掉，反而丢得更多。
      追加 ⇒ 时间顺序天然正确（先裁的在前、后摘的在后），且两批**不重叠**
      （被 truncate 裁掉的已经不在 `messages` 里，roll_days 不可能再摘到它们）。

    ⚠ `messages` 里的元素必须带 `role` / `content`，其余字段（`ts` 等）**一律丢掉** ——
       归档只留「谁说了什么」，别把内部字段沉淀进文件。
    ⚠ `ARCHIVE_KEEP=False` ⇒ 直接返回 0（回到「摘走即永久删除」的旧行为）。
    ⚠ 空列表 / 没开关 ⇒ 不写盘（省一次 IO，也别写出空档）。
    """
    if not ARCHIVE_KEEP or not date:
        return 0
    keep = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            keep.append({"role": role, "content": content})
    if not keep:
        return 0

    data = load(user_id)
    days = data["days"]
    slot = None
    for d in days:
        if d["date"] == date:
            slot = d
            break
    if slot is None:
        slot = {"date": date, "saved_at": time.time(), "messages": []}
        days.append(slot)
        days.sort(key=lambda x: x["date"])

    # ⚠ 疯话闸：单天封顶。正常一天远不到 400 条，超了只可能是异常滚雪球。
    room = max(0, ARCHIVE_MAX_MSGS - len(slot["messages"]))
    if room <= 0:
        return 0
    slot["messages"].extend(keep[:room])
    slot["saved_at"] = time.time()

    # ⚠ `ARCHIVE_MAX_DAYS=0` = **不限**（她要的是"不删"）；>0 才丢最早的。
    if ARCHIVE_MAX_DAYS and len(days) > ARCHIVE_MAX_DAYS:
        data["days"] = days[-ARCHIVE_MAX_DAYS:]

    return len(keep[:room]) if save(user_id, data) else 0
