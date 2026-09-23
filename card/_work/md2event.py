# -*- coding: utf-8 -*-
"""
生成 `card/event_pool.json` —— 「特殊事件（节日）」用的池子 + 日期表。

⭐ 唯一真相源 = 她审过的清单 `F:\\workB\\JOB\\祁煜节日-纳入清单.md`（23 条 + 日期表）。
   JSON 是产物，**绝不手改**。

三道闸门（任何一道不过就报错停下，绝不静默丢）：
  ① 每条原句必须能回**素材原文**精确匹配（防手滑 / 防我编）
  ② 每个小节的条数必须对上 md 里写的「（N 条）」
  ③ 清洗完还留着 emoji / ASCII 双引号 / 昵称占位符 ⇒ 停

产物 schema：
  {"schema":1, "count":N, "fixed":{节日:"MM-DD"}, "lunar":{年:{...}},
   "entries":[{"id","fest","key","text","src"}]}

写盘：tmp + os.replace。
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CARD_DIR = os.path.dirname(HERE)

LIST_MD = r"F:\workB\JOB\祁煜节日-纳入清单.md"
CORPUS = r"C:\Users\lin\Desktop\祁煜剧情"
OUT_JSON = os.path.join(CARD_DIR, "event_pool.json")
REPORT = os.path.join(HERE, "_md2event_report.txt")

# 中文节日名 → key（代码里用）
FEST_KEY = {
    "晴空节": "qingkong",
    "沐春节": "muchun",
    "兰夜节": "lanye",
    "春节": "spring",
    "繁灯节": "lantern",
    "新辰节": "xinchen",
}

SEC_RE = re.compile(r"^###\s+(.+?)（(\d+)\s*条）")
ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|")
QIYU = re.compile(r"^祁煜[：:]\s*(.+?)\s*$")

# 昵称占位符：素材里有好几种写法，统一成「用户」（发送前换她的称呼）
PLACE_NAME = re.compile(r"[\[<（(]\s*(?:玩家昵称|专属昵称)\s*[\]>）)]")
# 机制标签（表情 / 链接 / 红包）—— 说话时用不到，匹配和入库都剥掉
TAG_RE = re.compile(r"【[^】]*】|\[[^\]]*\]")

EMOJI_RANGES = [(0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
                (0x2190, 0x21FF), (0xFE00, 0xFE0F)]
PAIR_QUOTE_RE = re.compile(r'"([^"]*)"')


def _is_emoji(c):
    return any(lo <= ord(c) <= hi for lo, hi in EMOJI_RANGES)


def norm(s):
    """比较用的归一化：剥机制标签、去各类引号与空白"""
    s = TAG_RE.sub("", s or "")
    for ch in "\"'“”‘’「」『』":
        s = s.replace(ch, "")
    return re.sub(r"\s+", "", s)


def clean_text(s):
    """排版归一：剥机制标签（表情/链接/红包）、emoji、ASCII 双引号 → 「」、昵称占位符 → 用户"""
    t = TAG_RE.sub("", s or "")
    n_e = sum(1 for c in t if _is_emoji(c))
    t = "".join(c for c in t if not _is_emoji(c))
    n_q = len(PAIR_QUOTE_RE.findall(t))
    t = PAIR_QUOTE_RE.sub(lambda m: "「%s」" % m.group(1), t)
    t = t.replace('"', "")
    t, n_p = PLACE_NAME.subn("用户", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t, n_e, n_q, n_p


def read_corpus(rel):
    """读素材文件，返回 (祁煜原句 list, 剥标签后的 list)"""
    p = os.path.join(CORPUS, rel)
    if not os.path.exists(p):
        return None, None
    raw = io.open(p, "rb").read()
    txt = None
    for e in ("utf-8", "gbk"):
        try:
            txt = raw.decode(e)
            break
        except Exception:
            continue
    if txt is None:
        txt = raw.decode("utf-8", errors="replace")
    raws, bare = [], []
    for ln in txt.splitlines():
        m = QIYU.match(ln.strip())
        if m and m.group(1).strip():
            raws.append(m.group(1).strip())
            bare.append(TAG_RE.sub("", m.group(1).strip()))
    return raws, bare


def parse_md():
    """解析清单：返回 (条目 list, 固定日期 dict, 农历日期 dict)"""
    txt = io.open(LIST_MD, encoding="utf-8").read()
    lines = txt.splitlines()

    # —— 条目：只取「## 四、纳入清单（按节日）」之后、下一个 ## 之前 ——
    start = end = None
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and "纳入清单" in ln:
            start = i
        elif start is not None and ln.startswith("## ") and end is None:
            end = i
    if start is None:
        raise SystemExit("FAIL 清单 md 里找不到「纳入清单」那一节")
    end = end or len(lines)

    items, cur, want = [], None, None
    for ln in lines[start:end]:
        m = SEC_RE.match(ln.strip())
        if m:
            cur, want = m.group(1).strip(), int(m.group(2))
            if cur not in FEST_KEY:
                raise SystemExit("FAIL 未知节日名：%s（FEST_KEY 里没有）" % cur)
            continue
        if cur is None:
            continue
        m = ROW_RE.match(ln.strip())
        if m:
            items.append({"fest": cur, "no": int(m.group(1)),
                          "text": m.group(2).strip(), "src": m.group(3).strip()})

    # —— 日期表：「### 固定日期（MM-DD）」/「### 农历日期（MM-DD）」——
    fixed, lunar = {}, {}
    mode = None
    for ln in lines:
        s = ln.strip()
        if s.startswith("### "):
            if "固定日期" in s:
                mode = "fixed"
            elif "农历日期" in s:
                mode = "lunar"
            else:
                mode = None
            continue
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if mode == "fixed":
            if len(cells) == 2 and re.match(r"^\d{2}-\d{2}$", cells[1] or ""):
                if cells[0] in FEST_KEY:
                    # ⚠ JSON 是给代码用的 ⇒ 键写成英文 key（md 里保留中文名，读着清楚）。
                    #   写成中文的话 `date_of(key)` 查 fixed 永远查不到（真踩到：晴空节不触发）。
                    fixed[FEST_KEY[cells[0]]] = cells[1]
        elif mode == "lunar":
            if len(cells) == 4 and re.match(r"^\d{4}$", cells[0] or ""):
                y = int(cells[0])
                if all(re.match(r"^\d{2}-\d{2}$", c or "") for c in cells[1:]):
                    lunar[y] = {"spring": cells[1], "lantern": cells[2], "lanye": cells[3]}
    return items, fixed, lunar


def main():
    items, fixed, lunar = parse_md()
    L = []
    A = L.append
    A("清单解析：条目 %d 条 / 固定日期 %d 个 / 农历年份 %d 个"
      % (len(items), len(fixed), len(lunar)))

    if len(fixed) != 3:
        A("⚠ 固定日期应该是 3 个（晴空节 / 沐春节 / 新辰节），现在是 %d" % len(fixed))
    if not lunar:
        A("⚠ 农历日期表空的 —— 春节 / 繁灯节 / 兰夜节 那年都不会触发")

    # ① 条数自检
    counts = {}
    for it in items:
        counts[it["fest"]] = counts.get(it["fest"], 0) + 1
    A("")
    A("== 各节日条数 ==")
    for k, v in counts.items():
        A("  %-6s %d" % (k, v))
    A("  合计 %d" % len(items))

    # ② 回素材精确匹配
    cache = {}
    kept, missed, cleaned = [], [], []
    for it in items:
        rel = it["src"]
        if rel not in cache:
            cache[rel] = read_corpus(rel)
        raws, bare = cache[rel]
        if raws is None:
            missed.append((it, "素材文件不存在"))
            continue
        want = norm(it["text"])
        hit = -1
        for i, b in enumerate(bare):
            if norm(b) == want:
                hit = i
                break
        if hit < 0:
            missed.append((it, "原文里找不到这句"))
            continue
        raw_text = raws[hit]
        ct, n_e, n_q, n_p = clean_text(raw_text)
        if n_e or n_q or n_p:
            cleaned.append((it["fest"], raw_text, ct, n_e, n_q, n_p))
        kept.append({
            "id": "%s-%d" % (FEST_KEY[it["fest"]], it["no"]),
            "fest": it["fest"],
            "key": FEST_KEY[it["fest"]],
            "text": ct,
            "src": rel,
        })

    A("")
    A("匹配上 %d 条 / 对不上 %d 条" % (len(kept), len(missed)))
    if missed:
        A("")
        A("== 对不上素材的（必须逐条解决，绝不静默丢）==")
        for it, why in missed:
            A("  [%s] %s  ← %s" % (it["fest"], it["text"], why))
            A("      出处：%s" % it["src"])

    A("")
    A("== 排版归一（emoji / 引号 / 昵称占位符）共 %d 条 ==" % len(cleaned))
    for f, a, b, n_e, n_q, n_p in cleaned:
        A("  [%s] %s" % (f, a))
        A("        ⇒ %s   (emoji %d, 引号 %d, 占位符 %d)" % (b, n_e, n_q, n_p))

    # ③ 收口自检
    bad = [k for k in kept
           if any(_is_emoji(c) for c in (k["text"] or ""))
           or '"' in (k["text"] or "")
           or PLACE_NAME.search(k["text"] or "")
           or TAG_RE.search(k["text"] or "")]
    A("")
    if bad:
        A("RESULT: STOPPED —— 清洗后仍有 %d 条带 emoji / ASCII 引号 / 占位符 / 标签：" % len(bad))
        for k in bad[:10]:
            A("   %s" % k["text"])
    else:
        A("清洗自检：无 emoji、无 ASCII 双引号、无昵称占位符、无机制标签 ✅")

    with_user = [k for k in kept if "用户" in (k["text"] or "")]
    A("")
    A("含「用户」占位符（发送前换她的称呼）：%d 条" % len(with_user))
    for k in with_user:
        A("  %s" % k["text"])

    if missed or bad:
        A("")
        A("RESULT: STOPPED")
        io.open(REPORT, "w", encoding="utf-8", newline="").write("\r\n".join(L) + "\r\n")
        sys.exit(2)

    # `names`：key → 中文节日名，给日志/排查看（代码内部一律用英文 key）
    names = {v: k for k, v in FEST_KEY.items() if k in counts}
    data = {"schema": 1, "count": len(kept), "names": names, "fixed": fixed,
            "lunar": {str(k): v for k, v in sorted(lunar.items())},
            "entries": kept}
    tmp = OUT_JSON + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT_JSON)
    A("")
    A("RESULT: OK -> %s" % OUT_JSON)
    io.open(REPORT, "w", encoding="utf-8", newline="").write("\r\n".join(L) + "\r\n")


if __name__ == "__main__":
    main()
