# -*- coding: utf-8 -*-
"""跨批次一致性扫描：用批次 D/E 的最终裁定，回扫批次 A/B/C 产物（世界书 + 人设 md）"""
import io, os, re, sys

WORK = r"E:\ai-love\card\_work"
# 2026-09-17 起：世界书拆成目录，用 _wb_io 按文件名排序拼接读取
WB_DIR = os.path.join(WORK, "worldbook")
MD = os.path.join(WORK, "祁煜人设.md")
sys.path.insert(0, WORK)
import _wb_io
OUT = os.path.join(WORK, "_scan_out.txt")

# 旧口径 / 可能冲突的关键词 -> 说明
KEYS = {
    # 称呼类（已被 D 批三层优先级取代）
    "老婆": "旧称呼清单-已删", "傻瓜": "旧称呼清单-已删", "小笨蛋": "旧称呼清单-已删",
    "宝宝": "旧称呼清单-已删(注意他台词原话保留)", "小朋友": "旧称呼清单-已删",
    "主人": "旧称呼清单-已删(但wiki后缀称号/台词原话须保留)",
    # 已被 D 批明令禁止的反应
    "谁惹你了": "D批禁止项", "你平时不这么叫我": "D批禁止项", "出什么事了": "D批禁止项",
    # 职业口径（B 修订二弃用）
    "天才画家": "B修订二弃用", "声名大噪": "B修订二弃用", "前所未有": "B修订二弃用",
    "完成度": "与独一无二冲突",
    # 事实纠错项
    "深海珊瑚红": "已纠(官方为珊瑚红)", "小画家": "方向反了(是他对用户的称呼)",
    "潮汐之日": "应为潮汐逆流之日", "繁溪镇": "素材0次", "祁画师": "素材0次",
    "臭鱼": "素材0次", "骨相": "C批已删", "皮相": "C批已删", "发质": "B修订六已删",
    # 时期/设定
    "在意过去的你": "B修订已改", "超过现在的你": "B修订已改",
    "长发": "需判形态(临空短发/海神长发)",
    # 其它
    "完美主义": "B修订四核心改为独一无二",
}

def read(p):
    b = open(p, "rb").read()
    t = b.decode("utf-8-sig", errors="replace")
    return t.replace("\r\n", "\n")

def split_entries(t):
    parts = re.split(r"(?m)^### ", t)
    entries = []
    for p in parts[1:]:
        lines = p.split("\n")
        name = lines[0].strip()
        entries.append((name, p))
    return entries

def meta(body):
    order = ""
    m = re.search(r"(?m)^-?\s*order\s*[:：]\s*(\S+)", body)
    if m: order = m.group(1)
    keys = ""
    m = re.search(r"(?m)^-?\s*(?:keys?|key)\s*[:：]\s*(.+)$", body)
    if m: keys = m.group(1).strip()
    return order, keys

def main():
    out = []
    wb = _wb_io.read_text(WB_DIR)
    ents = split_entries(wb)
    out.append("=== 世界书条目清单（%d 条） ===" % len(ents))
    for i, (name, body) in enumerate(ents):
        order, keys = meta(body)
        out.append("[%02d] order=%-4s %s" % (i, order, name))
        out.append("      keys: %s" % keys)
    out.append("")

    out.append("=== 旧口径关键词命中（世界书） ===")
    hit_any = False
    for kw, why in KEYS.items():
        for i, (name, body) in enumerate(ents):
            for m in re.finditer(re.escape(kw), body):
                s = max(0, m.start() - 50); e = min(len(body), m.end() + 50)
                ctx = body[s:e].replace("\n", " / ")
                out.append("· [%02d] %s" % (i, name))
                out.append("    <%s> %s" % (why, ctx))
                hit_any = True
    if not hit_any:
        out.append("（无命中）")
    out.append("")

    out.append("=== 旧口径关键词命中（祁煜人设.md） ===")
    md = read(MD)
    hit_md = False
    for kw, why in KEYS.items():
        for m in re.finditer(re.escape(kw), md):
            s = max(0, m.start() - 60); e = min(len(md), m.end() + 60)
            ctx = md[s:e].replace("\n", " / ")
            # 定位所属字段
            head = md[:m.start()]
            fld = "?"
            for fm in re.finditer(r"(?m)^##\s+(.+)$", head):
                fld = fm.group(1).strip()
            out.append("· [%s] <%s> %s" % (fld, why, ctx))
            hit_md = True
    if not hit_md:
        out.append("（无命中）")

    txt = "\n".join(out)
    open(OUT, "wb").write(("\ufeff" + txt).replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    print("done -> %s" % OUT)

main()
