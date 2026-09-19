# -*- coding: utf-8 -*-
"""
祁煜「主动发朋友圈」S2 — 语料生成器（**本机跑一次**）。

        E:\\JOB\\朋友圈\\*.txt                     素材原文（只读，256 篇）
      ＋ card\\qzone_emoji_map.md                【黄豆豆：X】 → emoji（28 条）
      ＋ card\\qzone_hold.md                    暂缓名单（19 篇）
      ＋ card\\qzone_images\\                    配图（只做存在性对账，不搬文件）
                        ↓
        card\\qzone_pool.json                   产物（进仓库；服务器只读它，不跑本脚本）

⚠⚠ 四条铁律（别改）：
  1. 正文**只取首行** ⇒ **剥掉 `祁煜：` 前缀** ⇒ **`◇评论` 段整段丢弃**。
     全库 232/256 篇带评论段（里面是 `用户：…` / `祁煜 回复 用户：…` 的对话），
     带进去 = 发一整段聊天记录。
  2. 「用户」/`@用户` → **她的称呼** 这一步**不在这里做**（每人称呼不同），留到发送时。
  3. 只收**祁煜本人**发的（首行以 `祁煜` 开头）；「互动」整个目录排除（发布者是别人）。
  4. **md 是源、JSON 是产物** —— 改口径去改 md，改完重跑本脚本，**绝不手改 JSON**。

跑法（本机）：python card\\_work\\md2qzone.py
"""

import io
import json
import os
import re
import sys
import time

# ---- 素材原文（本机专属路径；服务器上不需要这个文件）----
CORPUS = r"E:\JOB\朋友圈"

# ---- 路径：本文件在 card\_work\ 下，产物与三个源表都在上一层 card\ ----
HERE = os.path.dirname(os.path.abspath(__file__))      # …\card\_work
CARD = os.path.dirname(HERE)                            # …\card
ROOT = os.path.dirname(CARD)                            # 项目根
EMOJI_MD = os.path.join(CARD, "qzone_emoji_map.md")
HOLD_MD = os.path.join(CARD, "qzone_hold.md")
IMAGES_DIR = os.path.join(CARD, "qzone_images")
OUT_JSON = os.path.join(CARD, "qzone_pool.json")
OUT_TXT = os.path.join(HERE, "_md2qzone_report.txt")

# ---- 解析用正则 ----
IMG_RE = re.compile(r"\[图片[：:]\s*([^\]]+?)\s*\]")                 # 配图
EXTRA_RE = re.compile(r"\[(链接|视频)[：:]\s*([^\]]*?)\s*\]")        # 链接 / 视频 ⇒ S2 跳过
BRACKET_RE = re.compile(r"【([^】]*)】")                            # 【黄豆豆：X】/【A|B】
PREFIX_RE = re.compile(r"^祁煜\s*[：:]\s*")                         # 首行前缀
EMO_PREFIX_RE = re.compile(r"^黄豆豆\s*[：:]\s*")

# 评论段的起点标记：从这里往后一律不要
COMMENT_MARK = "◇"

# hold 名单只认这几种开头的 id 行（防止把说明段/附录的 bullet 当 id —— 第一版就栽在这）
HOLD_PREFIXES = ("活动/", "日常/", "其他/", "剧情/")

# 🎂 生日专项：按**语料文件名**自动识别（随时可撤 —— 改口径只改这里）
#   为什么不能留在普通随机池：随机排期 ≈ 每 2~3 天一条，撞到 4 月某天发「生日快乐」
#   她一眼就知道发错了 ⇒ 生日篇目必须**按日期触发**。
#   ⚠ 判据是文件名里的「祁煜生日」/「玩家生日」，**正文不判** —— 正文里写「生日」的日常篇目很多。
BDAY_TAGS = (("祁煜生日", "rafayel"), ("玩家生日", "player"))


# ============================================================
#  源表 1：标记 → emoji
# ============================================================

def load_emoji_map(path):
    """
    读 `## 机器可读` 小节里的 `- <标记名>：<emoji>`。

    ⚠ 只在那个小节里认，且**名字里不许有 `*` / 空格 / 反引号** ——
      因为文件后面「说明」段的 bullet 也是 `- ` 开头
      （如 `- **可发池 223 篇里出现过的标记 = 28 种**…`），不设闸会被当成映射。
    """
    table = {}
    in_sec = False
    for raw in io.open(path, "r", encoding="utf-8"):
        line = raw.rstrip("\r\n")
        s = line.strip()
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
        name, _, val = body.partition("：")
        name, val = name.strip(), val.strip()
        if not name or not val:
            continue
        if ("*" in name) or (" " in name) or ("`" in name):
            continue
        table[name] = val
    return table


# ============================================================
#  源表 2：暂缓发布（hold）名单
# ============================================================

def load_hold(path):
    """
    读 `card\\qzone_hold.md`，返回 {篇目 id}。

    ⚠ 读法（与表头写的一致）：**只认行首 `- ` 且去前缀后以 `活动/`·`日常/`·`其他/`·`剧情/` 之一开头**的行；
      其余（表格、`* ` 说明、`# ` 注释、`>` 引用）一律忽略。
      ⇒ 表里的**说明段与附录必须用 `* ` 起头**，用 `- ` 会被当成额外 hold。
    """
    ids = set()
    for raw in io.open(path, "r", encoding="utf-8"):
        s = raw.strip()
        if not s.startswith("- "):
            continue
        body = s[2:].strip()
        if body.startswith(HOLD_PREFIXES):
            ids.add(body)
    return ids


# ============================================================
#  正文清理
# ============================================================

def apply_emoji(text, emap, warns):
    """
    【黄豆豆：X】 → emoji。一个 `【】` 里可以塞多个标记、用 `|` 分隔
    （如 `【黄豆豆：奋斗|黄豆豆：胜利】`，还可能重复同名）⇒ 按 `|` 拆开**逐个替换、不折叠**。

    ⚠ 兜底（跟 `qzone_emoji_map.md` 写的一致）：**任一标记没映射到 ⇒ 把 `【…】` 整段删掉**
      —— 宁可不发，也别让对方看到一对中括号 + 文字。
    """
    def _sub(m):
        inner = m.group(1)
        parts = [p.strip() for p in inner.split("|") if p.strip()]
        if not parts:
            warns.append("空标记【】已删")
            return ""
        out = []
        for p in parts:
            if not EMO_PREFIX_RE.match(p):
                bears = p[:12]
                warns.append("非黄豆豆标记 %r ⇒ 整段删" % bears)
                return ""
            name = EMO_PREFIX_RE.sub("", p).strip()
            if name not in emap:
                warns.append("未映射标记 %r ⇒ 整段删" % name)
                return ""
            out.append(emap[name])
        return "".join(out)

    return BRACKET_RE.sub(_sub, text)


def build_entry(raw, cid, cat):
    """
    原文 → 池子条目。返回 (entry, warns)。
    ⚠ 正文取值铁律就在这里：只取**首行**、剥 `祁煜：`、`◇` 之后全丢。
    """
    warns = []
    head = raw.split(COMMENT_MARK, 1)[0]          # 评论段整段丢弃

    first = ""
    for ln in head.splitlines():
        if ln.strip():
            first = ln.strip()
            break

    if not first:
        return None, ["首行为空（跳过）"]
    if not first.startswith("祁煜"):
        return None, ["首行不以「祁煜」开头（跳过）：%r" % first[:24]]

    text = PREFIX_RE.sub("", first).strip()
    if not text:
        return None, ["剥掉前缀后正文为空（跳过）"]

    images = []
    for name in IMG_RE.findall(head):
        name = name.strip()
        if name:
            images.append("card/qzone_images/" + name)

    extra = ""
    m = EXTRA_RE.search(head)
    if m:
        extra = "%s：%s" % (m.group(1), m.group(2))

    return {
        "id": cid,
        "cat": cat,
        "text": text,
        "images": images,
        "extra": extra,
        "hold": "",
        "bday": "",
    }, warns


# ============================================================
#  主流程
# ============================================================

def main():
    if not os.path.isdir(CORPUS):
        sys.exit("❌ 找不到语料目录：%s" % CORPUS)
    if not os.path.isfile(EMOJI_MD):
        sys.exit("❌ 找不到 %s" % EMOJI_MD)
    if not os.path.isfile(HOLD_MD):
        sys.exit("❌ 找不到 %s" % HOLD_MD)

    emap = load_emoji_map(EMOJI_MD)
    hold_ids = load_hold(HOLD_MD)

    L = []
    L.append("=== md2qzone 生成报告 ===")
    L.append("emoji 映射载入 = %d 条" % len(emap))
    L.append("hold 名单载入 = %d 条" % len(hold_ids))
    L.append("")

    entries = []
    warns = []
    n_all = n_inter = n_bad = 0

    for root, dirs, files in os.walk(CORPUS):
        dirs[:] = [d for d in dirs if not d.startswith("_")]
        for fn in sorted(files):
            if not fn.lower().endswith(".txt") or fn.startswith("_"):
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, CORPUS).replace("\\", "/")
            n_all += 1

            if rel.split("/")[0] == "互动":           # 发布者不是祁煜 ⇒ 整目录排除
                n_inter += 1
                continue

            cid = os.path.splitext(rel)[0]
            cat = os.path.dirname(rel).replace("\\", "/")

            raw = io.open(p, "rb").read().decode("utf-8-sig").replace("\ufeff", "")
            entry, w = build_entry(raw, cid, cat)
            if w:
                for x in w:
                    warns.append("%s :: %s" % (cid, x))
            if entry is None:
                n_bad += 1
                continue

            # 🎂 生日篇目（按文件名判，不按正文）
            for tag, kind in BDAY_TAGS:
                if tag in fn:
                    entry["bday"] = kind
                    break

            entry["text"] = apply_emoji(entry["text"], emap, warns)
            if not entry["text"].strip():
                warns.append("%s :: emoji 清理后正文为空（跳过）" % cid)
                n_bad += 1
                continue

            if cid in hold_ids:
                entry["hold"] = "hold"
            entries.append(entry)

    entries.sort(key=lambda e: e["id"])

    # ---- 自检 ----
    ids = [e["id"] for e in entries]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    held = [e for e in entries if e["hold"]]
    with_img = [e for e in entries if e["images"]]
    extras = [e for e in entries if e["extra"]]

    on_disk = set(os.listdir(IMAGES_DIR)) if os.path.isdir(IMAGES_DIR) else set()
    miss_img = []
    for e in with_img:
        for rel_img in e["images"]:
            if os.path.basename(rel_img) not in on_disk:
                miss_img.append((e["id"], rel_img, e["hold"]))

    leak = [e["id"] for e in entries
            if ("◇" in e["text"]) or e["text"].startswith("祁煜") or ("用户：" in e["text"])]
    brac = [e["id"] for e in entries if "【" in e["text"]]
    hold_missing = sorted(hold_ids - set(ids))        # 写在表里、池子里查不到的
    hold_unused = sorted({e["id"] for e in entries if e["hold"]} ^ hold_ids)

    # 🎂 生日篇目**双向都不进**普通池：既要从随机候选里剔除，也要单独列表给发圈模块用
    bdays = [e for e in entries if e.get("bday")]
    bday_raf = [e for e in bdays if e["bday"] == "rafayel"]
    bday_me = [e for e in bdays if e["bday"] == "player"]
    pool_after = [e for e in entries if not e["hold"] and not e["bday"]]
    sendable = [e for e in pool_after if not e["extra"]]

    data = {
        "schema": 2,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "corpus_root": CORPUS,
        "count": len(entries),
        "held": len(held),
        "bday": len(bdays),
        "with_image": len(with_img),
        "extra": len(extras),
        "entries": entries,
    }
    io.open(OUT_JSON, "w", encoding="utf-8", newline="\n").write(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n")

    # ---- 报告 ----
    L.append("=== 计数 ===")
    L.append("  扫到 .txt              = %d" % n_all)
    L.append("  其中「互动」目录        = %d（排除）" % n_inter)
    L.append("  构建失败（跳过）        = %d" % n_bad)
    L.append("  ⇒ 入池                 = %d" % len(entries))
    L.append("  ⇒ hold                 = %d" % len(held))
    L.append("  ⇒ 🎂 生日专项（移出普通池）= %d（祁煜 %d / 她的 %d）"
             % (len(bdays), len(bday_raf), len(bday_me)))
    L.append("  ⇒ 可发（去 hold、去生日）= %d" % len(pool_after))
    L.append("  ⇒ S2 实发（再跳 extra） = %d" % len(sendable))
    L.append("")
    L.append("=== 🎂 生日专项清单 ===")
    for e in bdays:
        L.append("  [%s] %-9s img=%d :: %s"
                 % (e["bday"], e["id"], len(e["images"]), e["text"][:52]))
    L.append("")
    L.append("=== 自检（全部应为空/0）===")
    L.append("  重复 id                = %d %s" % (len(dup), dup[:5]))
    L.append("  正文漏进评论段/前缀     = %d %s" % (len(leak), leak[:5]))
    L.append("  正文残留【】            = %d %s" % (len(brac), brac[:5]))
    L.append("  hold 表里查不到的 id    = %d %s" % (len(hold_missing), hold_missing[:5]))
    L.append("  hold 标记与表不一致     = %d %s" % (len(hold_unused), hold_unused[:5]))
    L.append("")
    L.append("=== 配图对账 ===")
    L.append("  可发池带图篇目          = %d" % len([e for e in pool_after if e["images"]]))
    L.append("  缺图处数（含 hold 内）  = %d" % len(miss_img))
    for cid, rel_img, h in miss_img:
        L.append("     [%s] %s <- %s" % ("hold" if h else "!! 未 hold 却缺图", cid, os.path.basename(rel_img)))
    L.append("")
    L.append("=== extra 非空（S2 跳过）===")
    for e in extras:
        L.append("  [%s] %s -> %s" % ("hold" if e["hold"] else "可发", e["id"], e["extra"]))
    L.append("")
    L.append("=== 分类分布（可发池）===")
    dist = {}
    for e in pool_after:
        dist[e["cat"]] = dist.get(e["cat"], 0) + 1
    for k in sorted(dist):
        L.append("  %-16s %d" % (k, dist[k]))
    L.append("")
    L.append("=== warning（%d 条）===" % len(warns))
    for w in warns[:60]:
        L.append("  " + w)
    if len(warns) > 60:
        L.append("  …（还有 %d 条）" % (len(warns) - 60))

    io.open(OUT_TXT, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")
    print("ok  entries=%d hold=%d sendable=%d warns=%d"
          % (len(entries), len(held), len(sendable), len(warns)))


if __name__ == "__main__":
    main()
