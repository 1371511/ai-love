# -*- coding: utf-8 -*-
"""
📄 网页端各页面（2026-09-29 从原来 1539 行的 `web/app.py` 拆出来）

口径是她定的：**负责一个功能就一个文件** —— 一页一个模块，
哪怕它在界面上没有独立的入口（比如头像的几条路由，入口都在 `/settings` 里）。

⭐ 每个模块的套路都一样：
    from base import app, _page, _current_uid, …      # 底座（**必须第一个** import）
    from Rafayel_xxx import …                         # 引擎（base 已把 ai-Rafayel 塞进 sys.path）
    @app.get(...) / @app.post(...)                    # 直接往共用的那个 app 上挂

⚠ 这些模块**只负责提供路由**，不会被别的页面 import 去调用 ——
   跨页共用的小工具（`_page` / `_esc` / `_rich` / `_safe_uid` / `_avatar_url` …）都在 `base.py`。
   ⇒ 所以页面之间**没有互相 import**，路由注册顺序只由 `web/app.py` 的 import 顺序决定。

⚠⚠ 拆这一刀**只搬家不改逻辑**：函数体、注释、文案、状态码、cookie 名字，一个字都没动
   （验证方式见当时那次拆分的对照脚本：路由表 + 每条路径的响应指纹必须与拆分前完全一致）。
"""
