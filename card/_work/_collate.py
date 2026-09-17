# -*- coding: utf-8 -*-
"""
世界书 ↔ 剧情素材 双向对照（方案 A）v2

2026-09-17 v2 修订（v1 的问题）：
  v1 反向扫描抓到 0 个词 —— 素材里用的是 “” 不是 「」，正则没覆盖。
  v1 正向把「主线里搜不到」当成可疑 —— 错。主线只占全库一小部分，
     多数条目的出处是逸闻/倾心之约/世界深处，搜不到是正常的。

v2 的三个视角：
  【1】引用核验：条目 source 里写了「主线X-Y」的，去核对那个文件真存在、
      里面真有这个词。抓的是**我们记错出处**（最危险：错出处会一直误导后续）。
  【2】逐文件覆盖度：每个标了祁煜的剧情文件，看有多少世界书条目能覆盖它。
      覆盖 0 = 这段剧情完全没进世界书，他聊到这儿必编。
  【3】未收录词表：祁煜相关行里的引号内专有名词，比对世界书。

三条硬约束（2026-09-15「天才」漏判换来的）：
  ① 检索范围包含**所有行**，不区分对话/旁白/资料卡/UI/演出（过滤就漏判）
  ② 正式跑之前先自检：已知有出处的句子搜不到就说明脚本错了
  ③ 中文归一化：全角半角 / 空白 / 标点统一后再比
"""
import os, re, io, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _wb_io

SRC = r"C:\Users\lin\Desktop\祁煜剧情\1恋与深空主线"
# 2026-09-17 起：世界书已拆成目录，读目录（_wb_io 按文件名排序拼接，排除 _ 前缀）
WB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worldbook")
OUT = r"F:\workB\JOB\lysk\_collate_out.txt"

MALES = ("祁煜", "沈星回", "黎深", "夏以昼", "秦彻")

# ---------------- 归一化 ----------------
# ⚠ 必须含中文弯引号 “”‘’（U+201C/201D/2018/2019）。
# 2026-09-17 踩到：漏了它们 → 所有带 “” 的原文句子都搜不到
# （「这是我的私人委托」误报为「查无此句」就是这么来的）。
PUNCT = (r"[，。！？、；：""''"
         "\u201c\u201d\u2018\u2019"
         r"（）()《》「」〈〉\[\]<>·—…\-~,.\!?;:'\"\s]")


def norm(s):
    if not s:
        return ""
    s = s.replace("\u00a0", " ")
    out = []
    for c in s:
        o = ord(c)
        if 0xFF01 <= o <= 0xFF5E:          # 全角 -> 半角
            out.append(chr(o - 0xFEE0))
        elif c == "\u3000":
            continue
        else:
            out.append(c)
    return re.sub(PUNCT, "", "".join(out))


# ---------------- 引号内专有名词 ----------------
QUOTES = [("「", "」"), ("“", "”"), ("‘", "’"),
          ("《", "》"), ("〈", "〉"), ("【", "】")]


def quoted(text, maxlen=16):
    res = []
    for a, b in QUOTES:
        for w in re.findall(re.escape(a) + r"([^" + re.escape(a + b) + r"]{1,%d})" % maxlen + re.escape(b), text):
            w = w.strip()
            if len(w) >= 2 and not re.match(r"^[\d\s]+$", w):
                res.append(w)
    return res


# ---------------- 读素材 ----------------
rows = []      # (rel, lineno, males, speaker, raw, norm)
files = {}     # rel -> (males, fulltext_norm, lines)
for dirpath, dirnames, filenames in os.walk(SRC):
    for fn in sorted(filenames):
        if not fn.lower().endswith(".txt"):
            continue
        fp = os.path.join(dirpath, fn)
        rel = os.path.relpath(fp, SRC)
        try:
            raw = open(fp, "rb").read().decode("utf-8-sig", errors="replace")
        except Exception:
            continue
        if os.sep not in rel:
            continue          # 根目录下的散文件（如 _校验报告.txt）不算素材
        # 多男主标记要拆开：(黎深,沈星回,祁煜) 不能整个当一个名字去比对
        males_file = sorted({m.strip()
                             for grp in re.findall(r"\(([^)]*)\)", fn)
                             for m in re.split(r"[,，、/]", grp)
                             if m.strip() in MALES})
        lines = [l.strip() for l in raw.replace("\r\n", "\n").split("\n")]
        for i, line in enumerate(lines, 1):
            if not line:
                continue
            sp = ""
            mm = re.match(r"^([^：:]{1,24})[：:]", line)
            if mm:
                sp = mm.group(1).strip()
            males_line = sorted({m for m in re.findall(r"（([^）]{1,8})）", sp) if m in MALES})
            rows.append((rel, i, males_file or males_line, sp, line, norm(line)))
        files[rel] = (males_file, norm("\n".join(lines)), lines)

corpus = "\n".join(r[5] for r in rows)


def find(w):
    nw = norm(w)
    if len(nw) < 2:
        return 0, []
    hits = [(r[0], r[1]) for r in rows if nw in r[5]]
    return len(hits), hits[:3]


def has_other_src(src):
    """出处里除了主线，还有没有别的栏目（逸闻 / 倾心之约 / 世界深处 …）。"""
    return any("主线" not in s
               for s in (x.strip() for x in re.split(r"[、；;]", src)) if s)


# ---------------- 读世界书 ----------------
wb_raw = _wb_io.read_text(WB_DIR)
entries = []
for part in re.split(r"(?m)^### ", wb_raw)[1:]:
    lines = part.split("\n")
    name = lines[0].strip()
    if name.startswith("x-"):
        continue
    keys, src, body = [], "", []
    for ln in lines[1:]:
        s = ln.strip()
        low = s.lower()
        if low.startswith("keys:"):
            keys = [k.strip() for k in s[5:].split(",") if k.strip()]
        elif low.startswith("source:"):
            src = s[7:].strip()
        elif low.startswith(("order:", "secondary_keys:", "constant:",
                             "selective:", "position:")):
            continue
        else:
            body.append(ln)
    btxt = "\n".join(body).strip()
    terms = list(keys) + quoted(btxt)
    seen, tl = set(), []
    for t in terms:
        nt = norm(t)
        if len(nt) < 2 or nt in seen:
            continue
        seen.add(nt)
        tl.append(t)
    entries.append({"name": name, "keys": keys, "source": src,
                    "body": btxt, "terms": tl})

wb_all = norm("\n".join(e["body"] for e in entries))

out = []
out.append("世界书 ↔ 主线素材 双向对照 v2")
out.append("素材：%s" % SRC)
out.append("文件 %d 个 / 有效行 %d 行 / 世界书条目 %d 条"
           % (len(files), len(rows), len(entries)))
out.append("")

# =========================================================
# 0. 自检（硬约束②）
# =========================================================
out.append("【0】脚本自检（这几条必须有命中，否则别信后面的结论）")
SELFTEST = [
    ("百年一遇的天才", "主线1-2 资料卡原文（v1 漏判的那条）"),
    ("捞鱼男", "主线1-2 下一个目标"),
    ("焰尾鱼", "主线1-1 涟漪"),
    ("白沙湾", "高频地名"),
    ("利莫里亚", "高频设定词"),
    ("鲸落城", "第6章核心场景"),
    ("撒丁岛", "主线1-12 不平等交易 祁煜台词"),
    ("盖亚生物科技", "主线6-07 鲸落城 旁白"),
]
bad = 0
for w, note in SELFTEST:
    n, loc = find(w)
    if not n:
        bad += 1
    where = "%s:%d" % loc[0] if loc else "—"
    out.append("  %s %-12s 命中 %-4d  %s  （%s）"
               % ("OK  " if n else "FAIL", w, n, where, note))
out.append("  自检结论：%s" % ("通过，可以信" if not bad else
                          "❗ 有 %d 条搜不到，脚本有问题，先修脚本" % bad))
out.append("")

# =========================================================
# 1. 引用核验：source 里写了「主线X-Y」的，文件存在吗、里面有这个词吗
# =========================================================
out.append("=" * 74)
out.append("【1】引用核验：世界书说这句话出自主线某节，核对对不对得上")
out.append("  抓的是**记错出处**——最危险，错出处会一直在后续批次误导判断")
out.append("")

# 建索引：小节名 -> 文件
by_sect = {}
for rel in files:
    base = os.path.basename(rel)
    m = re.search(r"\)([^.]+)", base)          # (祁煜)涟漪 -> 涟漪
    sect = m.group(1).strip() if m else base[:-4]
    by_sect.setdefault(sect, []).append(rel)

MAIN_CITE = re.compile(r"主线\s*(\d+)\s*-\s*(\d+)")
checked = 0
for e in entries:
    cites = MAIN_CITE.findall(e["source"])
    if not cites:
        continue
    checked += 1
    # 出处里除了主线还有没有别的栏目（逸闻 / 倾心之约 / 世界深处 …）
    has_other = has_other_src(e["source"])
    out.append("  [%s]  引用 %s%s"
               % (e["name"], ", ".join("主线%s-%s" % c for c in cites),
                  "（另有其它栏目出处）" if has_other else "（只引用了主线）"))
    for ch, sec in cites:
        # 章目录以数字开头
        cand = [r for r in files if r.split(os.sep)[0].startswith(ch + "于")
                or r.split(os.sep)[0].startswith(ch)]
        if not cand:
            # 出处里还写了别的栏目（逸闻/倾心之约…）的话，章节号可能是 wiki 另一套
            # 编号，不算错（例：怕猫条目的「主线7-8」== 主线1-7 第08小节）
            if has_other:
                out.append("      注：主线第 %s 章不在整理好的素材里，但该条目另有其它栏目出处，"
                           "可能是 wiki 另一套编号，不判错" % ch)
            else:
                out.append("      ❗ 主线第 %s 章不存在，且没有其它出处可以解释 → 引用可疑" % ch)
            continue
        ch_text = "\n".join(files[r][1] for r in cand)
        hit_terms = [t for t in e["terms"] if norm(t) in ch_text]
        out.append("      第%s章：文件 %d 个，本条目查询词命中 %d/%d"
                   % (ch, len(cand), len(hit_terms), len(e["terms"])))
        if not hit_terms and not has_other:
            out.append("      ❗ 声称出自主线%s-%s，且无其它出处，但该章里一个查询词都搜不到" % (ch, sec))
        elif len(hit_terms) < max(1, len(e["terms"]) // 3) and not has_other:
            out.append("      ⚠ 只引用了主线，命中偏少：%s" % "、".join(hit_terms[:8]))
out.append("")
out.append("  引用了主线的条目：%d 条" % checked)
out.append("  ※ 没引用主线的条目出处在逸闻/倾心之约/世界深处等栏目，")
out.append("    那些栏目还没整理，本脚本不判——等整理完再跑同一套。")
out.append("")

# =========================================================
# 2. 逐文件覆盖度：这段剧情，世界书知道多少
# =========================================================
out.append("=" * 74)
out.append("【2】逐文件覆盖度：标了祁煜的剧情文件，有多少世界书条目能覆盖它")
out.append("  覆盖 0 = 这段剧情完全没进世界书 → 他聊到这儿没有事实可用，只能编")
out.append("")

jy_files = sorted([r for r in files if "祁煜" in files[r][0]])

# 泛词剔除：命中超过 60% 祁煜文件的查询词说明太泛，不能当作「覆盖」的证据。
# （v1 的覆盖度全是 3~17，就是因为这种词在撑数）
docfreq = Counter()
for rel in jy_files:
    full = files[rel][1]
    for e in entries:
        for t in e["terms"]:
            if norm(t) in full:
                docfreq[(e["name"], t)] += 1
thresh = max(3, int(len(jy_files) * 0.6))


def distinctive(e):
    return [t for t in e["terms"] if docfreq[(e["name"], t)] <= thresh]


generic = sorted({t for e in entries for t in e["terms"]
                  if docfreq[(e["name"], t)] > thresh})
out.append("  已剔除的泛词（命中 >%d 个文件，不算覆盖证据）：%s"
           % (thresh, "、".join(generic[:20]) if generic else "无"))
out.append("")

zeros, lows = [], []
for rel in jy_files:
    full = files[rel][1]
    matched, mterms = [], []
    for e in entries:
        hit = [t for t in distinctive(e) if norm(t) in full]
        if hit:
            matched.append(e["name"])
            mterms += hit
    n = len(matched)
    tag = "❗" if n == 0 else ("⚠ " if n <= 2 else "  ")
    out.append("%s %-46s 覆盖条目 %d  %s"
               % (tag, rel, n, "、".join(matched[:5])))
    if n == 0:
        zeros.append(rel)
    elif n <= 2:
        lows.append(rel)

out.append("")
out.append("  祁煜相关文件 %d 个：零覆盖 %d 个 / 低覆盖(≤2) %d 个"
           % (len(jy_files), len(zeros), len(lows)))
out.append("")
out.append("  --- 零覆盖文件的祁煜原话样本（人工判要不要收）---")
for rel in zeros:
    out.append("")
    out.append("  ▸ %s" % rel)
    jl = [r[4] for r in rows if r[0] == rel and "祁煜" in r[3]]
    if not jl:
        jl = [l.strip() for l in files[rel][2] if l.strip()][:4]
    out.append("      （祁煜台词 %d 句）" % len(jl))
    for s in jl[:12]:
        out.append("      %s" % (s[:78] + ("…" if len(s) > 78 else "")))

out.append("")
out.append("  --- 低覆盖文件的祁煜原话样本 ---")
for rel in lows:
    jl = [r[4] for r in rows if r[0] == rel and "祁煜" in r[3]]
    if not jl:
        jl = [l.strip() for l in files[rel][2] if l.strip()][:3]
    out.append("")
    out.append("  ▸ %s" % rel)
    for s in jl[:6]:
        out.append("      %s" % (s[:78] + ("…" if len(s) > 78 else "")))

# =========================================================
# 3. 未收录词表
# =========================================================
out.append("")
out.append("=" * 74)
out.append("【3】反向词表：祁煜相关行里的引号内专有名词，世界书有没有")
out.append("")

jy_rows = [r for r in rows if "祁煜" in (r[2] or []) or "祁煜" in r[3]]
out.append("  祁煜相关行：%d 行（占主线 %.1f%%）"
           % (len(jy_rows), len(jy_rows) * 100.0 / max(1, len(rows))))

cnt = Counter()
where = {}
for r in jy_rows:
    for w in quoted(r[4]):
        cnt[w] += 1
        where.setdefault(w, "%s:%d" % (r[0], r[1]))

# 通用词剔除（数据驱动，不靠人工黑名单）：
# 在别的男主篇章（第 2~5 章、以及 1/6 章里没标祁煜的文件）里也出现的词，
# 说明是通用词汇 / UI 文案，不是祁煜线的专有名词。
other = Counter()
for rel in files:
    if "祁煜" in files[rel][0]:
        continue
    for ln in files[rel][2]:
        for w in quoted(ln):
            other[w] += 1

gap = [(w, c, where[w]) for w, c in cnt.most_common()
       if norm(w) and norm(w) not in wb_all and w not in other]
drop = [(w, c) for w, c in cnt.most_common() if w in other]
out.append("  判为通用词而剔除的（在别的男主篇章也出现）：%s"
           % ("、".join(w for w, _ in drop[:24]) if drop else "无"))
out.append("")
out.append("  --- 主线有、世界书里查不到的词（漏收录候选，按频次）---")
if gap:
    for w, c, loc in gap[:60]:
        out.append("    %-18s ×%-3d  %s" % (w, c, loc))
else:
    out.append("    （无）")
out.append("")
out.append("  引号词 %d 个 / 通用词 %d 个 / 未覆盖候选 %d 个"
           % (len(cnt), len(drop), len(gap)))

# =========================================================
# 5. 引文核验（正向最高优先级）：世界书引用的原话，原文里找不找得到
# =========================================================
out.append("")
out.append("=" * 74)
out.append("【5】引文核验：世界书正文/出处里写进来的原话，逐句回主线比对")
out.append("  抓的是**我们在编 / 记错出处**。只跑主线，所以：")
out.append("    · 出处标了主线的却搜不到 → ❗ 真问题")
out.append("    · 出处标的是其它栏目的搜不到 → 待那些栏目整理完再验")
out.append("")

corpus_n = norm(corpus)
qmiss_main, qmiss_other, qok = [], [], 0
for e in entries:
    cands, seen = [], set()
    for txt in (e["body"], e["source"]):
        for w in quoted(txt, 60):
            if len(w) >= 6 and w not in seen:
                seen.add(w)
                cands.append(w)
    # 出处里「（他：xxx）」这种直接引的台词也要验，它没被引号包住
    for w in re.findall(r"（(?:他|祁煜|玩家|用户)[：:]\s*([^）]{6,})）", e["source"]):
        # source 里常写成「他：A / B / C」，必须按 / 拆开，否则整段被当成一句引文
        for part in re.split(r"[/｜|]", w):
            part = part.strip()
            if len(part) >= 6 and part not in seen:
                seen.add(part)
                cands.append(part)
    e["quotes"] = cands
    main_only = ("主线" in e["source"]) and not has_other_src(e["source"])
    for w in cands:
        if norm(w) in corpus_n:
            qok += 1
        else:
            (qmiss_main if main_only else qmiss_other).append((e["name"], w))

out.append("  引用原话：命中 %d 句 / 主线里搜不到 %d 句" % (qok, len(qmiss_main) + len(qmiss_other)))
out.append("")
out.append("  --- ❗ 出处只写主线、却在主线里搜不到的（先查这几条）---")
if qmiss_main:
    for name, w in qmiss_main:
        out.append("    [%s]" % name)
        out.append("        %s" % w[:110])
else:
    out.append("    （无）")
out.append("")
out.append("  --- 出处含其它栏目、主线里搜不到的（待验，暂不判错）---")
for name, w in qmiss_other[:40]:
    out.append("    %-16s %s" % (name, w[:88]))
if not qmiss_other:
    out.append("    （无）")

# =========================================================
# 6. 小节级引用核验（最能抓「记错出处」）
# =========================================================
out.append("")
out.append("=" * 74)
out.append("【6】小节级引用核验：标了「主线X-Y 第N小节」的引文，是不是真在那一节")
out.append("  【1】只按章核对太粗 —— 主线1-2 和 主线1-7 都在第 1 章里，看不出差别。")
out.append("  这一节精确到小节：引文存在，但不在所标的小节 → ❗ 出处记错")
out.append("")

# 小节索引：(章号, 页号, 小节号) -> [rel]
sec_index = {}
for rel in files:
    parts = rel.split(os.sep)
    if len(parts) < 2:
        continue
    mch = re.match(r"(\d+)", parts[0])
    mpg = re.match(r"(\d+)", parts[1])
    msc = re.match(r"(\d+)", os.path.basename(rel))
    if not (mch and mpg):
        continue
    sec_index.setdefault((mch.group(1), mpg.group(1),
                          msc.group(1) if msc else ""), []).append(rel)


def expected_files(src):
    """把 source 里的「主线X-Y …」解析成具体文件。"""
    res = []
    for m in re.finditer(r"主线\s*(\d+)\s*[-－]\s*(\d+)", src):
        ch, pg = m.group(1), m.group(2)
        tail = src[m.end():m.end() + 14]
        sm = re.match(r"\s*(\d+)", tail)
        sm2 = re.search(r"第\s*(\d+)\s*小节", tail)
        sect = sm.group(1) if sm else (sm2.group(1) if sm2 else None)
        # 目录名带前导零（01始终），引用里不带（主线1-1）→ 必须按数值比
        if sect:
            res += [r for (c, p, s), v in sec_index.items()
                    if int(c) == int(ch) and int(p) == int(pg)
                    and s.lstrip("0") == sect.lstrip("0") for r in v]
        else:
            res += [r for (c, p, s), v in sec_index.items()
                    if int(c) == int(ch) and int(p) == int(pg) for r in v]
    return sorted(set(res))


wrong = []
for e in entries:
    exp = expected_files(e["source"])
    if not exp:
        continue
    for w in e.get("quotes", []):
        if norm(w) not in corpus_n:
            continue                       # 别处也搜不到，归【5】管
        if any(norm(w) in files[r][1] for r in exp):
            continue
        loc = find(w)[1][:1]
        wrong.append((e["name"], w, exp[0] if exp else "",
                      "%s:%d" % loc[0] if loc else "—"))

if wrong:
    for name, w, exp, loc in wrong:
        out.append("  ❗ [%s]" % name)
        out.append("      引文：%s" % w[:100])
        out.append("      标在：%s" % exp)
        out.append("      实际：%s" % loc)
else:
    out.append("  （无）")
out.append("")
out.append("  标了具体小节、且引文能在主线找到的条目已全部核对。")

# =========================================================
# 4. 章级统计
# =========================================================
out.append("")
out.append("=" * 74)
out.append("【4】章级：祁煜在哪几章出场，各占多少")
out.append("")
for ch in sorted({r[0].split(os.sep)[0] for r in jy_rows}):
    n = len([r for r in jy_rows if r[0].split(os.sep)[0] == ch])
    out.append("    %-20s 祁煜行 %d" % (ch, n))
allch = sorted({r.split(os.sep)[0] for r in files})
out.append("")
out.append("    主线共 %d 章：%s" % (len(allch), "、".join(allch)))

io.open(OUT, "w", encoding="utf-8").write("\n".join(out))
print("done -> %s  (%d 行)" % (OUT, len(out)))
