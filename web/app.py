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
import html
import json
import os
import re
import sys
import time

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "ai-Rafayel"))
from Rafayel_affinity import (  # noqa: E402
    compute, days_since, cum_at, MAX_LEVEL,
    load_sms_nodes, sms_full, egg_texts, load_egg_levels,
)

MEMORY_DIR = os.path.join(BASE, "memory")
USERS_PATH = os.path.join(BASE, "web", "users.json")
SALT = "rafael-affinity"


def _load_secret():
    """
    🔑 签名 cookie 用的密钥（2026-09-21 换掉开发期的固定值）。

    取值顺序：
      ① 环境变量 `WEB_SECRET`（部署时想自己指定就用这个）
      ② `web/.secret` 文件 —— **首次启动自动生成**（32 字节随机，secrets.token_hex）
      ③ 连文件都写不了 ⇒ 退回进程内随机值（每次重启都会把所有人踢下线，但至少有值）

    ⭐ 为什么要落到文件而不是每次随机生成：
       密钥一变，**所有已登录的 cookie 立刻作废** ⇒ 每重启一次服务，用户就得重新登录一次。
    ⚠ `web/.secret` **必须留在 .gitignore 里** —— 这是登录凭据，进仓库等于把钥匙挂门口。
    """
    import secrets

    env = (os.environ.get("WEB_SECRET") or "").strip()
    if env:
        return env.encode("utf-8")

    path = os.path.join(BASE, "web", ".secret")
    try:
        s = open(path, encoding="utf-8").read().strip()
        if s:
            return s.encode("utf-8")
    except Exception:
        pass

    s = secrets.token_hex(32)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(s)
        try:
            os.chmod(path, 0o600)          # 只有属主能读（Windows 上是空操作，不报错）
        except Exception:
            pass
    except Exception as e:
        print("⚠️ 写不了 %s（%s）⇒ 本次用进程内随机密钥，重启后会掉登录" % (path, e))
    return s.encode("utf-8")


SECRET = _load_secret()


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
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:12px}
.grid .card{margin:0;padding:1rem;text-align:center}
.grid .n{font-size:20px;font-weight:500;margin-top:4px}
.chip{display:inline-block;background:#F3F1EC;border-radius:999px;padding:4px 10px;font-size:12px;margin:0 8px 8px 0}
.note{background:#EEF4FB;border-radius:8px;padding:1rem;margin-bottom:12px;font-size:12px;color:#33506E}
.note ul{margin:6px 0 0;padding-left:18px}
input,button{font:inherit;padding:8px 10px;border-radius:8px;border:0.5px solid rgba(0,0,0,.2);background:#fff}
button{cursor:pointer;background:#222;color:#fff;border-color:#222;width:100%;margin-top:10px}
.err{color:#B03030;font-size:12px}
"""

# 短信详情页专用（模拟手机聊天）—— 只给那一页，别塞进全站 CSS 让每页都背一遍。
CHAT_CSS = """
.phone{border:0.5px solid rgba(0,0,0,.14);border-radius:16px;overflow:hidden;margin-bottom:14px;background:#EDEDED}
.ph-top{background:#F7F7F7;border-bottom:0.5px solid rgba(0,0,0,.1);padding:10px 14px;
        display:flex;align-items:center;justify-content:space-between}
.ph-top b{font-size:14px;font-weight:500}
.chat{padding:14px 12px 6px}
.row{display:flex;align-items:flex-start;margin-bottom:12px}
.row.me{flex-direction:row-reverse}
.av{flex:0 0 30px;width:30px;height:30px;border-radius:50%;background:#DCE7F5;color:#33506E;
    display:flex;align-items:center;justify-content:center;font-size:12px;margin:0 8px}
.row.me .av{background:#F6DDE7;color:#8E3556}
.bub{max-width:76%;background:#fff;border-radius:12px;padding:8px 11px;font-size:13.5px;
     box-shadow:0 0 0 .5px rgba(0,0,0,.06);word-break:break-word}
.row.me .bub{background:#D4537E;color:#fff}
.bub.tip{background:transparent;border:1px dashed rgba(0,0,0,.22);color:#999;box-shadow:none}
.sys{text-align:center;font-size:11.5px;color:#999;margin:6px 0 12px}
.pick{display:block;text-decoration:none;color:#222;background:#fff;
      border:0.5px solid rgba(0,0,0,.14);border-radius:10px;padding:9px 12px;margin-bottom:8px;font-size:13px}
.pick b{font-weight:500;color:#8E3556;margin-right:8px}
.pick .go{float:right;color:#999;font-size:12px}
.end{text-align:center;font-size:11.5px;color:#999;padding:4px 0 12px}
a.plain{color:inherit;text-decoration:none;display:block}
"""


def _page(body, title="他眼里的你", css=""):
    return HTMLResponse("""<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s</style></head><body><div class="wrap">%s</div></body></html>""" %
                        (title, CSS + css, body))


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
    # ⚠ 用 `cum_at()` 而非 `CUM[...]`：**等级没有上限**，过了官方表的 246 级
    #   `CUM` 就没下标了（会 IndexError）。
    pct = 0
    if a["next_at"]:
        low = cum_at(a["level"])
        span = max(1, a["next_at"] - low)
        pct = min(100, int((a["score"] - low) / span * 100))

    # 本档位内的进度：心动 1~30 / 倾情 31~50 / 眷恋 51~100 / 情衷 101 起
    # ⭐⭐ 最后一档（情衷）是**开放式**的 ⇒ 没有分母可用，
    #    所以这一条**整条不显示**，只留「本档第 N 级」。
    #    （2026-09-21 她指出：246 后面那个「+」就是没封顶，
    #     **别把 6140 / 246 当上限摆出来** —— 那是「官方表到哪」，不是天花板。）
    tier_n = a["level"] - a["tier_lo"] + 1
    open_tier = a["tier_hi"] >= MAX_LEVEL
    tier_size = max(1, a["tier_hi"] - a["tier_lo"] + 1)
    tier_pct = min(100, int(tier_n / tier_size * 100))
    next_hint = ("距 %d 级还差 %d 分" % (a["level"] + 1, a["to_next"])) \
        if a["next_at"] else ""

    if open_tier:
        tier_block = ('<div style="margin-top:12px">'
                      '<span class="hint">%s · 本档第 %d 级</span>'
                      '</div>') % (a["tier"], tier_n)
    else:
        tier_block = ('<div style="margin-top:12px">'
                      '<div style="display:flex;justify-content:space-between">'
                      '<span class="hint">%s</span>'
                      '<span class="hint">本档第 %d / %d 级</span>'
                      '</div>'
                      '<div class="bar" style="margin-top:4px">'
                      '<div style="width:%d%%"></div></div>'
                      '</div>') % (a["tier"], tier_n, tier_size, tier_pct)

    chips = "".join('<span class="chip">%s</span>' % x
                    for x in (a["likes"] + a["traits"] + ["不喜欢：" + x for x in a["dislikes"]]))
    if not chips:
        chips = '<span class="hint">他还没记住什么 —— 多聊几句就有了</span>'

    # ⭐ **没内容就整张卡不渲染**（她 2026-09-21 定的：这两张先隐藏）。
    #    ⚠ 不是「显示占位文案」—— 空卡片比没有卡片更打击人。
    #    ⇒ 好处是以后 `topics` / `milestones` 真有数据了，卡片**自己长出来**，不用再改一次代码。
    def _card(title, inner):
        if not inner:
            return ""
        return '<div class="card"><h2>%s</h2>%s</div>' % (title, inner)

    topics_card = _card("最近聊过", "".join(
        '<p style="margin:0 0 6px;font-size:12px" class="muted">%s</p>' % t
        for t in a["topics"]))

    ms_card = _card("他说过的那句话", "".join(
        '<p style="margin:0 0 6px;font-size:13px">「%s」</p>' % m
        for m in a["milestones"]))

    # 🎁 2026-09-21 她定的：入口**单独一张卡，紧跟「他说过的那句话」**——
    #    · 不塞进上面那张卡里（那是两回事）
    #    · 也不放底部导航（底部只留「设置 / 退出」）
    #    ⚠ 这一张**不跟着 milestones 空不空**：它自己是入口，不是那张卡的尾巴。
    _nodes = load_sms_nodes()
    _un = sum(1 for n in _nodes if n[0] <= a["level"])
    egg_card = ('<div class="card">'
                '<div style="display:flex;justify-content:space-between;align-items:baseline">'
                '<h2 style="margin:0">彩蛋</h2>'
                '<span class="hint">已解锁 %d / %d</span></div>'
                '<p class="muted" style="font-size:12px;margin:6px 0 0">'
                '每跨过一个等级，他就多一点想让你听见的。</p>'
                '<p style="margin:10px 0 0"><a href="/messages" class="hint">进去看看 →</a></p>'
                '</div>') % (_un, len(_nodes))

    # ⭐ 2026-09-21 她定的：**互动天数 / 连续天数整格撤掉** —— 这俩只从接入那天开始记，
    #    老用户认识一百多天却显示「3 天」，摆上去是误导（宁可不显示，也不给假数）。
    #    ⚠ 同理，`a["missing"]`（后台口径：memory/xxx.json、落盘、bot 侧）**绝不显示给用户**。
    missing = ""

    # 💰 token 消耗：她明确说「后台有人机感没关系」，这格就直给。
    #    ⭐ 口径写清楚：这是**累计请求量**，历史每轮都会重复计入，不是「聊了多少字」。
    def _fmt_tokens(n):
        n = int(n or 0)
        if n >= 10000:
            return "%.1f 万" % (n / 10000.0)
        if n >= 1000:
            return "%.1f 千" % (n / 1000.0)
        return str(n)

    tokens_txt = _fmt_tokens(a["tokens"]) if a.get("has_usage") else "—"
    tokens_unit = "token"

    # ⭐⭐ 「认识第 N 天」—— **她说哪天就是哪天**（2026-09-21 她的原话：
    #   「我给的是一个祁煜的载体，用户真正相遇的那天，由她们自己决定」）。
    #   ⇒ 用户在 /settings 自己填 `met_day`；**她填的优先于日志回填的 first_day**
    #     （bot 自己不记第一次是哪天，所以默认值只能来自她）。
    #   ⚠ 只写 `web/users.json`，**绝不写回 memory**（那份只读）。
    met_day = (rec.get("met_day") or "").strip()
    known = 0
    if met_day:
        known = days_since(met_day)
    elif a.get("known_days"):
        known = a["known_days"]

    if known:
        known_txt = "认识第 %d 天 · " % known
    else:
        known_txt = ""

    if a["last_active"]:
        sub = known_txt + "最近一次 %s" % a["last_active"][:10]
    else:
        sub = known_txt.rstrip(" · ") or "还没聊过"

    # 没填「相遇那天」⇒ 给一句引导（不然她根本不知道这个能自己定）
    if not met_day:
        sub += ('　<a href="/settings" class="hint">你们是哪天相遇的？</a>')

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
      <div class="big">%d <span style="font-size:13px" class="muted">分</span></div>
      <div class="bar"><div style="width:%d%%"></div></div>
      <p class="hint" style="margin:8px 0 0">%s</p>
      %s
    </div>
    <div class="grid">
      <div class="card"><p class="muted" style="font-size:12px;margin:0">聊过</p>
        <div class="n">%d</div><p class="hint" style="margin:2px 0 0">轮</p></div>
      <div class="card"><p class="muted" style="font-size:12px;margin:0">已用额度</p>
        <div class="n">%s</div><p class="hint" style="margin:2px 0 0">%s</p></div>
    </div>
    <div class="card"><h2>你们之间</h2>%s</div>
    %s%s%s
    <p style="text-align:center"><a href="/settings" class="hint">设置</a> · <a href="/logout" class="hint">退出</a></p>
    """ % (shown_name or "你",
           sub,
           (shown_name or "?")[:2],
           missing,
           a["tier"], a["level"], a["score"], pct, next_hint,
           tier_block,
           a["turns"], tokens_txt, tokens_unit,
           chips, topics_card, ms_card, egg_card)
    return _page(body)


# ============================================================
# 🎁 牵绊提升（2026-09-21 她定：牵绊短信不进 QQ ⇒ 单开一页放网页端；
#    页面名后来从「牵绊提升彩蛋」收成「牵绊提升」，「我的页」上那张卡只剩两个字「彩蛋」）
# ------------------------------------------------------------
# ⭐ 为什么单开一页：45 条短信带 A/B/C 三段分支，塞进 /me 会把主页撑到没法看。
# ⚠⚠ **没解锁的内容一个字都不许进 HTML**（不是用 CSS 藏起来）——
#    否则右键「查看源代码」就能把 200 级以后的剧情全读了。这条有断言守着。
# ⚠ 只读 memory：等级从 compute() 来，页面一个字都不写。
# ============================================================

def _esc(t):
    """素材 / 标题进页面前一律先转义 —— 是我们自己的文件，但别赌它永远没有尖括号。"""
    return html.escape(str(t or ""), quote=False)


_STICKER_TXT = re.compile(r"\[表情:([^\]]+)\]")


def _rich(t):
    """转义 + 把 `[表情:标签]` 画成小圆片（网页端没有图床，不硬塞真图）。"""
    return _STICKER_TXT.sub(
        r'<span class="chip" style="margin:0;padding:2px 8px">表情 · \1</span>', _esc(t))


def _lock_row(level, right=""):
    """未解锁的占位行：只说「第几级解锁」，**不带一点内容**。"""
    return ('<p class="hint" style="opacity:.5;margin:0 0 8px">🔒 第 %d 级解锁%s</p>'
            % (level, ("　" + _esc(right)) if right else ""))


@app.get("/messages", response_class=HTMLResponse)
async def messages_page(request: Request):
    """「牵绊提升」—— 跨级解锁的官方素材（45 条短信 + 86 条彩蛋短句）。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")

    a = compute(uid, MEMORY_DIR)
    lv = int(a["level"] or 1)

    nodes = load_sms_nodes()
    eggs = egg_texts()
    egg_lv = load_egg_levels()

    sms_un = [n for n in nodes if n[0] <= lv]
    sms_lk = [n for n in nodes if n[0] > lv]
    eg_un = [(egg_lv[i], eggs[i]) for i in range(len(eggs))
             if i < len(egg_lv) and egg_lv[i] <= lv]
    eg_lk = [egg_lv[i] for i in range(len(eggs))
             if i < len(egg_lv) and egg_lv[i] > lv]

    parts = ['<div class="card"><h1>牵绊提升</h1>'
             '<p class="muted" style="font-size:12px;margin:0">'
             '每跨过一个等级，他就多一点想让你听见的。</p>'
             '<p class="hint" style="margin:8px 0 0">已经解锁 %d / %d 条短信 · %d / %d 条彩蛋</p>'
             '</div>' % (len(sms_un), len(nodes), len(eg_un), len(eggs))]

    # ---------------- 短信：一条一个框，点进去才是详细对话 ----------------
    # ⭐ 2026-09-21 她定的版式（就是她截的那张图）：
    #    标题 + 右上「第 N 级 · 档位」+ 开头句一句 + 一行灰字。
    # ⭐ 灰字**从「就地展开」改成「跳转」** ⇒ 列表页轻了，完整对话单开一页（还能一句句聊）。
    parts.append('<h2 style="margin:18px 0 10px">短信</h2>')
    if not sms_un:
        parts.append('<p class="hint" style="margin:0 0 10px">还一条都没解锁 —— 到第 %d 级就有第一条了。</p>'
                     % (sms_lk[0][0] if sms_lk else 1))
    for lvl, tier, title, fn in reversed(sms_un):
        opening = sms_full(fn)["opening"]
        intro = ('<p style="margin:8px 0 0;font-size:13px">「%s」</p>' % _rich(opening)) if opening else ""
        parts.append('<a href="/messages/%d" class="plain"><div class="card">'
                     '<div style="display:flex;justify-content:space-between;align-items:baseline">'
                     '<h2 style="margin:0">%s</h2>'
                     '<span class="hint">%d 级 · %s</span></div>'
                     '%s'
                     '<p class="hint" style="margin:8px 0 0">展开看完整对话 →</p>'
                     '</div></a>'
                     % (lvl, _esc(title), lvl, _esc(tier), intro))
    if sms_lk:
        parts.append('<p class="hint" style="margin:16px 0 8px">—— 还没解锁 ——</p>')
        parts.extend(_lock_row(lvl, title) for lvl, _t, title, _fn in sms_lk)

    # ---------------- 彩蛋：一行一句，不占卡片 ----------------
    parts.append('<h2 style="margin:22px 0 10px">彩蛋</h2>')
    rows = "".join(
        '<p style="margin:0 0 8px;font-size:13px">「%s」'
        '<span class="hint">　第 %d 级</span></p>' % (_rich(t), lvl)
        for lvl, t in reversed(eg_un))
    if not rows:
        rows = '<p class="hint" style="margin:0">还没解锁 —— 到第 %d 级有第一条。</p>' \
            % (eg_lk[0] if eg_lk else 1)
    if eg_lk:
        rows += '<p class="hint" style="margin:14px 0 8px">—— 还没解锁 ——</p>'
        rows += "".join(_lock_row(x) for x in eg_lk)
    parts.append('<div class="card">%s</div>' % rows)

    parts.append('<p style="text-align:center"><a href="/me" class="hint">回去</a></p>')
    return _page("".join(parts), title="牵绊提升")


@app.get("/messages/{level}", response_class=HTMLResponse)
async def message_detail(request: Request, level: int, p: str = ""):
    """
    📱 一条牵绊短信的**详细对话页** —— 模拟手机互发消息，一句一句往下走。

    ⭐ 2026-09-21 她定的交互（原文：「系统：用户回复后再进行接下来的对话」）：
       进来先只看见**他开口的第一句** + 那一段的选项；**她选了之后**，
       她的话和他的回应才出现，再摆下一段的选项 …… 直到对话结束。
       ⚠ 素材本来就是 A/B/C 分支树（42 条 3 段、还有 4 段 / 2 段 / 0 段的），
         不是线性剧本 ⇒ **必须「选一个才往下走」**，一次性铺开就等于剧透自己。

    ⚠ 进度走 URL（`?p=0,1` = 第 1 段选 A、第 2 段选 B），三个好处：
       ① **一个字都不写盘**（web 端对 memory 只读这条铁律不破）
       ② **不需要 JS**（跟全站一致：服务端渲染 + 链接跳转）
       ③ 她能把这个链接存下来 / 发给别人看，进度跟着走
    ⚠⚠ 等级没到 ⇒ 直接弹回列表：**详情页也不许泄漏没解锁的内容**。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")

    a = compute(uid, MEMORY_DIR)
    lv = int(a["level"] or 1)

    node = None
    for n in load_sms_nodes():
        if n[0] == level:
            node = n
            break
    if node is None or level > lv:
        return RedirectResponse("/messages")

    _lv, tier, title, fn = node
    full = sms_full(fn)
    rec = (_load_users().get(uid) or {})
    shown = (rec.get("display_name") or "").strip() or a["name"] or "你"

    # 选了哪几个（URL 里的脏值一律当「没选」，宁可让她重聊，也别把页面搞成 500）
    picked = []
    for x in (p or "").split(","):
        x = x.strip()
        if x.isdigit():
            picked.append(int(x))
        elif x:
            picked.append(-1)

    him_av, her_av = "祁", (shown or "你")[0]
    rows = ['<div class="sys">%s</div>' % _esc(title)]

    def _bub(who, text):
        rows.append('<div class="%s"><div class="av">%s</div><div class="bub">%s</div></div>'
                    % ("row me" if who == "她" else "row",
                       _esc(her_av if who == "她" else him_av), _rich(text)))

    if full["opening"]:
        _bub("他", full["opening"])

    bi = 0
    done = True
    for b in full["blocks"]:
        if b["kind"] != "branch":
            _bub(b["who"], b["text"])
            continue
        idx = picked[bi] if bi < len(picked) else None
        if idx is None or not 0 <= idx < len(b["options"]):
            # 这一段还没选 ⇒ 摆出选项，**后面的内容一个字都不渲染**（break 掉了）
            done = False
            rows.append('<div class="sys">（她回复之后，对话才会继续）</div>')
            rows.append('<div class="row me"><div class="av">%s</div>'
                        '<div class="bub tip">（在下面 %d 个选项里选一个回复）</div></div>'
                        % (_esc(her_av), len(b["options"])))
            for i, op in enumerate(b["options"]):
                nxt = ",".join(str(x) for x in picked[:bi] + [i])
                rows.append('<a class="pick" href="/messages/%d?p=%s">'
                            '<b>%s</b>%s<span class="go">›</span></a>'
                            % (level, nxt, _esc(op["key"]), _esc(op["title"])))
            break
        op = b["options"][idx]
        for x in op["her"]:
            _bub("她", x)
        for x in op["him"]:
            _bub("他", x)
        bi += 1

    if done:
        rows.append('<div class="end">—— 说到这儿就停了 ——</div>')

    head = ('<div class="ph-top"><a href="/messages" class="hint">‹ 返回</a>'
            '<b>祁煜</b><span class="hint">第 %d 级</span></div>' % level)
    body = ('<div class="phone">%s<div class="chat">%s</div></div>'
            '<p style="text-align:center">'
            '<a href="/messages/%d" class="hint">↺ 从头再聊一遍</a> · '
            '<a href="/messages" class="hint">回列表</a></p>'
            % (head, "".join(rows), level))
    return _page(body, title="祁煜 · %s" % title, css=CHAT_CSS)


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

        <h2 style="margin-top:18px">你们相遇的那天</h2>
        <p class="hint" style="margin:0 0 6px">
          你说哪天，就是哪天 —— 他记不住日子，这件事归你说了算。<br>
          留空就不显示「认识第几天」。</p>
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
      <p style="margin:12px 0 0;text-align:center"><a href="/me" class="hint">回去</a></p>
    </div>""" % (_mask_uid(uid), msg, name, met_day)
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
