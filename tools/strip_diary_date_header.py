# -*- coding: utf-8 -*-
"""
🧹 剥掉日记正文开头的「日期抬头」（一次性，**不调 LLM**）。

## 什么时候用

`tools/rewrite_diary.py` 第一版的 prompt 里有一行【这段发生在 9月28日 星期一】，
模型很自然地按日记习惯**把这行抄成了抬头**，而且格式五种混着：

    9月28日 星期一 / 9月28日 周一 / 9月28日 晴 / 九月二十八日，周一。 / 九月三十日，周三。

而页面上卡片顶部**本来就有**一行日期（`group_diary()` 的 `day_label`）
⇒ 正文里这行是重复的噪音。2026-09-30 实测：43 条里 **41 条**长出了抬头。

⚠ **prompt 已经补了第 6 条禁掉它** ⇒ 正常情况下**不会再有**抬头要剥。
   这个脚本留着是给「已经长出来的那批」兜底 + 万一将来又重蹈覆辙。

## 做法

只剥**正文最开头**那一段「日期（阿拉伯或汉字数字）+ 可选星期 + 可选天气 + 收尾标点」，
其余一个字都不动。⚖ 两个刻意的不作为：
  - 剥完**不足 10 字** ⇒ 不动（宁可留着抬头，也不要写出一个空条目）；
  - **`src:"her"` 一律跳过**（她手写的没有抬头，也不该被这个脚本碰）。

写盘走引擎的 `rewrite_diary_text_by_her()` —— 同一套闸门（拒收 `src:"her"` /
不碰时间与 `day` / 原子落盘），**不自己 `json.dump`**。

## 用法（项目根）

    venv/bin/python tools/strip_diary_date_header.py            # 干跑，只打印
    venv/bin/python tools/strip_diary_date_header.py --apply    # 真写盘（先自动备份到 /tmp）

⚠ 跟一次性脚本的老规矩一样：**跑之前停掉 bot 和 web**（两个进程都写 `memory/`，
   而文件锁跨不了进程）。跑起来很快（不调模型），停机窗口只有几秒。
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "ai-Rafayel"))

import Rafayel_memory as M      # noqa: E402

# 抬头 = 日期（阿拉伯或汉字数字）+ 可选的星期 / 天气 + 收尾标点
DATE_HEAD = re.compile(
    r"^\s*"
    r"(?:\d{1,2}月\d{1,2}日|[〇零一二三四五六七八九十]{1,3}月[〇零一二三四五六七八九十]{1,3}日)"
    r"\s*[，,。.]?\s*"
    r"(?:(?:星期|周)[一二三四五六日天])?"
    r"\s*[，,。.]?\s*"
    r"(?:晴|阴|雨|多云|小雨|大雨|雪)?"
    r"\s*[，,。.]?\s*"
    r"\n*"
)

MIN_KEEP = 10       # 剥完短于这个长度 ⇒ 放弃（别写出空条目）


def strip_head(text):
    """返回 `(新正文, 有没有改)`。改不了 / 不该改 ⇒ 原样返回。"""
    m = DATE_HEAD.match(text)
    if not m:
        return text, False
    rest = text[m.end():].lstrip()
    if len(rest) < MIN_KEEP:
        return text, False
    return rest, True


def main():
    ap = argparse.ArgumentParser(description="剥掉日记正文开头的日期抬头")
    ap.add_argument("--memory", help="memory 目录，默认取项目根下的 memory/")
    ap.add_argument("--apply", action="store_true", help="真写盘（默认干跑，只打印）")
    args = ap.parse_args()

    mem = os.path.abspath(args.memory or os.path.join(HERE, "memory"))
    if not os.path.isdir(mem):
        print("❌ 找不到 memory 目录：%s" % mem)
        return 2
    M.MEMORY_DIR = mem

    todo = []
    for p in sorted(glob.glob(os.path.join(mem, "*_diary.json"))):
        uid = os.path.basename(p)[:-len("_diary.json")]
        if not uid.isdigit():
            continue
        for e in (M.load_diary(uid)["entries"] or []):
            if e.get("src") == "her":
                continue
            t = str(e.get("text") or "")
            new, changed = strip_head(t)
            if changed:
                todo.append((uid, str(e.get("id")), t, new))

    print("=" * 66)
    print("要剥抬头: %d 条（%s）" % (len(todo), mem))
    for uid, eid, old, new in todo[:3]:
        print("-" * 66)
        print(" 改前:", old[:70].replace("\n", " / "))
        print(" 改后:", new[:70].replace("\n", " / "))
    print("=" * 66)

    if not todo:
        print("没有要剥的 —— 已经干净了。")
        return 0
    if not args.apply:
        print("干跑结束 —— 没写盘。确认没问题后加 --apply。")
        return 0

    # ⚠ 备份放 /tmp（**不放进 memory/**）：这是「剥之前那一版」，只为了出问题能还原，
    #   不是要长期留档的东西，塞进 memory/ 只会跟 `_bak-diary-*` 混起来看不懂。
    bk = os.path.join("/tmp", "rw-backup-%s" % time.strftime("%m%d-%H%M"))
    os.makedirs(bk, exist_ok=True)
    for p in glob.glob(os.path.join(mem, "*_diary.json")):
        shutil.copy2(p, os.path.join(bk, os.path.basename(p)))
    print("改前已备份到:", bk)

    ok = bad = 0
    for uid, eid, _old, new in todo:
        if M.rewrite_diary_text_by_her(uid, eid, new):
            ok += 1
        else:
            bad += 1
            print("  ⚠ 写失败:", uid, eid)
    print("已剥 %d 条 / 失败 %d 条" % (ok, bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
