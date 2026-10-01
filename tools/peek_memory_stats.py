#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔍 memory/ 体检（只读 / 零写入 / 不打印对话正文）—— 2026-10-01

为什么有这个脚本：
  记忆优化（docs/情绪模块.md 第六节）要靠两个数定方向：
  ① long_term_summary 有没有人真顶到 1500 字上限 ⇒ 决定「按段落丢老记忆」做不做
  ② key_facts 里有没有「祁煜答应了：」类承诺条目 ⇒ 决定「承诺待办」做不做
  本地 memory/ 只有测试号（cli / 99000001 / 99000002）⇒ 真数在服务器。
  本脚本只输出 **数字与布尔**，不打印一条正文 ⇒ 别人的对话不经过它。

用法（在**服务器**上跑；本地跑也行，看到的只是测试号）：
    cd /data1/ai-love && python3 tools/peek_memory_stats.py
    # 或显式指定目录：
    python3 tools/peek_memory_stats.py /data1/ai-love/memory

只读 {uid}.json（_diary/_profile/_mood/_daily/... 一律不碰）。
"""
import json
import os
import sys

PROMISE_PREFIXES = ("祁煜答应了：", "祁煜保证了：", "祁煜说会记住：")
COMPRESSED_MARK = "...(较早记忆已压缩)..."


def is_main_memory(name):
    """只认 {uid}.json —— 纯数字文件名、不带下划线后缀。"""
    if not name.endswith(".json"):
        return False
    stem = name[:-5]
    return stem.isdigit()


def main():
    if len(sys.argv) > 1:
        mem_dir = sys.argv[1]
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        mem_dir = os.path.normpath(os.path.join(here, "..", "memory"))
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not os.path.isdir(mem_dir):
        print("目录不存在：%s" % mem_dir)
        return 1

    rows = []
    for name in sorted(os.listdir(mem_dir)):
        if not is_main_memory(name):
            continue
        path = os.path.join(mem_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            rows.append((name[:-5], "读取失败: %s" % e, "", "", "", "", ""))
            continue
        if not isinstance(data, dict):
            rows.append((name[:-5], "顶层不是 dict", "", "", "", "", ""))
            continue

        summary = data.get("long_term_summary") or ""
        facts = data.get("key_facts")
        facts = facts if isinstance(facts, list) else []
        promises = sum(1 for x in facts
                       if isinstance(x, str) and x.startswith(PROMISE_PREFIXES))
        msgs = data.get("messages")
        msgs = [m for m in msgs if isinstance(m, dict)] if isinstance(msgs, list) else []
        with_ts = sum(1 for m in msgs if isinstance(m.get("ts"), (int, float)))
        days = data.get("day_summaries")
        days = days if isinstance(days, list) else []

        rows.append((
            name[:-5],
            str(len(summary)),
            "有" if summary.startswith(COMPRESSED_MARK) else "无",
            str(len(facts)),
            str(promises),
            "%d/%d" % (with_ts, len(msgs)),
            str(len(days)),
        ))

    header = ("uid", "摘要字数", "触顶痕迹", "事实条数", "承诺条数", "带ts/消息", "跨天小结")
    widths = [len(h) for h in header]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    line = "  ".join(h.ljust(w) for h, w in zip(header, widths))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    print()
    print("目录：%s" % mem_dir)
    print("判定：① 「摘要字数」max < 1500 且「触顶痕迹」全无 ⇒「按段落丢老记忆」可以先不做")
    print("       ③ 「承诺条数」全 0 ⇒「承诺待办」别做（提取模板从没响过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
