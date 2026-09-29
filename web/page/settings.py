# -*- coding: utf-8 -*-
"""
⚙️ 设置（`/settings`）—— 显示名 / 「你们相遇的那天」/ 改密码。

路由：
  GET  `/settings`   设置页
  POST `/settings`   保存

⭐ 2026-09-21 她提的：原来是「没有改密码的地方」。
⚠ 只写 `web/users.json` —— **绝不碰 bot 的 memory**（那份只读，写坏他人设就崩）。
  ⇒ 所以「他怎么叫她」不在这里改，那得在 QQ 里跟他说。
"""
import hmac

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from base import (
    app, _page, _esc, _mask_uid, _hash, _current_uid, _load_users, _save_users, _avatar_url,
    _backbar,
)


def _check_met_day(day, uid=""):
    """
    校验「你们相遇的那天」。返回错误文案（人话），没问题返回空串。

    ⭐ 只校验**格式与常识**，**不替她决定是哪天** —— 这是她自己说了算的事。
    ⚠ 错误文案会进 URL ⇒ 别写引号、别写换行；也**不许出现后台词**。
    """
    import re
    import datetime
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", day or "")
    if not m:
        return "日期写得不对，照年-月-日那样填"
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return "这个日期不存在，再看看"
    today = datetime.date.today()
    if d > today:
        return "那天还没到呢"
    if d.year < 2000:
        return "太早了，换一个近点的日子"
    return ""


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
    met_day = (rec.get("met_day") or "").strip()

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

        <h2 style="margin-top:18px">你们相遇的那天</h2>
        <div><input name="met_day" type="date" value="%s"
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
    %s""" % (_mask_uid(uid), msg, name, met_day, av_preview, av_state, av_remove, _backbar())
    return _page(body, title="设置")


@app.post("/settings")
async def settings_save(request: Request, display_name: str = Form(""),
                        met_day: str = Form(""),
                        old_pwd: str = Form(""), new_pwd: str = Form(""),
                        new_pwd2: str = Form("")):
    """保存设置。⭐ 只能改**自己**的那条（uid 来自签名 cookie，不信任前端传的）。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")

    users = _load_users()
    rec = users.get(uid)
    if not rec:
        return RedirectResponse("/logout")

    name = (display_name or "").strip()
    new_pwd = (new_pwd or "").strip()

    # ⭐ 「相遇那天」—— 她自己填的，我们只做**格式与常识**校验，不替她决定是哪天。
    day = (met_day or "").strip()
    if day:
        err = _check_met_day(day, uid)
        if err:
            return RedirectResponse("/settings?err=" + err, status_code=303)

    if new_pwd:
        if not old_pwd or not hmac.compare_digest(rec.get("pwd", ""), _hash(old_pwd)):
            return RedirectResponse("/settings?err=" + "现在的密码不对", status_code=303)
        if len(new_pwd) < 6:
            return RedirectResponse("/settings?err=" + "新密码至少 6 位", status_code=303)
        if new_pwd != (new_pwd2 or "").strip():
            return RedirectResponse("/settings?err=" + "两次输入的新密码不一样", status_code=303)
        rec["pwd"] = _hash(new_pwd)

    rec["display_name"] = name
    rec["met_day"] = day
    users[uid] = rec
    _save_users(users)
    return RedirectResponse("/settings?ok=" + "已保存", status_code=303)
