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
    _nav_list, _who_block, MEMORY_DIR, met_known,
)
from Rafayel_affinity import compute


# 🖥 桌面（2026-09-29 第 3 步）：桌面上**左栏常驻**，而左栏渲染的就是这份入口列
#    （`base.py` 的 `.side` 里也是 `_nav_list()`）⇒ 正文这份在桌面是**重复**的，藏掉。
#    ⚠ 只藏**入口列**，上面那个 `.who`（头像 + 名字 + 认识第 N 天）留着
#      ⇒ 桌面 `/menu` = 「左栏 + 你这张卡」的落地页。
#    ⚠⚠ 窄屏**绝对不能藏**：手机上没有左栏，正文这份就是**唯一**的整页目录。
#    ⚠⚠ 为什么不用 `_page(..., nav=False)`：那样 `.wrap` 这个网格就只剩一个子元素，
#      正文会被塞进**第一列（172px）**、直接被压扁（真机量到 `mainW=172`，是踩过的 bug）。
#      ⇒ 左栏照出、藏正文这一份，网格始终是完整两列。 */
MENU_CSS = """
@media(min-width:900px){.menubody{display:none}
  /* 🖥 目录页自己那条「设置 / 退出」底栏也**桌面藏**：桌面上这两项在左栏
     （`_nav_list()` 默认 foot=True）已经有了，再出一条就是重复 ——
     跟上面 `.menubody` 同一个「正文这份桌面重复，藏」的逻辑。 */
  .menufoot{display:none}}

/* 📱 窄屏：把「设置 / 退出」底栏**真的推到屏幕底**（她 06:16 截图：底栏悬在
   列表正下方，下面空一大截）。
   根因：`.footnav` 是 `position:sticky;bottom:0` —— sticky 只在「内容比视口长」
   时才吸底；目录页列表短，底栏的自然位置就在列表下面 ⇒ 没东西可吸，悬在半空。
   修法：让 `.main` 至少撑满「一屏 − body 上下 padding（2rem×2）」并变 flex 列，
   底栏 `margin-top:auto` 推到底 —— 跟桌面那套 `.main:has(>.footnav…)` 同一思路。
   ⚠ `100vh / 100dvh` 双写：dvh 跟着手机地址栏收放（call.py 全屏那次同款处理）。
   ⚠ `margin-top:auto` 只覆盖 `.footnav` 的 margin-top（原本 0），
     左右 `-1rem`、底下 `-2rem`（抵消 body padding）照旧生效。
   ⚠ 只在窄屏做：桌面上 `.menufoot` 已被藏，这条没活干 —— 仍写上 media 限定，
     语义干净，也不给桌面 `.main` 白白加 min-height。 */
@media(max-width:899.9px){
  .main:has(> .menufoot){min-height:calc(100vh - 4rem);
      min-height:calc(100dvh - 4rem);display:flex;flex-direction:column}
  .main:has(> .menufoot)>.menufoot{margin-top:auto}
}

/* ⬛ 底栏字色（她 06:19 定：**黑色**，且**只改目录页这一条**——
   其他页的 `.footnav` 链接带 `hint` 类照旧灰字，这里不碰它们）。
   ⚠ 颜色走 `--c-ink`（全站正文字色）不用写死黑 —— 主题换色时它跟着走。
   ⚠ `text-decoration:none` 是必须的：HTML 那边去了 `hint` 类后是裸 `<a>`，
     浏览器默认给链接画下划线 —— 12px 灰字时看不出来，黑色就很显眼了。
   ⚠ 字号不动：仍吃 `.footnav` 的 12px（她只说了颜色）。 */
.menufoot a{color:var(--c-ink);text-decoration:none}
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

    # ⭐ 「认识第 N 天」跟她自己填的那天走。⚠⭐ 2026-09-30 她定的口径：
    #    **没填相遇日就是空**（不再回落到日志回填的 first_day —— 见 `base.met_known()`）。
    #    ⚠ 出现第三处用了 ⇒ 已经按当初的约定**下沉到 `base.met_known()`**，
    #      三处（主页 / 好感度后台 / 这里）共用一份，别再抄。
    met_day, known = met_known(uid, a)
    if known:
        sub = "认识第 %d 天" % known
    elif met_day:
        # 填了相遇日但算出来是 0（几乎不会走到）→ 当作确实还没说过话
        sub = "还没聊过"
    else:
        # ⚠ 这里**不能写「还没聊过」** —— 她可能已经聊了很多，只是没填相遇日。
        sub = "相遇那天还没填"

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
    # ⭐ `foot=False`（她 2026-10-02 定的）：「设置 / 退出」**不再占列表**，
    #    挪到页面底部一条左右底栏（见下面 `menufoot`）—— 左设置右退出、各占一半，
    #    复用全站现成的 `.footnav + .twobar`（跟 `_two_way_footer()` 那种底栏同一套样式）。
    #    ⚠ 桌面左栏不受影响：侧栏里这两项照旧（`_nav_list()` 默认 foot=True），
    #      那是桌面进设置的唯一入口。
    body = (_who_block(shown, sub, av)
            + '<div class="menubody">%s</div>' % _nav_list(foot=False)
            # ⚠ `.footnav` 必须是 `.main` 的直接子元素（不能关进 `.card`/`.menubody`）——
            #   `position:sticky` 只在自己父块范围内贴底，关进去就提前不贴了。
            # ⚠ 类名带 `menufoot`：桌面用 MENU_CSS 整条藏掉（左栏已有，别重复）。
            # ⚠ 链接**不带 `hint`**（她 06:18 定：「字体要黑色」）—— `hint` 是灰字
            #   （`--c-hint`），去掉后颜色继承 body 的 `--c-ink`（正文字色，近黑）；
            #   字号仍是 `.footnav` 给的 12px，跟其他页底栏一致。
            + '<p class="footnav menufoot"><span class="twobar">'
            '<a href="/settings">设置</a>'
            '<a href="/logout">退出</a>'
            '</span></p>')
    return _page(body, title="目录", css=MENU_CSS)
