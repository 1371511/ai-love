# -*- coding: utf-8 -*-
"""世界书多文件读写（2026-09-17 拆分后）。

`_work/worldbook/` 目录下所有**非 `_` 开头**的 `.md` 按**文件名排序**拼接成一份文本。
拼接顺序必须与条目 order 递增一致（`_audit_worldbook.py` 会校验这个单调性）。

约定：
  · 文件名序号前缀（10 / 12 / 14 / 20 / …）是**排序锚**，不要改。
  · `_` 前缀的文件（如 `_README.md`）**不参与拼接**，用来放说明文字。
  · 每个文件以换行结尾；文件之间不放 `---` 也不影响结果
    （`md2card.py` 会剥离条目正文首尾的分离线）。
"""
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WB_DIR = os.path.join(HERE, "worldbook")
README = os.path.join(WB_DIR, "_README.md")


def list_files(wb_dir=None):
    """参与拼接的文件名列表（已排序，已排除 `_` 前缀）。"""
    d = wb_dir or WB_DIR
    return sorted(
        n for n in os.listdir(d)
        if n.endswith(".md") and not n.startswith("_")
    )


def read_text(wb_dir=None):
    """把目录里所有参与拼接的文件按序连成一份文本（换行统一成 LF）。"""
    d = wb_dir or WB_DIR
    parts = []
    for n in list_files(d):
        with io.open(os.path.join(d, n), "r", encoding="utf-8", newline="") as f:
            parts.append(f.read().replace("\r\n", "\n").replace("\r", "\n"))
    return "".join(parts)


def read_lines(wb_dir=None):
    """拼接后按行返回，等价于旧版 `open("worldbook.md").read().split("\\n")`。"""
    return read_text(wb_dir).split("\n")


def iter_files(wb_dir=None):
    """逐个产出 (文件名, 绝对路径)，供需要**逐文件写回**的脚本使用（如 _sanitize_md.py）。"""
    d = wb_dir or WB_DIR
    for n in list_files(d):
        yield n, os.path.join(d, n)


def read_lines_indexed(wb_dir=None):
    """返回 (lines, marks)。

    `lines` 是拼接后的行列表；`marks` 是 [(起始行号, 文件名)]（行号从 1 起）。
    多文件模式下，报错里的行号是「拼接后的行号」，靠 marks 才能还原成「哪个文件」。
    """
    d = wb_dir or WB_DIR
    lines, marks = [], []
    for n in list_files(d):
        with io.open(os.path.join(d, n), "r", encoding="utf-8", newline="") as f:
            t = f.read().replace("\r\n", "\n").replace("\r", "\n")
        marks.append((len(lines) + 1, n))
        lines += t.split("\n")
    return lines, marks


def locate(marks, lineno):
    """给定拼接后的行号，返回它所在的文件名（找不到返回 None）。"""
    name = None
    for start, n in marks:
        if lineno >= start:
            name = n
        else:
            break
    return name


if __name__ == "__main__":
    names = list_files()
    total = 0
    print("世界书目录：%s" % WB_DIR)
    for n, p in iter_files():
        with io.open(p, "rb") as f:
            b = f.read()
        lines = b.count(b"\n")
        total += lines
        bare = b.count(b"\n") - b.count(b"\r\n")
        print("  %-32s %4d 行  bareLF=%d" % (n, lines, bare))
    print("  合计 %d 个文件 / %d 行" % (len(names), total))
