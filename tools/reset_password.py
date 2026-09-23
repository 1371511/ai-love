# -*- coding: utf-8 -*-
"""
🔑 重置网页端（好感后台）某个人的密码 —— **开发者手动工具**。

为什么是「开发者手动」而不是「用户在网页上自助」
------------------------------------------------
网页端是**公网直开**的（8081 谁都能连）。一旦做了「填 QQ 号就能自助重置」，
知道别人 QQ 号的人就能把那个人踢出去、自己登进去 —— 比现在统一默认密码还糟。
而自助重置要验证身份，就得再收集邮箱 / 手机 / 安全问题，这个项目里一样都没有。

⭐ 现成的身份锚点只有一个：**QQ 私聊**（她能私聊到祁煜 = 她就是那个号）。
所以口径定为：她忘了密码 ⇒ 她来找你（开发者）⇒ 你跑这一条命令 ⇒ 你把新密码告诉她。

用法
----
    # ① 看现在有哪些号（谁开过、谁只是聊过还没开）
    python tools/reset_password.py --list

    # ② 重置成统一默认密码（qiyu2026）—— 最常用
    python tools/reset_password.py 1357977618

    # ③ 直接设成你指定的密码（她想要个好记的）
    python tools/reset_password.py 1357977618 新的密码

    # ④ 从来没登过的号也能预设（memory 里有他 = 真聊过 ⇒ 帮他开号并设密码）
    python tools/reset_password.py 1357977618          # 同上，自动开号

    # ⑤ 连 memory 里都没有的号（多半是打错了）⇒ 默认拒绝，确认了再加 --force
    python tools/reset_password.py 1357977618 --force

⚠ 三条铁律
----------
① **只写 `web/users.json`**，绝不碰 bot 的 memory（那份只读，写坏他人设就崩）。
② **写之前自动备份**到 `web/users.json.bak-<时间戳>`（保留最近 5 份）⇒ 改错了能还原。
③ **密码哈希不自己实现** —— 直接 import `web/app.py` 的 `_hash`，
   跟网页端校验用的是同一个函数（各写一份迟早对不上，那种 bug 最难查）。

服务器上
--------
    cd ~/ai-love && venv/bin/python tools/reset_password.py --list
"""
import argparse
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "web"))
sys.path.insert(0, os.path.join(BASE, "ai-Rafayel"))

import app as WEB  # noqa: E402

BAK_KEEP = 5


def _backup():
    """写之前备份 `users.json`，只留最近 N 份。返回备份路径。"""
    src = WEB.USERS_PATH
    if not os.path.exists(src):
        return ""
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = "%s.bak-%s" % (src, ts)
    shutil.copy2(src, dst)
    d = os.path.dirname(src)
    olds = sorted((f for f in os.listdir(d) if f.startswith("users.json.bak-")),
                  reverse=True)
    for f in olds[BAK_KEEP:]:
        try:
            os.remove(os.path.join(d, f))
        except OSError:
            pass
    return dst


def _list(users, known):
    print("已开号的（web/users.json，共 %d）：" % len(users))
    if not users:
        print("  （空）")
    for u in sorted(users):
        print("  %-16s %s" % (u, (users[u] or {}).get("note", "") or ""))
    print("")
    never = sorted(known - set(users))
    print("聊过、但还没开号的（登录时会自动开号，共 %d）：" % len(never))
    if not never:
        print("  （无）")
    for u in never:
        print("  %-16s" % u)
    print("")
    print("默认密码：%s" % WEB.DEFAULT_PWD)


def main():
    ap = argparse.ArgumentParser(description="重置网页端某个人的密码（开发者工具）")
    ap.add_argument("uid", nargs="?", help="QQ 号")
    ap.add_argument("new_pwd", nargs="?", help="新密码；不填就重置成默认密码")
    ap.add_argument("--list", action="store_true", help="只列出账号，不改任何东西")
    ap.add_argument("--force", action="store_true",
                    help="memory 里没这个号也照样开（多半是打错号，慎用）")
    args = ap.parse_args()

    users = WEB._load_users()
    known = WEB._known_uids()

    if args.list or not args.uid:
        _list(users, known)
        return 0

    uid = args.uid.strip()
    if uid not in known and not args.force:
        print("⚠️ %s 不在 memory 里（= 没跟他说过话），多半是 QQ 号打错了。" % uid)
        print("   确认要开这个号就加 --force。")
        return 2

    pwd = args.new_pwd or WEB.DEFAULT_PWD
    existed = uid in users
    old_note = (users.get(uid) or {}).get("note", "")

    bak = _backup()
    users[uid] = {"pwd": WEB._hash(pwd), "note": old_note}
    WEB._save_users(users)

    print("✅ 已重置 %s" % uid)
    print("   新密码：%s%s" % (pwd, "（统一默认密码）" if not args.new_pwd else ""))
    print("   %s" % ("改了他原来的密码" if existed else "新开的号（他之前没登过）"))
    if bak:
        print("   备份：%s" % os.path.basename(bak))
    print("")
    print("👉 把新密码告诉她，让她登录后去 /settings 里自己改一个。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
