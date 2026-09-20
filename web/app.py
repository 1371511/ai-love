# -*- coding: utf-8 -*-
"""
🌐 好感系统 · 网页端（最小可跑版）

⭐ 三条铁律（改代码前先看这里）
  1. **只读 memory，一个字都不写** —— 那批文件是 bot 的记忆，写坏他人设就崩。
  2. **好感度绝不进 QQ 对话** —— 系统数据，一进聊天就破「不露机器人那一面」。
  3. **登录后只能看自己** —— cookie 带签名，服务端不信任前端传的 uid。

⚠ 不连数据库：数据就是 `memory/*.json`。bot 的真相源本来就是文件，
   再加一套库就得双写或同步，两份数据打架是最难查的 bug。十几人的量，文件够。
"""

import hashlib
import hmac
import json
import os
import sys
import time

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "ai-Rafayel"))
from Rafayel_affinity import compute, CUM, MAX_LEVEL  # noqa: E402

MEMORY_DIR = os.path.join(BASE, "memory")
USERS_PATH = os.path.join(BASE, "web", "users.json")
SECRET = b"rafael-affinity-dev"          # ⚠ 上线前换成随机值，别用这个
SALT = "rafael-affinity"


def _hash(pwd):
    return hashlib.sha256((SALT + pwd).encode("utf-8")).hexdigest()


def _load_users():
    if not os.path.exists(USERS_PATH):
        return {}
    with open(USERS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_users(users):
    """写 `web/users.json`（**不是** bot 的 memory —— 那份只读）。先写临时文件再替换。"""
    tmp = USERS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    os.replace(tmp, USERS_PATH)


def _mask_uid(uid):
    """QQ 号脱敏：135*****18 —— 截图发出去也不至于泄露全号。"""
    uid = str(uid or "")
    if len(uid) <= 4:
        return uid
    return uid[:3] + "*" * (len(uid) - 5) + uid[-2:]


# ---------------------------------------------------------------- 登录限流
# ⚠ 统一默认密码（qiyu2026）+ 公网直开 ⇒ 光靠密码挡不住「拿 QQ 号一个个试」，
#    这里按 IP 挡：窗口内错够次数就锁十分钟。进程内计数（重启清零，够用）。
_LOGIN_FAILS = {}
LOGIN_FAIL_LIMIT = 5        # 窗口内最多错几次
LOGIN_FAIL_WINDOW = 60      # 窗口（秒）
LOGIN_LOCK_SECONDS = 600    # 超了锁多久


def _login_blocked(ip):
    """被锁 ⇒ 返回还要等多少秒；没锁 ⇒ 0。"""
    now = time.time()
    arr = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < LOGIN_FAIL_WINDOW]
    _LOGIN_FAILS[ip] = arr
    if len(arr) >= LOGIN_FAIL_LIMIT:
        return max(1, int(LOGIN_LOCK_SECONDS - (now - arr[0])))
    return 0


def _login_note_fail(ip):
    _LOGIN_FAILS.setdefault(ip, []).append(time.time())


def _login_clear(ip):
    _LOGIN_FAILS.pop(ip, None)


def _sign(uid):
    return hmac.new(SECRET, str(uid).encode("utf-8"), hashlib.sha256).hexdigest()[:20]


def _current_uid(request: Request):
    raw = request.cookies.get("rafael_uid") or ""
    if "." not in raw:
        return None
    uid, sig = raw.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(uid)):
        return None
    return uid


app = FastAPI()

CSS = """
body{margin:0;padding:2rem 1rem;background:#FAFAF8;color:#222;font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.6}
.wrap{max-width:560px;margin:0 auto}
.card{background:#fff;border:0.5px solid rgba(0,0,0,.12);border-radius:12px;padding:1rem 1.25rem;margin-bottom:12px}
.muted{color:#777} .hint{color:#999;font-size:12px}
h1{font-size:16px;font-weight:500;margin:0 0 2px}
h2{font-size:13px;font-weight:500;margin:0 0 10px}
.big{font-size:26px;font-weight:500;margin:6px 0 10px}
.bar{height:6px;background:#EEE;border-radius:3px;overflow:hidden}
.bar>div{height:100%;background:#D4537E;border-radius:3px}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:12px}
.grid .card{margin:0;padding:1rem;text-align:center}
.grid .n{font-size:20px;font-weight:500;margin-top:4px}
.chip{display:inline-block;background:#F3F1EC;border-radius:999px;padding:4px 10px;font-size:12px;margin:0 8px 8px 0}
.note{background:#EEF4FB;border-radius:8px;padding:1rem;margin-bottom:12px;font-size:12px;color:#33506E}
.note ul{margin:6px 0 0;padding-left:18px}
input,button{font:inherit;padding:8px 10px;border-radius:8px;border:0.5px solid rgba(0,0,0,.2);background:#fff}
button{cursor:pointer;background:#222;color:#fff;border-color:#222;width:100%;margin-top:10px}
.err{color:#B03030;font-size:12px}
"""


def _page(body, title="他眼里的你"):
    return HTMLResponse("""<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s</style></head><body><div class="wrap">%s</div></body></html>""" %
                        (title, CSS, body))


@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request, err: str = ""):
    if _current_uid(request):
        return RedirectResponse("/me")
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
    users = _load_users()
    rec = users.get(uid)
    if not rec or not hmac.compare_digest(rec.get("pwd", ""), _hash(pwd or "")):
        _login_note_fail(ip)
        return RedirectResponse("/?err=" + "账号或密码不对", status_code=303)

    _login_clear(ip)
    resp = RedirectResponse("/me", status_code=303)
    resp.set_cookie("rafael_uid", "%s.%s" % (uid, _sign(uid)), httponly=True, samesite="lax")
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/")
    resp.delete_cookie("rafael_uid")
    return resp


@app.get("/me", response_class=HTMLResponse)
async def me(request: Request):
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    a = compute(uid, MEMORY_DIR)
    # 显示名：网页自己存的优先，没有就用他记住的称呼（画像 name，来自 memory，只读）
    rec = (_load_users().get(uid) or {})
    shown_name = (rec.get("display_name") or "").strip() or a["name"]

    # 距离「下一级」的进度（用官方累计分表，不是拍脑袋的分档）
    pct = 0
    if a["next_at"]:
        low = CUM[a["level"]]
        span = max(1, a["next_at"] - low)
        pct = min(100, int((a["score"] - low) / span * 100))

    # 本档位内的进度：心动 1~30 / 倾情 31~50 / 眷恋 51~100 / 情衷 101~246
    tier_n = a["level"] - a["tier_lo"] + 1
    tier_size = max(1, a["tier_hi"] - a["tier_lo"] + 1)
    tier_pct = min(100, int(tier_n / tier_size * 100))
    next_hint = ("距 %d 级还差 %d 分" % (a["level"] + 1, a["to_next"])) \
        if a["next_at"] else "已经是最高一级了"

    chips = "".join('<span class="chip">%s</span>' % x
                    for x in (a["likes"] + a["traits"] + ["不喜欢：" + x for x in a["dislikes"]]))
    if not chips:
        chips = '<span class="hint">他还没记住什么 —— 多聊几句就有了</span>'

    topics = "".join('<p style="margin:0 0 6px;font-size:12px" class="muted">%s</p>' % t
                     for t in a["topics"]) or \
        '<p style="margin:0;font-size:12px" class="hint">往后这里会出现你们最近聊过的事</p>'

    if a["milestones"]:
        ms = "".join('<p style="margin:0 0 6px;font-size:13px">「%s」</p>' % m for m in a["milestones"])
    else:
        ms = '<p style="margin:0;font-size:12px" class="hint">' \
             '跨过一级的时候他会说一句话，到时候这里就有</p>'

    # ⭐⭐ **绝不把 `a["missing"]` 显示给用户** —— 那是后台口径（memory/xxx.json、落盘、bot 侧），
    #    一上页面就破「不露机器人那一面」（2026-09-21 她指出的同类问题：不要露出机器人那一面）。
    #    用户能看的只有这句人话：
    missing = ""
    if not a["has_daily"]:
        missing = ('<div class="note">互动天数、连续天数这些从今天才开始记'
                   ' —— 多跟他聊几天，这儿就满了。</div>')

    # 没数据就显示「—」，别给用户看一串 0（她：登进来全是 0 很打击积极性）
    days_txt = str(a["days"]) if a["has_daily"] else "—"
    streak_txt = str(a["streak"]) if a["has_daily"] else "—"

    body = """
    <div class="card" style="display:flex;align-items:center;justify-content:space-between">
      <div>
        <h1>%s</h1>
        <p class="muted" style="font-size:12px;margin:0">%s</p>
      </div>
      <div style="width:36px;height:36px;border-radius:50%%;background:#EEF4FB;color:#33506E;
                  display:flex;align-items:center;justify-content:center;font-size:13px">%s</div>
    </div>
    %s
    <div class="card">
      <div style="display:flex;justify-content:space-between">
        <span class="muted" style="font-size:13px">好感度</span>
        <span class="muted" style="font-size:13px">%s · %d 级</span>
      </div>
      <div class="big">%d <span style="font-size:13px" class="muted">/ %d</span></div>
      <div class="bar"><div style="width:%d%%"></div></div>
      <p class="hint" style="margin:8px 0 0">%s</p>
      <div style="margin-top:12px">
        <div style="display:flex;justify-content:space-between">
          <span class="hint">%s</span>
          <span class="hint">本档第 %d / %d 级</span>
        </div>
        <div class="bar" style="margin-top:4px"><div style="width:%d%%"></div></div>
      </div>
    </div>
    <div class="grid">
      <div class="card"><p class="muted" style="font-size:12px;margin:0">聊过</p>
        <div class="n">%d</div><p class="hint" style="margin:2px 0 0">轮</p></div>
      <div class="card"><p class="muted" style="font-size:12px;margin:0">互动</p>
        <div class="n">%s</div><p class="hint" style="margin:2px 0 0">天</p></div>
      <div class="card"><p class="muted" style="font-size:12px;margin:0">连续</p>
        <div class="n">%s</div><p class="hint" style="margin:2px 0 0">天</p></div>
    </div>
    <div class="card"><h2>他记住的你</h2>%s</div>
    <div class="card"><h2>最近聊过</h2>%s</div>
    <div class="card"><h2>他说的那句话</h2>%s</div>
    <p style="text-align:center"><a href="/settings" class="hint">设置</a> · <a href="/logout" class="hint">退出</a></p>
    """ % (shown_name or "你",
           ("最近活跃 %s" % a["last_active"]) if a["last_active"] else "还没聊过",
           (shown_name or "?")[:2],
           missing,
           a["tier"], a["level"], a["score"], CUM[MAX_LEVEL], pct, next_hint,
           a["tier"], tier_n, tier_size, tier_pct,
           a["turns"], days_txt, streak_txt,
           chips, topics, ms)
    return _page(body)


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

    msg = ""
    if err:
        msg = '<p class="err">%s</p>' % err
    elif ok:
        msg = '<p class="hint" style="color:#2B7A4B">%s</p>' % ok

    body = """
    <div class="card">
      <h1>设置</h1>
      <p class="muted" style="font-size:12px;margin:0">账号 %s　（QQ 号做了脱敏）</p>
      %s
      <form method="post" action="/settings" style="margin-top:16px">
        <h2>显示名</h2>
        <p class="hint" style="margin:0 0 6px">只改网页上怎么显示；他怎么叫你，得在 QQ 里跟他说。</p>
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
      <p style="margin:12px 0 0;text-align:center"><a href="/me" class="hint">回去</a></p>
    </div>""" % (_mask_uid(uid), msg, name)
    return _page(body, title="设置")


@app.post("/settings")
async def settings_save(request: Request, display_name: str = Form(""),
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

    if new_pwd:
        if not old_pwd or not hmac.compare_digest(rec.get("pwd", ""), _hash(old_pwd)):
            return RedirectResponse("/settings?err=" + "现在的密码不对", status_code=303)
        if len(new_pwd) < 6:
            return RedirectResponse("/settings?err=" + "新密码至少 6 位", status_code=303)
        if new_pwd != (new_pwd2 or "").strip():
            return RedirectResponse("/settings?err=" + "两次输入的新密码不一样", status_code=303)
        rec["pwd"] = _hash(new_pwd)

    rec["display_name"] = name
    users[uid] = rec
    _save_users(users)
    return RedirectResponse("/settings?ok=" + "已保存", status_code=303)


if __name__ == "__main__":
    import uvicorn
    # ⭐ 默认**只听本机**（127.0.0.1）—— 服务器上跑起来也只有本机/SSH 隧道能连，
    #    安全组一个端口都不用开 ⇒ 公网扫不到。
    # ⚠ 想让手机直接连，才改 `WEB_HOST`：
    #     - 组网（Tailscale 这类）⇒ 填服务器在那个网里的 IP（推荐，仍然不暴露公网）
    #     - 0.0.0.0 ⇒ **所有人都能连**（必须同时开安全组 + 换掉默认密码，别图省事这么干）
    uvicorn.run(app,
                host=os.environ.get("WEB_HOST", "127.0.0.1"),
                port=int(os.environ.get("WEB_PORT", "8081")))
