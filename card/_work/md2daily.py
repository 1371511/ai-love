# -*- coding: utf-8 -*-
"""
生成 `card/daily_pool.json` —— 「她问起你今天怎么过的」用的日常池。

⭐ 池子的**唯一真相源 = 她审过的清单** `F:\\workB\\JOB\\祁煜日常-纳入扩写清单.md`（162 条）。
   不靠规则重算 —— 重算出来是 171 条，跟她逐条审过的那份对不齐（短句判定口径不同）。
   ⇒ 这里解析清单拿到正文，再回 `qzone_pool.json` 按正文取 id / cat。

排除（在她那份之后叠加的）：
  - 邀请/祈使句 1 条：「来一局紧张又刺激的消除小游戏吧！」——
    不适合当「我今天做了什么」的回答（她 2026-09-24 02:22 定的）。

⚠ 图一律不带（她 2026-09-24 03:47 定的）：问答场景发图会打断对话节奏。
⚠ 正文**一字不改**（含 emoji）——「照原话说」是最高原则。

产物 schema：
  {"schema":1, "count":N, "entries":[{"id","cat","text"}]}

写盘：tmp + os.replace。纪律：遇对不上的正文**直接报错停下**，绝不静默丢弃。
"""
import io
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
CARD_DIR = os.path.dirname(HERE)

LIST_MD = r"F:\workB\JOB\祁煜日常-纳入扩写清单.md"
POOL_JSON = os.path.join(CARD_DIR, "qzone_pool.json")
OUT_JSON = os.path.join(CARD_DIR, "daily_pool.json")
REPORT = os.path.join(CARD_DIR, "_work", "_md2daily_report.txt")

# 邀请/祈使句：不适合当「我今天做了什么」的回答
BAN_EXACT = {
    "来一局紧张又刺激的消除小游戏吧！",
}

ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(\d+)\s*\|")
SEC_RE = re.compile(r"^###\s+(.+?)（(\d+)\s*条）")
PLACE_RE = re.compile(r"[\[【][^\]】]*[\]】]")


# ⚠⭐ 排版清洗（2026-09-24 真机冒烟暴露的冲突）：
#   池子原句里有 30 条带 emoji、若干条带 ASCII 双引号，而人设卡的【不许这么写】
#   明写着「不写 emoji」「不用 ASCII 双引号」（会被原样注入）。日常问答又要求
#   **照原话说** ⇒ 模型照抄就跟禁令打架（实测回出「…也不过如此😎」「号称"分手催化剂"…」）。
#   ⇒ 裁断：**改的是排版符号，不是内容** ——
#       · emoji 一律剥掉（要表情就走表情包那套，不由原句带出来）
#       · ASCII 双引号成对换成「」，孤立的直接去掉
#     全角引号（“”）本来就是允许的，不动。
EMOJI_RANGES = [(0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
                (0x2190, 0x21FF), (0xFE00, 0xFE0F)]
PAIR_QUOTE_RE = re.compile(r'"([^"]*)"')


def _is_emoji(c):
    return any(lo <= ord(c) <= hi for lo, hi in EMOJI_RANGES)


def clean_text(s):
    """剥 emoji + ASCII 双引号 → 「」。返回 (清洗后, 剥掉几个 emoji, 换了几处引号)"""
    t = s or ""
    n_e = sum(1 for c in t if _is_emoji(c))
    t = "".join(c for c in t if not _is_emoji(c))
    n_q = len(PAIR_QUOTE_RE.findall(t))
    t = PAIR_QUOTE_RE.sub(lambda m: "「%s」" % m.group(1), t)
    t = t.replace('"', "")            # 落单的引号直接去掉
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t, n_e, n_q


def norm(s):
    """比较用的归一化：去表情占位符、各类引号、空白

    ⚠ md 表格里正文自带的 `|` 会写成 `\\|`（否则表格会断列），
      而池子 JSON 里是单个 `|` ⇒ 不还原就永远对不上（真踩到：过山车那条）。
    """
    s = (s or "").replace("\\|", "|")
    s = PLACE_RE.sub("", s)
    for ch in "\"'“”‘’「」『』":
        s = s.replace(ch, "")
    return re.sub(r"\s+", "", s)


def parse_list():
    """从她审过的清单 md 里取正文（按 `### 类目（N 条）` 下面的表格第 2 列）"""
    txt = io.open(LIST_MD, encoding="utf-8").read()
    lines = txt.splitlines()
    # 只取「## 四、纳入清单（按类目）」之后的部分
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and "纳入清单" in ln:
            start = i
            break
    if start is None:
        raise SystemExit("FAIL 清单 md 里找不到「纳入清单」那一节")
    out, cur, want = [], None, None
    for ln in lines[start:]:
        m = SEC_RE.match(ln.strip())
        if m:
            cur, want = m.group(1).strip(), int(m.group(2))
            continue
        if cur is None:
            continue
        if ln.startswith("## "):
            cur = None
            continue
        m = ROW_RE.match(ln.strip())
        if m:
            out.append({"cat": cur, "text": m.group(2).strip()})
    return out, want


def main():
    items, _ = parse_list()
    pool = json.load(io.open(POOL_JSON, encoding="utf-8"))
    ents = pool.get("entries") or []
    by_norm = {}
    for e in ents:
        by_norm.setdefault(norm(e.get("text")), e)

    L = []
    A = L.append
    A("清单解析到 %d 条" % len(items))
    if len(items) != 162:
        A("⚠ 清单条数是 %d，不是 162 —— 解析可能有问题，先看这里" % len(items))

    kept, missed, banned, cleaned = [], [], [], []
    for it in items:
        t = it["text"]
        if t in BAN_EXACT:
            banned.append(it)
            continue
        e = by_norm.get(norm(t))
        if e is None:
            missed.append(it)
            continue
        raw_text = e.get("text") or ""
        ct, n_e, n_q = clean_text(raw_text)
        if n_e or n_q:
            cleaned.append((it["cat"], raw_text, ct, n_e, n_q))
        kept.append({"id": e.get("id"), "cat": it["cat"], "text": ct})

    A("对上池子 %d 条 / 对不上 %d 条 / 按邀请句排除 %d 条" % (len(kept), len(missed), len(banned)))
    A("")
    if missed:
        A("== 对不上池子的（要逐条看，不能静默丢）==")
        for it in missed:
            A("  [%s] %s" % (it["cat"], it["text"]))
        A("")
    if banned:
        A("== 排除的邀请句 ==")
        for it in banned:
            A("  [%s] %s" % (it["cat"], it["text"]))
        A("")

    A("")
    A("== 排版清洗（剥 emoji / ASCII 双引号→「」）共 %d 条 ==" % len(cleaned))
    for cat_, a, b, n_e, n_q in cleaned:
        A("  [%s] %s  ⇒  %s   (emoji %d, 引号 %d)" % (cat_, a, b, n_e, n_q))
    A("")

    # 报一下：带「用户」占位符的（要换成她的称呼）、原本带图的（已不带）
    with_user = [k for k in kept if "用户" in (k["text"] or "")]
    img = 0
    for k in kept:
        e = by_norm.get(norm(k["text"]))
        if e and (e.get("images") or []):
            img += 1
    A("含「用户」占位符（发送前要换成她的称呼）：%d 条" % len(with_user))
    for k in with_user[:10]:
        A("  %s" % k["text"])
    A("原本带图（已按她定的不带图处理）：%d 条" % img)
    A("")
    cats = {}
    for k in kept:
        cats[k["cat"]] = cats.get(k["cat"], 0) + 1
    A("== 最终池子类目分布 ==")
    for k, v in sorted(cats.items(), key=lambda x: -x[1]):
        A("  %-12s %d" % (k, v))
    A("")
    A("最终条数：%d" % len(kept))

    if missed:
        A("")
        A("RESULT: STOPPED（有对不上的条目，先解决再生成）")
        io.open(REPORT, "w", encoding="utf-8", newline="").write("\r\n".join(L) + "\r\n")
        sys.exit(2)

    # ⭐ 收口自检：清洗完还留着 emoji 或 ASCII 引号 ⇒ 说明 clean_text 漏了，直接停下
    bad_final = [k for k in kept
                 if any(_is_emoji(c) for c in (k["text"] or "")) or '"' in (k["text"] or "")]
    if bad_final:
        A("")
        A("RESULT: STOPPED —— 清洗后仍有 %d 条带 emoji / ASCII 引号：" % len(bad_final))
        for k in bad_final[:10]:
            A("   %s" % k["text"])
        io.open(REPORT, "w", encoding="utf-8", newline="").write("\r\n".join(L) + "\r\n")
        sys.exit(2)
    A("")
    A("清洗自检：池子里已无 emoji、无 ASCII 双引号 ✅")

    data = {"schema": 1, "count": len(kept), "entries": kept}
    tmp = OUT_JSON + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT_JSON)
    A("")
    A("RESULT: OK -> %s" % OUT_JSON)

    with io.open(REPORT, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(L) + "\r\n")


if __name__ == "__main__":
    main()
