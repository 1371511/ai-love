# -*- coding: utf-8 -*-
r"""世界书体检（2026-10-04 加，配合卡面剧情批次）。

只读，不写任何文件。跑法：

    cd /d E:\ai-love\card\_work
    python _chk_wb.py

查六件事（任何一项有问题 ⇒ exit 2）：
  1. md 源条数 == JSON 条数（漏文件 / 多条目）
  2. 触发词不重复（不区分大小写）
  3. order 不重复（审计只查文件内递增，跨文件撞号它抓不到）
  4. 没有正文 > 1400 字的条目（超过 WB_MAX_CHARS ⇒ 永远注入不进来）
  5. 没有空正文 / 空触发词
  6. 正文里没有 `**` 加粗、ASCII 双引号（应写「」）
"""
import json
import os
import re
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
WB_JSON = os.path.join(os.path.dirname(HERE), "worldbook.json")
MAX_CHARS = 1400          # Rafayel_config.WB_MAX_CHARS


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ents = json.load(open(WB_JSON, encoding="utf-8"))["entries"]

    # md 源条数
    sys.path.insert(0, HERE)
    import _wb_io
    md_n = len(re.findall(r"^###\s+(.*)$", _wb_io.read_text(), flags=re.M))

    keys = collections.defaultdict(list)
    orders = collections.defaultdict(list)
    longs, empty_c, empty_k, style = [], [], [], []

    for k, v in ents.items():
        name = v.get("comment", "uid=%s" % k)
        content = v.get("content", "")
        orders[v.get("order")].append(name)
        if len(content) > MAX_CHARS:
            longs.append((name, len(content)))
        if not content.strip():
            empty_c.append(name)
        if not (v.get("key") or []):
            empty_k.append(name)
        for w in v.get("key", []):
            keys[w.lower()].append(name)
        if "**" in content:
            style.append("%s（有 ** 加粗）" % name)
        if '"' in content:
            style.append("%s（有 ASCII 双引号）" % name)

    dup_k = {w: n for w, n in keys.items() if len(n) > 1}
    dup_o = {o: n for o, n in orders.items() if len(n) > 1}

    print("世界书体检 —— %s" % WB_JSON)
    print("  条目数        : JSON %d / md 源 %d %s" %
          (len(ents), md_n, "OK" if len(ents) == md_n else "❌ 不一致（漏文件或多条目）"))
    print("  触发词总数    : %d" % sum(len(v.get("key", [])) for v in ents.values()))
    print("  order 范围    : %s → %s" % (min(orders), max(orders)))
    print("  正文总字数    : %d（最长 %d 字）" %
          (sum(len(v.get("content", "")) for v in ents.values()),
           max(len(v.get("content", "")) for v in ents.values())))
    print()
    print("  [1] 条数一致      : %s" % ("OK" if len(ents) == md_n else "❌"))
    print("  [2] 触发词重复    : %s" % ("OK" if not dup_k else "❌ %s" % dup_k))
    print("  [3] order 撞号    : %s" % ("OK" if not dup_o else "❌ %s" % dup_o))
    print("  [4] 超 %d 字条目 : %s" % (MAX_CHARS, "OK" if not longs else "❌ %s" % longs))
    print("  [5] 空正文/空词   : %s" %
          ("OK" if not empty_c and not empty_k else "❌ 空正文=%s 空触发词=%s" % (empty_c, empty_k)))
    print("  [6] 粗体/引号残留 : %s" % ("OK" if not style else "❌ %s" % style))

    bad = bool(dup_k or dup_o or longs or empty_c or empty_k or style) or len(ents) != md_n
    print("\n结论：%s" % ("❌ 有问题，改完再生成" if bad else "✅ 全部通过"))
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
