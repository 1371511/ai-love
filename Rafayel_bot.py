import asyncio
import glob
import json
import os
import sys

import websockets

# ========== 导入祁煜（Rafayel）的对话引擎 ==========
# 2026-09-17 搬家：引擎代码都在 ai-Rafayel\ 子目录，而入口 bot 仍留在项目根 ——
# 所以先把代码目录挂进 sys.path，下面那句 from Rafayel_chat import … 才找得到。
_CODE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai-Rafayel")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from Rafayel_chat import get_reply, record_proactive, take_opening
from Rafayel_config import (
    AUTO_GREET, AUTO_GREET_IDLE_HOURS, AUTO_GREET_SCAN_SECONDS, MEMORY_DIR,
    QZONE_CMD_PREFIX, QZONE_CMD_UIDS, QZONE_RECEIPT_TIMEOUT, QZONE_TEST_TEXT,
)
from Rafayel_greet import try_greet
from Rafayel_qzone import UGC_ALL, UGC_PARTIAL, build_payload

# ============================================
def your_ai_lover_response(user_message: str, user_id: str) -> str:
    """调用祁煜的对话引擎（Rafayel_chat）"""
    # 直接调用 get_reply，它会自动管理该用户的对话历史和记忆
    return get_reply(user_message, user_id)


async def send_text(websocket, message_type, user_id, group_id, text):
    """按 OneBot v11 协议发一条消息（私聊 / 群聊）"""
    if message_type == "private":
        response = {
            "action": "send_private_msg",
            "params": {"user_id": int(user_id), "message": text}
        }
    elif message_type == "group":
        response = {
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": text}
        }
    else:
        return
    await websocket.send(json.dumps(response))
    print(f"[💬] 已回复: {text[:50]}...")


# ============================================
# 主动发朋友圈（2026-09-19 新增 · 最小验证版）
# ============================================
# 目前只有「手动测试指令」这一条通路 —— 在 QQ 里发：
#     #发说说 内容       → **默认私有**：只发信人可见
#     #发说说公开 内容   → 所有人可见
# 就会真的发一条说说；NapCat 的回执（如果有）会打进服务端日志（[🧾]）。
# ⚠ 排期 / 选条 / LLM 兜底都还没做：先把「这个号到底发不发得出去」验证掉，
#   否则风控一拦，后面全白写。

# 请求回执表 {echo: asyncio.Future}
#   ⚠ 为什么非要有它：send_qzone_msg 会因为风控 / 权限在**业务层**失败
#      （返回 retcode != 0），**不会抛异常** —— 光 await ws.send() 什么都看不出来。
_pending_receipts = {}
_receipt_seq = 0


def _next_echo():
    global _receipt_seq
    _receipt_seq += 1
    return "qzone-%d" % _receipt_seq


def _judge_receipt(receipt):
    """把一条回执翻译成 (ok, info)。"""
    if receipt.get("retcode") == 0:
        tid = (receipt.get("data") or {}).get("tid")
        return True, (str(tid) if tid else "未返回 tid")
    return False, "retcode=%s / %s" % (
        receipt.get("retcode"),
        receipt.get("message") or receipt.get("wording") or "")


async def _watch_receipt(echo, fut):
    """
    ⭐ **在后台任务里**等回执 —— 只能这么做，绝不能再挡在主流程里。

    ⚠⚠ 真根因（2026-09-19 读码定位，**不是 NapCat 的锅**）：
      bot 的接收循环是
          async for message in websocket:
              await process_napcat_message(message, ws)
      —— **处理当前这条消息的期间，循环不会去读下一帧**。
      所以在 `process_napcat_message` 里 `await` 等回执 = 自己锁死自己：
      回执确实回来了，但它排在**后面那帧**里，要等当前这条处理完才会被读到
      ⇒ **必然超时**，跟 NapCat 回不回、跟可见范围都没关系。

      旁证：公开（ugc_right=1）和私有（16）**两次都超时**，而两次说说其实都发出去了。

    所以现在改成 fire-and-forget：送出即返回，回执真回来了就在后台记一条日志。
    """
    try:
        receipt = await asyncio.wait_for(fut, timeout=QZONE_RECEIPT_TIMEOUT)
        ok, info = _judge_receipt(receipt)
        print("[🧾] 说说回执 %s -> %s %s" % (echo, "成功" if ok else "失败", info))
    except asyncio.TimeoutError:
        print("[🧾] 说说回执 %s 没到（该接口可能不回响应；不影响发送）" % echo)
    except Exception as e:
        print("[🧾] 说说回执 %s 处理出错：%s" % (echo, e))
    finally:
        _pending_receipts.pop(echo, None)


async def send_qzone(websocket, content, ugc_right=UGC_ALL, target_uins=None):
    """
    把一条说说**送出去**（不等回执，立即返回）。

    返回 (sent, info)：
        True  → 请求已交给 NapCat（**只代表送出去了**，不代表一定发布成功）
        False → 连送都没送出去（WS 异常）

    ⚠ 为什么不等回执：见 `_watch_receipt` 的注释 —— 在主流程里等**必然超时**。
      实测两次（公开 / 私有）说说**都真的发出去了** ⇒「没回执」≠「失败」。
      回执结果只进日志（[🧾]），**不回传给调用方**，免得二期又把它当成功判据。
    """
    try:
        echo = _next_echo()
        payload = build_payload(content, ugc_right=ugc_right,
                                target_uins=target_uins, echo=echo)
        fut = asyncio.get_running_loop().create_future()
        _pending_receipts[echo] = fut
        await websocket.send(json.dumps(payload))
        asyncio.create_task(_watch_receipt(echo, fut))
        return True, "已送出"
    except Exception as e:
        return False, "发送异常：%s" % e


async def maybe_handle_qzone_cmd(websocket, message_type, user_id, group_id, raw_message):
    """
    朋友圈测试指令。命中并处理了返回 True，否则 False（交回给正常对话流程）。

        #发说说 内容       → **默认私有**：仅发信人可见（ugc_right=16 + target_uins=[发信人]）
        #发说说公开 内容   → 所有人可见（ugc_right=1）
        #发说说私 内容     → 「私」保留为别名，等价于默认

    ⚠ 2026-09-19 把**默认翻了**：原来默认公开、要私有得写「私」。
      她实测踩到坑（A 发指令、B 也看到）⇒ 二期方案本来就是「每人一条私有」，
      默认就不该是公开。想公开发就明写「公开」，不靠记一个字。
    """
    text = (raw_message or "").strip()
    if not QZONE_CMD_PREFIX or not text.startswith(QZONE_CMD_PREFIX):
        return False
    if QZONE_CMD_UIDS and user_id not in [str(u) for u in QZONE_CMD_UIDS]:
        print("[⚠️] 朋友圈指令被忽略（%s 不在白名单）" % user_id)
        return False

    rest = text[len(QZONE_CMD_PREFIX):].strip()
    public = rest.startswith("公开")
    if public:
        rest = rest[2:].strip()
    elif rest.startswith("私"):
        # 兼容旧写法：#发说说私 xxx —— 现在默认就是私有，这个「私」只是别名
        rest = rest[1:].strip()

    content = rest or QZONE_TEST_TEXT
    ugc = UGC_ALL if public else UGC_PARTIAL
    targets = None if public else [user_id]

    print("[🧪] 朋友圈测试：ugc_right=%s targets=%s / 正文 %r"
          % (ugc, targets, content[:40]))
    sent, note = await send_qzone(websocket, content,
                                  ugc_right=ugc, target_uins=targets)

    # ⚠ 2026-09-19：改成「送出即回」，不再干等 20 秒回执
    #   （在主流程里等**必然超时**，原因见 _watch_receipt 的注释）。
    if sent:
        tip = "✅ 说说已送出 —— 去空间看看；回执（如果有）我打在服务端日志里"
    else:
        tip = "❌ 没送出去：%s" % note
    await send_text(websocket, message_type, user_id, group_id, tip)
    print("[🧪] " + tip)
    return True

# ============================================
# WebSocket 服务端（连接 NapCat）
# ============================================

# 存储所有连接的 WebSocket 客户端（用于发送消息）
connected_clients = set()

async def handle_message(websocket):
    """处理来自 NapCat 的 WebSocket 连接"""
    print(f"[✅] NapCat 已连接: {websocket.remote_address}")
    connected_clients.add(websocket)

    try:
        async for message in websocket:
            # 解析 NapCat 转发的消息
            try:
                data = json.loads(message)
                await process_napcat_message(data, websocket)
            except json.JSONDecodeError:
                print(f"[⚠️] 收到非 JSON 数据: {message}")
    except websockets.exceptions.ConnectionClosed:
        print(f"[❌] NapCat 连接断开: {websocket.remote_address}")
    finally:
        connected_clients.discard(websocket)

async def process_napcat_message(data, websocket):
    """处理 NapCat 转发的消息"""
    # 2026-09-19：API 回执 —— 没有 post_type，只带 echo / retcode。
    #   以前这里会被静默丢弃（下面的判断只认 post_type == "message"），
    #   所以「发说说到底成没成功」永远查不到。现在按 echo 找回来。
    # ⚠ 2026-09-19 加的探针：把**任何没有 post_type 的帧**原样打出来。
    #   实测教训：#发说说 会报「等回执超时」，可那句说说**其实发成功了**（她在空间能看到）
    #   ⇒ 先搞清楚 NapCat 到底回没回、回的是什么形状，再决定二期敢不敢依赖回执。
    if "post_type" not in data:
        echo = data.get("echo")
        fut = _pending_receipts.get(echo) if echo is not None else None
        if fut is not None and not fut.done():
            fut.set_result(data)
        else:
            print("[🔎] 无 post_type 的帧（echo=%r）：%s"
                  % (echo, json.dumps(data, ensure_ascii=False)[:300]))
        return

    # 只处理消息事件（私聊和群聊）
    if data.get("post_type") == "message":
        message_type = data.get("message_type")  # "private" 或 "group"
        user_id = str(data.get("user_id"))
        raw_message = data.get("raw_message", "")

        # 群聊消息可以加上群号
        group_id = data.get("group_id") if message_type == "group" else None

        print(f"[📩] 收到 {message_type} 消息: {raw_message} (来自: {user_id})")

        # 2026-09-19：朋友圈测试指令（#发说说 …）—— 命中就直接处理掉，不进对话。
        if await maybe_handle_qzone_cmd(websocket, message_type, user_id,
                                        group_id, raw_message):
            return

        # 2026-09-18：新用户第一次说话时，先主动打一声招呼再回答。
        #   旧版 QQ 端完全没有开场白 —— 用户第一条消息进来就直接进入问答，
        #   明明卡里写了 5 条 alternate_greetings 却只在 CLI 里用过。
        #   take_opening 只在「确实是第一次」时返回文本，老朋友返回空串。
        opening = take_opening(user_id)
        if opening:
            await send_text(websocket, message_type, user_id, group_id, opening)

        # 调用你的 AI 恋人逻辑
        reply = your_ai_lover_response(raw_message, user_id)

        # 构造回复消息（符合 OneBot v11 协议）
        if reply:
            await send_text(websocket, message_type, user_id, group_id, reply)

# ============================================
# 主动打招呼（他忍不住先开口）
# ============================================

async def auto_greet_scan():
    """
    扫一遍私聊过的用户：谁冷场够久了，就让他主动说一句。

    最后活跃时间直接取 memory\{uid}.json 里的 saved_at（save_memory 每次都会写），
    不额外改记忆结构。uid 必须纯数字（QQ 号），免得给 "cli" 这种测试号发消息。
    """
    if not AUTO_GREET or not connected_clients:
        return
    ws = next(iter(connected_clients))

    for path in glob.glob(os.path.join(MEMORY_DIR, "*.json")):
        uid = os.path.splitext(os.path.basename(path))[0]
        if uid.endswith("_profile") or uid.endswith("_greet"):
            continue
        if not uid.isdigit():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        text = try_greet(uid, data.get("saved_at", ""))
        if not text:
            continue

        await send_text(ws, "private", uid, None, text)
        # ⚠ 发出去之后立刻写进对话历史 —— 否则她回话时模型不知道上一句是他说的，
        #   会出现「接不住」的回复（她 2026-09-18 实测反馈）。
        #   放在 send 之后：发送失败（抛异常）就不留下"他说过"的假记录。
        record_proactive(uid, text)
        print("[📣] 主动打招呼 -> %s" % uid)


async def auto_greet_loop():
    while True:
        await asyncio.sleep(AUTO_GREET_SCAN_SECONDS)
        try:
            await auto_greet_scan()
        except Exception as e:
            print("[⚠️] 主动打招呼扫描出错：%s" % e)


async def main():
    """启动 WebSocket 服务器"""
    print("=" * 50)
    print("🤖 AI 恋人 Bot 启动中...")
    print("📡 监听地址: ws://127.0.0.1:8080/onebot/v11/ws")
    print("💡 请确保 NapCat 已配置反向 WebSocket 指向此地址")
    print("=" * 50)

    # 启动 WebSocket 服务
    async with websockets.serve(handle_message, "0.0.0.0", 8080):
        print("📡 监听地址: ws://0.0.0.0:8080/onebot/v11/ws")
        if AUTO_GREET:
            asyncio.create_task(auto_greet_loop())
            print("📣 主动打招呼已开启（冷场 %s 小时后他会先开口）" % AUTO_GREET_IDLE_HOURS)
        if QZONE_CMD_PREFIX:
            print("🧪 朋友圈测试指令已开启：「%s 内容」=只你可见；「%s公开 内容」=所有人可见"
                  % (QZONE_CMD_PREFIX, QZONE_CMD_PREFIX))
        await asyncio.Future()  # 永久运行

if __name__ == "__main__":
    asyncio.run(main())
