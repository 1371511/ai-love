# -*- coding: utf-8 -*-
"""
生成 `card/event_pool.json` —— 「特殊事件（节日）」用的池子 + 日期表。

⭐ 唯一真相源 = 她审过的清单 `F:\\workB\\JOB\\祁煜节日-纳入清单.md`（39 条 + 日期表）。
   JSON 是产物，**绝不手改**。

五道闸门（任何一道不过就报错停下，绝不静默丢）：
  ① 每条**原句**必须能回**素材原文**精确匹配（防手滑 / 防我编）
     · 出处列写 `自写` 的（游戏里没这个节日的素材）**跳过①**，改走 ④
     · 表格支持**四列**：`| 序号 | 台词（发出的） | 原句（素材逐字） | 出处 |`
       ⇒ 多句原句用全角 `｜` 隔开，**每一句都要逐字搜得到**（这是「原句+编写」的地基）
  ② 每个小节的条数必须对上 md 里写的「（N 条）」
  ③ 清洗完还留着 emoji / ASCII 双引号 / 昵称占位符 / 机制标签 ⇒ 停
  ④ **自写闸**（2026-09-24 加）：自写条目没有原文兜底 ⇒ 长度 6~40 字、
     句末必须是 。？！…、无 emoji / ASCII 引号 / 星号 / 占位符 / 标签
  ⑤ **原句+编写闸**（2026-09-24 晚加，她定的「原句 + 适当编写」）：
     台词比原句多出来的部分**必须接在原句后面**（顺序包含，不是把原句改写掉）；
     那条（拼接/补收尾）额外过一遍 ④ 的硬规矩（长度 / 句末 / 禁用符号）。
     ⚠ 台词 == 原句拼接（一个自造的字都没有）⇒ 仍算 `素材`，只是多记一条 `based_on`。

产物 schema：
  {"schema":1, "count":N, "fixed":{节日:"MM-DD"}, "lunar":{年:{...}},
   "entries":[{"id","fest","key","text","src"}]}
  ⭐ 自写条目的 src = "自写" 且多一个 `written: true`；
     拼接/编写条目**保留 src = 素材路径**（可溯源），多一条 `based_on: [原句…]`，
     其中**真加了我写的字**的那种再多一个 `adapted: true`；
     拼接/编写出来偏长的（她 2026-09-24 17:37 定的）再多一条 `bubbles: [气泡…]`
     —— **每句原句一条、我加的收尾单独一条**，拼起来必须正好等于 `text`。
     （bot 侧照它分条连发，见 `Rafayel_event.bubbles_of`。）

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
    # 2026-09-24 第二批：中秋（= 游戏里的咏月节/咏夜节）· 除夕 · 端午 · 圣诞
    "中秋节": "zhongqiu",
    "除夕": "chuxi",
    "端午节": "duanwu",
    "圣诞节": "shengdan",
}

# 固定日期表里应有几个（晴空节 / 沐春节 / 新辰节 / 圣诞节）
FIXED_EXPECT = 4

SEC_RE = re.compile(r"^###\s+(.+?)（(\d+)\s*条")
QIYU = re.compile(r"^祁煜[：:]\s*(.+?)\s*$")
# 四列表格里「多句原句」的分隔符 —— 用**全角**｜，不会和 markdown 的列分隔符 | 撞
PART_SEP = "｜"


def row_cells(ln):
    """把一行 markdown 表格拆成单元格；不是数据行返回 None。"""
    s = (ln or "").strip()
    if not s.startswith("|"):
        return None
    cells = [c.strip() for c in s.strip("|").split("|")]
    if len(cells) < 3 or not cells[0].isdigit():
        return None
    return cells

# 昵称占位符：素材里有好几种写法，统一成「用户」（发送前换她的称呼）
PLACE_NAME = re.compile(r"[\[<（(]\s*(?:玩家昵称|专属昵称)\s*[\]>）)]")
# 机制标签（表情 / 链接 / 红包）—— 说话时用不到，匹配和入库都剥掉
TAG_RE = re.compile(r"【[^】]*】|\[[^\]]*\]")

EMOJI_RANGES = [(0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
                (0x2190, 0x21FF), (0xFE00, 0xFE0F)]
PAIR_QUOTE_RE = re.compile(r'"([^"]*)"')

# —— 自写条目（游戏里没素材的节日，只能按人设写）——
#   出处列以这个开头 ⇒ **跳过「回素材精确匹配」**，改走下面这道自写闸。
#   ⚠ 自写没有素材兜底，所以这道闸是它唯一的质量保证：过不了就报错停下。
WRITTEN_TAG = "自写"
WRITTEN_MIN, WRITTEN_MAX = 6, 40   # 语料实测 P90=29 / P99=38 ⇒ 上限 40
WRITTEN_END = "。？！…"            # 句末必须落在这几个上（不能停在逗号上）
# ⚠ 拼接/编写的长度口径（2026-09-24 晚定）：**闸门卡的是「我加的字」，不是整条**。
#   两条素材原话接起来天然就 40+ 字（她的「2 和 4 合并」= 56 字），拿单句的 40 字上限去卡
#   整条等于把「合并」这件事直接禁掉。所以：① 我加的部分 ≤ WRITTEN_MAX
#   ② 整条给一个宽松上限 MERGED_MAX（防拼成一段话）。
MERGED_MAX = WRITTEN_MAX * 2


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
    declared = {}                  # 节日 → md 里声明的「（N 条）」
    for ln in lines[start:end]:
        m = SEC_RE.match(ln.strip())
        if m:
            cur, want = m.group(1).strip(), int(m.group(2))
            if cur not in FEST_KEY:
                raise SystemExit("FAIL 未知节日名：%s（FEST_KEY 里没有）" % cur)
            if cur in declared:
                raise SystemExit("FAIL 节日名重复出现：%s" % cur)
            declared[cur] = want
            continue
        if cur is None:
            continue
        cells = row_cells(ln)
        if cells:
            # 三列：| 序号 | 原句 | 出处 |                        ⇒ 原句 = 台词（一字未改）
            # 四列：| 序号 | 台词（发出的） | 原句（素材逐字） | 出处 |  ⇒ 支持多句原句（｜隔开）
            text, base, src = cells[1], cells[1], cells[2]
            if len(cells) >= 4:
                base, src = cells[2], cells[3]
            items.append({"fest": cur, "no": int(cells[0]),
                          "text": text, "base": base, "src": src})

    # —— 日期表：「### 固定日期（MM-DD）」/「### 农历日期（MM-DD）」——
    fixed, lunar = {}, {}
    mode = None
    lunar_cols = []          # 农历表「列 → key」的映射，从表头读出来
    for ln in lines:
        s = ln.strip()
        if s.startswith("### "):
            if "固定日期" in s:
                mode = "fixed"
            elif "农历日期" in s:
                mode = "lunar"
            else:
                mode = None
            lunar_cols = []
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
            # ⚠ 表头动态认列：`| 农历年 | 春节（正月初一） | … |` ⇒ 按中文名映射到 key。
            #   早期**写死 3 列**（spring/lantern/lanye），加一个农历节日就得改代码。
            #   这次加中秋/除夕、后面再加别的，只要 md 里补一列即可 ⇒ 已改成动态。
            if cells and cells[0] == "农历年":
                cols = []
                for c in cells[1:]:
                    m = re.match(r"^([\u4e00-\u9fa5]+)", c or "")
                    cols.append(FEST_KEY.get(m.group(1)) if m else None)
                lunar_cols = cols
                continue
            if not lunar_cols or len(cells) != len(lunar_cols) + 1:
                continue
            if not re.match(r"^\d{4}$", cells[0] or ""):
                continue
            y = int(cells[0])
            row = {}
            ok = True
            for key, val in zip(lunar_cols, cells[1:]):
                if not key or not re.match(r"^\d{2}-\d{2}$", val or ""):
                    ok = False
                    break
                row[key] = val
            if ok and row:
                lunar[y] = row
    return items, fixed, lunar, declared


def main():
    items, fixed, lunar, declared = parse_md()
    L = []
    A = L.append
    A("清单解析：条目 %d 条 / 固定日期 %d 个 / 农历年份 %d 个"
      % (len(items), len(fixed), len(lunar)))

    if len(fixed) != FIXED_EXPECT:
        A("⚠ 固定日期应该是 %d 个，现在是 %d" % (FIXED_EXPECT, len(fixed)))
    if not lunar:
        A("⚠ 农历日期表空的 —— 春节 / 繁灯节 / 兰夜节 那年都不会触发")

    # ① 条数自检 —— ⚠ 必须**真的跟 md 里声明的「（N 条）」比对**。
    #   2026-09-24 踩到：原来这里只把条数打印出来、`want` 取了却没用 ⇒
    #   小节标题写成 `（3 条 · 自写）` 时正则匹配不上、那 6 条被算进上一个节日，
    #   打印出来是「除夕 11」也没人管（闸门形同虚设）⇒ 现在改成硬校验。
    counts = {}
    for it in items:
        counts[it["fest"]] = counts.get(it["fest"], 0) + 1
    A("")
    A("== 各节日条数（md 声明 vs 实际）==")
    mism = []
    for k, v in counts.items():
        w = declared.get(k)
        flag = "OK" if w == v else "!! 声明 %s" % w
        A("  %-6s %2d  %s" % (k, v, flag))
        if w != v:
            mism.append((k, w, v))
    for k, w in declared.items():
        if k not in counts:
            A("  %-6s  0  !! 声明 %s，一条都没解析到（标题格式不对？）" % (k, w))
            mism.append((k, w, 0))
    A("  合计 %d" % len(items))

    # ② 回素材精确匹配（自写条目跳过这一道，走 ②b 的自写闸）
    cache = {}
    kept, missed, cleaned, written, adapted = [], [], [], [], []
    for it in items:
        rel = it["src"]
        if (rel or "").startswith(WRITTEN_TAG):
            ct, n_e, n_q, n_p = clean_text(it["text"])
            written.append({"fest": it["fest"], "no": it["no"], "text": ct,
                            "n_e": n_e, "n_q": n_q, "n_p": n_p})
            kept.append({
                "id": "%s-%d" % (FEST_KEY[it["fest"]], it["no"]),
                "fest": it["fest"],
                "key": FEST_KEY[it["fest"]],
                "text": ct,
                "src": WRITTEN_TAG,
                "written": True,
            })
            continue
        # —— 素材 / 拼接 / 原句+编写 ——
        #    先按 ｜ 拆出若干**原句**，每一句都必须能在那篇素材里逐字搜到（闸门①）。
        parts = [p.strip() for p in (it.get("base") or it["text"]).split(PART_SEP)
                 if p.strip()]
        if not parts:
            missed.append((it, "「原句」列是空的"))
            continue
        if rel not in cache:
            cache[rel] = read_corpus(rel)
        raws, bare = cache[rel]
        if raws is None:
            missed.append((it, "素材文件不存在"))
            continue
        hits, bad_parts = [], []
        for p in parts:
            wp = norm(p)
            h = -1
            for i, b in enumerate(bare):
                if norm(b) == wp:
                    h = i
                    break
            if h < 0:
                bad_parts.append(p)
            else:
                hits.append(h)
        if bad_parts:
            missed.append((it, "「原句」在素材里搜不到：%s" % PART_SEP.join(bad_parts)))
            continue
        base_texts = [raws[i] for i in hits]      # 素材里那几句的**原文**
        # 台词 == 原句拼接 ⇒ 一个自造的字都没有（纯照抄 / 纯拼接）
        verbatim = norm(it["text"]) == norm("".join(parts))

        if verbatim and len(base_texts) == 1:
            # 老路子：正文直接取**素材原文**（不取 md 里那行，防手滑）
            ct, n_e, n_q, n_p = clean_text(base_texts[0])
            if n_e or n_q or n_p:
                cleaned.append((it["fest"], base_texts[0], ct, n_e, n_q, n_p))
            kept.append({
                "id": "%s-%d" % (FEST_KEY[it["fest"]], it["no"]),
                "fest": it["fest"],
                "key": FEST_KEY[it["fest"]],
                "text": ct,
                "src": rel,
            })
            continue

        # —— 拼接（多句原话接起来）或 原句+编写（加了收尾）——
        ct, n_e, n_q, n_p = clean_text(it["text"])
        # 闸门⑤-1：多出来的部分必须**接在原句后面** ⇒ 各原句按顺序原样出现在台词里
        s, pos, in_order = norm(ct), 0, True
        for p in parts:
            np_ = norm(p)
            at = s.find(np_, pos)
            if at < 0:
                in_order = False
                break
            pos = at + len(np_)
        # 我额外加的字（= 台词去掉各原句后剩下的那点）—— 长度闸只卡这部分
        rest, tail_ok = ct, True
        for p in parts:
            i = rest.find(p)
            if i < 0:
                tail_ok = False
                break
            rest = rest[:i] + rest[i + len(p):]
        tail_len = len(rest) if tail_ok else len(ct)

        # —— 气泡切分（2026-09-24 17:37 她定的）——
        #   「合并出来的一条 56 字太长 ⇒ 拆成两条气泡连着发」。
        #   切法：**每句原句一条、我加的收尾单独一条** ——
        #   他的短信原话本来就是这样一句一条发的（素材里 `祁煜：A` / `祁煜：B` 是两条）。
        #   ⚠ 紧跟原句的句末标点归**那一条**（原句常常没标点，那个句号是我补的）。
        bub, rest2, can = [], ct, True
        for p in parts:
            i = rest2.find(p)
            if i < 0:
                can = False
                break
            j = i + len(p)
            while j < len(rest2) and rest2[j] in WRITTEN_END:
                j += 1
            bub.append(rest2[:j])
            rest2 = rest2[j:]
        if not can:
            bub = []
        elif rest2.strip():
            bub.append(rest2)
        if len(bub) < 2:
            bub = []                  # 只有一条就不必写进产物

        rec = {"fest": it["fest"], "no": it["no"], "text": ct,
               "based_on": base_texts, "src": rel, "n_e": n_e, "n_q": n_q,
               "n_p": n_p, "in_order": in_order, "tail_len": tail_len,
               "bubbles": bub}
        entry = {
            "id": "%s-%d" % (FEST_KEY[it["fest"]], it["no"]),
            "fest": it["fest"],
            "key": FEST_KEY[it["fest"]],
            "text": ct,
            "src": rel,
            "based_on": base_texts,
        }
        if bub:
            entry["bubbles"] = bub
        if not verbatim:
            entry["adapted"] = True
            adapted.append(rec)
        if n_e or n_q or n_p:
            cleaned.append((it["fest"], PART_SEP.join(base_texts), ct, n_e, n_q, n_p))
        kept.append(entry)

    n_mat = len(kept) - len(written) - len(adapted)
    A("")
    A("素材条目：匹配上 %d 条 / 对不上 %d 条" % (n_mat, len(missed)))
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

    # ②b 自写闸（游戏里没素材的节日 ⇒ 没有原文兜底，只能靠这几条硬规矩把关）
    A("")
    A("== 自写条目（按人设写，共 %d 条）==" % len(written))
    bad_w = []
    for w in written:
        ct = w["text"]
        why = []
        if not (WRITTEN_MIN <= len(ct) <= WRITTEN_MAX):
            why.append("长度 %d 不在 %d~%d" % (len(ct), WRITTEN_MIN, WRITTEN_MAX))
        if not ct.endswith(tuple(WRITTEN_END)):
            why.append("句末标点不在 %s 里" % WRITTEN_END)
        if any(_is_emoji(c) for c in ct) or '"' in ct or "*" in ct \
                or PLACE_NAME.search(ct) or TAG_RE.search(ct):
            why.append("含 emoji / ASCII 引号 / 星号 / 占位符 / 标签")
        A("  [%s] %s" % (w["fest"], ct))
        A("        %d 字 ⇒ %s" % (len(ct), "OK" if not why else "；".join(why)))
        if why:
            bad_w.append((w["fest"], ct, why))

    # ②c 原句+编写闸（2026-09-24 晚加）—— 她定的「原句 + 适当编写」
    #     · 地基已在 ② 验过：每一句原句都能在素材里逐字搜到
    #     · 这里再验两件：① 多出来的字**接在原句后面**（不是把原句改写掉）
    #                     ② 整条过一遍自写闸（长度 / 句末 / 禁用符号）
    A("")
    A("== 原句+编写条目（素材打底、我补了字，共 %d 条）==" % len(adapted))
    bad_a = []
    for w in adapted:
        ct = w["text"]
        why = []
        if not w["in_order"]:
            why.append("多出来的部分没有接在原句后面（顺序包含失败 ⇒ 等于改写了原句）")
        if w["tail_len"] > WRITTEN_MAX:
            why.append("我加的那部分 %d 字，超过 %d" % (w["tail_len"], WRITTEN_MAX))
        if len(ct) > MERGED_MAX:
            why.append("整条 %d 字，超过 %d（拼接别拼成一段话）" % (len(ct), MERGED_MAX))
        if not ct.endswith(tuple(WRITTEN_END)):
            why.append("句末标点不在 %s 里" % WRITTEN_END)
        if w["bubbles"] and "".join(w["bubbles"]) != ct:
            why.append("气泡拼起来不等于整条（切分内部不一致）")
        if w["n_e"] or w["n_q"] or w["n_p"] or '"' in ct or "*" in ct \
                or PLACE_NAME.search(ct) or TAG_RE.search(ct):
            why.append("含 emoji / ASCII 引号 / 星号 / 占位符 / 标签")
        A("  [%s] %s" % (w["fest"], ct))
        A("        原句：%s" % PART_SEP.join(w["based_on"]))
        A("        整条 %d 字 / 我加 %d 字 / %d 条气泡 ⇒ %s"
          % (len(ct), w["tail_len"], len(w["bubbles"]) or 1,
             "OK" if not why else "；".join(why)))
        for b in w["bubbles"]:
            A("          · %s" % b)
        if why:
            bad_a.append((w["fest"], ct, why))

    # ②d 分多条气泡的条目一览（她 2026-09-24 17:37 定的：合并出来偏长的那条拆开连发）
    multi = [k for k in kept if k.get("bubbles")]
    A("")
    A("== 分多条气泡的条目（共 %d 条，其余都是单条）==" % len(multi))
    for k in multi:
        A("  [%s] %d 条" % (k["id"], len(k["bubbles"])))
        for b in k["bubbles"]:
            A("        · %s" % b)

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

    # ⑤ 日期可达性（2026-09-24 加）：每个**有台词**的节日都必须能查到日期，
    #    否则那些台词永远不会被触发（`date_of` 返回 None ⇒ 那天不触发）。
    #    ⚠ 这类 bug 已经踩过两次：① fixed 的键写成中文名 ⇒ 晴空节不触发；
    #    ② 端午只加了台词、忘了在日期表里加列 ⇒ 端午不触发。⇒ 以后忘了配日期，这里直接停。
    dateless = []
    all_keys = sorted({k["key"] for k in kept})
    for key in all_keys:
        if key in fixed:
            continue
        miss_y = [y for y, row in sorted(lunar.items()) if key not in row]
        if miss_y:
            dateless.append((key, miss_y))
    A("")
    A("== 日期可达性（有台词的节日必须查得到日期）==")
    if dateless:
        for key, ys in dateless:
            A("  !! %s 在 %d 个年份里查不到日期：%s"
              % (key, len(ys), ",".join(str(y) for y in ys)))
    else:
        A("  %d 个有台词的节日全部可达 ✅" % len(all_keys))

    if missed or bad or bad_w or bad_a or mism or dateless \
            or len(fixed) != FIXED_EXPECT:
        A("")
        if mism:
            A("RESULT: STOPPED —— 小节条数与 md 声明的「（N 条）」对不上：")
            for k, w, v in mism:
                A("   %s：声明 %s 条，实际解析到 %s 条" % (k, w, v))
        if len(fixed) != FIXED_EXPECT:
            A("RESULT: STOPPED —— 固定日期表应是 %d 个，现在是 %d 个" % (FIXED_EXPECT, len(fixed)))
        if bad_w:
            A("RESULT: STOPPED —— 自写条目有 %d 条不过闸：" % len(bad_w))
            for f, ct, why in bad_w:
                A("   [%s] %s   ← %s" % (f, ct, "；".join(why)))
        if bad_a:
            A("RESULT: STOPPED —— 原句+编写有 %d 条不过闸：" % len(bad_a))
            for f, ct, why in bad_a:
                A("   [%s] %s   ← %s" % (f, ct, "；".join(why)))
        if dateless:
            A("RESULT: STOPPED —— 有节日的台词查不到日期（永远不会触发）：")
            for key, ys in dateless:
                A("   %s：%d 个年份缺日期" % (key, len(ys)))
        if missed or bad:
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
