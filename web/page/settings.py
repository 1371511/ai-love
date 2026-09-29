# -*- coding: utf-8 -*-
"""
⚙️ 设置（`/settings`）—— 显示名 / 改密码 / 头像。

路由：
  GET  `/settings`   设置页
  POST `/settings`   保存（显示名 + 改密码）

⭐ 2026-09-21 她提的：原来是「没有改密码的地方」。
⚠ 只写 `web/users.json` —— **绝不碰 bot 的 memory**（那份只读，写坏他人设就崩）。
  ⇒ 所以「他怎么叫她」不在这里改，那得在 QQ 里跟他说。

⚠⭐ 2026-09-30 她提：「/settings 里『你们相遇的那天』，不要了」
  ⇒ 那一格**已从本页移除**，相遇日现在**只有一处**能改：
     主页「关于你」卡里那一行 → `/home/edit/met_day`。
  ⇒ 数据字段（users.json 的 `met_day`）**保留不动**，只是本页不再展示/不再写入。
"""
import hmac

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from base import (
    app, _page, _esc, _mask_uid, _hash, _current_uid, _load_users, _save_users, _avatar_url,
    _backbar,
)

# ⚠⭐ `met_day`（你们相遇那天）2026-09-30 **已移出本页** —— 唯一入口是主页
#    `/home/edit/met_day`。原先在本页并用到的 `_check_met_day()` 也随之搬到
#    `web/base.py`（`page/home.py` 从那里 import）。
#    ⚠ 别在本页再加一个日期框 —— 她明确说过「不要了」。


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, ok: str = "", err: str = ""):
    """
    ⭐ 个人信息页（2026-09-21 她提的：没有改密码的地方）。

    只写 `web/users.json` —— **绝不碰 bot 的 memory**（那份只读，写坏他人设就崩）。
    ⇒ 所以「他怎么叫她」不在这里改，那得在 QQ 里跟他说。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    rec = (_load_users().get(uid) or {})
    name = (rec.get("display_name") or "").strip()

    # ⚠ `ok` / `err` 都是从 **URL** 来的 ⇒ 一律**转义**再进 HTML。
    #    不然 `?err=<script>…` 这种链接发给别人点，就是一个反射型 XSS。
    #    （头像那两条用**短码**传，省得中文进 URL。）
    _OK = {"avatar": "头像已更新", "avatar_off": "头像已移除"}
    msg = ""
    if err:
        msg = '<p class="err">%s</p>' % _esc(err)
    elif ok:
        msg = '<p class="hint" style="color:#2B7A4B">%s</p>' % _esc(_OK.get(ok, ok))

    # 🖼 头像那一段要用的三块
    _av = _avatar_url(uid)
    if _av:
        av_preview = ('<img src="%s" alt="" style="width:56px;height:56px;border-radius:50%%;'
                      'object-fit:cover;display:block;background:#EEF4FB">' % _av)
        av_state = "现在用的就是这张"
        av_remove = ('<form method="post" action="/settings/avatar/remove">'
                     '<button type="submit" class="ghost">移除头像</button></form>')
    else:
        av_preview = ('<div style="width:56px;height:56px;border-radius:50%%;background:#EEF4FB;'
                      'color:#33506E;display:flex;align-items:center;justify-content:center;'
                      'font-size:13px">%s</div>' % _esc((name or "?")[:2]))
        av_state = "还没有头像"
        av_remove = ""

    # ⚠⭐ 2026-09-21 她定：`/settings` 上的**小提示只留「头像」那一条**。
    #    这一屏（显示名 / 相遇那天）的两段 hint 文字**已删**，账号那行的尾巴也清了 ⇒ 别再补回来。
    #    ⚠⚠ 说明必须写在**这里**（Python 注释），**绝不能写成 `<body>` 里的 HTML 注释** ——
    #        下面是一个 `"""..."""` 模板字符串，注释照原样发到浏览器（右键看源码就能读到），
    #        等于把删掉的文案又送回去了（2026-09-21 真踩：验证脚本当场抓到 5 条 FAIL）。
    #    ⚠ 字段本身**没动**：`placeholder="留空就用他记住的称呼"` 和日期控件照旧。
    # ⚠ `.footnav` 那行在模板里位于 `</div>` **之后**（内容列 `.main` 的直接子元素）：
    #    sticky 只在自己的父块范围内贴底，关进 .card 里的话滚到卡片头之前就不贴了。
    #    ⚠⭐ 说明写在这儿（Python 注释），别写进下面的模板 —— `"""` 里的 HTML 注释会原样发到浏览器。
    body = """
    <div class="card">
      <h1>设置</h1>
      <p class="muted" style="font-size:12px;margin:0">账号 %s</p>
      %s
      <form method="post" action="/settings" style="margin-top:16px">
        <h2>显示名</h2>
        <div><input name="display_name" value="%s" placeholder="留空就用他记住的称呼"
                    style="width:100%%;box-sizing:border-box"></div>

        <h2 style="margin-top:18px">改密码</h2>
        <div><input name="old_pwd" type="password" placeholder="现在的密码"
                    style="width:100%%;box-sizing:border-box"></div>
        <div style="margin-top:8px"><input name="new_pwd" type="password"
                    placeholder="新密码（不改就留空）" style="width:100%%;box-sizing:border-box"></div>
        <div style="margin-top:8px"><input name="new_pwd2" type="password"
                    placeholder="再输一次新密码" style="width:100%%;box-sizing:border-box"></div>
        <button type="submit">保存</button>
      </form>

      <!-- 🖼 头像：**必须单开一个 form**（要 enctype=multipart，而且 HTML 不许 form 套 form） -->
      <h2 style="margin-top:22px">头像</h2>
      <p class="hint" style="margin:0 0 8px">传一张图当你的头像（png / jpg / webp / gif，不超过 2 MB）。</p>
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
        %s
        <span class="hint">%s</span>
      </div>
      <form method="post" action="/settings/avatar" enctype="multipart/form-data">
        <input type="file" name="pic" accept="image/png,image/jpeg,image/webp,image/gif"
               style="width:100%%;box-sizing:border-box">
        <button type="submit">上传</button>
      </form>
      %s
    </div>
    %s""" % (_mask_uid(uid), msg, name, av_preview, av_state, av_remove, _backbar())
    return _page(body, title="设置")


@app.post("/settings")
async def settings_save(request: Request, display_name: str = Form(""),
                        old_pwd: str = Form(""), new_pwd: str = Form(""),
                        new_pwd2: str = Form("")):
    """
    保存设置。⭐ 只能改**自己**的那条（uid 来自签名 cookie，不信任前端传的）。

    ⚠⭐ 2026-09-30 起本页**不再管 `met_day`**（她：「/settings 里『你们相遇的那天』不要了」）。
      ⇒ 这里**绝不能**再出现 `rec["met_day"] = ...`：字段删掉后它会被写成空，
        等于把她在主页填的日子抹掉。数据留给 `/home/edit/met_day` 那一条路。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")

    users = _load_users()
    rec = users.get(uid)
    if not rec:
        return RedirectResponse("/logout")

    name = (display_name or "").strip()
    new_pwd = (new_pwd or "").strip()

    if new_pwd:
        if not old_pwd or not hmac.compare_digest(rec.get("pwd", ""), _hash(old_pwd)):
            return RedirectResponse("/settings?err=" + "现在的密码不对", status_code=303)
        if len(new_pwd) < 6:
            return RedirectResponse("/settings?err=" + "新密码至少 6 位", status_code=303)
        if new_pwd != (new_pwd2 or "").strip():
            return RedirectResponse("/settings?err=" + "两次输入的新密码不一样", status_code=303)
        rec["pwd"] = _hash(new_pwd)

    # ⭐ **只写这两个字段**。别顺手 `rec[k] = v` 遍历整个 form ——
    #   那样会把没在本表单里的字段（比如 `met_day`）写成空/null。
    rec["display_name"] = name
    users[uid] = rec
    _save_users(users)
    return RedirectResponse("/settings?ok=" + "已保存", status_code=303)
