# -*- coding: utf-8 -*-
"""
祁煜的「QQ 空间说说」能力（2026-09-19 新增）。

本文件只放**纯函数**：接口名、可见范围枚举、拼请求体。
真正的发送在 `Rafayel_bot.py` —— 因为 websocket 是接线层的东西，
放在这里会让引擎层反向依赖入口层（跟当初拆 config 层是同一个理由）。

⚠ 发说说的调用方式和发私聊**完全一样**：
    沿着 NapCat 反连进来的那条 WS 回发 {"action": …, "params": …}
  不用开 NapCat 的 HTTP 服务端，不用加端口，也不用重启 NapCat。

⚠ 本文件目前有**两条通路**在用：
  ① 手动测试指令（bot 里的 `#发说说`）—— S1 阶段留下的验证口子，仍在；
  ② **S2 自动化**（`Rafayel_qzone_auto.py` 选条 + 排期，`Rafayel_bot.py::auto_qzone_scan()` 发送）。
  本文件自己**不碰排期、不碰选条**，只提供接口名 + 拼请求体。
"""

# ============================================================
#  OneBot 扩展接口
# ============================================================

QZONE_SEND_ACTION = "send_qzone_msg"        # 发表说说（NapCat ≥ v4.18.14 / 2026-08-04）
QZONE_DELETE_ACTION = "delete_qzone_msg"    # 删除说说（备用，本批未用）


# ============================================================
#  ugc_right —— 说说可见范围
# ============================================================
# 枚举对齐自第三方实现（php-qzone 的 updateRight），NapCat 沿用同一套。
# ⚠ 16 / 128 这两种必须同时给 target_uins，否则底层拼不出权限字符串。

UGC_ALL = 1         # 所有人可见
UGC_FRIENDS = 4     # 好友可见
UGC_PARTIAL = 16    # 部分好友可见 —— 需要 target_uins（做「只她可见」就用这个）
UGC_SELF = 64       # 仅自己可见
UGC_EXCLUDE = 128   # 部分好友不可见 —— 需要 target_uins

UGC_NEEDS_TARGET = (UGC_PARTIAL, UGC_EXCLUDE)


# ⚠⚠ 真机实测结论（2026-09-19 深夜，retcode=1200）：**裸的本地路径 NapCat 不认** ——
#   它把 `file` 的值当 **URL** 解析，`/home/.../倒影.png` 直接「图片处理失败，识别URL失败」，
#   而且**整条说说发不出去**（不只是丢图）。后果比原先猜的「静默丢图」重。
#   ⇒ 现在统一把本地路径转成 `file://` URI（见 build_payload 里的 `_img_uri`）。
#   ⏳ `file://` + 字符串数组若还不行，按这个梯子往下试：
#      ① 翻成本开关 True（对象数组） ② 再不行改 `base64://`（单张最大 3.5MB，base64 后
#      ~4.7MB，仍远小于 WS 单帧 16MB 上限，只是费带宽）。
QZONE_IMAGES_AS_OBJECTS = False
# ⚠⚠ 真机实测结论（2026-09-20 凌晨，第二层）：加了 file:// 后 NapCat 仍失败，
#   这回是 `ENOENT: no such file or directory, open '/home/.../涂鸦叽/05-得意.gif'`
#   —— 而机器人侧 `os.path.isfile` 明明过了（选条时只返回真存在的图）。
#   ⇒ **NapCat 看不见这台机器上的这个目录**（多半跑在 Docker 里、没挂载 ai-love）。
#   ⇒ 文件系统这条路彻底堵死 ⇒ 改走 `base64://`：把图片字节直接塞进请求，
#     不依赖任何挂载。体积核算：说说配图最大 3.5MB ⇒ base64 ~4.7MB，
#     远低于 WS 单帧 ~16MB 上限（表情包 gif 更小）。
QZONE_IMAGES_AS_BASE64 = True


def _img_uri(p):
    """
    把图片引用规范成 NapCat 认的形态（真机梯子走完了：裸路径 ✗ → file:// ✗ → base64 ✓待验）。

    - 已经带 scheme（http:// https:// file:// base64://）⇒ 原样返回
    - `QZONE_IMAGES_AS_BASE64` ⇒ 读文件字节，`base64://<b64>`（**不依赖文件系统**，
      NapCat 在 Docker 里没挂载目录也能收）
    - 否则 `file://` 前缀（留作回退：哪天把目录挂进 NapCat 容器了再翻回 False）
    """
    p = str(p)
    if "://" in p:
        return p
    if QZONE_IMAGES_AS_BASE64:
        import base64
        with open(p, "rb") as f:
            return "base64://" + base64.b64encode(f.read()).decode("ascii")
    return "file://" + p


def build_payload(content, ugc_right=UGC_ALL, target_uins=None, images=None, echo=None):
    """
    拼一条 send_qzone_msg 请求体（**只拼，不发**）。

    content     : 说说正文
    ugc_right   : 见上面的枚举
    target_uins : 可见 / 不可见名单（QQ 号）。ugc_right 为 16 或 128 时**必填**，
                  其它取值下即使传了也不生效（NapCat 会忽略）。
    images      : 配图。传**本地绝对路径**的列表 —— 别传 base64
                  （WS 单帧 ~16 MB，base64 还有 ~33% 膨胀；本项目配图最大一张 3.5 MB）。
                  留空 = 纯文字说说。
    echo        : 回执标识。**发说说务必带上** —— 否则业务层失败（retcode != 0）
                  时收不到任何信号，会误以为发成功了。

    返回可直接 json.dumps 后沿 WS 发出的 dict。
    """
    if not content or not str(content).strip():
        raise ValueError("说说正文不能为空")

    right = int(ugc_right)
    params = {"content": str(content).strip(), "ugc_right": right}

    if right in UGC_NEEDS_TARGET:
        uins = [int(u) for u in (target_uins or [])]
        if not uins:
            raise ValueError("ugc_right=%d 时必须提供 target_uins" % right)
        params["target_uins"] = uins

    imgs = [str(i) for i in (images or []) if i]
    if imgs:
        # ⚠ 真机实测（2026-09-19）：裸路径 = retcode 1200「识别URL失败」⇒ 一律先转 file:// URI
        uris = [_img_uri(i) for i in imgs]
        # 形状由 QZONE_IMAGES_AS_OBJECTS 决定（若 file:// + 字符串还不行再翻 True）
        params["images"] = ([{"file": u} for u in uris]
                            if QZONE_IMAGES_AS_OBJECTS else uris)

    payload = {"action": QZONE_SEND_ACTION, "params": params}
    if echo:
        payload["echo"] = echo
    return payload
