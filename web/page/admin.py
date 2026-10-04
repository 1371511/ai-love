# -*- coding: utf-8 -*-
"""
🛡 管理员后台（`/admin`）—— **另一个界面**，不是用户端里的一页。

⭐ 三条形态口径（她 2026-10-04 定的，别"顺手统一"回去）
  ① **自己的登录页**：打开 `/admin` 就是后台**自己的**登录框，用管理员账号密码进
     —— **不需要**先去用户端用 QQ 号登录。
  ② **自己的外壳**：顶栏 + 左侧分区 + **自己一套 CSS**。
     ⚠⚠ **绝不用 `base._page()`** —— 那一套是全站 CSS + `NAV`（手机目录页 / 桌面左栏），
       用了就等于「她那个站点里多了一页」（第一版就是这么写错的）。
  ③ **自己的会话**：cookie `rafael_admin`，跟用户的 `rafael_uid` **完全分开**，
     而且**签名命名空间也不同**（`admin:` 前缀，见 `base._sign_admin`）
     ⇒ 拿一个有效的**用户** cookie **伪造不出**管理员 cookie。

路由（10 条）：
  GET  `/admin`                    控制台首页 ＝ 用户列表（⚠ 未登录 ⇒ 后台登录页）
  POST `/admin/login`              后台登录（发 `rafael_admin` cookie）
  GET  `/admin/logout`             退出后台（只清后台 cookie，**不动**用户端登录态）
  GET  `/admin/audit`              审计日志
  GET  `/admin/u/{uid}`            单个用户详情（账号状态 + 动作）
  GET  `/admin/u/{uid}/memory`     📂 **记忆与数据**（内容层：原文 / 日记 / 情绪 / 原始 JSON）
  POST `/admin/u/{uid}/pwd`        重置密码（默认密码 / 指定密码）
  POST `/admin/u/{uid}/toggle`     停用 / 启用
  POST `/admin/u/{uid}/role`       设 / 取消管理员
  POST `/admin/u/{uid}/note`       改备注

⚠ 注册顺序：`/admin/login` `/admin/logout` `/admin/audit` 这些**具体路由**
  写在 `/admin/u/{uid}`（参数路由）**前面**（红 1）。目前段名不同、不吃，
   但顺序照规矩写死，将来加 `/admin/xxx` 也不用再想一遍。

⭐ 数据口径（跟 `docs/规划/多角色与存储-规划.md` §5 对齐）
  · **只写 `web/` 自己的数据**（`users.json` + `admin_audit.jsonl`）⇒ **ADR-22 零开口**。
    ⚠ 读 memory 是允许的，但**不许 import** `Rafayel_memory` / `Rafayel_calls` / `Rafayel_mood`
      那些**写盘模块** —— 本页**自己 `json.load`**（跟 `page/chat.py` 读原话同款）。
  · **可见范围**（2026-10-05 更新）：
      ① 账号层（**主职**：账号状态 / 重置密码 / 停用 / 权限 / 备注）
      ② 元信息层（数量与标签：轮数 / token / 日记条数 / 通话通数 / 情绪标签）
      ③ ✅ **内容层已放开**（`/admin/u/{uid}/memory`）—— 她明确要的（「查找问题数据，
         所以我需要用户的记忆记录」）⇒ 能看原文，**每次打开写一条审计**。
    ⚠⚠ 账号层**不要**「相遇那天 / 认识第 N 天」那两个字段（她 2026-10-05 明确说不需要）
      —— 那是用户端自己的纪念日口径，后台只管账号状态。
  · 每条路由**自己**校验一次管理员；未登录 ⇒ 登录页，无权限 ⇒ 登录页带一句说明
    （**不返回 403**，免得确认"这里有个后台"）。
"""
import hmac
import json
import os
import time

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from base import (
    ADMIN_COOKIE, ADMIN_ROLE, DEFAULT_PWD, app, _audit, _current_admin, _current_uid,
    _esc, _hash, _is_admin, _known_uids, _load_users, _login_blocked, _login_clear,
    _login_note_fail, _sign_admin, _user_rec, read_audit, reset_pwd,
    set_user_disabled, set_user_note, set_user_role,
)
# ✅ `Rafayel_config` 在 `check_static.py` 的 `WEB_WHITELIST` 里（只读、不开口子）
#    ⇒ 拿 `mem_path()` 不算破 ADR-22。它也是 Step 0 之后**唯一的**路径出口。
from Rafayel_config import mem_path


# ============================================================ 🎨 后台自己的样式
# ⚠ 这一套跟全站那份 CSS（`base.CSS`，她的粉色系）**没有任何关系** ——
#   后台是干活用的控制台：中性灰底 + 深色顶栏 + 信息密度优先。
ADMIN_CSS = """
:root{--a-bg:#F4F5F7;--a-card:#fff;--a-ink:#1F2430;--a-mut:#6B7280;--a-line:#E3E6EA;
  --a-bar:#232A36;--a-acc:#3B6FD4;--a-ok:#2F8F5B;--a-danger:#C0392B;--a-r:8px}
body{margin:0;background:var(--a-bg);color:var(--a-ink);line-height:1.6;
  font:13.5px/1.6 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.ahd{display:flex;align-items:center;gap:12px;background:var(--a-bar);color:#E8EAEE;
  padding:0 14px;height:46px;position:sticky;top:0;z-index:9}
.ahd b{font-size:14px;font-weight:600}
.ahd .sp{flex:1}
.ahd a{color:#B9C1CE;text-decoration:none;font-size:12.5px}
.ahd a:hover{color:#fff}
.ahd .who{font-size:12px;color:#8E97A6;font-family:ui-monospace,Consolas,monospace}
.awrap{display:flex;align-items:flex-start;min-height:calc(100vh - 46px)}
.anav{width:150px;flex:0 0 150px;padding:12px 8px;display:flex;flex-direction:column;gap:2px}
.anav a{display:block;padding:7px 10px;border-radius:6px;color:#3C4453;
  text-decoration:none;font-size:13px}
.anav a:hover{background:#E9ECF1}
.anav a.on{background:var(--a-card);color:var(--a-acc);font-weight:600;
  box-shadow:0 0 0 1px var(--a-line)}
.amain{flex:1;min-width:0;padding:4px 16px 28px}
.acard{background:var(--a-card);border:1px solid var(--a-line);border-radius:var(--a-r);
  padding:14px 16px;margin:12px 0}
.acard h1{margin:0 0 4px;font-size:17px}
.acard h2{margin:18px 0 8px;font-size:14px}
.acard h2:first-child{margin-top:0}
.mut{color:var(--a-mut);font-size:12px;margin:0}
.err{color:var(--a-danger);font-size:12.5px;margin:10px 0 0}
.ok{color:var(--a-ok);font-size:12.5px;margin:10px 0 0}
.atbl{width:100%;border-collapse:collapse;font-size:12.5px}
.atbl th,.atbl td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--a-line);
  white-space:nowrap}
.atbl th{color:var(--a-mut);font-weight:500;font-size:11.5px}
.atbl tr:last-child td{border-bottom:0}
.acard a{color:var(--a-acc);text-decoration:none}
.acard a:hover{text-decoration:underline}
.atbl a{color:var(--a-acc);text-decoration:none}
.atbl a:hover{text-decoration:underline}
.scroll{overflow-x:auto}
.tag{font-size:11px;border-radius:4px;padding:1px 6px;white-space:nowrap}
.tag.ad{background:#E7EEFC;color:#274C99}
.tag.off{background:#FBEAE8;color:#9E2F22}
.tag.only{background:#F0F1F3;color:#6B7280}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px 16px}
.grid b{display:block;font-size:11.5px;color:var(--a-mut);font-weight:400}
.grid span{font-size:13.5px}
.acts{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.acts form{display:inline-flex;gap:6px;align-items:center;margin:0}
input{font:inherit;padding:7px 9px;border:1px solid #CFD4DB;border-radius:6px;background:#fff}
input:focus{outline:2px solid #C7D6F5;border-color:var(--a-acc)}
button{font:inherit;padding:7px 12px;border:1px solid var(--a-acc);border-radius:6px;
  background:var(--a-acc);color:#fff;cursor:pointer}
button:hover{filter:brightness(1.06)}
button.ghost{background:#fff;color:#3C4453;border-color:#CFD4DB}
button.danger{background:#fff;color:var(--a-danger);border-color:#E5B5AF}
.w100{width:100%}
.note{background:#FFF8E6;border:1px solid #F0D9A0;border-radius:6px;padding:8px 10px;
  font-size:12.5px;color:#7A5B12;margin:10px 0 0}
.mono{font-family:ui-monospace,Consolas,monospace}
.log{font-size:12.5px;line-height:2}
.log code{color:var(--a-mut);font-size:11.5px}
/* 📂 记忆与数据那一页：可读渲染 + 原始 JSON + 体检告警 */
details{margin:6px 0}
summary{cursor:pointer;font-size:12.5px;color:var(--a-acc)}
pre{background:#F8F9FB;border:1px solid var(--a-line);border-radius:6px;padding:10px;
  font:11.5px/1.5 ui-monospace,Consolas,monospace;white-space:pre-wrap;
  word-break:break-word;max-height:420px;overflow:auto;margin:8px 0}
.mm{background:#F8F9FB;border:1px solid var(--a-line);border-radius:6px;padding:7px 9px;
  margin:6px 0;font-size:12.5px;word-break:break-word}
.mm b{color:var(--a-mut);font-weight:500;margin-right:6px}
.mm.me{background:#EEF3FC;border-color:#D6E2F7}
.mm.bad{background:#FFF8E6;border-color:#F0D9A0;color:#7A5B12}
.sick{background:#FBEAE8;border:1px solid #E5B5AF;border-radius:6px;padding:8px 10px;
  font-size:12.5px;color:#9E2F22}
.sick ul{margin:6px 0 0;padding-left:20px}
.good{background:#EDF7F0;border:1px solid #BCE0C9;border-radius:6px;padding:8px 10px;
  font-size:12.5px;color:#2F6B47}
.two{display:flex;align-items:center;gap:12px}
.av{width:52px;height:52px;border-radius:50%;object-fit:cover;display:block;
  background:#E9ECF1;color:#4C5566;text-align:center;line-height:52px;font-size:13px}
@media(max-width:760px){
  .awrap{display:block}
  .anav{width:auto;flex:none;flex-direction:row;overflow-x:auto;padding:8px}
  .amain{padding:4px 10px 24px}
}
.lgw{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.lg{width:100%;max-width:330px;background:var(--a-card);border:1px solid var(--a-line);
  border-radius:10px;padding:22px}
.lg h1{margin:0 0 2px;font-size:18px}
.lg .sub{margin:0 0 14px;color:var(--a-mut);font-size:12px}
.lg input{margin-bottom:8px}
"""

# ⚠ 后台的整页外壳：**没有 `NAV`、没有全站 CSS**。
SHELL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s</style></head><body>
<header class="ahd"><b>🛡 管理员后台</b><span class="sp"></span>
<span class="who">%s</span><a href="/admin/logout">退出</a></header>
<div class="awrap"><nav class="anav">%s</nav><main class="amain">%s</main></div>
</body></html>"""

LOGIN_SHELL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s</style></head><body>%s</body></html>"""

NAV_ITEMS = (("users", "/admin", "用户"), ("audit", "/admin/audit", "审计日志"))


def _sec_headers():
    """
    🛡 后台每个响应的安全头 —— **每一条都有理由，别删**（2026-10-04 加固那一批）。

    ⚠ 跟用户端页面不同：后台**没有任何外部资源、没有一行 JS**（样式是内联 `<style>`、
      表单只提交给自己、也没有图片）⇒ **CSP 能收到最紧**。
      ⇒ 将来要往后台加东西（外链图 / 图表库 / JS），**先想清楚放开哪一条**，
        别顺手把 `default-src 'none'` 改成 `*`。
    """
    return {
        "Cache-Control": "no-store",          # 后台全是运维数据，绝不缓存
        "X-Frame-Options": "DENY",            # 防点击劫持（谁也别想用 iframe 套后台）
        "X-Content-Type-Options": "nosniff",  # 防 MIME 嗅探
        "Referrer-Policy": "no-referrer",     # 别让后台地址从 Referer 漏出去
        "X-Robots-Tag": "noindex, nofollow, noarchive",   # 搜索引擎别收录
        "Content-Security-Policy": ("default-src 'none'; style-src 'unsafe-inline'; "
                                    "form-action 'self'; base-uri 'none'; "
                                    "frame-ancestors 'none'"),
    }


def _shell(title, body, active="users", me=""):
    """后台自己的整页外壳（顶栏 + 左侧分区 + 自己的 CSS）。"""
    items = "".join('<a href="%s"%s>%s</a>'
                    % (href, ' class="on"' if key == active else "", label)
                    for key, href, label in NAV_ITEMS)
    return HTMLResponse(SHELL % (_esc(title), ADMIN_CSS, _esc(me), items, body),
                        headers=_sec_headers())


def _login_page(err="", prefill=""):
    """后台**自己的**登录页（没有侧栏、没有全站 CSS）。"""
    body = """<div class="lgw"><div class="lg">
      <h1>🛡 管理员后台</h1>
      <p class="sub">用管理员账号登录 —— 跟用户端是**两套**登录态</p>
      <form method="post" action="/admin/login">
        <input name="uid" value="%s" placeholder="管理员账号" class="w100" autofocus>
        <input name="pwd" type="password" placeholder="密码" class="w100">
        <button type="submit" class="w100">进入后台</button>
      </form>
      %s
    </div></div>""" % (_esc(prefill), ('<p class="err">%s</p>' % _esc(err)) if err else "")
    return HTMLResponse(LOGIN_SHELL % (_esc("管理员登录"), ADMIN_CSS, body),
                        headers=_sec_headers())


def _prefill_uid(request):
    """
    登录页**预填**账号：如果用户端此刻正登着一个**管理员**号，就把 uid 填上（少打一次字）。

    ⚠ 只预填 uid。**密码绝不预填**，也绝不用用户会话**直接放行**
      —— 那正好把「两套登录态」这条口径毁掉了。
    """
    u = _current_uid(request)
    return u if (u and _is_admin(u)) else ""


# ============================================================ 🧰 小工具
def _read_mem(uid, kind=None):
    """读 `memory/{uid}[_kind].json`（**只读**）。读不了 / 结构不对 ⇒ `{}`。"""
    try:
        with open(mem_path(kind, uid), encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _as_list(v):
    """⚠ 红线 13：从 json 取出来的列表**先看类型再看内容**（int/str 会被逐字符迭代）。"""
    return v if isinstance(v, list) else []


def _ts(t):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(t)))
    except Exception:
        return ""


def _meta(uid):
    """
    元信息层 —— **只有数量与标签，一个字的原文都不取**。

    ⚠ 这是「能判断这个号活不活跃」与「读别人私聊」之间的分界线：
      情绪只取**标签名**（不取 `cause`）、日记/通话只取**条数**（不取正文）。
    """
    mem, prof = _read_mem(uid), _read_mem(uid, "profile")
    diary, calls = _read_mem(uid, "diary"), _read_mem(uid, "calls")
    mood, usage = _read_mem(uid, "mood"), _read_mem(uid, "usage")
    daily = _read_mem(uid, "daily")
    tags = sum(len(_as_list(prof.get(k))) for k in ("likes", "dislikes", "traits"))
    try:
        turns = int(mem.get("turn_count") or 0)
    except Exception:
        turns = 0
    try:
        total = int(usage.get("total") or 0)
    except Exception:
        total = 0
    return {"turns": turns, "last": _ts(mem.get("last_msg_at")) or str(mem.get("saved_at") or ""),
            "key_facts": len(_as_list(mem.get("key_facts"))),
            "diary": len(_as_list(diary.get("entries"))),
            "calls": len(_as_list(calls.get("calls"))), "tags": tags,
            "mood": str(mood.get("mood") or ""), "token": total,
            "days": len(_as_list(daily.get("days")))}


def _chip(rec, known_now):
    if rec.get("disabled"):
        return '<span class="tag off">已停用</span>'
    if str(rec.get("role") or "") == ADMIN_ROLE:
        return '<span class="tag ad">管理员</span>'
    if not known_now:
        return '<span class="tag only">仅账号</span>'
    return ""


def _who():
    """列表要显示的人 = **有账号的** ∪ **memory 里聊过的**（跟登录自动开通同一口径）。"""
    return sorted(set(_load_users()) | set(_known_uids()))


def _msg(ok="", err=""):
    if err:
        return '<p class="err">%s</p>' % _esc(err)
    if ok:
        return '<p class="ok">%s</p>' % _esc(ok)
    return ""


def _back_to_login(request, err=""):
    """未登录 / 无权限 —— 一律回**后台自己的**登录页（不返回 403，不泄露"这里有后台"）。"""
    return _login_page(err, _prefill_uid(request))


# ============================================================ 🔑 登录 / 退出
@app.post("/admin/login")
async def admin_login(request: Request, uid: str = Form(""), pwd: str = Form("")):
    """
    后台登录 ⇒ 发**独立**的 `rafael_admin` cookie。

    ⚠ 限流复用用户端那套（`_login_blocked`，按 IP）：后台更值得挡，
      反正同一个 IP 打两个入口本来就该一起算。
    """
    ip = request.client.host if request.client else "?"
    uid = (uid or "").strip()
    left = _login_blocked(ip)
    if left:
        return _login_page("试太多次了，%d 秒后再试" % left, uid)

    rec = _user_rec(uid) if uid else {}
    # ⚠ 先验密码，**再**说「不是管理员 / 已停用」—— 顺序反了就等于告诉陌生人
    #   「这个号存在」。跟用户端登录里那条停用判定同一个口径。
    if not uid or not hmac.compare_digest(str(rec.get("pwd") or ""), _hash(pwd or "")):
        _login_note_fail(ip)
        return _login_page("账号或密码不对", uid)
    if rec.get("disabled"):
        _login_note_fail(ip)
        return _login_page("这个号已停用", uid)
    if str(rec.get("role") or "") != ADMIN_ROLE:
        _login_note_fail(ip)
        return _login_page("这个号没有后台权限", uid)

    _login_clear(ip)
    _audit("login", uid, "从 %s 登录后台" % ip, by=uid)      # ⭐ 后台登录也留痕
    resp = RedirectResponse("/admin", status_code=303)
    # 🛡 cookie 三件套（加固那一批）：
    #   · `httponly`  —— JS 读不到（后台本来也没有 JS，防的是将来）
    #   · `samesite="strict"` —— 跨站请求**一律不带**这个 cookie（防 CSRF）
    #   · `secure` —— ⚠ **只在真走 HTTPS 时加**：本地 `http://127.0.0.1` 调试时加了它，
    #     浏览器**根本不存这个 cookie**（静默丢掉，表现为"登录了却还在登录页"，很难查）。
    #     nginx 已经把 `X-Forwarded-Proto` 传下来了（见 `deploy/coralrafayel.conf`）。
    resp.set_cookie(ADMIN_COOKIE, "%s.%s" % (uid, _sign_admin(uid)),
                    httponly=True, samesite="strict",
                    secure=(request.headers.get("x-forwarded-proto", "").lower() == "https"))
    return resp


@app.get("/admin/logout")
async def admin_logout():
    """退出后台。⚠ **只清后台那个 cookie** —— 不动用户端的 `rafael_uid`。"""
    resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie(ADMIN_COOKIE)
    return resp


# ============================================================ 📋 用户列表
@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request, ok: str = "", err: str = ""):
    me = _current_admin(request)
    if not me:
        return _back_to_login(request, err)

    known = _known_uids()
    rows = []
    for u in _who():
        rec = _user_rec(u)
        m = _meta(u)
        name = (rec.get("display_name") or "").strip() or \
            str(_read_mem(u, "profile").get("name") or "") or "—"
        rows.append(
            '<tr><td class="mono"><a href="/admin/u/%s">%s</a></td><td>%s</td><td>%s</td>'
            '<td>%s</td><td>%s</td><td><a href="/admin/u/%s/memory">记忆</a></td></tr>'
            % (_esc(u), _esc(u), _esc(name), _chip(rec, u in known),
               _esc(m["last"] or "—"), "{:,} / {:,}".format(m["turns"], m["token"]),
               _esc(u)))

    body = """
    <div class="acard">
      <h1>用户</h1>
      <p class="mut">共 %d 个号 · 当前管理员 <span class="mono">%s</span></p>
      %s
      <div class="note">⚠ 这一页**不含**任何聊天原文 / 日记正文 / 情绪原因 ——
        只有账号层与元信息层（数量、标签）。</div>
      <div class="scroll" style="margin-top:12px">
        <table class="atbl">
          <tr><th>账号</th><th>名字</th><th>状态</th><th>最后活跃</th>
              <th>轮数 / token</th><th>记忆</th></tr>
          %s
        </table>
      </div>
    </div>""" % (len(rows), _esc(me), _msg(ok, err),
                 "".join(rows) or '<tr><td colspan="6" class="mut">（空）</td></tr>')
    return _shell("用户", body, "users", me)


# ============================================================ 🗄 审计日志
@app.get("/admin/audit", response_class=HTMLResponse)
async def admin_audit_page(request: Request):
    me = _current_admin(request)
    if not me:
        return _back_to_login(request)
    logs = read_audit(200)
    rows = "".join(
        '<tr><td class="mono">%s</td><td class="mono">%s</td><td class="mono">%s</td>'
        "<td>%s</td><td>%s</td></tr>"
        % (_esc(r.get("ts", "")), _esc(r.get("by", "")), _esc(r.get("target", "")),
           _esc(r.get("action", "")), _esc(r.get("detail", "")))
        for r in logs)
    body = """
    <div class="acard">
      <h1>审计日志</h1>
      <p class="mut">最近 %d 条（新的在前）· 文件 <span class="mono">web/admin_audit.jsonl</span></p>
      <div class="note">⚠ 只追加、不重写 ⇒ 不会被"整份覆盖"写坏；
        也**不会被后台自己删除** —— 要清得手工上去改文件。</div>
      <div class="scroll" style="margin-top:12px">
        <table class="atbl">
          <tr><th>时间</th><th>操作人</th><th>对象</th><th>动作</th><th>说明</th></tr>
          %s
        </table>
      </div>
    </div>""" % (len(logs), rows or '<tr><td colspan="5" class="mut">（还没有记录）</td></tr>')
    return _shell("审计日志", body, "audit", me)


# ============================================================ 👤 用户详情
@app.get("/admin/u/{uid}", response_class=HTMLResponse)
async def admin_user(request: Request, uid: str, ok: str = "", err: str = ""):
    me = _current_admin(request)
    if not me:
        return _back_to_login(request)
    uid = (uid or "").strip()
    if uid not in _load_users() and uid not in _known_uids():
        return RedirectResponse("/admin?err=" + _esc("没有这个号"), status_code=303)

    rec = _user_rec(uid)
    m = _meta(uid)
    name = (rec.get("display_name") or "").strip() or "—"
    is_self = (str(uid) == str(me))
    is_admin_now = str(rec.get("role") or "") == ADMIN_ROLE

    # 🚫 对自己**不给**停用 / 不给取消权限（免得把自己锁在后台外面）
    toggle_btn = "" if is_self else (
        '<form method="post" action="/admin/u/%s/toggle">'
        '<button type="submit" class="%s">%s</button></form>'
        % (_esc(uid), "ghost" if rec.get("disabled") else "danger",
           "启 用" if rec.get("disabled") else "停 用"))
    role_btn = "" if (is_self and is_admin_now) else (
        '<form method="post" action="/admin/u/%s/role">'
        '<input type="hidden" name="role" value="%s">'
        '<button type="submit" class="ghost">%s</button></form>'
        % (_esc(uid), "" if is_admin_now else ADMIN_ROLE,
           "取消管理员" if is_admin_now else "设为管理员"))

    body = """
    <div class="acard">
      <h1 class="mono">%s</h1>
      <p class="mut">%s %s</p>
      <p style="margin:8px 0 0"><a href="/admin/u/%s/memory">📂 查看记忆与数据 →</a>
        <span class="mut">（对话原文 / 日记 / 情绪 / 画像 / 通话 / 原始 JSON）</span></p>
      %s
      <div class="grid" style="margin-top:14px">
        <div><b>显示名</b><span>%s</span></div>
        <div><b>最后活跃</b><span>%s</span></div>
        <div><b>对话轮数</b><span>%s</span></div>
        <div><b>token 累计</b><span>%s</span></div>
        <div><b>聊过的天</b><span>%s</span></div>
        <div><b>他记住的事</b><span>%s 条</span></div>
        <div><b>画像标签</b><span>%s 条</span></div>
        <div><b>日记</b><span>%s 条</span></div>
        <div><b>通话</b><span>%s 通</span></div>
        <div><b>最近情绪</b><span>%s</span></div>
        <div><b>创建 / 改密 / 登录</b><span class="mono">%s</span></div>
        <div><b>改密由谁</b><span>%s</span></div>
      </div>
      <div class="note">⚠ 以上是**元信息层**：只有数量与标签，不含任何原文。</div>
    </div>

    <div class="acard">
      <h2>重置密码</h2>
      <p class="mut" style="margin-bottom:8px">改完把新密码告诉她，让她自己去用户端「设置」里换一个。</p>
      <div class="acts">
        <form method="post" action="/admin/u/%s/pwd">
          <button type="submit">重置成默认密码</button>
          <span class="mut">（%s）</span>
        </form>
      </div>
      <form method="post" action="/admin/u/%s/pwd" style="margin-top:8px">
        <input name="pwd" placeholder="或指定一个新密码（至少 6 位）" class="w100">
        <button type="submit" class="ghost" style="margin-top:8px">设成这个密码</button>
      </form>

      <h2>账号状态</h2>
      <div class="acts">%s %s</div>
      <p class="mut" style="margin-top:8px">停用 = **立刻踢下线**（用户端 `_current_uid()` 与
        后台 `_current_admin()` 都会拦住已经登录的会话）。防的是「号被扫」，不是惩罚。</p>

      <h2>备注（只给开发者看）</h2>
      <form method="post" action="/admin/u/%s/note">
        <input name="note" value="%s" placeholder="比如：小辞的朋友 / 测试号" class="w100">
        <button type="submit" class="ghost" style="margin-top:8px">保存备注</button>
      </form>
    </div>""" % (
        _esc(uid), _chip(rec, True), _esc("（你自己）" if is_self else ""),
        _esc(uid), _msg(ok, err),
        _esc(name), _esc(m["last"] or "—"),
        "{:,}".format(m["turns"]), "{:,}".format(m["token"]), str(m["days"]),
        str(m["key_facts"]), str(m["tags"]), str(m["diary"]), str(m["calls"]),
        _esc(m["mood"] or "—"),
        _esc(" / ".join([str(rec.get("created_at") or "—"),
                         str(rec.get("password_changed_at") or "—"),
                         str(rec.get("last_login_at") or "—")])),
        _esc(rec.get("pwd_by") or "—"),
        _esc(uid), _esc(DEFAULT_PWD), _esc(uid), toggle_btn, role_btn, _esc(uid),
        _esc(rec.get("note") or ""))
    return _shell("用户 %s" % uid, body, "users", me)


# ============================================================ 📂 记忆与数据（内容层）
# ⚠⭐ 这一页**是内容层** —— 能直接看到她的**聊天原文 / 日记正文 / 情绪原因**。
#   2026-10-05 她明确要的（原话：「管理员偶尔需要帮助用户，重置密码，查找问题数据
#   所以我需要用户的记忆记录等等」）⇒ 之前「内容层不做」那条口径**已按她的决定放开**。
#   ⚠ 代价与对策：
#     ① **每次打开都写一条审计**（`view_memory`）—— 内容是别人的私聊，
#        留个「谁在什么时候看过谁的记忆」；
#     ② **只读**：全部 `json.load`，一个字节都不写；
#        ⚠ 也**绝不 import** `Rafayel_memory` / `Rafayel_calls` 那些**写盘模块**
#          （引一个就是 ADR-22 多开一个口子）⇒ 这里所有渲染都是手写的；
#     ③ 内容进 HTML 前**一律 `_esc()`** —— 她原话里带 `<script>` 的概率不为零。

# (kind, 中文名, 一句话说明)；`kind=None` 就是 `{uid}.json` 那个主档
_MEM_FILES = [
    (None, "对话主档", "messages / long_term_summary / key_facts / day_summaries"),
    ("profile", "画像", "他学到的 + 她自己填的（likes / dislikes / traits / birthday）"),
    ("diary", "日记", "entries —— 每 8 轮一条，一天可以多条"),
    ("mood", "情绪", "mood / level / cause / since / prev"),
    ("calls", "通话记录", "calls[] —— 每通一整条台词，不裁不压"),
    ("archive", "原话留档", "days[].messages —— 被 12 轮窗口裁掉的原话"),
    ("daily", "每日统计", "days / streak / her_initiated / media / unlocked"),
    ("usage", "token 记账", "calls / prompt / completion / cache_hit / total"),
    ("greet", "打招呼排期", "next_at / count / recent"),
    ("qzone", "说说排期", "next_at / sent / recent_cats"),
    ("event", "节日去重", "sent / used"),
    ("bday", "生日去重", "按年去重"),
]
# 只做「可读渲染」的那几个；其余文件统一走 `<details>` + 原始 JSON
_READABLE = (None, "diary", "calls")


def _raw(uid, kind=None):
    """文件**原始文本**（给「原始 JSON」那一块）。读不了 ⇒ 空串。"""
    try:
        with open(mem_path(kind, uid), encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _size_mtime(uid, kind=None):
    try:
        st = os.stat(mem_path(kind, uid))
        return ("%d B" % st.st_size,
                time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)))
    except Exception:
        return "—", ""


def _file_label(kind):
    for k, label, _d in _MEM_FILES:
        if k == kind:
            return label
    return kind or "对话主档"


def _type_audit(uid):
    """
    🩺 **数据体检** —— 这一页最值钱的东西：把「类型不对」的字段挑出来。

    ⚠ 为什么值钱：同一个坑这个项目**踩过三次**（`docs/网页端.md` 红线 13）——
      `key_facts` 不是 list 时：计分侧 `len()` 抛 TypeError ⇒ **三个页面整页 500**；
      展示侧**逐字符迭代** ⇒ 页面上**一行一个字**；写侧把整句话**炸成字符数组存回去**，
      而且**一声不响**。⇒ 与其等它发作，不如在这儿一眼看出来。

    返回 `[(kind, 字段, 现状, 期望), …]`。
    """
    want = [
        (None, "messages", list), (None, "pending_summary", list),
        (None, "key_facts", list), (None, "day_summaries", list),
        (None, "long_term_summary", str), (None, "turn_count", int),
        ("profile", "likes", list), ("profile", "dislikes", list),
        ("profile", "traits", list), ("profile", "name", (str, type(None))),
        ("diary", "entries", list), ("calls", "calls", list),
        ("daily", "days", list), ("usage", "days", dict), ("mood", "prev", dict),
    ]
    out = []
    for kind, field, typ in want:
        d = _read_mem(uid, kind)
        if field not in d or isinstance(d[field], typ):
            continue
        names = typ.__name__ if isinstance(typ, type) else " / ".join(t.__name__ for t in typ)
        out.append((kind, field, type(d[field]).__name__, names))
    # ⚠ 列表里混进非字符串 —— 写侧最容易被它坑（`_add_one()` 是逐项比的）
    for kind, field in ((None, "key_facts"), ("profile", "likes"),
                        ("profile", "dislikes"), ("profile", "traits")):
        v = _read_mem(uid, kind).get(field)
        if isinstance(v, list) and any(not isinstance(x, str) for x in v):
            out.append((kind, field, "列表里混了非字符串项", "全是 str"))
    return out


def _bubbles(rows, limit=40):
    """把 `messages` 渲成可读对话（⚠ 全部 `_esc()`）。超长只显示最近 N 条。"""
    rows = _as_list(rows)
    out = []
    if len(rows) > limit:
        out.append('<div class="mm bad">⚠ 只显示最近 %d 条（共 %d 条）'
                   "—— 完整内容看最下面的原始 JSON</div>" % (limit, len(rows)))
    for mm in rows[-limit:]:
        if not isinstance(mm, dict):
            out.append('<div class="mm bad">⚠ 这一条不是对象：%s</div>' % _esc(repr(mm)[:200]))
            continue
        role = mm.get("role")
        who = "她" if role == "user" else ("他" if role == "assistant" else str(role))
        txt = mm.get("content")
        txt = txt if isinstance(txt, str) else repr(txt)
        ts = _ts(mm.get("ts"))
        out.append('<div class="mm%s"><b>%s%s</b>%s</div>'
                   % (" me" if who == "她" else "", _esc(who),
                      (" · " + _esc(ts)) if ts else "",
                      _esc(txt).replace("\n", "<br>")))
    return "".join(out) or '<div class="mut">（没有消息）</div>'


def _diary_block(entries):
    entries = _as_list(entries)
    if not entries:
        return '<div class="mut">（没有日记）</div>'
    out = []
    for e in entries:
        if not isinstance(e, dict):
            out.append('<div class="mm bad">⚠ 这一条不是对象：%s</div>' % _esc(repr(e)[:200]))
            continue
        head = " · ".join(x for x in (str(e.get("day") or ""), str(e.get("ts") or ""),
                                      "src=" + str(e.get("src") or ""),
                                      ("mood=" + str(e.get("mood"))) if e.get("mood") else "") if x)
        out.append('<div class="mm"><b>%s</b><br>%s</div>'
                   % (_esc(head), _esc(str(e.get("text") or "")).replace("\n", "<br>")))
    return "".join(out)


def _calls_block(calls, limit=5):
    calls = _as_list(calls)
    if not calls:
        return '<div class="mut">（没有通话记录）</div>'
    out = ['<div class="mut">共 %d 通，下面是最近 %d 通（完整看最下面的原始 JSON）</div>'
           % (len(calls), min(limit, len(calls)))]
    for c in calls[-limit:]:
        if not isinstance(c, dict):
            continue
        out.append('<div class="mm"><b>%s → %s · %s · %d 句%s</b>%s</div>' % (
            _esc(_ts(c.get("start_ts"))), _esc(_ts(c.get("end_ts"))),
            _esc(str(c.get("id") or "")), len(_as_list(c.get("lines"))),
            " · 已摘要" if c.get("summarized") else "",
            _bubbles(c.get("lines"), limit=200)))
    return "".join(out)


@app.get("/admin/u/{uid}/memory", response_class=HTMLResponse)
async def admin_memory(request: Request, uid: str):
    """
    📂 **记忆与数据**（内容层）：对话原文 / 日记正文 / 情绪 / 画像 / 通话 / 原始 JSON。

    ⚠ 每次打开写一条审计（`view_memory`）—— 这是**别人的私聊**，留痕是这一页的代价。
    """
    me = _current_admin(request)
    if not me:
        return _back_to_login(request)
    uid = (uid or "").strip()
    if uid not in _load_users() and uid not in _known_uids():
        return RedirectResponse("/admin?err=" + _esc("没有这个号"), status_code=303)
    _audit("view_memory", uid, "", by=me)

    mem = _read_mem(uid)

    # ---- ① 文件清单（顺便当"这个号到底有哪些数据"的总览）
    frows = []
    for kind, label, desc in _MEM_FILES:
        size, mt = _size_mtime(uid, kind)
        d = _read_mem(uid, kind)
        keys = ("、".join(list(d.keys())[:8]) + ("…" if len(d) > 8 else "")) if d \
            else "（没有这个文件）"
        frows.append('<tr><td>%s<br><span class="mut">%s</span></td>'
                     '<td class="mono">%s</td><td class="mono">%s</td>'
                     '<td class="mut">%s</td></tr>'
                     % (_esc(label), _esc(desc), _esc(size), _esc(mt), _esc(keys)))

    # ---- ② 体检
    sick = _type_audit(uid)
    if sick:
        sickhtml = ('<div class="sick">🩺 <b>发现 %d 处类型不对</b>'
                    "（红线 13 那类：会让页面 500、或页面上「一行一个字」）：<ul>%s</ul></div>"
                    % (len(sick), "".join(
                        "<li><code>%s</code> 的 <code>%s</code> 现在是 <b>%s</b>，"
                        "应该是 <b>%s</b></li>"
                        % (_esc(_file_label(k)), _esc(f), _esc(g), _esc(w)) for k, f, g, w in sick)))
    else:
        sickhtml = '<div class="good">🩺 数据体检通过：没有发现类型不对的字段。</div>'

    # ---- ③ 对话主档（可读）
    facts = _as_list(mem.get("key_facts"))
    days = _as_list(mem.get("day_summaries"))
    lts = mem.get("long_term_summary")
    talk = (
        '<div class="acard"><h2>对话主档 <span class="mut mono">%s.json</span></h2>'
        '<p class="mut">轮数 %s · 最后消息 %s · 存盘 %s · key_facts %d 条 · 日小结 %d 条</p>'
        '<h2>长期摘要（喂 prompt 的那段）</h2><pre>%s</pre>'
        '<h2>他记住的事（key_facts）</h2>%s'
        '<h2>最近的消息（messages）</h2>%s'
        '<h2>日小结（day_summaries）</h2>%s</div>'
        % (_esc(uid), _esc(str(mem.get("turn_count") or "—")),
           _esc(_ts(mem.get("last_msg_at")) or "—"), _esc(str(mem.get("saved_at") or "—")),
           len(facts), len(days),
           _esc(lts if isinstance(lts, str) else repr(lts)) or '<span class="mut">（空）</span>',
           ("<ul>%s</ul>" % "".join("<li>%s</li>" % _esc(x) for x in facts)) if facts
           else '<div class="mut">（没有）</div>',
           _bubbles(mem.get("messages")),
           "".join('<div class="mm"><b>%s</b><br>%s</div>'
                   % (_esc(str(d.get("date") or "")),
                      _esc(str(d.get("text") or "")).replace("\n", "<br>"))
                   for d in days if isinstance(d, dict)) or '<div class="mut">（没有）</div>'))

    # ---- ④ 日记 / 通话（可读）
    diary = '<div class="acard"><h2>日记 <span class="mut mono">%s_diary.json</span></h2>%s</div>' \
        % (_esc(uid), _diary_block(_read_mem(uid, "diary").get("entries")))
    calls = '<div class="acard"><h2>通话记录 <span class="mut mono">%s_calls.json</span></h2>%s</div>' \
        % (_esc(uid), _calls_block(_read_mem(uid, "calls").get("calls")))

    # ---- ⑤ 其余文件：JSON 折叠块（画像 / 情绪 / 统计 / 用量 / 排期）
    other = []
    for kind, label, _desc in _MEM_FILES:
        if kind in _READABLE:
            continue
        txt = _raw(uid, kind)
        if not txt:
            continue
        other.append('<details><summary>%s <span class="mut mono">%s</span></summary>'
                     "<pre>%s</pre></details>"
                     % (_esc(label), _esc(("%s.json" % uid) if not kind else
                                          ("%s_%s.json" % (uid, kind))), _esc(txt)))
    other_card = '<div class="acard"><h2>其它文件（原始内容）</h2>%s</div>' \
        % ("".join(other) or '<div class="mut">（没有）</div>')

    # ---- ⑥ 原始 JSON（全部，排查结构问题用）
    rawd = []
    for kind, label, _desc in _MEM_FILES:
        txt = _raw(uid, kind)
        if not txt:
            continue
        rawd.append('<details><summary>%s · <span class="mono">%s</span>'
                    '（<span class="mut">%s</span>）</summary><pre>%s</pre></details>'
                    % (_esc(label),
                       _esc(("%s.json" % uid) if not kind else ("%s_%s.json" % (uid, kind))),
                       _esc(_size_mtime(uid, kind)[0]), _esc(txt)))
    raw_card = ('<div class="acard"><h2>原始 JSON</h2>'
                '<div class="note">⚠ 全是**只读**读出来的原文，一个字节都没改。'
                "排查「字段结构不对」看这里最准。</div>%s</div>"
                % ("".join(rawd) or '<div class="mut">（什么都没有）</div>'))

    head = ('<div class="acard"><h1 class="mono">%s</h1>'
            '<p class="mut">📂 <b>记忆与数据</b> · <a href="/admin/u/%s">‹ 回到账号页</a>'
            ' · <a href="/admin">用户列表</a></p>'
            '<div class="note">⚠ 这一页是<b>内容层</b>：能直接看到她的聊天原文 / 日记正文 / '
            "情绪原因。<b>每次打开都写一条审计</b>（`view_memory`）。</div>"
            '<h2>文件清单</h2><div class="scroll"><table class="atbl">'
            "<tr><th>文件</th><th>大小</th><th>改过</th><th>顶层字段</th></tr>%s</table></div>"
            "%s</div>"
            % (_esc(uid), _esc(uid), "".join(frows), sickhtml))

    return _shell("记忆 %s" % uid, head + talk + diary + calls + other_card + raw_card,
                  "users", me)


# ============================================================ ⚡ 动作（全部 303 回详情）
def _guard(request):
    return _current_admin(request)


@app.post("/admin/u/{uid}/pwd")
async def admin_reset_pwd(request: Request, uid: str, pwd: str = Form("")):
    me = _guard(request)
    if not me:
        return _back_to_login(request)
    uid, pwd = (uid or "").strip(), (pwd or "").strip()
    if pwd and len(pwd) < 6:
        return RedirectResponse("/admin/u/%s?err=%s" % (uid, "新密码至少 6 位"),
                                status_code=303)
    ok, why = reset_pwd(uid, pwd or None, by=me)
    if not ok:
        return RedirectResponse("/admin/u/%s?err=%s" % (uid, _esc(why)), status_code=303)
    return RedirectResponse("/admin/u/%s?ok=%s" % (
        uid, _esc("已重置成默认密码，请转告她" if not pwd else "已设成新密码，请转告她")),
        status_code=303)


@app.post("/admin/u/{uid}/toggle")
async def admin_toggle(request: Request, uid: str):
    me = _guard(request)
    if not me:
        return _back_to_login(request)
    uid = (uid or "").strip()
    ok, why = set_user_disabled(uid, not bool(_user_rec(uid).get("disabled")), by=me)
    return RedirectResponse("/admin/u/%s?%s=%s" % (uid, "ok" if ok else "err", _esc(why)),
                            status_code=303)


@app.post("/admin/u/{uid}/role")
async def admin_role(request: Request, uid: str, role: str = Form("")):
    me = _guard(request)
    if not me:
        return _back_to_login(request)
    uid = (uid or "").strip()
    ok, why = set_user_role(uid, role, by=me)
    return RedirectResponse("/admin/u/%s?%s=%s" % (uid, "ok" if ok else "err", _esc(why)),
                            status_code=303)


@app.post("/admin/u/{uid}/note")
async def admin_note(request: Request, uid: str, note: str = Form("")):
    me = _guard(request)
    if not me:
        return _back_to_login(request)
    uid = (uid or "").strip()
    ok, why = set_user_note(uid, note, by=me)
    return RedirectResponse("/admin/u/%s?%s=%s" % (uid, "ok" if ok else "err", _esc(why)),
                            status_code=303)
