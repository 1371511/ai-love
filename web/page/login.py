# -*- coding: utf-8 -*-
"""
🚪 首页 · 登录 / 登出

路由：
  GET  `/`          首页 = **登录页**（已登录直接弹去 `/menu`）
  POST `/login`     ★ 登录（过 IP 限流 → 查 `web/users.json` → 必要时**当场开号** → 发签名 cookie）
  GET  `/logout`    清 cookie

⭐ 「跟他聊过的人就能进来」这条是她 2026-09-21 定的，见下面 `login()` 里的注释 ——
   那段逻辑别简化成「白名单」。
"""
import hmac

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from base import (
    app, _page, _current_uid,
    _hash, _known_uids, _load_users, _save_users, _sign, DEFAULT_PWD,
    _login_blocked, _login_clear, _login_note_fail,
    # 🔐 账号表的**读-改-写事务**（开号 + 记 `last_login_at` 都要写这张表）
    _users_txn, _now,
)


@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request, err: str = ""):
    # ⚠ 已登录 ⇒ 去**目录页**（2026-09-29 起登录后的落点是 `/menu`，不再是 `/me`）。
    if _current_uid(request):
        return RedirectResponse("/menu")
    body = """
    <div class="card">
      <h1>他眼里的你</h1>
      <p class="muted" style="font-size:13px;margin:0">用你的 QQ 号登录</p>
      <form method="post" action="/login" style="margin-top:14px">
        <div><input name="uid" placeholder="QQ 号" style="width:100%%;box-sizing:border-box"></div>
        <div style="margin-top:8px"><input name="pwd" type="password" placeholder="密码" style="width:100%%;box-sizing:border-box"></div>
        <button type="submit">进去看看</button>
      </form>
      %s
    </div>""" % (('<p class="err">%s</p>' % err) if err else "")
    return _page(body)


@app.post("/login")
async def login(request: Request, uid: str = Form(""), pwd: str = Form("")):
    ip = request.client.host if request.client else "?"
    left = _login_blocked(ip)
    if left:
        return RedirectResponse("/?err=" + "试太多次了，%d 秒后再试" % left, status_code=303)

    uid = (uid or "").strip()
    # ⚠⚠ 下面这一整段是**读-改-写**（可能开号、一定要记 `last_login_at`）⇒
    #    必须整段包进 `_users_txn()`：这是**跨进程**那把锁，
    #    防的是 CLI（`tools/reset_password.py`）正好在同一秒改这张表 ⇒ 静默丢更新。
    #    ⚠ 只锁「写」没用 —— 丢更新发生在「读」和「写」之间。
    with _users_txn():
        users = _load_users()
        rec = users.get(uid)
        # ⭐ 2026-09-21 她定的：**跟他聊过的人就能进来** —— `users.json` 里没这个人，
        #    但 memory 里有他的文件（= 真聊过）且密码是统一默认密码 ⇒ **当场开号**。
        #    ⚠ 别再靠「手工往白名单里加」：漏一个，那个人就永远登不上、
        #      也看不到自己的数据（当天正是这么踩的 —— 全站只有她自己那个号能进）。
        if rec is None and uid in _known_uids() \
                and hmac.compare_digest(_hash(pwd or ""), _hash(DEFAULT_PWD)):
            rec = {"pwd": _hash(DEFAULT_PWD), "note": "自动开通（memory 里有他）",
                   "created_at": _now()}
            users[uid] = rec
        if not rec or not hmac.compare_digest(rec.get("pwd", ""), _hash(pwd or "")):
            _login_note_fail(ip)
            return RedirectResponse("/?err=" + "账号或密码不对", status_code=303)
        # 🚫 停用（2026-10-04）：**过了密码这一关才说** ——
        #    不然「这个号已停用」本身就是一句「这个号存在」的泄露。
        if rec.get("disabled"):
            _login_note_fail(ip)
            return RedirectResponse("/?err=" + "这个号已停用", status_code=303)
        rec["last_login_at"] = _now()
        users[uid] = rec
        _save_users(users)

    _login_clear(ip)
    # ⚠ 登录成功 ⇒ 落到**目录页**（2026-09-29 起为 `/menu`；原来直接进 `/me`）。
    resp = RedirectResponse("/menu", status_code=303)
    resp.set_cookie("rafael_uid", "%s.%s" % (uid, _sign(uid)), httponly=True, samesite="lax")
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/")
    resp.delete_cookie("rafael_uid")
    return resp
