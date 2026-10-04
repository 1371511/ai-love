# -*- coding: utf-8 -*-
"""
☎️ 通话记录（2026-10-02 · 她定：「通话记录就以单次通话作为记忆保存，不需要截断压缩」）

**一通电话 = 一条完整记录**，落 `memory/{uid}_calls.json`：

    {"calls": [
        {"id": "1759...", "start_ts": 1759..., "end_ts": 1759...,
         "summarized": false,
         "lines": [{"role": "assistant", "content": "…", "ts": 1759...}, …]},
        …   # 早的在前、新的在后（**升序**，跟 `messages` 一个方向）
    ]}

⭐⭐ 为什么不能继续寄生在 `memory/{uid}.json` 里
------------------------------------------------------------
那边的历史只有 `MAX_HISTORY_TURNS`（12）轮，超了从**头部**裁（`truncate_history()`）；
裁掉的原话虽然进了 `{uid}_archive.json`，但那是**按自然日分块**的另一种结构，
通话页读不出来。⇒ 聊得长的一通电话，开场白（哨兵 `CALL_OPEN`）先被裁
⇒ 通话页扫不到起点 ⇒ 退成「只看最后一句」⇒ **一整通电话在页面上只剩一句**。
⇒ 她要的正是这个：**记录**这一层不许被裁、不许被压。

⚠⭐ 三条边界（跟 `Rafayel_archive` 那套同一个口径，别越线）
------------------------------------------------------------
1. **只存不喂**：本模块**没有**任何「给 prompt 用」的函数。
   引擎侧只在「该给这一通写摘要了」的时候读一次（`pending_call()`），
   读出来的原话进的是**摘要**，不是把它塞回上下文 ——
   原话要是进了 prompt，等于把 12 轮裁剪白做（上下文无限长、前缀缓存全废）。
2. **独立文件**：⚠⚠ 绝不写进 `memory/{uid}.json` —— 那份被 `cm` 缓存
   **整份覆盖**（谁后写谁赢），塞进去会被静默抹掉。跟日记 / 情绪 / 归档一个道理。
3. **只 import config**：不引 `Rafayel_memory`（避免环），也不引任何引擎模块。
   现在碰它的只有 `web/page/call.py`（读 + 写）和 `Rafayel_llm.summarize_call`
   （只读那一通、然后标记已摘要）。

⚠ 谁在碰这个文件（就三处，别再加第四处）：
   · `web/page/call.py` —— **写**：`open_call` / `append_turn`（通话页自己写，她定的），
     以及**读**：`last_call`（通话页字幕）、`load`（历史通话页）；
   · `Rafayel_memory.summarize_call` —— **读** `pending_call`（为了写摘要），
     成功之后 `mark_summarized` 打标记。
   ⇒ 写盘只发生在**网页端**（单写者）；引擎那边只改一个布尔标记。
     ⚠ 哪天 QQ 端也有了通话功能，这条要先重新想清楚（两个进程同时写就会互相覆盖，
       跟 `memory/{uid}.json` 那个 `cm` 覆盖坑是同一类）。
"""
import json
import os
import time

from Rafayel_config import CALLS_KEEP, CALLS_MAX_LINES, mem_dir, mem_path


def _path(user_id):
    return mem_path("calls", str(user_id))


def _blank():
    return {"calls": []}


def _new_id(calls, ts):
    """一通电话的 id = 毫秒时间戳；真撞了（同毫秒拨通两次）挂个尾巴。"""
    cid = str(int(float(ts) * 1000))
    while any(c.get("id") == cid for c in calls):
        cid += "x"
    return cid


def _norm_call(d):
    """
    一条通话记录 → 规整过的 dict；**不合格返回 `None`**（不是抛）。

    ⚠ 类型闸（全站红线，见 `MEMORY.md`）：先看 `isinstance` 再看内容 ——
       文件被手改成 list / str 时不能去迭代它、也不能 `.get()`。
    ⚠ 落盘的数据里只留 `role` / `content` / `ts` 三个键：通话记录是给她看的，
       别把引擎内部字段（`name` 之类）沉淀进文件。
    ⚠⚠ **「一句都没说」的通话要留着**，别当垃圾条目滤掉：
       那是「拨出去了、那头没接上」（她那句界碑发了、他的回应没回来），
       历史通话页有专门的一支给它显示「未接通」。删了就是篡改记录。
       ⇒ 合格判据只看**有没有起点**（`start_ts`，退一步用第一句的 `ts`），
         不看 `lines` 空不空。
    """
    if not isinstance(d, dict):
        return None
    lines = []
    for m in (d.get("lines") or []):
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            ts = m.get("ts")
            lines.append({"role": role, "content": content,
                          "ts": ts if isinstance(ts, (int, float)) else None})
    head = d.get("start_ts")
    if not isinstance(head, (int, float)):
        head = lines[0]["ts"] if lines else None
    if not isinstance(head, (int, float)):
        return None                      # 起点和内容都没有 ⇒ 这条是垃圾，丢掉
    tail = d.get("end_ts")
    if not isinstance(tail, (int, float)):
        tail = lines[-1]["ts"] if lines else head
    # 🎲 开场白那一句（2026-10-03 随机话题池上线后加）。
    #   ⚠ **老记录没有这个键**（界碑当年不入档）⇒ 一律回落空串，
    #     调用方拿到空串就用 `CALL_HELLO` 顶上，**旧通话不会因为缺字段而读不出来**。
    hello = d.get("hello")
    if not isinstance(hello, str) or not hello.strip():
        hello = ""
    return {"id": str(d.get("id") or _new_id([], head)),
            "start_ts": head, "end_ts": tail, "hello": hello,
            "summarized": bool(d.get("summarized")),
            "lines": lines}


def load(user_id):
    """
    读整份通话记录，返回 `{"calls": [ … 早的在前 … ]}`。

    ⚠ 读坏了返回空档（**绝不抛**）：这份文件是给她翻着看的，
       挂了顶多是「历史通话页空了」，绝不能拖垮对话本身。
    """
    p = _path(user_id)
    if not os.path.exists(p):
        return _blank()
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print("⚠️ 通话记录读取失败（当空档处理）：%s" % e)
        return _blank()
    if not isinstance(raw, dict):
        return _blank()
    calls = raw.get("calls")
    if not isinstance(calls, list):
        return _blank()
    out = []
    for d in calls:
        c = _norm_call(d)
        if c is not None:
            out.append(c)
    return {"calls": out}


def save(user_id, data):
    """原子写（tmp + replace，跟 `Rafayel_archive` / `Rafayel_mood` 同一套）。"""
    try:
        os.makedirs(mem_dir(), exist_ok=True)
        p = _path(user_id)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception as e:
        print("⚠️ 通话记录落盘失败（不影响通话）：%s" % e)
        return False


def _trim(data):
    """总量上限。⚠ `CALLS_KEEP=0` = **不限**（她要的是"不删"）；>0 才丢最早的。"""
    if CALLS_KEEP and len(data["calls"]) > CALLS_KEEP:
        data["calls"] = data["calls"][-CALLS_KEEP:]


def open_call(user_id, ts=None, hello=""):
    """
    新开一通电话（界碑：她那句「（按下接听）…」发出的那一刻）。返回新那通的 id。

    ⚠⭐ 界碑那句**不入 `lines`** —— 它只是「一通电话从这儿开始」的记号，
      真实内容从他那句回应开始。跟通话页字幕 / 历史通话页的
      「`start = i + 1`」是同一个口径：不然每一通开头都挂着一条
      「（按下接听）喂？」，读起来像在刷屏。
    🎲 但 2026-10-03 起开场白是**随机话题**（不再是固定那一句）⇒ 单独存进
      `hello` 字段：界碑那句本身仍不进 `lines`（免得刷屏），但**必须留得下来** ——
      `_call_ctx` 拼插话上下文时要用**这一通真实的那句**当首句，
      拿固定 `CALL_HELLO` 顶上就是给模型一句假界碑。
      ⚠ 老记录没有这个键 ⇒ `_norm_call` 回落空串，调用方用 `CALL_HELLO` 顶。
    ⚠ `start_ts` = 这一刻 ⇒ 通话页右上角那个计时器就是从它开始数的。
    ⚠ 这一通**说完之前**也会落盘（`lines` 可能是空的）：她要是拨出去那边没接上，
      历史通话页会给它显示「未接通」—— 那是真发生过的一次拨号，别抹掉。
    """
    ts = float(ts if ts is not None else time.time())
    hello = str(hello or "").strip()
    data = load(user_id)
    cid = _new_id(data["calls"], ts)
    data["calls"].append({"id": cid, "start_ts": ts, "end_ts": ts,
                          "hello": hello,
                          "summarized": False, "lines": []})
    _trim(data)
    if not save(user_id, data):
        return ""
    return cid


def append_turn(user_id, role, content, ts=None):
    """
    追加一句到**最后一通**电话里（一通都没有就先开一通）。返回是否写盘。

    ⭐⭐ 谁是「一通电话的开头」由调用方定（`web/page/call.py` 用
       `base._is_call_open()` 判 —— 判据本体在 `Rafayel_config.CALL_OPEN`，
       引擎和网页端**共用同一份**）。这里不自己再判一遍：
       多一处判据 = 将来改文案时有一处不会跟着动。
    ⚠ `end_ts` 每次追加都跟着往后推 ⇒ 它天然就是「这通电话最后一句的时刻」。
       写摘要/日记时拿它当时间坐标（`summarize_call` 用它当日记的 `ts`），
       这样**补摘**一通旧电话时，日记也会落在**那天**，不会跑到今天。
    ⚠ 空内容 / 怪角色一律不写（引擎报错时 `reply` 可能是空串）。
    """
    if role not in ("user", "assistant"):
        return False
    content = (content or "").strip()
    if not content:
        return False
    ts = float(ts if ts is not None else time.time())

    data = load(user_id)
    calls = data["calls"]
    if not calls:
        # 🎲 兜底开的那通也要带界碑 —— 她这句话本身就是开场白
        #   （老记录/异常路径才会走到这儿，界碑别留空，否则 `_call_ctx` 拿到空串）。
        # ⚠⚠ 2026-10-05 修：这里原来写的是 `text` —— **未定义的名字** ⇒ 一执行就
        #   `NameError`（`call_selftest` 抓到的）。本函数签名是
        #   `append_turn(user_id, role, content, ts)`，本地变量只有 `content`。
        #   ⇒ 这条分支只在「一通记录都没有、却先来了一句台词」时才走到（异常路径 /
        #     老记录），所以线上没炸过；但它确实是**执行必崩**的。
        calls.append({"id": _new_id(calls, ts), "start_ts": ts, "end_ts": ts,
                      "hello": (content if role == "user" else "").strip(),
                      "summarized": False, "lines": []})
    cur = calls[-1]
    # ⚠ 疯话闸（`CALLS_MAX_LINES=400`）：这**不是「截断」**—— 正常一通电话远不到，
    #   到了只可能是异常滚雪球（模型卡住来回刷）。撞上就**不写**并打日志，
    #   宁可少一句，也不让文件无限涨。
    if len(cur["lines"]) >= CALLS_MAX_LINES:
        print("⚠️ 通话记录撞上单通上限 %d 条，本条不再记录" % CALLS_MAX_LINES)
        return False
    cur["lines"].append({"role": role, "content": content, "ts": ts})
    cur["end_ts"] = ts
    return save(user_id, data)


def last_call(user_id):
    """最后一通（可能正在通话中）；一通都没有 ⇒ `None`。给通话页渲染用。"""
    calls = load(user_id)["calls"]
    return calls[-1] if calls else None


def pending_call(user_id, call_id=None):
    """
    该写摘要的那一通。没有 ⇒ `None`。

    `call_id` 给定 ⇒ **只看那一通**（是「有内容、还没摘要」就返回它，否则 `None`）。
    `call_id` 不给 ⇒ 返回**最早**的一通「有内容、还没摘要」。

    ⭐⭐ 为什么默认是**最早**（FIFO）而不是最新：
       ① 补摘（她挂断后隔了几天才再拨）要能往前捞，不能只认最后一通；
       ② **摘要进 `long_term_summary` 是按顺序追加的** —— 乱序摘会让他的长期记忆
          时间线错乱（先记了今天这通，再补上周那通，读起来像倒带）。
          FIFO 天然保证「先打的电话先被摘要」。
    ⭐⭐ 为什么要能**指定 id**：摘要是异步跑的，跑起来的时候列表末尾可能已经
       又开了一通新的（她挂断完立刻重拨）⇒ 「最早的那通」和「刚挂的那通」不是一个。
       调用方（`web/page/call.py`）用它把目标钉死，**不靠猜**。
    ⚠ 按「有 `lines`」过滤：刚拨通还没说上话的那通（只有界碑）不算，
      不然会把一通正在进行的电话提前摘掉。
    """
    for c in load(user_id)["calls"]:
        if c["summarized"] or not c["lines"]:
            continue
        if call_id is None or c["id"] == call_id:
            return c
    return None


def pending_ids(user_id, exclude_last=False):
    """
    该写摘要的**全部**通话 id（早的在前）。给调用方一次把欠账清掉。

    ⚠ `exclude_last=True` ⇒ 不看最后一通 —— **「一通新的电话刚拨通/正在说」**那个
      场景专用：那时候最后一通是**活着的**，摘它就是把人家说到一半的电话结掉。
      `/call/connect`（新一通刚开始）走这条；`/call/end`（刚挂断）不排除 ——
      挂断那一刻列表末尾那通已经结束了，正该是它。
    """
    calls = load(user_id)["calls"]
    if exclude_last and calls:
        calls = calls[:-1]
    return [c["id"] for c in calls if not c["summarized"] and c["lines"]]


def mark_summarized(user_id, call_id):
    """
    把某一通标成「已摘要」（幂等）。返回是否真改了。

    ⚠ 按 `id` 定位，**不按位置**：摘要是异步跑的（后台线程），
      跑完时列表末尾可能已经又开了一通新的，按下标回去改会改错人。
    ⚠ 幂等是必须的：挂断钮能被连点、`/call/connect` 能被刷新触发两次
      ⇒ 没有这个标记就会重复调 LLM、重复往长期记忆里抄同一通电话。
    """
    if not call_id:
        return False
    data = load(user_id)
    hit = False
    for c in data["calls"]:
        if c["id"] == call_id and not c["summarized"]:
            c["summarized"] = True
            hit = True
    return save(user_id, data) if hit else False
