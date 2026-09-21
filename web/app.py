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

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "ai-Rafayel"))
from Rafayel_affinity import (  # noqa: E402
    compute, days_since, cum_at, MAX_LEVEL,
    load_sms_nodes, sms_full,
)

MEMORY_DIR = os.path.join(BASE, "memory")
USERS_PATH = os.path.join(BASE, "web", "users.json")

# 🖼 头像（2026-09-21 她提的「头像上传」）
#    ⚠ 存 `web/avatars/{uid}.{ext}` —— 这是**用户数据**，在 .gitignore 里，不进仓库。
#       它也**不是** bot 的 memory（那份只读，一个字都不许写）。
#    ⚠ 扩展名**由内容嗅探决定**，不采信客户端交上来的文件名 ⇒ 路径穿越那条路天然堵死。
AVATAR_DIR = os.path.join(BASE, "web", "avatars")
AVATAR_EXTS = ("png", "jpg", "webp", "gif")
AVATAR_MAX = 2 * 1024 * 1024        # 2 MB

# 🖼 祁煜**自己的**头像（2026-09-21 她给的那张蓝海油画 —— 用在短信详情页的聊天气泡上）
#    ⚠ 跟上面的 `web/avatars/` **正相反**：这是**项目素材**、**要进仓库**
#      （所以别往 `.gitignore` 里加 `web/assets/`）。
#    ⚠ 走**白名单**：URL 里只出现 key（`/asset/qiyu`），真文件名与目录在代码里写死 ⇒ 无路径穿越。
ASSET_DIR = os.path.join(BASE, "web", "assets")
ASSET_FILES = {"qiyu": "qiyu.jpg"}
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
/* ⚠⭐ 2026-09-21 修过一次：这条原来只写在 CHAT_CSS（**详情页专用**）里，
   可**列表页 `/messages` 也在用** `class="plain"` ⇒ 那一页拿不到它，
   整块卡片就掉回浏览器默认的**蓝色下划线链接**（她一眼看出来的那个）。
   ⇒ 教训：**共用样式就该放这份共用 CSS**，别塞进某一页的专用串里。 */
a.plain{color:inherit;text-decoration:none;display:block}
/* 卡片里**唯一**那个入口「展开查看 →」（她 2026-09-21 定的：
   别再拿 `<a>` 把整张卡包起来 —— 卡里除它以外都该是普通的字） */
a.cta{color:#8E3556;text-decoration:none;font-size:12px}
button.ghost{background:#fff;color:#777;border-color:rgba(0,0,0,.2)}
img.avatar{width:36px;height:36px;border-radius:50%;object-fit:cover;display:block;background:#EEF4FB}
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
    display:flex;align-items:center;justify-content:center;font-size:12px;margin:0 8px;
    overflow:hidden}
.av img{width:100%;height:100%;object-fit:cover;display:block;border-radius:50%}
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
"""


def _page(body, title="他眼里的你", css=""):
    return HTMLResponse("""<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s</style></head><body><div class="wrap">%s</div></body></html>""" %
                        (title, CSS + css, body))


# ============================================================
# 🖼 头像（2026-09-21 她提的）
# ------------------------------------------------------------
# ⭐ 全站**零 JS** 的老规矩不变：上传就是一个普通的 `<form enctype="multipart/form-data">`，
#    浏览器自己就会发 multipart，不需要一行脚本。
# ⚠ 只认**自己登录的那个人的**头像：uid 一律来自签名 cookie，绝不从表单里取。
# ============================================================

def _safe_uid(uid):
    """uid 进文件名前先洗一遍（本来就该是纯 QQ 号，但别赌）。"""
    return re.sub(r"[^0-9A-Za-z_-]", "", uid or "")


def _sniff_image(raw):
    """
    按**文件头**认图，返回 `png` / `jpg` / `webp` / `gif`；认不出来返回 `""`。

    ⚠ 为什么不信 `content_type` 和文件名：那两个都是**客户端说了算的字符串**。
      我们只信字节 —— 顺手把「传个脚本改名叫 .png」那条也堵掉。
    """
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return ""


def _avatar_file(uid):
    """这个用户头像的**真实路径**（没传过 / uid 不合法 ⇒ 返回 ""）。"""
    safe = _safe_uid(uid)
    if not safe:
        return ""
    for ext in AVATAR_EXTS:
        p = os.path.join(AVATAR_DIR, "%s.%s" % (safe, ext))
        if os.path.isfile(p):
            return p
    return ""


def _avatar_url(uid):
    """
    头像的访问地址（没传过返回 ""）。

    ⭐ 尾巴挂个 `?v=改动时间` —— 换了头像链接跟着变，浏览器不会攥着旧图不撒手
      （比让用户自己「强刷一下」体面多了）。
    """
    p = _avatar_file(uid)
    if not p:
        return ""
    try:
        v = int(os.path.getmtime(p))
    except OSError:
        v = 0
    return "/avatar?v=%d" % v


def _drop_avatar(uid):
    """删掉这个用户已有的头像（换扩展名时别留垃圾）。返回删掉了几个。"""
    safe = _safe_uid(uid)
    if not safe:
        return 0
    n = 0
    for ext in AVATAR_EXTS:
        p = os.path.join(AVATAR_DIR, "%s.%s" % (safe, ext))
        if os.path.isfile(p):
            try:
                os.remove(p)
                n += 1
            except OSError:
                pass
    return n


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
    # ⭐ 2026-09-21 她定的第二处：连这句里的「分」也去掉 —— 页面上**凡是数字都不挂单位**。
    next_hint = ("距 %d 级还差 %d" % (a["level"] + 1, a["to_next"])) \
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
    # ⚠⭐ 2026-09-21 改名（她看到 15 级面板空着时顺手指出的）：
    #    这张卡数的是 **`load_sms_nodes()` = 45 条短信**的解锁数、点进去也是 `/messages`；
    #    而 86 条**彩蛋按她定的口径根本不进网页端**（§19.6/§19.7，只走 QQ）——
    #    所以叫「彩蛋」是**名实不符**。⇒ 改成 **「牵绊短信」**。
    #    ⚠ 彩蛋那条线**没被动**：QQ 端照发，「/me」上那张「他说过的那句话」照读彩蛋。
    _nodes = load_sms_nodes()
    _un = sum(1 for n in _nodes if n[0] <= a["level"])
    sms_card = ('<div class="card">'
                '<div style="display:flex;justify-content:space-between;align-items:baseline">'
                '<h2 style="margin:0">牵绊短信</h2>'
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

    # 🖼 头像：传了图就显示图；没传就退回「名字首字」那个小圆片（老样子）。
    #    ⚠ 尺寸保持 36px 没动 —— 换图的收益是「那是张真脸」，不是顺手把版式改一遍。
    _av = _avatar_url(uid)
    if _av:
        _av_html = '<img src="%s" alt="" class="avatar">' % _av
    else:
        _av_html = ('<div style="width:36px;height:36px;border-radius:50%%;background:#EEF4FB;'
                    'color:#33506E;display:flex;align-items:center;justify-content:center;'
                    'font-size:13px">%s</div>' % _esc((shown_name or "?")[:2]))

    body = """
    <div class="card" style="display:flex;align-items:center;justify-content:space-between">
      <div>
        <h1>%s</h1>
        <p class="muted" style="font-size:12px;margin:0">%s</p>
      </div>
      <div style="width:36px;height:36px">%s</div>
    </div>
    %s
    <div class="card">
      <div style="display:flex;justify-content:space-between">
        <span class="muted" style="font-size:13px">好感度</span>
        <span class="muted" style="font-size:13px">%s · %d 级</span>
      </div>
      <div class="big">%d</div>
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
           _av_html,
           missing,
           a["tier"], a["level"], a["score"], pct, next_hint,
           tier_block,
           a["turns"], tokens_txt, tokens_unit,
           chips, topics_card, ms_card, sms_card)
    return _page(body)


# ============================================================
# 🎁 牵绊提升（2026-09-21 她定：牵绊短信不进 QQ ⇒ 单开一页放网页端；
#    页面名后来从「牵绊提升彩蛋」收成「牵绊提升」；「我的页」上那张入口卡
#    2026-09-21 又从「彩蛋」改成 **「牵绊短信」** —— 它数的是短信，别再叫彩蛋）
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
    """
    「牵绊提升」—— 跨级解锁的官方素材（45 条短信）。

    ⚠⚠ 2026-09-21 她定的口径：**彩蛋不上网页端**。
       那 86 句是「**QQ 端聊天时可用的语料**」—— 等级到了，他在对话里偶尔提一句；
       而不是摆成一页给人从头翻到尾。所以这一页**只剩短信**，一个字彩蛋都不渲染。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")

    a = compute(uid, MEMORY_DIR)
    lv = int(a["level"] or 1)

    nodes = load_sms_nodes()
    sms_un = [n for n in nodes if n[0] <= lv]
    sms_lk = [n for n in nodes if n[0] > lv]

    parts = ['<div class="card"><h1>牵绊提升</h1>'
             '<p class="muted" style="font-size:12px;margin:0">'
             '每跨过一个等级，他就多一点想让你听见的。</p>'
             '<p class="hint" style="margin:8px 0 0">已经解锁 %d / %d 条短信</p>'
             '</div>' % (len(sms_un), len(nodes))]

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
        # ⭐ 2026-09-21 她定的：**别再拿 `<a>` 把整张卡包起来** ——
        #    卡里除了那行「展开查看 →」，其余全当普通的字（标题、档位、开头句都不该是链接色）。
        parts.append('<div class="card">'
                     '<div style="display:flex;justify-content:space-between;align-items:baseline">'
                     '<h2 style="margin:0">%s</h2>'
                     '<span class="hint">%d 级 · %s</span></div>'
                     '%s'
                     '<p style="margin:8px 0 0">'
                     '<a href="/messages/%d" class="cta">展开查看 →</a></p>'
                     '</div>'
                     % (_esc(title), lvl, _esc(tier), intro, lvl))
    if sms_lk:
        parts.append('<p class="hint" style="margin:16px 0 8px">—— 还没解锁 ——</p>')
        parts.extend(_lock_row(lvl, title) for lvl, _t, title, _fn in sms_lk)

    # ⚠⚠ 2026-09-21 她拆掉的「彩蛋」段落（86 句、一行一句）**不要加回来** ——
    #     她定：彩蛋是**QQ 端聊天时偶尔提到的语料**（等级到了才解锁），
    #     不是摆成一页给人从头翻的展示内容。彩蛋只走 QQ，网页端只有短信。

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

    # 🖼 他的头像 = 那张蓝海油画（2026-09-21 她给的图），走白名单路由、别把路径写进 HTML。
    #    她的还是「名字首字」那个小圆片。
    him_av = '<img src="/asset/qiyu" alt="祁煜">'
    her_av = (shown or "你")[0]
    rows = ['<div class="sys">%s</div>' % _esc(title)]

    def _bub(who, text):
        # ⚠ 他的头像是 **HTML**（`<img>`）⇒ 这条**不能再 `_esc`**；她的仍是纯文本，照旧转义。
        av = _esc(her_av) if who == "她" else him_av
        rows.append('<div class="%s"><div class="av">%s</div><div class="bub">%s</div></div>'
                    % ("row me" if who == "她" else "row", av, _rich(text)))

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

    # ⚠ 2026-09-21 她定：标题栏**不加**头像（只有气泡里那个换真图）。别再往这儿塞 `<img>`。
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

      <p style="margin:12px 0 0;text-align:center"><a href="/me" class="hint">回去</a></p>
    </div>""" % (_mask_uid(uid), msg, name, met_day, av_preview, av_state, av_remove)
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


# ============================================================
# 🖼 头像：上传 / 移除 / 取图（2026-09-21 她提的）
# ------------------------------------------------------------
# ⚠ 只动 `web/avatars/`（**在 .gitignore 里**）—— bot 的 memory 仍然只读，一个字不写。
# ============================================================

@app.post("/settings/avatar")
async def avatar_upload(request: Request, pic: UploadFile = File(None)):
    """
    上传头像。**零 JS**：就是一个普通 multipart 表单，提交完 303 回设置页。

    ⚠ 校验一律看**字节**，不信客户端报的 `content_type` / 文件名：
      ① 只读 `AVATAR_MAX + 1` 字节 ⇒ 超大文件不会先把内存吃掉；
      ② `_sniff_image()` 认**文件头** ⇒ 不是真图就拒（改名的假图也拦得住）；
      ③ 落盘名 = `{洗过的 uid}.{嗅探出来的扩展名}` ⇒ 文件名完全由我们定，路径穿越无从谈起。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if not _safe_uid(uid):
        return RedirectResponse("/settings?err=" + "账号信息不对，重新登录一下", status_code=303)

    raw = b""
    if pic is not None:
        try:
            raw = await pic.read(AVATAR_MAX + 1)
        except Exception:
            raw = b""

    if not raw:
        return RedirectResponse("/settings?err=" + "没选到图片，再试一次", status_code=303)
    if len(raw) > AVATAR_MAX:
        return RedirectResponse("/settings?err=" + "图太大了，换一张 2 MB 以内的", status_code=303)

    ext = _sniff_image(raw)
    if not ext:
        return RedirectResponse("/settings?err=" + "只认 png / jpg / webp / gif 这几种图",
                                status_code=303)

    os.makedirs(AVATAR_DIR, exist_ok=True)
    _drop_avatar(uid)                      # 换了格式时别把旧的那张留成垃圾
    dst = os.path.join(AVATAR_DIR, "%s.%s" % (_safe_uid(uid), ext))
    with open(dst, "wb") as f:
        f.write(raw)
    return RedirectResponse("/settings?ok=avatar", status_code=303)


@app.post("/settings/avatar/remove")
async def avatar_remove(request: Request):
    """删掉自己的头像（还是普通表单 POST，没有一行 JS）。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    _drop_avatar(uid)
    return RedirectResponse("/settings?ok=avatar_off", status_code=303)


@app.get("/avatar")
async def avatar_get(request: Request):
    """
    看**自己**的头像。

    ⚠ 只按签名 cookie 里的 uid 取，**不收任何路径参数** —— 别人的头像根本没有入口能拿到。
    ⭐ `no-store`：刚换完图刷新就得是新图（链接上那个 `?v=` 是第二道保险）。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    p = _avatar_file(uid)
    if not p:
        return RedirectResponse("/")
    return FileResponse(p, headers={"Cache-Control": "no-store"})


@app.get("/asset/{name}")
async def asset_get(request: Request, name: str):
    """
    项目自带的静态素材（现在就一张：**祁煜的头像**）。

    ⚠ 走**白名单** —— `name` 只用来查表，**不拼进路径** ⇒ 路径穿越进不来。
    ⚠ 只放**项目素材**；用户自己传的头像走 `/avatar`，两者**别混**
      （那个是用户数据、不进仓库；这个是仓库里的图）。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    fn = ASSET_FILES.get(name)
    if not fn:
        return RedirectResponse("/")
    p = os.path.join(ASSET_DIR, fn)
    if not os.path.isfile(p):
        return RedirectResponse("/")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})


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
