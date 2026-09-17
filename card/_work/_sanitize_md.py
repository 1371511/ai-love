# -*- coding: utf-8 -*-
"""
清洗 `worldbook/` 目录里「会被注入」的正文：
  1. 去掉 markdown 粗体标记 `**`（SillyTavern 按纯文本注入，`**` 会原样出现在提示词里）
  2. 把正文里的 ASCII 引号 " 成对换成中文引号「」

只处理条目的正文，**不动** 元数据行与 `##` 组标题。
`_` 前缀的文件（如 `_README.md`）不参与处理。

2026-09-17：世界书拆成 `worldbook/` 多文件后，本脚本改为**逐文件**清洗并写回。

用法：python _sanitize_md.py [--dry]
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _wb_io                    # noqa: E402

ENTRY_RE = re.compile(r"^###\s+(.*)$")
GROUP_RE = re.compile(r"^##\s+(.*)$")
META_RE = re.compile(r"^-?\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


def pair_quotes(line):
    """把一行里成对的 ASCII 双引号换成「」。返回 (结果, 是否成对)。"""
    out = []
    open_next = True
    for ch in line:
        if ch == '"':
            out.append("「" if open_next else "」")
            open_next = not open_next
        else:
            out.append(ch)
    return "".join(out), open_next


def sanitize(lines):
    """清洗一个文件的行列表，返回 (fixed_lines, stats)。"""
    fixed, unbalanced = [], []
    n_bold = n_quote = 0
    in_entry = False
    in_header = True
    cur_name = ""

    for i, line in enumerate(lines, start=1):
        if ENTRY_RE.match(line):
            in_entry = True
            in_header = True
            cur_name = ENTRY_RE.match(line).group(1).strip()
            fixed.append(line)
            continue
        if GROUP_RE.match(line):
            in_entry = False
            fixed.append(line)
            continue
        if not in_entry:
            fixed.append(line)
            continue

        if in_header:
            if META_RE.match(line) or line.strip() == "":
                fixed.append(line)
                continue
            in_header = False

        new = line
        if "**" in new:
            n_bold += new.count("**") // 2
            new = new.replace("**", "")
        if '"' in new:
            new, balanced = pair_quotes(new)
            n_quote += 1
            if not balanced:
                unbalanced.append("第 %d 行（%s）引号数为奇数" % (i, cur_name))
        fixed.append(new)

    return fixed, {"bold": n_bold, "quote_lines": n_quote, "unbalanced": unbalanced}


def main():
    dry = "--dry" in sys.argv
    files = list(_wb_io.iter_files())
    if not files:
        print("[FAIL] %s 下没有任何参与拼接的 .md 文件" % _wb_io.WB_DIR)
        sys.exit(1)

    tb = tq = 0
    all_odd = []
    for fname, path in files:
        with io.open(path, "r", encoding="utf-8", newline="") as f:
            raw = f.read().replace("\r\n", "\n").replace("\r", "\n")
        fixed, st = sanitize(raw.split("\n"))
        tb += st["bold"]
        tq += st["quote_lines"]
        all_odd += ["%s %s" % (fname, u) for u in st["unbalanced"]]

        if not dry:
            # 本项目硬规范：UTF-8 无 BOM + CRLF（按字节写，杜绝 LF 老坑复发）。
            with io.open(path, "wb") as f:
                f.write("\r\n".join(fixed).encode("utf-8"))
        print("  [%s] %-32s 粗体 %d 处 / 含引号 %d 行"
              % ("dry" if dry else "ok", fname, st["bold"], st["quote_lines"]))

    print("[sanitize] %d 个文件；去粗体 %d 处；处理含引号行 %d 行" % (len(files), tb, tq))
    if all_odd:
        print("[WARN] 以下行引号不是成对，已按「起始为「」处理，请人工确认：")
        for u in all_odd:
            print("       " + u)
    else:
        print("[OK] 引号全部成对")
    print("[dry] 未写回文件" if dry else "[OK] 已写回（UTF-8 无 BOM / CRLF）")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
