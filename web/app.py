# -*- coding: utf-8 -*-
"""
🚀 网页端入口 —— 只干两件事：**装配**（import 各页）+ **启动**（uvicorn）。

（2026-09-29 从原来 1539 行的单文件 `app.py` 拆出来。口径是她定的：
  **负责一个功能就一个文件**；页面文件统一放在 `web/page/` 下面。）

⭐ 三条铁律（只读 memory / 好感度不进 QQ 对话 / 登录后只能看自己）现在写在 `base.py` 顶上。
🗂 想找某一页的代码：
     web/base.py           底座（**无路由**）：路径 / 密钥 / 用户 / 登录态 / 页面外壳 / 转义工具
     web/page/login.py      `/`（首页=登录页）· `/login` · `/logout`
     web/page/menu.py       `/menu` **目录页**（登录后的落点；入口表在 `base.NAV`）
     web/page/home.py       `/home` 主页（个人信息 + 他记住的你 + 他记住的事）
     web/page/diary.py      `/diary` 日记（他每几轮写下的那段话，按天分组；能增删改）
     web/page/me.py         `/me`（**老地址跳转壳** → `/affinity`）
     web/page/affinity.py   `/affinity` 好感度后台（原「我的页」的内容整块搬来的）
     web/page/messages.py   `/messages` 家族
     web/page/settings.py   `/settings` 家族
     web/page/avatar.py     `/avatar` · `/asset/{name}` · `/settings/avatar*`
     web/page/chat.py       `/chat` · `/chat/send`
     web/page/call.py       `/call` 家族（📹 视频通话，2026-10-02 起分批落地）

⚠ 别在这个文件里写路由 —— 加功能请落到 `page/` 下对应的那个文件里。
⚠ 启动方式没变：`cd web && python app.py`（`start-web.sh` 一个字都没改）。
"""

import os

# ⚠⚠ **这几行的顺序 = 路由注册顺序**，跟拆分前那一个文件里的前后位置逐条对齐过
#    （登录 → 目录 → 我的页 → 好感度后台 → 牵绊短信 → 设置 → 头像 → 对话窗口），别随手调。
#    ⭐ 各页面之间**不互相 import**（跨页共用的小工具全在 `base.py`）—— 顺序能保住就靠这条。
#    ⚠ `app = FastAPI()` 在 `base.py`：下面每一行 import 时，各页的
#      `@app.get/@app.post` 就已经往它上面挂好了。
#    ⚠ `me` 现在是**跳转壳**，真正的内容页是紧跟其后的 `affinity`（2026-09-29 搬的）。
from page import login       # noqa: F401  首页 · 登录 / 登出
from page import menu        # noqa: F401  目录页（登录后落这儿）
from page import home        # noqa: F401  主页（她自己的那一页：个人信息 + 他记住的）
from page import diary       # noqa: F401  日记（他每隔几轮写下的那段话，可按天多次）
from page import me          # noqa: F401  老地址跳转壳 → /affinity
from page import affinity    # noqa: F401  好感度后台（原「我的页」的内容）
from page import messages    # noqa: F401  牵绊提升（短信）
from page import settings    # noqa: F401  设置
from page import avatar      # noqa: F401  头像 · 项目素材
from page import chat        # noqa: F401  对话窗口
from page import call        # noqa: F401  视频通话（`/call`）

from base import app         # noqa: F401  ← 装配好的那个 FastAPI 实例，给下面 uvicorn 用


# ⚠⭐ 原样再导出一次 —— `tools/reset_password.py` 是 `import app as WEB` 之后取
#    `WEB.USERS_PATH / WEB.DEFAULT_PWD / WEB._load_users / WEB._known_uids /
#    WEB._hash / WEB._save_users` 这 6 个名字的。拆文件时它们搬去了 `base.py`，
#    在这儿再挂一份 ⇒ **那个工具一个字都不用改**
#    （它文档里「密码哈希绝不各写一份」那条约定，也就这么保住了）。
from base import (           # noqa: E402,F401
    USERS_PATH, DEFAULT_PWD, _hash, _known_uids, _load_users, _save_users,
)


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
