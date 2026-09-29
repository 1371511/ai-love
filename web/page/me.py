# -*- coding: utf-8 -*-
"""
🚚 `/me` —— **老地址的跳转壳**（2026-09-29 起）。

这一页原来的内容（好感度 / 轮数 / 额度 / 画像标签 / 他说过的那句话 / 牵绊短信入口）
**整块搬去了 `page/affinity.py`**，路由也从 `/me` 改成 `/affinity`。
本文件只剩一条跳转，专接旧地址：
  · 她手机上存的书签 / 桌面图标
  · 没跟上的老链接（2026-09-29 起页内链接已**全部改指 `/menu` 与 `/affinity`**，
    这一条只保旧地址不 404）
  ⇒ 照旧能落到新页面。

⚠ 别把内容搬回来 —— 这一页已经空了，内容在 `page/affinity.py`。
⚠ 「主页」**不是**这个文件：它将来是自己的页面（`/home`），入口现在先灰着。
⚠ 未登录时仍然送回 `/`（跟搬之前一模一样），别简化成无条件跳 `/affinity`。
"""
from fastapi import Request
from fastapi.responses import RedirectResponse

from base import app, _current_uid


@app.get("/me")
async def me_legacy(request: Request):
    """老地址 → 新地址（`/affinity`）；未登录照旧回登录页。"""
    if not _current_uid(request):
        return RedirectResponse("/")
    return RedirectResponse("/affinity")
