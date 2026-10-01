#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
📥 把服务器拉下来的记忆快照，改名导入成本地已有的测试号 —— 2026-10-01

为什么需要它：
  本地要能在页面上看到真实数据，得用 `web/users.json` 里**已有**的 uid 才登得进去；
  而 `users.json` 是 **git 跟踪文件**（往里加新 uid 会进提交）。
  ⚠ 反过来 `memory/` 整个被 `.gitignore` 挡住（`git ls-files memory/` 为空）
    ⇒ memory/ 里的文件叫什么名字，git 都看不见。
  ⇒ 最干净的路子：**把拉下来的数据改名成本地已有的测试号 `cli`**，不动 users.json 一个字节。

用法（在仓库根跑）：
    python tools/import_memory_snapshot.py <源uid> <目标uid>
    # 例：把刚拉进 memory/ 的 <源uid>* 变成 cli*（cli 在 web/users.json 里已有 ⇒ 能登进去看）
    python tools/import_memory_snapshot.py <源uid> cli

行为（**只改文件名，内容一个字节不动**）：
  1. 备份现有 `memory/<目标uid>*.json` → `memory/_bak-snapshot-<时间戳>/`
  2. 逐个把 `memory/<源uid>.json` / `memory/<源uid>_*.json`
     复制成 `memory/<目标uid>.json` / `memory/<目标uid>_*.json`
  3. **不删源文件** —— 原样留着，等你确认真实数据在页面上跑通了再自己清

⚠ 跑之前请确认：源文件已经在 `memory/` 里（由 scp 拉下来）。
   本脚本 **不联网、不 import 引擎模块、不写 memory/ 以外的地方**。
"""
import os
import shutil
import sys
import time


def split_uid(name):
    """`<uid>_diary.json` → `<uid>`；`<uid>.json` → `<uid>`。"""
    stem = name[:-5] if name.endswith(".json") else name
    return stem.split("_")[0]


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    src_uid, dst_uid = sys.argv[1].strip(), sys.argv[2].strip()
    if src_uid == dst_uid:
        print("⚠ 源和目标相同，什么都不用做。")
        return 1

    here = os.path.dirname(os.path.abspath(__file__))
    mem_dir = os.path.normpath(os.path.join(here, "..", "memory"))
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not os.path.isdir(mem_dir):
        print("找不到 memory 目录：%s" % mem_dir)
        return 1

    names = [n for n in os.listdir(mem_dir)
             if n.endswith(".json") and split_uid(n) == src_uid]
    if not names:
        print("⚠ memory/ 里没有 %s*.json —— 先 scp 拉下来再跑本脚本。" % src_uid)
        print("  目录：%s" % mem_dir)
        return 1

    # ① 备份现有的目标号（一个都没有就跳过）
    olds = [n for n in os.listdir(mem_dir)
            if n.endswith(".json") and split_uid(n) == dst_uid]
    bak_dir = None
    if olds:
        bak_dir = os.path.join(mem_dir, "_bak-snapshot-%s" % time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(bak_dir, exist_ok=True)
        for n in olds:
            shutil.copy2(os.path.join(mem_dir, n), os.path.join(bak_dir, n))
        print("① 备份 %d 个旧文件 → %s" % (len(olds), os.path.relpath(bak_dir, mem_dir)))
    else:
        print("① 原本没有 %s* 文件，无需备份" % dst_uid)

    # ② 改名复制
    print("② 导入：")
    for n in sorted(names):
        new_name = dst_uid + n[len(src_uid):]
        shutil.copy2(os.path.join(mem_dir, n), os.path.join(mem_dir, new_name))
        print("   %-34s → %s" % (n, new_name))

    # ③ 提示
    print()
    print("✅ 好了。现在用 `%s` 登录本地网页端，看到的就是这份数据。" % dst_uid)
    print("   ⚠ 页面上的称呼/资料是**那个用户**的（profile 一起导进来了），不是你的。")
    print("   ⚠ 源文件 %s*.json 还留在 memory/ 里没动；确认跑通后可以删：" % src_uid)
    print("        python -c \"import os,glob;[os.remove(f) for f in glob.glob(r'%s%s*.json')]\""
          % (mem_dir.replace("\\", "/") + "/", src_uid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
