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

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "ai-Rafayel"))
from Rafayel_affinity import compute, LEVELS  # noqa: E402

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
async def login(uid: str = Form(""), pwd: str = Form("")):
    uid = (uid or "").strip()
    users = _load_users()
    rec = users.get(uid)
    if not rec or not hmac.compare_digest(rec.get("pwd", ""), _hash(pwd or "")):
        return RedirectResponse("/?err=" + "账号或密码不对", status_code=303)
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

    pct = 0
    if a["next_at"]:
        lo = [t for t, _ in LEVELS if t <= a["score"]]
        low = lo[-1] if lo else 0
        span = max(1, a["next_at"] - low)
        pct = min(100, int((a["score"] - low) / span * 100))

    chips = "".join('<span class="chip">%s</span>' % x
                    for x in (a["likes"] + a["traits"] + ["不喜欢：" + x for x in a["dislikes"]]))
    if not chips:
        chips = '<span class="hint">他还没记住什么 —— 多聊几句就有了</span>'

    topics = "".join('<p style="margin:0 0 6px;font-size:12px" class="muted">%s</p>' % t
                     for t in a["topics"]) or \
        '<p style="margin:0;font-size:12px" class="hint">近期话题还没做（批 2）</p>'

    if a["milestones"]:
        ms = "".join('<p style="margin:0 0 6px;font-size:13px">「%s」</p>' % m for m in a["milestones"])
    else:
        ms = '<p style="margin:0;font-size:12px" class="hint">跨级那天他会说一句话 —— 还没做（批 2）</p>'

    missing = ""
    if a["missing"]:
        missing = '<div class="note"><b>还缺的数据</b><ul>' + \
            "".join("<li>%s</li>" % m for m in a["missing"]) + "</ul></div>"

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
        <span class="muted" style="font-size:13px">%s · 第 %d 级</span>
      </div>
      <div class="big">%d</div>
      <div class="bar"><div style="width:%d%%"></div></div>
      <p class="hint" style="margin:8px 0 0">%s</p>
    </div>
    <div class="grid">
      <div class="card"><p class="muted" style="font-size:12px;margin:0">对话</p><div class="n">%d</div></div>
      <div class="card"><p class="muted" style="font-size:12px;margin:0">她先开口</p><div class="n">%d</div></div>
      <div class="card"><p class="muted" style="font-size:12px;margin:0">连续天数</p><div class="n">%d</div></div>
    </div>
    <div class="card"><h2>他记住的你</h2>%s</div>
    <div class="card"><h2>最近聊过</h2>%s</div>
    <div class="card"><h2>他说的那句话</h2>%s</div>
    <p style="text-align:center"><a href="/logout" class="hint">退出</a></p>
    """ % (a["name"] or "你",
           ("最近活跃 %s" % a["last_active"]) if a["last_active"] else "还没聊过",
           (a["name"] or "?")[:2],
           missing,
           a["level_name"], a["level"], a["score"], pct,
           ("距下一级还差 %d" % a["to_next"]) if a["next_at"] else "已经是最高一级",
           a["turns"], a["she_initiated"], a["streak"],
           chips, topics, ms)
    return _page(body)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8081)
