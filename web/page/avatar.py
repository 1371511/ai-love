# -*- coding: utf-8 -*-
"""
🖼 头像 与 项目素材。

路由：
  POST `/settings/avatar`         上传头像（普通 multipart 表单，**零 JS**）
  POST `/settings/avatar/remove`  移除头像
  GET  `/avatar`                  看**自己**的头像
  GET  `/asset/sticker`           表情包真图（涂鸦叽，`?t=标签`）
  GET  `/asset/{name}`            项目自带的静态素材（现在只有一张：**祁煜的头像**）

⚠⚠ `/asset/sticker` **必须写在 `/asset/{name}` 前面** —— 参数路由会吃掉它。

⭐ 全站**零 JS** 的老规矩：上传就是一个普通的 `<form enctype="multipart/form-data">`，
   浏览器自己就会发 multipart，不需要一行脚本。
⚠ 只认**自己登录的那个人的**头像：uid 一律来自签名 cookie，绝不从表单里取。
⚠ 只动 `web/avatars/`（**在 .gitignore 里**）—— bot 的 memory 仍然只读，一个字不写。

🗂 读头像的小工具（`_avatar_file` / `_avatar_url`）在 `base.py`：
   它们被「我的页 / 设置 / 对话窗口」三处共用，不属于这一条路由。
⭐ 2026-09-30：**写头像**那三件套（`sniff_image` / `drop_avatar` / `save_avatar`）也搬去了
   `base.py` —— 主页新开的 `/home/edit/profile` 要用同一份「按字节认图」的逻辑。
   ⚠ 本文件的路由名**没变**（还是 `/settings/avatar*`），设置页照旧能用。
"""
import os

from fastapi import File, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from base import (
    app, _current_uid, _avatar_file, AVATAR_MAX, BASE,
    # 🖼 写入三件套 2026-09-30 搬去了 `base.py`（`/home/edit/profile` 也要用同一份）
    save_avatar, drop_avatar,
)
# 🎨 表情包：标签 ⇒ 磁盘路径。经引擎门面拿，本页不解析 `card/stickers.md`。
#    ⚠ 这是**只读**调用（只读 md + 只 stat 文件），不写盘 ⇒ 不破 ADR-22。
from Rafayel_chat import pick_sticker

# 🖼 祁煜**自己的**头像（2026-09-21 她给的那张蓝海油画 —— 用在短信详情页的聊天气泡上）
#    ⚠ 跟 `web/avatars/` **正相反**：这是**项目素材**、**要进仓库**
#      （所以别往 `.gitignore` 里加 `web/assets/`）。
#    ⚠ 走**白名单**：URL 里只出现 key（`/asset/qiyu`），真文件名与目录在代码里写死 ⇒ 无路径穿越。
ASSET_DIR = os.path.join(BASE, "web", "assets")
ASSET_FILES = {"qiyu": "qiyu.jpg"}


@app.post("/settings/avatar")
async def avatar_upload(request: Request, pic: UploadFile = File(None)):
    """
    上传头像。**零 JS**：就是一个普通 multipart 表单，提交完 303 回设置页。

    ⚠ 校验 / 落盘全在 `base.save_avatar()` 里（2026-09-30 搬过去的）——
      本路由只管「收字节 + 回哪一页」。那儿不放心的话去看那一份，别在这儿再写一遍。
    ⚠ 只读 `AVATAR_MAX + 1` 字节 ⇒ 超大文件不会先把内存吃掉。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    raw = b""
    if pic is not None:
        try:
            raw = await pic.read(AVATAR_MAX + 1)
        except Exception:
            raw = b""
    err = save_avatar(uid, raw)
    if err:
        return RedirectResponse("/settings?err=" + err, status_code=303)
    return RedirectResponse("/settings?ok=avatar", status_code=303)


@app.post("/settings/avatar/remove")
async def avatar_remove(request: Request):
    """删掉自己的头像（还是普通表单 POST，没有一行 JS）。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    drop_avatar(uid)
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


@app.get("/asset/sticker")
async def asset_sticker(request: Request, t: str = ""):
    """
    表情包真图（涂鸦叽）。`t` = 标签（`得意` / `撒娇` 这种，别名也认）。

    ⭐ 2026-10-01 加的：原先网页端只画个灰圆片「表情 · 得意」，看不出是哪张。
      标签→文件的映射**不在这儿解析** —— 调引擎的门面 `Rafayel_chat.pick_sticker()`，
      跟 QQ 端共用同一份 `card/stickers.md` 真相源、同一套别名表、
      同一个「缺图就当没这张」的兜底。**别在本文件里再写一遍解析。**

    ⚠⚠ **本路由必须注册在下面 `/asset/{name}` 之前** —— 否则 `/asset/sticker`
      会先被那条**参数路由**吃掉（`name="sticker"`），查表查不到 ⇒ 302 回首页，
      表现是「图全裂」。这是本仓库的老坑（「具体路由注册在参数路由前」）。
    ⚠ 路径穿越进不来：`t` 只作为**字典的 key** 去查表，真路径由 `pick_sticker`
      从 `card/stickers/` 拼出来；本路由自己**不拼任何路径**。
    ⚠ 拿不到（标签不认识 / 那张图不在盘上 / 表情功能被 `STICKER_ENABLE` 关了）
      ⇒ 302 回 `/`，客服端就是「裂图」而不是几百 KB 的错误页。
      ⚠ 不返回 404：这一页的所有其它失败路径都是 302，保持一致。
    ⚠ 缓存头跟 `/asset/qiyu` 一致（`private, max-age=86400`）：素材是仓库里的图、
      不随用户变，但也不该进公共缓存 ⇒ `private`。gif 有 40KB，缓存下来值得。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    p = pick_sticker(t)
    if not p:
        return RedirectResponse("/")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})


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
