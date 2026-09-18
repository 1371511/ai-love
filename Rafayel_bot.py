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
#     #发说说 内容       → 所有人可见
#     #发说说私 内容     → 只发信人可见
# 就会真的发一条说说，并把 NapCat 的 retcode 回给你。
# ⚠ 排期 / 选条 / LLM 兜底都还没做：先把「这个号到底发不发得出去」验证掉，
#   否则风控一拦，后面全白写。

# 请求回执表 {echo: asyncio.Future}
#   ⚠ 为什么非要有它：send_qzone_msg 会因为风控 / 权限在**业务层**失败
#      （返回 retcode != 0），**不会抛异常** —— 光 await ws.send() 什么都不知道。
_pending_receipts = {}
_receipt_seq = 0


def _next_echo():
    global _receipt_seq
    _receipt_seq += 1
    return "qzone-%d" % _receipt_seq


async def send_qzone(websocket, content, ugc_right=UGC_ALL, target_uins=None,
                     timeout=None):
    """
    发一条说说，并**等 NapCat 的回执**。

    返回 (ok, info)：ok=True 时 info 是说说 tid；ok=False 时 info 是失败原因。
    """
    if timeout is None:
        timeout = QZONE_RECEIPT_TIMEOUT

    echo = _next_echo()
    payload = build_payload(content, ugc_right=ugc_right,
                            target_uins=target_uins, echo=echo)

    fut = asyncio.get_running_loop().create_future()
    _pending_receipts[echo] = fut
    try:
        await websocket.send(json.dumps(payload))
        try:
            receipt = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return False, "等回执超时（%ss）—— 该版本 NapCat 可能没有这个接口" % timeout
    finally:
        _pending_receipts.pop(echo, None)

    if receipt.get("retcode") == 0:
        tid = (receipt.get("data") or {}).get("tid")
        return True, (str(tid) if tid else "未返回 tid")
    return False, "retcode=%s / %s" % (
        receipt.get("retcode"),
        receipt.get("message") or receipt.get("wording") or "")


async def maybe_handle_qzone_cmd(websocket, message_type, user_id, group_id, raw_message):
    """
    朋友圈测试指令。命中并处理了返回 True，否则 False（交回给正常对话流程）。
    """
    text = (raw_message or "").strip()
    if not QZONE_CMD_PREFIX or not text.startswith(QZONE_CMD_PREFIX):
        return False
    if QZONE_CMD_UIDS and user_id not in [str(u) for u in QZONE_CMD_UIDS]:
        print("[⚠️] 朋友圈指令被忽略（%s 不在白名单）" % user_id)
        return False

    rest = text[len(QZONE_CMD_PREFIX):].strip()
    only_sender = rest.startswith("私")
    if only_sender:
        rest = rest[1:].strip()

    content = rest or QZONE_TEST_TEXT
    ugc = UGC_PARTIAL if only_sender else UGC_ALL
    targets = [user_id] if only_sender else None

    print("[🧪] 朋友圈测试：ugc_right=%s / 正文 %r" % (ugc, content[:40]))
    ok, info = await send_qzone(websocket, content,
                                ugc_right=ugc, target_uins=targets)

    tip = ("✅ 说说已发出（tid=%s）" % info) if ok else ("❌ 说说发送失败：%s" % info)
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
    echo = data.get("echo")
    if echo is not None and "post_type" not in data:
        fut = _pending_receipts.get(echo)
        if fut is not None and not fut.done():
            fut.set_result(data)
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
            print("🧪 朋友圈测试指令已开启：私聊发「%s 内容」" % QZONE_CMD_PREFIX)
        await asyncio.Future()  # 永久运行

if __name__ == "__main__":
    asyncio.run(main())
