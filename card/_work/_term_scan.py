# -*- coding: utf-8 -*-
"""
扫描 E:\\JOB 全库，统计候选「称呼」与关键术语的出现次数、出处文件与上下文例句。

用途：为祁煜酒馆卡的世界书「称呼」条目提供素材实证——只收录真实出现过的称呼。
输出：E:\\ai-love\\card\\_work\\称呼词频报告.md

运行：
  python _term_scan.py
"""
import os
import re
import sys
import io
from collections import OrderedDict

ROOT = r"E:\JOB"
OUT = r"E:\ai-love\card\_work\称呼词频报告.md"

# 候选称呼，按用途分组
GROUPS = OrderedDict([
    ("对祁煜的称呼 / 代号", [
        "祁煜", "祁老师", "祁教授", "小画家", "大画家", "祁画师", "画师",
        "莫亚", "塞壬", "海神", "海神大人", "潜行者", "海妖", "人鱼", "神明",
        "MO", "小鱼", "鱼鱼", "那条鱼", "臭鱼", "焰尾鱼",
    ]),
    ("对用户的称呼（说话人=祁煜时）", [
        "保镖小姐", "猎人小姐", "小猎人", "宝宝", "宝贝", "公主殿下", "公主",
        "女王大人", "主人", "老婆", "亲爱的", "小笨蛋", "笨蛋", "傻瓜",
        "大小姐", "小朋友", "小辞",
    ]),
    ("世界观术语", [
        "利莫里亚", "临空市", "白沙湾", "Mo Art Studio", "罗镜城", "海神冢",
        "繁溪镇", "歌岛", "潮汐之日", "潮汐逆流", "火种", "断潮戟", "唤海神杖",
        "鲸哨", "Evol", "灵空行动", "猎人协会", "深空猎人", "流浪体", "侵蚀",
        "乱流", "唐知理", "喵喵牌", "喵呜徽章", "涂鸦叽", "啵啵鱼", "芥末章章",
        "嘉兰百合", "深海珊瑚红", "灯塔", "灯芯",
    ]),
])

# ASCII 词需要词边界，避免 MO 命中 MOMENT 之类
ASCII_RE = re.compile(r"^[A-Za-z]+$")

SAMPLE_LIMIT = 4        # 每个词最多保留几条例句
CJK_PUNCT = "。！？\n"


def build_regex(term: str) -> re.Pattern:
    if ASCII_RE.match(term):
        return re.compile(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])")
    return re.compile(re.escape(term))


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            if not fn.lower().endswith(".txt"):
                continue
            if fn.startswith("_"):          # 跳过 _校验报告.txt
                continue
            yield os.path.join(dirpath, fn)


def split_sentences(text):
    """粗切句：按中文句末标点与换行切，保留较长的片段。"""
    parts = re.split(r"(?<=[。！？…])|\n", text)
    return [p.strip() for p in parts if len(p.strip()) >= 6]


def main():
    stats = OrderedDict()
    for group, terms in GROUPS.items():
        stats[group] = OrderedDict((t, {"count": 0, "files": [], "samples": []}) for t in terms)

    regexes = {g: {t: build_regex(t) for t in terms} for g, terms in GROUPS.items()}

    file_count = 0
    for path in iter_files(ROOT):
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            print(f"[WARN] 读取失败 {path}: {e}")
            continue
        file_count += 1
        rel = os.path.relpath(path, ROOT)

        for group, terms in GROUPS.items():
            for t in terms:
                matches = list(regexes[group][t].finditer(text))
                if not matches:
                    continue
                rec = stats[group][t]
                rec["count"] += len(matches)
                if rel not in rec["files"]:
                    rec["files"].append(rel)
                if len(rec["samples"]) < SAMPLE_LIMIT:
                    sents = split_sentences(text)
                    for s in sents:
                        if t in s:
                            if len(s) > 60:
                                s = s[:60] + "…"
                            line = f"{rel} ▸ {s}"
                            if line not in rec["samples"]:
                                rec["samples"].append(line)
                            if len(rec["samples"]) >= SAMPLE_LIMIT:
                                break

    lines = []
    lines.append("# 称呼 / 术语 词频实证报告")
    lines.append("")
    lines.append(f"- 扫描根目录：`{ROOT}`")
    lines.append(f"- 扫描文件数：**{file_count}**（已跳过 `_校验报告.txt`）")
    lines.append(f"- 生成脚本：`E:\\ai-love\\card\\_work\\_term_scan.py`")
    lines.append("- 用途：世界书条目只收录**这里出现过**的说法；出现 0 次的一律不采用。")
    lines.append("")

    for group, terms in GROUPS.items():
        lines.append(f"## {group}")
        lines.append("")
        lines.append("| 词 | 次数 | 命中文件数 |")
        lines.append("|---|---:|---:|")
        rows = sorted(stats[group].items(), key=lambda kv: -kv[1]["count"])
        for t, rec in rows:
            lines.append(f"| {t} | {rec['count']} | {len(rec['files'])} |")
        lines.append("")

        hits = [(t, rec) for t, rec in rows if rec["count"] > 0]
        zero = [t for t, rec in rows if rec["count"] == 0]
        if zero:
            lines.append(f"**出现 0 次（不可采用）**：{'、'.join(zero)}")
            lines.append("")

        for t, rec in hits:
            lines.append(f"### {t}　（{rec['count']} 次 / {len(rec['files'])} 个文件）")
            lines.append("")
            for s in rec["samples"]:
                lines.append(f"- {s}")
            lines.append("")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[OK] 扫描 {file_count} 个文件 → {OUT}")


if __name__ == "__main__":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
