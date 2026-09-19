# -*- coding: utf-8 -*-
"""
朋友圈「她在他那条说说底下留了话」⇒ 他跑来私聊找她。

2026-09-20 定，S4（双向互动）在这台服务器上的**落地形态**。

⭐ 为什么不是「在空间里回复她那条评论」：
  原生 NapCat 只能发/删说说，**读不到评论**；装了 qzone-bridge 之后实测依然读不到内容 ——
    · `get_comment_list`  ⇒ 报「tid 不匹配」（**bridge 给的 tid 每次解析都变**，
                             同一条说说第一次 3f73… 第二次 873a…，永远对不上）
    · `get_msg`（详情）    ⇒ retcode 1502 detail unavailable
    · `get_emotion_list`   ⇒ 只有 `cmtnum` 计数，**评论区不嵌内容**
  ⚠ 记住这个坑：**别拿 bridge 的 tid 去反查任何东西** —— 它会漂。
     判据只能用「正文 + 评论条数」。

⇒ 改成：轮询发现某条说说的**评论数涨了** ⇒ 他知道她留了话（但不知道写了什么）
  ⇒ 随机延迟几分钟 ⇒ 主动私聊来找她，她回什么他接什么（私聊链路本来就是通的）。
  真人也常这样跑来问一句，比 AI 味的楼层回复自然得多。

⚠ 分层：本模块只 import `Rafayel_config`，不碰 LLM、不碰 websocket。
⚠ 一切异常都吞掉并降级 —— 她**不能察觉**这一路的存在（锁定口径：不露机器人那一面）。
"""

import json
import os
import random
import time

import requests

from Rafayel_config import (MEMORY_DIR, QZONE_BRIDGE_TIMEOUT, QZONE_BRIDGE_URL,
                            QZONE_CMT_DELAY_MAX, QZONE_CMT_DELAY_MIN,
                            QZONE_CMT_FEED_NUM, QZONE_CMT_HOUR_END,
                            QZONE_CMT_HOUR_START, QZONE_CMT_MAX_PER_DAY)

POSTS_FILE = "_qzone_posts.json"          # 全局：说说正文 → 是谁的那条
CMT_FILE = "_qzone_cmt.json"              # 全局：评论数快照 + 待处理队列


# ============================================================
#  小工具
# ============================================================

def _path(name):
    return os.path.join(MEMORY_DIR, name)


def _load(name, default):
    p = _path(name)
    if not os.path.exists(p):
        return dict(default) if isinstance(default, dict) else default
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else (dict(default) if isinstance(default, dict) else default)
    except Exception:
        return dict(default) if isinstance(default, dict) else default


def _save(name, data):
    os.makedirs(MEMORY_DIR, exist_ok=True)
    p = _path(name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def text_key(text):
    """
    说说的「指纹」—— ⚠ **不能用 tid**（bridge 的 tid 每次都变，见文件头）。

    取正文前 24 个字当键（说说正文是稳定的，tid 不是）。
    """
    s = "".join((text or "").split())
    return s[:24]


# ============================================================
#  发过的说说 → 是谁的那条
# ============================================================

def note_posted(user_id, text):
    """
    发完一条说说记一笔：这条正文是**发给谁**的那条。

    ⚠ 同一篇正文可能发给多个人（去重按用户独立）⇒ 记成 uid 列表。
       扫描时如果一个正文对应多个人 ⇒ **跳过**（分不清是谁留的话，宁可不发也不错发）。
    """
    k = text_key(text)
    if not k:
        return
    d = _load(POSTS_FILE, {"map": {}})
    m = d.get("map") or {}
    uids = list(m.get(k) or [])
    uid = str(user_id)
    if uid not in uids:
        uids.append(uid)
    m[k] = uids[-5:]                      # 只留最近几个，别无限涨
    d["map"] = m
    _save(POSTS_FILE, d)


def owner_of(k):
    """
    这条正文是发给谁的。**唯一**才返回 uid，多个或一个都没有 ⇒ 返回 (None, 原因)。
    """
    d = _load(POSTS_FILE, {"map": {}})
    uids = (d.get("map") or {}).get(k) or []
    if len(uids) == 1:
        return uids[0], "ok"
    if not uids:
        return None, "这条说说不是自动发的（没记录）"
    return None, "同一条正文发给了 %d 个人，分不清是谁留的话" % len(uids)


# ============================================================
#  拉说说列表（qzone-bridge REST）
# ============================================================

def fetch_feeds(uin, num=None):
    """
    拉最近几条说说。返回 (list, err) —— err 非空就是失败了。

    每条形如 {"key": 正文指纹, "content": 正文, "cmtnum": 评论数}。
    ⚠ **只读 cmtnum**，评论内容在这台服务器上拿不到（见文件头）。
    """
    try:
        r = requests.post(
            QZONE_BRIDGE_URL.rstrip("/") + "/get_emotion_list",
            json={"user_id": str(uin), "num": int(num or QZONE_CMT_FEED_NUM)},
            timeout=QZONE_BRIDGE_TIMEOUT,
        )
        d = r.json()
    except Exception as e:
        return [], "bridge 请求失败：%s" % e

    if d.get("status") == "failed" or (d.get("retcode") not in (0, None)):
        return [], "bridge 返回失败：%s" % (d.get("message") or d.get("wording") or d)

    out = []

    def walk(o):
        if isinstance(o, dict):
            content = o.get("content")
            if content and "cmtnum" in o:
                out.append({
                    "key": text_key(content),
                    "content": content,
                    "cmtnum": int(o.get("cmtnum") or 0),
                    # ⚠ tid 每次解析都变，只用于「当次解析批次内」对事件（见文件头）
                    "tid": str(o.get("tid") or ""),
                })
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(d.get("data") if isinstance(d, dict) else d)
    return out, ""


# ============================================================
#  评论数比对
# ============================================================

def _blank_state():
    return {"counts": {}, "pending": [], "date": "", "today": 0}


def load_state():
    return _load(CMT_FILE, _blank_state())


def save_state(st):
    _save(CMT_FILE, st)


def _today(now=None):
    now = now or time.localtime()
    return time.strftime("%Y-%m-%d", now)


def _in_hours(now=None):
    now = now or time.localtime()
    return QZONE_CMT_HOUR_START <= now.tm_hour < QZONE_CMT_HOUR_END


def scan_new_comments(uin, now=None):
    """
    扫一遍：哪条说说的评论数涨了 ⇒ 排进「待去找她」的队列。

    返回 (新增条数, 日志行列表)。
    ⚠ 第一次见到某条说说 ⇒ **只记基线**（不然部署前她留过的评论会让他半夜冒出来）。
    """
    now = now or time.localtime()
    posts, err = fetch_feeds(uin)
    if err:
        return 0, [err]

    st = load_state()
    counts = st.get("counts") or {}
    pending = list(st.get("pending") or [])
    logs, added = [], 0
    known_keys = {p.get("key") for p in pending}

    for p in posts:
        k = p["key"]
        if not k:
            continue
        old = counts.get(k)
        counts[k] = p["cmtnum"]
        if old is None:
            continue                                  # 首次见到：只记基线
        if p["cmtnum"] <= old:
            continue
        if k in known_keys:
            continue                                  # 已经在等了
        uid, why = owner_of(k)
        if not uid:
            logs.append("跳过一条（%s）" % why)
            continue
        due = time.mktime(now) + random.randint(QZONE_CMT_DELAY_MIN * 60,
                                                QZONE_CMT_DELAY_MAX * 60)
        pending.append({"key": k, "uid": uid, "due": due,
                        "content": p["content"], "cmtnum": p["cmtnum"]})
        known_keys.add(k)
        added += 1
        logs.append("有人在他那条说说下留话了：%s（%d → %d）"
                    % (p["content"][:20], old, p["cmtnum"]))

    st["counts"] = counts
    st["pending"] = pending
    save_state(st)
    return added, logs


def take_ready(now=None):
    """
    取出「已经到时间了」的那几条（改天/改状态在调用方处理）。

    返回 list，每条 {"key","uid","content","cmtnum","due"}。
    ⚠ 时段不对就先不取（留到下一次扫描），免得半夜去找她。
    """
    now = now or time.localtime()
    if not _in_hours(now):
        return []

    st = load_state()
    today = _today(now)
    if st.get("date") != today:
        st["date"] = today
        st["today"] = 0
    if int(st.get("today", 0)) >= QZONE_CMT_MAX_PER_DAY:
        save_state(st)
        return []

    pending = list(st.get("pending") or [])
    due_now, keep = [], []
    for p in pending:
        if time.mktime(now) >= float(p.get("due") or 0):
            # ⚠ 隔太久（>1 天）就算了 —— 那种很可能早就聊过了，再提反而怪
            if time.mktime(now) - float(p.get("due") or 0) > 86400:
                continue
            if len(due_now) + int(st.get("today", 0)) < QZONE_CMT_MAX_PER_DAY:
                due_now.append(p)
            else:
                keep.append(p)
        else:
            keep.append(p)

    st["pending"] = keep
    save_state(st)
    return due_now


def mark_done(now=None):
    """调用方真的发出去了 ⇒ 当天计数 +1。"""
    now = now or time.localtime()
    st = load_state()
    today = _today(now)
    if st.get("date") != today:
        st["date"] = today
        st["today"] = 0
    st["today"] = int(st.get("today", 0)) + 1
    save_state(st)


# ============================================================
#  ⭐ 事件订阅（2026-09-20 深夜实测：WS 事件里带评论内容！）
# ============================================================
# bridge 的事件形状（真机抓到的）：
#   {"post_type":"notice", "notice_type":"qzone_comment",
#    "user_id":<她的QQ>, "sender_name":<昵称>, "comment_id":"2",
#    "comment_content":"\\t\\t…什么巧合？ \\t\\t…",   ← 一堆制表符，要清洗
#    "post_uin":<他的QQ>, "post_tid":"873aeeda…"}      ← tid 仍是那一次解析的，会漂

def clean_comment(text):
    """评论正文清洗：掐掉 HTML 带的一堆制表符/换行/首尾空白。"""
    s = (text or "").replace("\\t", " ").replace("\\n", " ")
    s = s.replace("\t", " ").replace("\n", " ").replace("\r", " ")
    return " ".join(s.split()).strip()


def is_known_user(user_id):
    """评论者是不是我们认识的（私聊过的）用户 —— 陌生人不理。"""
    p = os.path.join(MEMORY_DIR, "%s.json" % str(user_id))
    return os.path.isfile(p)


def remembered_tid_content():
    """最近一次拉列表时记下的 tid → 正文（事件里的 post_tid 就拿它来认正文）。"""
    st = load_state()
    return st.get("tids") or {}


def remember_tids(pairs):
    """拉完列表把 tid→正文 存起来（⭐ 只存**最近一次**的解析结果，旧的覆盖掉）。"""
    st = load_state()
    st["tids"] = dict(pairs)
    save_state(st)


def mark_replied(comment_id):
    """这条评论已经回过了 ⇒ 记下来，别回第二次。"""
    if not comment_id:
        return
    st = load_state()
    ids = list(st.get("replied") or [])
    cid = str(comment_id)
    if cid not in ids:
        ids.append(cid)
    st["replied"] = ids[-50:]             # 只留最近 50 条，别无限涨
    save_state(st)


def already_replied(comment_id):
    return str(comment_id) in (load_state().get("replied") or [])


def refresh_baseline(uin):
    """
    事件路径回复成功后把计数基线刷一遍 ——
    ⚠ 否则计数轮询（A 方案那条路）看到 cmtnum 涨了会**重复触发**（他又私聊来问一遍）。
    """
    posts, err = fetch_feeds(uin)
    if err:
        return
    st = load_state()
    counts = st.get("counts") or {}
    tids = {}
    for p in posts:
        counts[p["key"]] = p["cmtnum"]
        if p.get("tid"):
            tids[p["tid"]] = p["content"]
    st["counts"] = counts
    if tids:
        st["tids"] = tids
    save_state(st)


def send_comment(target_uin, target_tid, content):
    """
    在空间里回复她那条评论（走 bridge 的 `send_comment`）。
    返回 (True, "") 或 (False, 原因) —— 失败一律静默降级，不抛异常。
    """
    try:
        r = requests.post(
            QZONE_BRIDGE_URL.rstrip("/") + "/send_comment",
            json={"target_uin": str(target_uin),
                  "target_tid": str(target_tid),
                  "content": content},
            timeout=QZONE_BRIDGE_TIMEOUT,
        )
        d = r.json()
    except Exception as e:
        return False, "bridge 请求失败：%s" % e
    if d.get("status") == "failed" or (d.get("retcode") not in (0, None)):
        return False, "bridge 返回失败：%s" % (d.get("message") or d)
    return True, ""
