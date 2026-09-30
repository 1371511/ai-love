# -*- coding: utf-8 -*-
"""
🧱 网页端 · 底座（2026-09-29 从原来 1539 行的 `web/app.py` 拆出来）

这个文件**不放任何路由**，只放每个页面都要用的东西：
路径常量 / 签名 cookie 的密钥 / 用户读写 / 登录限流 / 页面外壳 / 转义与样式串。

⭐ 三条铁律（改任何页面前先看这里）
  1. **只读 memory，一个字都不写** —— 那批文件是 bot 的记忆，写坏他人设就崩。
     ⚠ 唯一的开口是 `/chat` 与 `/chat/send`（为什么破、破到什么程度，见 `page/chat.py` 文件头）。
  2. **好感度绝不进 QQ 对话** —— 系统数据，一进聊天就破「不露机器人那一面」。
  3. **登录后只能看自己** —— cookie 带签名，服务端不信任前端传的 uid。

⚠ 不连数据库：数据就是 `memory/*.json`。bot 的真相源本来就是文件，
   再加一套库就得双写或同步，两份数据打架是最难查的 bug。十几人的量，文件够。

⚠⭐ `app = FastAPI()` **就在这个文件里** —— 各页面模块 `from base import app` 之后
   直接 `@app.get(...)` 往它上面挂路由。于是 `web/app.py` 只需按固定顺序 import 各页
   就能装配出完整站点（**import 顺序 = 路由注册顺序**，别随手调）。

🗂 页面在 `web/page/` 下：login(首页·登录) / menu(目录) / me(老地址跳转壳)
   / affinity(好感度后台) / messages(牵绊短信) / settings(设置) / avatar(头像·素材)
   / chat(对话窗口)。
"""

import datetime
import hashlib
import hmac
import html
import json
import os
import re
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ⚠ 这一行必须在**任何** `import Rafayel_*` 之前生效 —— 每个页面模块都先 import base，
#    所以搁这儿最稳（原来在 app.py 里，搬过来位置等价）。
sys.path.insert(0, os.path.join(BASE, "ai-Rafayel"))

# 🔢 「认识第 N 天」要用引擎那条 `days_since()`（`YYYY-MM-DD` ⇒ 第几天，含首尾）。
#    ⚠ 位置**必须在**上面那句 `sys.path.insert` 之后（那行就是给它铺路的）。
#    ⚠ `Rafayel_affinity` 在 `check_static.py` 的 `WEB_WHITELIST` 里（只读引擎）
#      ⇒ 网页端 import 它**不算**破 ADR-22，不用去 `WEB_WRITE_EXCEPTION` 开口子。
from Rafayel_affinity import days_since  # noqa: E402

MEMORY_DIR = os.path.join(BASE, "memory")
USERS_PATH = os.path.join(BASE, "web", "users.json")

# 🖼 头像（2026-09-21 她提的「头像上传」）
#    ⚠ 存 `web/avatars/{uid}.{ext}` —— 这是**用户数据**，在 .gitignore 里，不进仓库。
#       它也**不是** bot 的 memory（那份只读，一个字都不许写）。
#    ⚠ 扩展名**由内容嗅探决定**，不采信客户端交上来的文件名 ⇒ 路径穿越那条路天然堵死。
#    ⚠ 读头像的小工具（`_avatar_file` / `_avatar_url`）也在这个文件里 —— 它们被
#      「好感度后台 / 设置 / 对话窗口」三处共用，属于底座，不属于 `/avatar` 那一条路由。
AVATAR_DIR = os.path.join(BASE, "web", "avatars")
AVATAR_EXTS = ("png", "jpg", "webp", "gif")
AVATAR_MAX = 2 * 1024 * 1024        # 2 MB（只有 page/avatar.py 的「上传」用得到，整块放这儿更清楚）

SALT = "rafael-affinity"
DEFAULT_PWD = "qiyu2026"        # ⭐ 统一默认密码：所有人第一次都用这个进来（在 /settings 里自己改）


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


# ------------------------------------------------------------ 相遇日 / 认识天数
# ⭐⭐ 这两件东西**全站只认一个口径**（2026-09-30 她定）：
#     · 相遇日 = 她自己填的那一天，存 `web/users.json` 的 `met_day`
#       （**不进 memory** —— 那份是 bot 的记忆，只读）。
#     · 认识第 N 天 = **从相遇日算出来的**；相遇日空着就**是空**。
#     ⚠ 原来还有一条回落：「她没填 ⇒ 拿日志回填的 `first_day` 顶一下」。
#       现在**故意去掉** —— 那个 first_day 是从聊天日志倒推的，
#       在她眼里就是「系统替她瞎猜了一个相识纪念日」，不该由我们先开这个口。
#       （`compute()` 返回值里的 `first_day` / `known_days` **字段保留**，
#         `/affinity` 的日志类信息还能用；改的只是**界面口径**。）
#     ⚠ 三个页面共用（`home` 主页面 / `affinity` 后台 / `menu` 目录页）——
#       所以下沉到这里，别在页面里各抄一份（抄三份必然漂）。
def _check_met_day(day):
    """
    校验「你们相遇的那天」。返回错误文案（人话），没问题返回空串。

    ⭐ 只校验**格式与常识**，**不替她决定是哪天** —— 这是她自己说了算的事。
    ⚠ 错误文案会进 URL ⇒ 别写引号、别写换行；也**不许出现后台词**。
    ⚠ 2026-09-30 从 `page/settings.py` 搬过来：主页的「相遇」那一行现在也能改了，
      两个入口（`/settings` 与 `/home/edit/met_day`）必须走**同一个**校验，
      不然会出现「设置页拒的日子、主页收下了」。
    """
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


def met_known(uid, a=None):
    """
    取「你们相遇」+「认识第 N 天」。返回 `(met_day, known)`：
    `met_day` 空 ⇒ `known` 必为 **0**（页面按「—」/「还没填」显示）。

    ⚠ `a` 传 `compute()` 的结果只是**为了少读一次文件**（顺带留个口子给将来），
      本函数**不再拿它对认识天数做任何回落**。
    """
    rec = (_load_users().get(str(uid)) or {})
    met_day = str(rec.get("met_day") or "").strip()
    return met_day, (days_since(met_day) if met_day else 0)


def _known_uids():
    """
    ⭐ memory 里出现过的人 = **真的跟他说过话的人**（`memory/{QQ}.json` 等）。

    ⚠ 只取**纯数字**：`*_daily` / `*_usage` / `*_profile` 这些尾巴会带出同一个 QQ，
      用 `_` 切一刀再判 `isdigit()` 就都归一了（顺带把 `cli` 这种测试号排除掉）。
    """
    out = set()
    if not os.path.isdir(MEMORY_DIR):
        return out
    for name in os.listdir(MEMORY_DIR):
        if not name.endswith(".json"):
            continue
        uid = name[:-5].split("_")[0]
        if uid.isdigit():
            out.add(uid)
    return out


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

CSS = """/* 🎨 主题变量层（2026-09-30 预留，等「自定义美化」那批才真正用上）
   ⚠ 现在每一项的值 = 接线前那些硬编码色值，**一字未改** ⇒ 渲染结果零变化。
   ⚠ 已接线：这份全站 CSS + `page/home.py` 的 HOME_CSS。
     还没接的：chat / menu / messages 那几份页面专用串 —— 等各自那批顺手换。 */
:root{
  --c-bg:#FAFAF8;--c-card:#fff;--c-ink:#222;--c-muted:#777;--c-hint:#999;
  --c-line:rgba(0,0,0,.12);--c-line-2:rgba(0,0,0,.2);
  --c-hair:rgba(0,0,0,.08);--c-hair-2:rgba(0,0,0,.1);
  --c-brand:#D4537E;--c-brand-ink:#8E3556;--c-brand-soft:#EEF4FB;--c-brand-deep:#33506E;
  --c-brand-halo:rgba(212,83,126,.18);--c-veil:rgba(0,0,0,.34);
  --c-chip:#F3F1EC;--c-sunk:#EEE;--c-sunk-soft:#F2F2F2;
  --c-danger:#B03030;--c-ok:#4CAF7D;--c-off:#B4B2A9;
  --r-card:12px;--r-ctl:8px
}
body{margin:0;padding:2rem 1rem;background:var(--c-bg);color:var(--c-ink);font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.6}
.wrap{max-width:560px;margin:0 auto}
.card{background:var(--c-card);border:0.5px solid var(--c-line);border-radius:var(--r-card);padding:1rem 1.25rem;margin-bottom:12px}
.muted{color:var(--c-muted)} .hint{color:var(--c-hint);font-size:12px}
h1{font-size:16px;font-weight:500;margin:0 0 2px}
h2{font-size:13px;font-weight:500;margin:0 0 10px}
.big{font-size:26px;font-weight:500;margin:6px 0 10px}
.bar{height:6px;background:var(--c-sunk);border-radius:3px;overflow:hidden}
.bar>div{height:100%;background:var(--c-brand);border-radius:3px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:12px}
.grid .card{margin:0;padding:1rem;text-align:center}
.grid .n{font-size:20px;font-weight:500;margin-top:4px}
.chip{display:inline-block;background:var(--c-chip);border-radius:999px;padding:4px 10px;font-size:12px;margin:0 8px 8px 0}
.note{background:var(--c-brand-soft);border-radius:8px;padding:1rem;margin-bottom:12px;font-size:12px;color:var(--c-brand-deep)}
.note ul{margin:6px 0 0;padding-left:18px}
input,button{font:inherit;padding:8px 10px;border-radius:var(--r-ctl);border:0.5px solid var(--c-line-2);background:var(--c-card)}
button{cursor:pointer;background:var(--c-ink);color:var(--c-card);border-color:var(--c-ink);width:100%;margin-top:10px}
.err{color:var(--c-danger);font-size:12px}
/* ⚠⭐ 2026-09-21 修过一次：这条原来只写在 CHAT_CSS（**详情页专用**）里，
   可**列表页 `/messages` 也在用** `class="plain"` ⇒ 那一页拿不到它，
   整块卡片就掉回浏览器默认的**蓝色下划线链接**（她一眼看出来的那个）。
   ⇒ 教训：**共用样式就该放这份共用 CSS**，别塞进某一页的专用串里。 */
a.plain{color:inherit;text-decoration:none;display:block}
/* ⭐ 2026-09-29 从 `page/chat.py` 的 `TALK_CSS` **搬到这儿** —— 跟上面 `a.plain` 是同一个教训。
   `.dot`（那个绿色在线点）**两个页都在用**：`/me` 的「跟他说话」卡 + `/chat` 的顶栏。
   原来只写在 `TALK_CSS` 里，而那份**只有 `/chat` 加载** ⇒ **`/me` 上那个点根本不渲染**
   （那张卡右上角孤零零挂着一个「在」字，她截图看出来的）。
   ⇒ 规矩复述：**共用的样式就放这份共用 CSS**，别塞进某一页的专用串里。
   ⚠ 只管「小部件级」的样式；`.footnav` / `.bar-bottom` 那种「必须是 `.wrap` 里
     那层 `.main` 的直接子元素」的布局件，继续留在各页 / 底座原处。 */
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--c-ok);
     margin-left:6px;vertical-align:middle}
/* 卡片里**唯一**那个入口「展开查看 →」（她 2026-09-21 定的：
   别再拿 `<a>` 把整张卡包起来 —— 卡里除它以外都该是普通的字） */
a.cta{color:var(--c-brand-ink);text-decoration:none;font-size:12px}
button.ghost{background:var(--c-card);color:var(--c-muted);border-color:var(--c-line-2)}
img.avatar{width:36px;height:36px;border-radius:50%;object-fit:cover;display:block;background:var(--c-brand-soft)}
/* ⭐ 2026-09-21 她定：**底部那一条钉在屏幕底部**（原话「让它一直保持在界面里」）——
   原本是每页各自一行居中的灰字链接，页面一长就跟着滚走。
   ⚠ 内容**一个字都没加**（还是各页原来那几个链接）—— 她之前定的「底部只留设置 / 退出」没动。
   ⚠⭐ 2026-09-22 改成 **`position:sticky`**（她截图：`fixed` 那条底下还漏出一截页面内容 ——
   iOS Safari 的 `fixed;bottom:0` 跟真实可视区对不齐，页面能从它底下再滚一截）。
   sticky 在**文档流里**、跟着内容走 ⇒ 内容**不可能**跑到它下面，也不需要再给 body 留位。
   ⇒ 负 margin 是三件事：左右 `-1rem` 抵消 body 的左右留白（**通栏**）、
     底下 `-2rem` 抵消 body 的 `padding:2rem`（滚到底时那条贴着屏幕最底下，不再悬空 2rem）。
   ⇒ 改 body 的左右/底部 padding，**必须同步改这三个 margin**。*/
.footnav{position:sticky;bottom:0;z-index:10;
         background:var(--c-bg);border-top:0.5px solid var(--c-hair-2);
         padding:10px 1rem;text-align:center;font-size:12px;
         margin:0 -1rem -2rem}
/* 🔀 底栏里那两个动作：**左右各占一半**（2026-09-30 她提「不要靠太近，容易误按」）
   ⇒ 点击区是整个半条，中间空 12px；原来「A · B」只隔一个点，手指一点就戳错。
   ⚠ 每格上下也给 8px padding ⇒ 触摸区约 36px 高，比光文字那条大一圈。
   ⚠⭐ **2026-09-30 从 `page/home.py` 的 `HOME_CSS` 搬到这里**（她截图：底栏两个链接挤成一团）：
      `_two_way_footer()` 早就下沉到本文件了，**样式却没跟着走** —— 于是
      ① `/home/edit/{kind}`（称呼/生日/相遇日）调的是 `_page(body)`，**没带 `css=HOME_CSS`**；
      ② `/diary/new`、`/diary/e` 只带 `DIARY_CSS`；
      两处的 `.twobar` 都落空 ⇒ 退化成两个行内链接贴着排。
      ⇒ **规矩**：`.footnav` 这个部件的样式一律留在本文件（全站 CSS），
        用它的页面**不需要也不该**再自己写一遍 —— 否则新页面又会漏。
      ⚠ 别再往 `HOME_CSS` 里放回一份：重复的规则改起来必然只改一处，迟早打架。 */
.twobar{display:flex;gap:12px}
.twobar a{flex:1 1 0;padding:8px 0;text-align:center}
/* 🧭 目录页那几行入口（2026-09-29 新增）—— 由 `_nav_list()` 渲染，
   手机渲染成一整页（`page/menu.py`）、桌面渲染成左侧栏（纯 CSS，同一份 HTML）。
   ⚠ 灰项（预留 / 待填）渲染成 `<span class="off">` 而不是 `<a>` —— 点不动的东西不该是链接。
   ⚠ `.navgap` 是「设置 / 退出」上面那道分隔：一个高度 12px 的空行。 */
.who{display:flex;align-items:center;gap:11px;margin-bottom:18px}
.who-n{font-size:16px;font-weight:500;margin:0;line-height:1.35}
.who-s{font-size:12px;color:var(--c-hint);margin:0;line-height:1.35}
.nav{display:flex;flex-direction:column}
.nav>*{display:flex;justify-content:space-between;align-items:center;gap:8px;
       padding:11px 2px;font-size:14px;color:var(--c-ink);text-decoration:none;
       border-bottom:0.5px solid var(--c-hair)}
.nav>a.on{font-weight:500;color:var(--c-brand-ink)}
.nav>.off{color:var(--c-off)}
.nav>.navgap{height:12px;padding:0;display:block}
.nav>*:last-child{border-bottom:none}

/* 🖥 桌面版（2026-09-29 第 3 步，她定的）—— 左栏常驻 + 右列内容。
   ⭐ **一份数据、两种形态**：左栏用的就是 `_nav_list()` 那份 HTML，`page/menu.py` 的手机整页
     也是它 ⇒ 加 / 改名 / 灰掉一个入口**只改 `NAV`**，两端自动同步。
   ⚠ 窄屏 `.side` 直接 `display:none`（整页目录在 `page/menu.py` 里）
     ⇒ 下面这条 `@media` 是**纯加法**，900px 以下的渲染结果跟加它之前**逐字节一样**。
   ⚠ 她 2026-09-29 明确「右侧内容布置我要自己重排」⇒ 这里**只搭壳**（左栏 + 可换的右列），
     不替她定右列内容。
   ⚠⚠ 断点 900 不是随便取的：左栏 172 + 间距 32 + 右列 660 + body 左右各 1rem = **896**
     ⇒ 899 就会挤，900 刚好放得下。改任何一个数都要把这条重算。 */
.side{display:none}
@media(min-width:900px){
  .main:has(> .footnav:not(.backonly)){align-self:stretch;display:flex;flex-direction:column}
  .main:has(> .footnav:not(.backonly))>.footnav{margin:14px 0 -2rem;margin-top:auto}
  /* 整体靠左（不平分剩余空间）—— 照她看的那版 mockup */
  .wrap{max-width:none;margin:0;display:grid;
        grid-template-columns:172px minmax(0,660px);
        column-gap:32px;align-items:start;justify-content:start}
  /* 左栏钉住不跟着滚。`align-items:start` 是必须的：它让左栏的高度**只有内容那么高**，
     这样 sticky 才有活动余地（默认 `stretch` 会把它拉满整列、等于没得贴）。 */
  .side{display:block;position:sticky;top:2rem}
  .main{min-width:0}
  /* 桌面有常驻左栏 ⇒ 纯「回目录」那条底栏是重复的，藏掉。
     ⚠⭐ **只藏 `.backonly`**（`_backbar()` 渲染的那条）——
       `/messages/{level}` 的底栏里还有「↺ 从头再聊一遍」这种**页面级动作**，不能一起藏。 */
  .footnav.backonly{display:none}
  /* 底栏那套「左右通栏 + 底下抵消 body padding」是给手机通栏设计的；桌面上它挂在
     660px 的内容列下面，通栏会横着溢出去 ⇒ 归零，紧贴内容列。 */
  .footnav{margin:14px 0 0}
}"""

# 📱 模拟手机聊天那套外壳 —— `page/messages.py` 的详情页 + `page/chat.py` 的对话窗口**共用**。
#    ⚠ 原文案是「只给那一页，别塞进全站 CSS 让每页都背一遍」，2026-09-29 拆文件时
#      它变成两页共用 ⇒ 归底座（跟 `CSS` 里那条 `a.plain` 是同一个教训：
#      **共用的样式就该放共用那份**，别塞进某一页的专用串里）。
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
/* ⭐ 2026-09-22 她嫌「像放 PPT」：新冒出来的那几行（外面包的那个 fresh 层）
   淡入一下 —— 一点点动效就够，别做成整页动画。⚠ 晕动症偏好关掉就别动。
   ⚠ 注释里**别照抄那段 HTML**（写 `class=...` 会让「页面里有没有」的断言误命中）。*/
.fresh>div{animation:pop .22s ease-out}
@keyframes pop{from{opacity:.35;transform:translateY(6px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.fresh>div{animation:none}}
"""


def _page(body, title="他眼里的你", css="", script="", nav=True):
    # ⭐ `script` 只给**短信详情页**用（2026-09-22 的局部刷新）；其余各页照旧零 JS。
    # ⭐ 2026-09-29 加 `nav`：**左栏只在桌面显示**（窄屏 `.side` 是 `display:none`），
    #    内容就是 `_nav_list()` —— 跟 `page/menu.py` 共用同一份 HTML。
    # ⚠⭐ `/menu` **不**用 `nav=False` —— 它原来传过，但那样 `.wrap` 这个网格只剩一个子元素，
    #    正文会被塞进第一列（172px）直接压扁（真机量到 `mainW=172`）。
    #    ⇒ 现在改成「左栏照出、正文那份入口列桌面用 CSS 藏掉」，见 `page/menu.py` 的 `MENU_CSS`。
    #    这个开关留着给「将来真有个整页都不需要导航的页」用，目前**没有页面传 False**。
    # ⚠ 正文外面那层 `<div class="main">` 是给桌面网格当右列的（窄屏它只是个普通块，
    #    不产生任何视觉差异）。`.footnav` / `.bar-bottom` 现在挂在这一层下面 ——
    #    **别把它们关进 `.card` 里**（`position:sticky` 只在自己父块范围内贴底，关进去就提前不贴了）。
    # ⭐ 2026-09-21 加的 `no-store`（她提：「模拟短信重置之后不是从头开始」）：
    #    这些页面**全是她私人的内容**（好感度是她的、聊天进度也是她的），而且同一串 URL
    #    会 render 出不同的东西（`/messages/13` 和 `?p=0,1` 是同一页的两个进度）——
    #    一旦被浏览器（尤其手机里的微信 / QQ 内置浏览器）缓存，就会**拿中途那一版冒充开头**。
    #    ⇒ 一句话：**私人页一律不缓存**。（ PNG/JPG 素材走 `FileResponse`，各有各的缓存头，不受这条影响。）
    side = ('<aside class="side">%s</aside>' % _nav_list()) if nav else ""
    return HTMLResponse("""<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s</style></head><body><div class="wrap">%s<div class="main">%s</div></div>%s</body></html>""" %
                        (title, CSS + css, side, body, script or ""),
                        headers={"Cache-Control": "no-store"})


def _safe_uid(uid):
    """uid 进文件名前先洗一遍（本来就该是纯 QQ 号，但别赌）。"""
    return re.sub(r"[^0-9A-Za-z_-]", "", uid or "")


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


# 🖼 头像的**写入**三件套（2026-09-30 从 `page/avatar.py` 搬到这儿）。
#    ⚠ 搬家的原因：现在**两个页面**都要传头像了 —— `/settings`（老地方）+ `/home/edit/profile`
#      （主页新开的更改页，她 2026-09-30 要的）。原封不动复制一份 = 「按字节认图」这道
#      安全逻辑出现两个副本，早晚只修一份。
#    ⚠ 跟「QQ 端 / 网页端各写各的」那条**不冲突**：那说的是两端输入形态不同；
#      这里是**同一个浏览器表单**被两个页面复用，形态一模一样。
#    ⚠ 路由没搬（`/settings/avatar*` 还在 `page/avatar.py`）—— 搬的只是工具函数。
def sniff_image(raw):
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


def drop_avatar(uid):
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


def save_avatar(uid, raw):
    """
    校验并落盘头像。返回 `""` = 成功；否则返回**给她看的**错误文案（人话，不带后台词）。

    校验一律看**字节**：① 空 => 没选到；② 超 `AVATAR_MAX` => 太大；
    ③ `sniff_image()` 不认 => 不是真图。
    ⚠ 落盘名 = `{洗过的 uid}.{嗅探出来的扩展名}` ⇒ 文件名完全由我们定，路径穿越无从谈起。
    """
    if not raw:
        return "没选到图片，再试一次"
    if len(raw) > AVATAR_MAX:
        return "图太大了，换一张 2 MB 以内的"
    ext = sniff_image(raw)
    if not ext:
        return "只认 png / jpg / webp / gif 这几种图"
    safe = _safe_uid(uid)
    if not safe:
        return "账号信息不对，重新登录一下"
    os.makedirs(AVATAR_DIR, exist_ok=True)
    drop_avatar(uid)                       # 换了格式时别把旧的那张留成垃圾
    with open(os.path.join(AVATAR_DIR, "%s.%s" % (safe, ext)), "wb") as f:
        f.write(raw)
    return ""


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


# ---------------------------------------------------------------- 🧭 导航
# ⭐ 2026-09-29 她定的 IA：**「目录页」是登录之后的落点**（`/menu`），
#   `NAV` 这一张表就是它的全部内容 —— **一份数据、两种形态**：
#     · 手机：一整个页面（`page/menu.py`）
#     · 桌面：左侧栏（纯 CSS，见第 3 步；用的是同一份 HTML）
#   ⇒ 想加 / 改名一个入口，**只改这张表**，别去各页手写链接
#     （原来「底部那条」在 4 个页面里各写一遍，就是没这张表的后果）。
#
# ⚠ `state` 三档（决定渲染成 `<a>` 还是灰 `<span>`）：
#     ""          正常，可点（必须有 href）
#     "reserved"  预留位 —— 灰 + 右侧标「预留」
#     "todo"      还没做 —— 灰 + 右侧标「待填」（现在这一档已经清空了）
#   ⚠ 灰项**一律 href 留空、渲染成 `<span>`** —— 点不动的东西不该是链接。
# ⚠ 她 2026-09-29 定的两条：**「设置」沉到最下**（她原话「设置一般放在最下面哦」）；
#   预留位**要显示出来**，不是藏起来。
NAV_STATE_TAG = {"reserved": "预留", "todo": "待填"}

NAV = [
    # ⭐ 2026-09-30 点亮：主页（她自己的那一页）真的有了，见 `page/home.py`。
    {"label": "主页",       "href": "/home",     "state": ""},
    {"label": "跟他说话",   "href": "/chat",     "state": ""},
    {"label": "牵绊短信",   "href": "/messages", "state": ""},
    {"label": "好感度后台", "href": "/affinity", "state": ""},
    {"label": "小游戏",     "href": "",          "state": "reserved"},
    {"label": "未来信件",   "href": "",          "state": "reserved"},
    # ⭐ 2026-09-30 点亮：日记（`page/diary.py`）—— 他每几轮写下的那段话。
    #    她定的：**「纪念日」这一格改成「日记」**（原先是没名字的预留位）。
    #    ⚠ 位置不动：它本来就在「未来信件」和「美化主题」中间，改的只有名字和 href。
    {"label": "日记",       "href": "/diary",    "state": ""},
    # ⭐ 2026-09-30 她按截图定的：**「美化主题」就占这一格**（原先是没名字的「预留」）。
    #    仍留 `reserved` —— 功能页还没做，**点不动的东西不该是链接**（等做了再把 href 填上）。
    #    ⚠ 所以「自定义美化」那批的入口**在目录页这儿，不在 `/settings`**。
    {"label": "美化主题",   "href": "",          "state": "reserved"},
    {"label": "预留",       "href": "",          "state": "reserved"},
]

# 沉底那一组（`_nav_list()` 会在它上面插一道 `.navgap` 分隔）
NAV_FOOT = [
    {"label": "设置", "href": "/settings"},
    {"label": "退出", "href": "/logout"},
]

MENU_PATH = "/menu"


def _nav_list(active=""):
    """
    🧭 把 `NAV` 渲染成一列入口。**目录页整页 / 桌面左栏共用这一份 HTML。**

    ⚠ 桌面左栏（第 3 步）= 同一份 HTML + 一段 `@media` CSS ⇒ 两端永远同步。
    `active` 传当前页的 href（如 `"/chat"`）⇒ 那一项高亮。
    """
    out = []
    for it in NAV:
        label = _esc(it["label"])
        tag = NAV_STATE_TAG.get(it["state"], "")
        tail = '<span class="hint">%s</span>' % tag if tag else ""
        if it["href"]:
            cls = "on" if it["href"] == active else ""
            out.append('<a href="%s"%s><span>%s</span>%s</a>'
                       % (it["href"], (' class="%s"' % cls) if cls else "", label, tail))
        else:
            out.append('<span class="off"><span>%s</span>%s</span>' % (label, tail))
    out.append('<div class="navgap"></div>')
    for it in NAV_FOOT:
        out.append('<a href="%s"><span>%s</span></a>' % (it["href"], _esc(it["label"])))
    return '<nav class="nav">%s</nav>' % "".join(out)


def _backbar():
    """
    功能页底部那条「回目录」（她 2026-09-29 定的：**只留这一个**，
    设置 / 退出在目录页里已经有了，手机上少一项少干扰）。

    ⚠ 必须是 `.wrap` 里那层内容列（`.main`）的直接子元素 —— 跟 `.footnav` 一个道理
      （`position:sticky` 只在自己父块范围内贴底，关进 `.card` 里就提前不贴了）。
    ⚠ `/chat` **不用这条**（它底部是输入栏，叠一条会吃掉 40px 屏幕）—— 她 2026-09-29 定的
      「方案 B」：把回目录**并进输入栏那一行**，见 `page/chat.py` 的 `.bk`。
    ⚠ `/messages/{level}` 详情页也不用这条：它底部是自己的「↺ 从头再聊一遍 · ‹ 返回目录」两条链接。

    ⭐ 类名带 `backonly`（2026-09-29）：桌面版按它把**整条**藏掉（左栏常驻就重复了），
      见 `CSS` 末尾那条 `@media`。**只有「纯粹就是一条回目录」的底栏才配用这个类** ——
      底栏里但凡还有别的动作（比如「↺ 从头再聊一遍」），桌面就不能整条藏。
    """
    return '<p class="footnav backonly"><a href="%s" class="hint">‹ 返回目录</a></p>' % MENU_PATH


def _two_way_footer(href, label):
    """
    底部那一条**带两个动作**的导航 —— `‹ 回XX · ‹ 返回目录`。

    ⭐ 2026-09-30 从 `page/home.py` **下沉到这里**（她要求「日记功能页的底栏也一样」）：
      原来只有 `/home/edit/*` 家族在用。日记的「写一条 / 改一条」也要同款
      ⇒ 不回主页，而是回 `/diary`；再放一个网页页里就会变成
      `page/diary.py` **反向 import `page.home`**，方向是脏的 ⇒ 下沉到共用底座。

    ⚠ **不能用 `_backbar()`**：那个只渲染一个动作，而且带 `backonly` 类 ——
      类名一挂上，桌面版就把**整条**藏掉（见上面 `_backbar()` 的说明）。
      底栏里但凡有第二个动作就不能带它，**这是底座里写死的规矩**。
    ⚠ 必须是内容列 `.main` 的**直接子元素**，`position:sticky` 才贴得住
      （跟 `_backbar()` / `.footnav` 同一个道理）。

    ⭐ 拿 2026-09-30 她提的：「两个位置不要靠太近，容易误按」⇒ **左右各占一半**
      （`.twobar`），点击区是整半条，中间空 12px。原来那种「A · B」中间只隔一个点，
      手指一点就戳错。
    """
    return ('<p class="footnav"><span class="twobar">'
            '<a href="%s" class="hint">%s</a>'
            '<a href="%s" class="hint">‹ 返回目录</a>'
            '</span></p>' % (href, label, MENU_PATH))


def _who_block(name, sub, avatar_html):
    """👤 头像 + 名字 + 一行小字。目录页用；第 3 步的桌面左栏也复用它（同一份 HTML）。"""
    return ('<div class="who"><div>%s</div>'
            '<div><p class="who-n">%s</p><p class="who-s">%s</p></div></div>'
            % (avatar_html, _esc(name or "你"), _esc(sub or "")))
