# -*- coding: utf-8 -*-
"""
🖼 头像 与 项目素材。

路由：
  POST `/settings/avatar`         上传头像（普通 multipart 表单，**零 JS**）
  POST `/settings/avatar/remove`  移除头像
  GET  `/avatar`                  看**自己**的头像
  GET  `/asset/{name}`            项目自带的静态素材（现在只有一张：**祁煜的头像**）

⭐ 全站**零 JS** 的老规矩：上传就是一个普通的 `<form enctype="multipart/form-data">`，
   浏览器自己就会发 multipart，不需要一行脚本。
⚠ 只认**自己登录的那个人的**头像：uid 一律来自签名 cookie，绝不从表单里取。
⚠ 只动 `web/avatars/`（**在 .gitignore 里**）—— bot 的 memory 仍然只读，一个字不写。

🗂 读头像的小工具（`_avatar_file` / `_avatar_url`）在 `base.py`：
   它们被「我的页 / 设置 / 对话窗口」三处共用，不属于这一条路由。
"""
import os

from fastapi import File, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from base import (
    app, _current_uid, _safe_uid, _avatar_file, AVATAR_DIR, AVATAR_EXTS, AVATAR_MAX, BASE,
)

# 🖼 祁煜**自己的**头像（2026-09-21 她给的那张蓝海油画 —— 用在短信详情页的聊天气泡上）
#    ⚠ 跟 `web/avatars/` **正相反**：这是**项目素材**、**要进仓库**
#      （所以别往 `.gitignore` 里加 `web/assets/`）。
#    ⚠ 走**白名单**：URL 里只出现 key（`/asset/qiyu`），真文件名与目录在代码里写死 ⇒ 无路径穿越。
ASSET_DIR = os.path.join(BASE, "web", "assets")
ASSET_FILES = {"qiyu": "qiyu.jpg"}


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
