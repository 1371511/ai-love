import asyncio
import json
import websockets

# ========== 导入你的 AI 恋人逻辑 ==========
from Rafayel import get_reply

# ============================================
def your_ai_lover_response(user_message: str, user_id: str) -> str:
    """调用你的 AI 恋人逻辑"""
    # 直接调用 get_reply，它会自动管理该用户的对话历史和记忆
    return get_reply(user_message, user_id)

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
    # 只处理消息事件（私聊和群聊）
    if data.get("post_type") == "message":
        message_type = data.get("message_type")  # "private" 或 "group"
        user_id = str(data.get("user_id"))
        raw_message = data.get("raw_message", "")

        # 群聊消息可以加上群号
        group_id = data.get("group_id") if message_type == "group" else None

        print(f"[📩] 收到 {message_type} 消息: {raw_message} (来自: {user_id})")

        # 调用你的 AI 恋人逻辑
        reply = your_ai_lover_response(raw_message, user_id)

        # 构造回复消息（符合 OneBot v11 协议）
        if reply:
            if message_type == "private":
                # 私聊回复
                response = {
                    "action": "send_private_msg",
                    "params": {
                        "user_id": int(user_id),
                        "message": reply
                    }
                }
            elif message_type == "group":
                # 群聊回复
                response = {
                    "action": "send_group_msg",
                    "params": {
                        "group_id": group_id,
                        "message": reply
                    }
                }
            else:
                return

            # 发送回复到 NapCat
            await websocket.send(json.dumps(response))
            print(f"[💬] 已回复: {reply[:50]}...")

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
        await asyncio.Future()  # 永久运行

if __name__ == "__main__":
    asyncio.run(main())
