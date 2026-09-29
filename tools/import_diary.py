# -*- coding: utf-8 -*-
"""
📥 一次性导入工具：把 `long_term_summary` 里**已经攒下**的内容，拆成日记条目。

为什么需要它
------------
2026-09-30 之前，bot 每次摘要只往 `long_term_summary` 里追加（1500 字的滚动窗口），
**界面上从来没显示过**。日记上线之后，那些老内容还是空的 —— 这本工具就是把它们
搬过去。**一次性**，搬完就该删，别留着当常规操作。

它跟 `backfill_daily.py` 是同一类东西（一次性回填），三条铁律也一样：
    ① **只读 `{uid}.json`，一个字都不写** —— 那份对话记忆是 bot 的，写坏他人设就崩。
       本工具唯一产出是 `memory/{uid}_diary.json`。
    ② **默认 dry-run**，必须显式 `--apply` 才写盘；写之前先把已存在的日记文件备份到
       `memory/_bak-diary-<时间戳>/`。
    ③ **不删 / 不覆盖**：已经导过一次的人（日记文件里已有条目）默认跳过，
       重复跑不会叠加出两份。真想重来加 `--force`（会先清掉**上次导入出来的**那几条，
       **她自己手写的一条都不动** —— 见 `purge_legacy()`）。

怎么用
------
    # ① 先看会导成什么样（不写盘）
    python tools/import_diary.py

    # ② 确认预览没问题再写
    python tools/import_diary.py --apply

    # ③ 只导一个人 / 指定 memory 目录（服务器上的那份不在项目里）
    python tools/import_diary.py --uid 1357977618
    python tools/import_diary.py --memory /data1/ai-love/memory --apply

⚠ 三件必须先知道的事
--------------------
1. **历史条目的时间是「编」的。** `long_term_summary` 是一整段拼起来的字符串，
   **里面没有任何时间戳** ⇒ 每条几点几分无从考证。本工具的做法：
   全部挂到「最后一次活跃那天」（见下面取值顺序），时间从那天 21:00 往前
   每条退 1 分钟。⇒ 显示出来的 HH:MM **不代表真实时刻**，只保证
   ① 落在同一天 ② 先后顺序跟原文本一致。**日期是准的，钟点不是。**
2. **分段的依据是按空行切**（`long_term_summary` 就是用 `"\\n\\n"` 拼的，每段是
   一次摘要）。模型偶尔会写出多段 ⇒ 可能**多切出一条**。⇒ 所以默认 dry-run，
   请你先看一眼预览；觉得切碎了就先手工删掉那几块再导。
3. **窗口被压过的那部分找不回来了。** 超过 1500 字之后，引擎会把旧的从**前面砍掉**
   （并留下 `...(较早记忆已压缩)...` 这个标记）⇒ 那批最早的摘要**永久丢失**，
   本工具没法变魔法。真要找回只能去服务器更早的 `{uid}.json` 备份里翻。

「最后一次活跃那天」的取值顺序
------------------------------
    `--day` > `{uid}.json` 里的 `last_msg_at`（她最后说话的时间）
            > `saved_at`（上次存盘时间）> 文件的修改时间 > 今天
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "ai-Rafayel"))

import Rafayel_config as CFG      # noqa: E402
import Rafayel_memory as M        # noqa: E402

# 🈳 这两句是引擎写的占位 / 兜底文案，**不是**真摘要 ⇒ 一律不导
PLACEHOLDERS = (
    "（你们刚开始聊天，还没有值得记录的重要事件。）",
    "（本轮无可摘要内容）",
)
# 🗜 滚窗口压缩标记（超过 1500 字时加在最前面）。它后面那段可能是**被砍了一半的**。
COMPRESS_MARK = "...(较早记忆已压缩)..."

RE_BLANKS = re.compile(r"\n\s*\n")


# ---------------------------------------------------------------- 读

def read_memory(path):
    """读 `memory/{uid}.json`。读不了 ⇒ 返回 `{}`（坏文件不该让整趟导入挂掉）。"""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        print("   ⚠️  读不了 %s：%s" % (os.path.basename(path), e))
        return {}


def pick_day(data, path, override=""):
    """
    决定这批条目挂在哪一天。返回 `YYYY-MM-DD`。

    ⚠ 见文件头第 1 条：**日期是准的**（来自「最后一次活跃」那个真时间戳），钟点才是编的。
    ⚠ `saved_at` 是引擎写的 `"YYYY-MM-DD HH:MM:SS"` ⇒ 直接切前 10 位就行，
       不用 `strptime` 反解（跨机器时区不一致，`strptime` 反而容易出错）。
    """
    if override:
        return override
    stamp = data.get("last_msg_at")
    if isinstance(stamp, (int, float)) and stamp > 0:
        return M._day_key(float(stamp))
    for key in ("saved_at", "updated_at"):
        v = str(data.get(key) or "").strip()
        if len(v) >= 10 and v[4] == "-":
            return v[:10]
    try:
        return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
    except Exception:
        return time.strftime("%Y-%m-%d")


def split_blocks(text):
    """
    把一整段 `long_term_summary` 拆成若干条。返回 `(blocks, truncated)`。

    ⚠ `truncated=True` 说明这份摘要被 1500 字窗口压缩过 ⇒ **第一块可能是半句话**。
    ⚠ 见文件头第 2 条：按空行切是**启发式**，模型写多段就会多切。
    ⭐ 短于 10 字的碎片会并回上一条 —— 那种多半是模型输出里的残句 / 小标题，
       单独立一条在日记里很怪。
    """
    truncated = False
    s = (text or "").strip()
    if not s:
        return [], False
    if s.startswith(COMPRESS_MARK):
        truncated = True
        s = s[len(COMPRESS_MARK):].lstrip("\n")

    blocks = [b.strip() for b in RE_BLANKS.split(s) if b.strip()]
    blocks = [b for b in blocks if b not in PLACEHOLDERS]

    merged = []
    for b in blocks:
        if merged and len(b) < 10:
            merged[-1] = merged[-1] + "\n" + b
        else:
            merged.append(b)
    return merged, truncated


def plan_blocks(blocks, day):
    """
    给每一条分配时间戳。返回 `[(ts, text), ...]`。

    ⭐ 从那天 **21:00** 往前，每条退 1 分钟：**最后一条（最新的）时间最晚**
    ⇒ 列表页「天内正序」排出来跟原文顺序一致。
    ⚠ 为什么不干脆全给同一个时间：`sorted` 是稳定的，同 ts 也能保序，
       但那样日记里会并排显示五个一样的 `21:00`，看着像 bug。
    """
    try:
        base = datetime.datetime.strptime(day + " 21:00:00", "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        base = time.time()
    n = len(blocks)
    return [(base - (n - 1 - i) * 60, t) for i, t in enumerate(blocks)]


# ---------------------------------------------------------------- 写

def backup(memory_dir, names):
    """把已经存在的日记文件整份备份走，返回备份目录（没东西可备 ⇒ None）。"""
    if not names:
        return None
    dst = os.path.join(memory_dir, "_bak-diary-" + time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dst, exist_ok=True)
    for fn in names:
        shutil.copy2(os.path.join(memory_dir, fn), os.path.join(dst, fn))
    return dst


def purge_legacy(uid):
    """
    🧹 把上次导入出来的那批条目清掉（`legacy=True` 的），**她自己手写的一条都不动**。

    ⭐ 为什么 `--force` 必须先清一遍：
       直接追加会把同一批旧摘要导两遍（她一路核对过来发现日记里全是双胞胎）。
    ⭐ 为什么**只删 legacy**：
       导入出来的东西 = `long_term_summary` 的副本，**再导一次就能重建**；
       她手写在网页上的那几条**没有第二份** —— 删了就真没了。
       ⇒ 判据是 `legacy` 标记，不是时间先后（她可能在上次导入之后才手写）。
    ⚠ 一条一条走 `delete_diary_by_her()`：写盘那套（原子替换 / 脏文件不覆盖）
      全在引擎那边，这里不再抄一遍。
    """
    n = 0
    for e in list(M.load_diary(uid)["entries"]):
        if e.get("legacy") and M.delete_diary_by_her(uid, str(e.get("id"))):
            n += 1
    return n


def apply_uid(uid, items, day):
    """落盘。⭐ 走引擎的 `add_diary()` —— 格式 / 上限 / 脏文件保护全在那边。"""
    n = 0
    for ts, text in items:
        if M.add_diary(uid, text, src="he", ts=ts, day=day, legacy=True):
            n += 1
    return n


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="把 long_term_summary 里已有的内容导成日记")
    ap.add_argument("--memory", help="memory 目录，默认取项目根下的 memory/")
    ap.add_argument("--uid", action="append", default=[], help="只导这几个人，可重复")
    ap.add_argument("--day", help="指定挂在那天（YYYY-MM-DD）；不给就按最后一次活跃推算")
    ap.add_argument("--apply", action="store_true", help="真写盘（默认 dry-run）")
    ap.add_argument("--force", action="store_true",
                    help="已经导过也重导（会先清掉上次导入出来的那几条，她手写的不动）")
    args = ap.parse_args()

    memory_dir = os.path.abspath(args.memory or os.path.join(HERE, "memory"))
    if not os.path.isdir(memory_dir):
        print("❌ 找不到 memory 目录：%s" % memory_dir)
        return 2
    M.MEMORY_DIR = memory_dir

    if not CFG.DIARY_ENABLE:
        print("❌ `DIARY_ENABLE` 现在是 False ⇒ 日记功能关着，导入没有意义。")
        print("   先把 `ai-Rafayel/Rafayel_config.py` 里的 `DIARY_ENABLE` 改回 True 再来。")
        return 2

    # 目录里所有看起来是 QQ 号的记忆文件（`_daily` / `_profile` / `_diary` 这些尾巴排除掉）
    uids = []
    for fn in os.listdir(memory_dir):
        if not fn.endswith(".json"):
            continue
        uid = fn[:-5]
        if uid.isdigit():
            if args.uid and uid not in args.uid:
                continue
            uids.append(uid)
    uids.sort()
    if args.uid:
        missing = [u for u in args.uid if u not in uids]
        for u in missing:
            print("⚠️  --uid %s 在 %s 里没有记忆文件" % (u, memory_dir))
    if not uids:
        print("❌ 没找到任何人的记忆文件。")
        return 2

    plans, skipped = [], []
    for uid in uids:
        path = os.path.join(memory_dir, "%s.json" % uid)
        data = read_memory(path)
        raw_summary = str(data.get("long_term_summary") or "").strip()
        if not raw_summary or raw_summary in PLACEHOLDERS:
            continue                                   # 还没攒下东西，跳过就好
        blocks, truncated = split_blocks(raw_summary)
        if not blocks:
            continue
        # ⭐ 幂等：导过一次就不再导（她可能会忍不住跑第二遍）
        dpath = os.path.join(memory_dir, "%s_diary.json" % uid)
        had = bool(M.load_diary(uid)["entries"]) if os.path.isfile(dpath) else False
        if had and not args.force:
            skipped.append((uid, len(blocks)))
            continue
        day = pick_day(data, path, args.day)
        plans.append((uid, day, blocks, truncated))

    if not plans:
        print("没有需要导的人。（%s 里的 `long_term_summary` 都是空的，或者已经导过了）"
              % memory_dir)
        for uid, n in skipped:
            print("   %s  已跳过：日记里已有内容（真要重导加 --force）" % uid)
        return 0

    print("")
    print("%s  memory 目录：%s" % ("【写入】" if args.apply else "【预演，不写盘】", memory_dir))
    print("=" * 74)

    if args.apply:
        dst = backup(memory_dir, ["%s_diary.json" % u for u, _d, _b, _t in plans
                                  if os.path.isfile(os.path.join(memory_dir,
                                                                 "%s_diary.json" % u))])
        if dst:
            print("🗂️  已备份已存在的日记文件 → %s" % dst)

    total = 0
    for uid, day, blocks, truncated in plans:
        items = plan_blocks(blocks, day)
        cleared = purge_legacy(uid) if args.apply else 0
        n = apply_uid(uid, items, day) if args.apply else len(items)
        total += n
        flag = "  ⚠ 被窗口压缩过，第一条可能是半句" if truncated else ""
        print("")
        print("%s   挂在 %s  共 %d 条%s" % (uid, day, n, flag))
        if cleared:
            print("      （--force：先清掉了上次导入的 %d 条；她手写的一条没动）" % cleared)
        for i, (_ts, t) in enumerate(items[:3]):
            one = t.replace("\n", " / ")
            print("      %d. %s%s" % (i + 1, one[:44], "…" if len(one) > 44 else ""))
        if len(items) > 3:
            print("      … 还有 %d 条" % (len(items) - 3))

    print("")
    print("=" * 74)
    print("合计 %d 个人 / %d 条。" % (len(plans), total))
    for uid, n in skipped:
        print("   %s  已跳过：日记里已有 %s 条（真要重导加 --force）" % (uid, n))

    if not args.apply:
        print("")
        print("上面是预演。确认预览里的分段没问题，再加 --apply 真写盘。")
    else:
        print("")
        print("⚠ 导入的这批**时间是编的**（文件开头第 1 条说了为什么）：日期对，钟点不对。")
        print("  介意的话去网页端改掉那几条的时间性描述，或者干脆删掉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
