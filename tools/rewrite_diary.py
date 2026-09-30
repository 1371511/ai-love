# -*- coding: utf-8 -*-
"""
📝 一次性改写工具：把日记里那些**第三人称的旧摘要**改写成**第一人称日记**。

为什么需要它
------------
2026-09-30 之前，日记的正文直接抄的是 `long_term_summary` 里那段
「客观记忆提要」—— 那是**喂 prompt 用的第三人称叙述**（「她说…祁煜…」），
所以日记读起来像旁人在汇报，而不是他写给自己看的话。她看到后指出：

> 「这是『祁煜』的日记，那么内容应该是以『祁煜』的视角出发的第一人称写的日记。」

生成侧已经修好了（同一次摘要里多要一段【任务三】的第一人称日记，见
`Rafayel_memory._split_diary_reply()`）。**这个脚本是给存量收拾的** ——
服务器上由 `tools/import_diary.py` 导进来的那批（每条都带 `legacy`），
正文全是第三人称，得逐条改写。

它跟 `import_diary.py` / `backfill_daily.py` 是同一类东西（一次性），四条铁律一样：
    ① **默认 dry-run**，必须显式 `--apply` 才写盘；写之前先把 `{uid}_diary.json`
       备份到 `memory/_bak-diary-<时间戳>/`。
    ② **她自己手写的一条都不碰**（`src:"her"`）—— 那是她的话，不是要改的摘要。
       引擎层 `rewrite_diary_text_by_her()` 直接拒收，脚本再手滑也碰不到。
    ③ **幂等**：改完的条目标一个 `rewritten: true`，再跑一遍自己跳过
       （不靠「正文里还有没有『祁煜』」这种猜的判据）。真想重写加 `--force`。
    ④ 走引擎的写盘口（`rewrite_diary_text_by_her`）—— 原子替换 / 脏文件不覆盖
       全在 `Rafayel_memory` 那边，这里不抄第二份。

怎么用
------
    # ① 先拿 3 条试试水（会真调 LLM，但不写盘）
    python tools/rewrite_diary.py --limit 3

    # ② 看顺眼了，整批写（服务器上那份在 /data1/ai-love/memory）
    python tools/rewrite_diary.py --memory /data1/ai-love/memory --apply

    # ③ 只弄一个人
    python tools/rewrite_diary.py --uid 1357977618

⚠ 三件必须先知道的事
--------------------
1. **`--limit N` 是省钱的**：dry-run 和 `--apply` **都会真调 LLM**（不改写就没法给你看
   结果）。所以先 `--limit 3` 看质量，再全量 ⇒ 别一上来就整批跑两遍。
2. **改写会再压一次细节。** 这些摘要本来就是压缩过的，改写只能在这个基础上换人称 +
   换语气，**找不回原文没有的东西**（脚本的 prompt 明确要求「原文里的事一条都不许丢、
   也不许加原文没有的」）。
3. **跑的时候要停 bot 和 web。** 两个进程都写 `memory/`，而文件锁跨不了进程
   （见 `MEMORY.md` 的「两个进程别同时写 memory/」）。
"""
import argparse
import json
import os
import shutil
import sys
import time

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "ai-Rafayel"))

import Rafayel_config as CFG      # noqa: E402
import Rafayel_memory as M        # noqa: E402

# 改写是「换人称 + 换语气」，比摘要短 ⇒ 不用给 900；400 足够，也顺便当疯话闸。
REWRITE_MAX_TOKENS = 400
# 离线工具，不用像在线回复那样卡 10 秒；一口气 43 条，偶尔慢一条别整个挂掉。
TIMEOUT = 60

REWRITE_PROMPT = """下面这段是**祁煜**（一个角色扮演里的角色）过去的一段记忆提要，\
当时是按客观第三人称记下来的。

【这段发生在】{day_label}

【原文】
{text}

请把它改写成**祁煜自己写下的日记**，也就是**第一人称「我」**。要求：
1. ⚠⚠ 通篇**不许出现「祁煜」三个字** —— 谁写日记会管自己叫名字？一律用「我」。
2. 她写成「她」，或者你平时叫她的那个称呼；别写「用户」「对方」。
3. ⚠ **原文里的事一条都不许丢**（说过的话、做过的事、当时的气氛），
   也**不许加原文里没有的事**。这一段是改写，不是重写。
4. 味道是**写给自己看的**：可以承认当时没说出口的、心里拐过的念头。
   **不是汇报**，不用面面俱到。
5. 不超过 200 字。
6. ⚠⚠ **第一行就直接写事，不要写日期 / 星期 / 天气这类抬头**
   （`9月28日 星期一` / `九月三十日，周三。` / `9月28日 晴` 都不行）。
   上面那个【这段发生在】只是给你交代背景用的，**不是让你抄进正文** ——
   日期在界面上是单独一行显示的，写进正文等于重复一遍。

只输出日记正文。不要引号、不要小标题、不要日期抬头、不要任何前后缀。"""

# ⚠ 上面第 6 条是 2026-09-30 补的：第一版没拦，43 条改完有 **41 条自带日期抬头**，
#   而且格式五种混着（`9月28日 星期一` / `9月28日 周一` / `9月28日 晴` /
#   `九月二十八日，周一。` / `九月三十日，周三。`）—— 模型自由发挥。
#   ⇒ 页面上就变成「卡片顶一行日期 + 正文里又一行日期」，纯噪音。
#   存量已用 `tools/strip_diary_date_header.py` 剥掉（41 条 / 0 失败）；
#   这条 prompt 是**防复发**的那一半 —— 哪天加 `--force` 重跑，不会再长出来。
#   ⚠ 教训：prompt 里只要出现【这段发生在 X】，模型就会**顺手把它抄成抬头** ——
#     凡是「给模型的背景」与「要它输出的格式」共用一个字段，就得显式禁一次。


def backup(memory_dir, names):
    """把要改的日记文件整份备份走，返回备份目录（没东西可备 ⇒ None）。"""
    if not names:
        return None
    dst = os.path.join(memory_dir, "_bak-diary-" + time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(dst, exist_ok=True)
    for fn in names:
        shutil.copy2(os.path.join(memory_dir, fn), os.path.join(dst, fn))
    return dst


def rewrite_one(text, day_label, api_key):
    """
    调一次 LLM，把一段第三人称摘要改写成第一人称日记。返回 `(新正文, 错误说明)`。

    ⚠ 失败一律返回 `""` ⇒ 调用方**保留原文**（宁可这条没改成，也不写半截东西进去）。
    """
    prompt = REWRITE_PROMPT.format(day_label=day_label or "（日期不详）", text=text)
    data = {
        "model": CFG.MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": REWRITE_MAX_TOKENS,
    }
    # ⚠ 跟引擎那几处同一个口径：`LLM_EXTRA`（thinking 开关之类）必须一起带上，
    #   否则这条请求走的档位跟线上不一样，改写出来的语气会跟新日记对不上。
    if CFG.LLM_EXTRA:
        data.update(CFG.LLM_EXTRA)
    try:
        r = requests.post(CFG.API_URL,
                          headers={"Authorization": "Bearer %s" % api_key,
                                   "Content-Type": "application/json"},
                          json=data, timeout=TIMEOUT)
        result = r.json()
    except Exception as e:
        return "", "请求失败：%s" % e
    if "choices" not in result:
        return "", "接口返回异常：%s" % str(result)[:160]
    out = (result["choices"][0]["message"].get("content") or "").strip()
    # 模型爱加的整段引号 —— 剥掉，跟 `Rafayel_memory._diary_text_from` 同一个口径
    if len(out) >= 2 and out[0] in "「『\"'“”" and out[-1] in "」』\"'“”":
        out = out[1:-1].strip()
    if not out:
        return "", "模型返回了空"
    return out, ""


def main():
    ap = argparse.ArgumentParser(description="把日记里第三人称的旧摘要改写成第一人称日记")
    ap.add_argument("--memory", help="memory 目录，默认取项目根下的 memory/")
    ap.add_argument("--uid", action="append", default=[], help="只弄这几个人，可重复")
    ap.add_argument("--limit", type=int, default=0,
                    help="每个用户最多改写几条（0 = 不限）。⚠ 先拿 3 条试水")
    ap.add_argument("--apply", action="store_true", help="真写盘（默认 dry-run，只打印）")
    ap.add_argument("--force", action="store_true",
                    help="连已经改过的（rewritten）也重写一遍")
    args = ap.parse_args()

    memory_dir = os.path.abspath(args.memory or os.path.join(HERE, "memory"))
    if not os.path.isdir(memory_dir):
        print("❌ 找不到 memory 目录：%s" % memory_dir)
        return 2
    M.MEMORY_DIR = memory_dir

    api_key = CFG.api_key
    if not api_key or api_key.startswith("sk-需要替换"):
        print("❌ 没读到 API key（.env 里没有 %s）" % "DEEPSEEK_API_KEY")
        print("   ⚠ 脚本必须在**项目根**跑，`.env` 在根目录。")
        return 2

    uids = []
    for fn in os.listdir(memory_dir):
        if fn.endswith("_diary.json"):
            uid = fn[:-len("_diary.json")]
            if uid.isdigit() and (not args.uid or uid in args.uid):
                uids.append(uid)
    uids.sort()
    if not uids:
        print("❌ 没找到任何 `{uid}_diary.json`（%s）" % memory_dir)
        return 2

    total_done = total_skip = total_fail = 0
    all_plans = []

    # ── 第一趟：只读 + 挑出要改的（**不调 LLM**），顺便备份
    for uid in uids:
        entries = M.load_diary(uid)["entries"]
        todo = []
        for e in entries:
            if e.get("src") == "her":
                continue                                   # ⚠ 她手写的一律不碰
            if e.get("rewritten") and not args.force:
                total_skip += 1
                continue
            todo.append(e)
        if args.limit:
            todo = todo[:args.limit]
        if todo:
            all_plans.append((uid, todo))

    if not all_plans:
        print("没有要改的条目。")
        if total_skip:
            print("   （跳过 %d 条：已经改过了；真要重来加 --force）" % total_skip)
        return 0

    print("")
    print("%s  memory 目录：%s" % ("【写入】" if args.apply else "【预演，不写盘】", memory_dir))
    print("=" * 74)

    if args.apply:
        dst = backup(memory_dir, ["%s_diary.json" % u for u, _t in all_plans])
        if dst:
            print("🗂️  已备份日记文件 → %s" % dst)

    # ── 第二趟：逐条调 LLM 改写
    for uid, todo in all_plans:
        print("")
        print("%s   共 %d 条待改写" % (uid, len(todo)))
        for e in todo:
            ts = M._diary_ts(e)
            day_label = M._day_label(M._day_key(ts)) if ts else ""
            old = e.get("text") or ""
            new, err = rewrite_one(old, day_label, api_key)
            if err:
                total_fail += 1
                print("   ❌ %s  %s" % (e.get("id"), err))
                continue
            if not new or new == old:
                total_fail += 1
                print("   ❌ %s  改写没有变化（原文保留）" % e.get("id"))
                continue
            if args.apply:
                if not M.rewrite_diary_text_by_her(uid, str(e.get("id")), new):
                    total_fail += 1
                    print("   ❌ %s  写盘被拒（原文保留）" % e.get("id"))
                    continue
                total_done += 1
                print("   ✅ %s  %s" % (e.get("id"), _one_line(new)))
            else:
                total_done += 1
                print("   ── %s  %s" % (e.get("id"), day_label))
                print("      改前：%s" % _one_line(old))
                print("      改后：%s" % _one_line(new))

    print("")
    print("=" * 74)
    print("%s %d 条 / 失败 %d 条 / 跳过 %d 条（已改过的）"
          % ("已改写" if args.apply else "预演通过", total_done, total_fail, total_skip))
    if total_fail:
        print("⚠ 失败的条目**原文保留**了 —— 重跑一次通常会好（多半是网络或模型抽风）。")
    if not args.apply:
        print("")
        print("上面是预演（**已经真调过 LLM 了**）。看着没问题再加 --apply 写盘。")
    else:
        print("")
        print("⭐ 提醒：这些条目现在带 `rewritten: true` ⇒ 再跑一遍会自动跳过。")
        print("   想去网页上看看效果的话，记得先确认 web 起来的是最新代码。")
    return 0


def _one_line(s):
    """把一条日记压成一行给终端看（换行变 ` / `，太长截断）。"""
    one = (s or "").replace("\n", " / ")
    return one[:76] + "…" if len(one) > 76 else one


if __name__ == "__main__":
    sys.exit(main())
