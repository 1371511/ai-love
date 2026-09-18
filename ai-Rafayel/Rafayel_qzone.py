# -*- coding: utf-8 -*-
"""
祁煜的「QQ 空间说说」能力（2026-09-19 新增）。

本文件只放**纯函数**：接口名、可见范围枚举、拼请求体。
真正的发送在 `Rafayel_bot.py` —— 因为 websocket 是接线层的东西，
放在这里会让引擎层反向依赖入口层（跟当初拆 config 层是同一个理由）。

⚠ 发说说的调用方式和发私聊**完全一样**：
    沿着 NapCat 反连进来的那条 WS 回发 {"action": …, "params": …}
  不用开 NapCat 的 HTTP 服务端，不用加端口，也不用重启 NapCat。

⚠ 本文件目前**只有「手动测试」这一条通路在用**（见 bot 里的 #发说说 指令）。
  排期、选条、LLM 兜底都还没做 —— 先验证「这个号到底发不发得出去」。
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


def build_payload(content, ugc_right=UGC_ALL, target_uins=None, echo=None):
    """
    拼一条 send_qzone_msg 请求体（**只拼，不发**）。

    content     : 说说正文
    ugc_right   : 见上面的枚举
    target_uins : 可见 / 不可见名单（QQ 号）。ugc_right 为 16 或 128 时**必填**，
                  其它取值下即使传了也不生效（NapCat 会忽略）。
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

    payload = {"action": QZONE_SEND_ACTION, "params": params}
    if echo:
        payload["echo"] = echo
    return payload
