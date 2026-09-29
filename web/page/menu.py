# -*- coding: utf-8 -*-
"""
🧭 目录页（`/menu`）—— **登录之后的落点**（2026-09-29 她定的 IA）。

· 手机：一整页入口列（就是这个文件渲染出来的）
· 桌面：**同一份 HTML** 渲染成左侧栏（纯 CSS `@media`，第 3 步做，不另写一套）

⇒ 两端只有**一份数据**：`base.py` 的 `NAV`。
⚠ 想加 / 改 / 隐藏一个入口 ⇒ 改 `base.py` 的 `NAV`，
  **别在这个文件里手写入口链接**（原来「底部那条」在 4 个页面里各写一遍，就是没那张表的后果）。

⚠ 只读 memory：名字 / 天数从 `compute()` 与 `web/users.json` 来，本页**一个字都不写**。
"""
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from base import (
    app, _page, _esc, _avatar_url, _current_uid, _load_users,
    _nav_list, _who_block, MEMORY_DIR,
)
from Rafayel_affinity import compute, days_since


# 🖥 桌面（2026-09-29 第 3 步）：桌面上**左栏常驻**，而左栏渲染的就是这份入口列
#    （`base.py` 的 `.side` 里也是 `_nav_list()`）⇒ 正文这份在桌面是**重复**的，藏掉。
#    ⚠ 只藏**入口列**，上面那个 `.who`（头像 + 名字 + 认识第 N 天）留着
#      ⇒ 桌面 `/menu` = 「左栏 + 你这张卡」的落地页。
#    ⚠⚠ 窄屏**绝对不能藏**：手机上没有左栏，正文这份就是**唯一**的整页目录。
#    ⚠⚠ 为什么不用 `_page(..., nav=False)`：那样 `.wrap` 这个网格就只剩一个子元素，
#      正文会被塞进**第一列（172px）**、直接被压扁（真机量到 `mainW=172`，是踩过的 bug）。
#      ⇒ 左栏照出、藏正文这一份，网格始终是完整两列。 */
MENU_CSS = """
@media(min-width:900px){.menubody{display:none}}
"""


@app.get("/menu", response_class=HTMLResponse)
async def menu_page(request: Request):
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")

    a = compute(uid, MEMORY_DIR)
    rec = (_load_users().get(uid) or {})
    # 显示名：网页自己存的优先，没有就用他记住的称呼（画像 name，来自 memory，只读）
    shown = (rec.get("display_name") or "").strip() or a["name"]

    # ⭐ 「认识第 N 天」跟她自己填的那天走（`met_day` 优先于日志回填的 first_day）——
    #    ⚠ 口径与 `page/affinity.py` 的头部完全一致；**这段只有两处用**，
    #      将来出现第三处就把它下沉到 `base.py`（别再抄第三遍）。
    met_day = (rec.get("met_day") or "").strip()
    known = days_since(met_day) if met_day else (a.get("known_days") or 0)
    sub = ("认识第 %d 天" % known) if known else "还没聊过"

    # 🖼 头像：传了图就出图，没传退回「名字首字」小圆片（与 `/affinity` 同一口径）
    _av = _avatar_url(uid)
    if _av:
        av = ('<img src="%s" alt="" style="width:34px;height:34px;border-radius:50%%;'
              'object-fit:cover;display:block">' % _av)
    else:
        av = ('<div style="width:34px;height:34px;border-radius:50%%;background:#EEF4FB;'
              'color:#33506E;display:flex;align-items:center;justify-content:center;'
              'font-size:12px">%s</div>' % _esc((shown or "?")[:2]))

    # ⚠ 这里**不传 `active`** —— 目录页自己不是任何一个入口，不该有高亮项。
    #    （功能页那边将来要传自己的 href；第 3 步的左栏会用到。）
    # ⚠⭐ 入口列包在 `.menubody` 里：桌面靠 `MENU_CSS` 把这一份藏掉（左栏那份照出）
    #    ⇒ 桌面上不会同一份导航出现两次。**别把 `.who` 也包进去**（那张卡桌面要留）。
    body = _who_block(shown, sub, av) + '<div class="menubody">%s</div>' % _nav_list()
    return _page(body, title="目录", css=MENU_CSS)
