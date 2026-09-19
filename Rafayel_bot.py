import asyncio
import glob
import json
import os
import random
import sys

import websockets

# ========== 导入祁煜（Rafayel）的对话引擎 ==========
# 2026-09-17 搬家：引擎代码都在 ai-Rafayel\ 子目录，而入口 bot 仍留在项目根 ——
# 所以先把代码目录挂进 sys.path，下面那句 from Rafayel_chat import … 才找得到。
_CODE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai-Rafayel")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from Rafayel_chat import get_reply, recent_context, record_proactive, take_opening
from Rafayel_config import (
    AUTO_GREET, AUTO_GREET_IDLE_HOURS, AUTO_GREET_SCAN_SECONDS, MEMORY_DIR,
    QZONE_AUTO, QZONE_AUTO_GAP_DAYS_MAX, QZONE_AUTO_GAP_DAYS_MIN,
    QZONE_AUTO_REMIND_DELAY_MAX, QZONE_AUTO_REMIND_DELAY_MIN, QZONE_AUTO_SCAN_SECONDS,
    QZONE_BDAY, QZONE_BDAY_RAFAYEL,
    QZONE_CMD_FAIL_TEXT, QZONE_CMD_PREFIX, QZONE_CMD_UIDS, QZONE_RECEIPT_TIMEOUT,
    QZONE_TEST_TEXT, STICKER_CMD_PREFIX, STICKER_IMAGE_AS_FILE_URI,
    STICKER_SUB_TYPE,
)
from Rafayel_greet import try_greet
from Rafayel_sticker import available_tags, pick_sticker, plain_text, split_segments
from Rafayel_qzone import UGC_ALL, UGC_PARTIAL, build_payload
from Rafayel_qzone_auto import (
    bday_due, bday_pool, has_record, image_paths, mark_bday_sent, mark_posted,
    pick_manual, pick_post, pool_stats, reminder_text, render_text, should_post,
)

# ============================================
def your_ai_lover_response(user_message: str, user_id: str) -> str:
    """调用祁煜的对话引擎（Rafayel_chat）"""
    # 直接调用 get_reply，它会自动管理该用户的对话历史和记忆
    return get_reply(user_message, user_id)


def _img_uri(p):
    """
    裸本地路径 ⇒ file:// URI（说说配图真机证明 NapCat 把 file 的值当 URL 解析）。

    ⚠ 聊天表情包这条路径**没真机验过** ⇒ 留了退路 `STICKER_IMAGE_AS_FILE_URI`：
      `#表情` 发出去什么都没收到的话，把它翻成 False 退回裸路径再试。
    """
    p = str(p)
    if "://" in p or not STICKER_IMAGE_AS_FILE_URI:
        return p
    return "file://" + p


def build_message(text):
    """
    把他的回复翻译成 OneBot 的 message 字段。

    返回两种形状（OneBot v11 都认）：
      · **纯文字** ⇒ 直接返回 str（跟以前一模一样，所有老调用方无感）
      · **带表情** ⇒ 消息段数组 `[{"type":"text"…}, {"type":"image"…}]`

    ⭐ **纯表情形态**：语料里 6 成以上他是不说话、只甩一张图的 ⇒
       segments 里只有 image 段，这里原样返回 —— 她收到的就是一张图。
    ⚠ **任何情况下都不许把 `[表情:xxx]` 原样发出去**：标签没命中 / 图缺了 /
       功能关了，一律走 `plain_text` 把标记剥掉（她会真的看见那六个字）。
    ⚠ `sub_type` = 贴图样式（`STICKER_SUB_TYPE`），不放大图；待真机实测确认。
    """
    segments, dropped = split_segments(text)
    if dropped:
        print("[🖼️] %d 个表情标签没匹配到图，已丢掉（不会原样发出去）" % dropped)

    imgs = [v for k, v in segments if k == "image"]
    if not imgs:
        return plain_text(text)

    out = []
    for kind, val in segments:
        if kind == "text":
            out.append({"type": "text", "data": {"text": val}})
        else:
            # ⚠ 跟朋友圈配图同一个坑（2026-09-19 真机）：裸路径 NapCat 未必认 ⇒ 统一 file:// URI
            out.append({"type": "image",
                        "data": {"file": _img_uri(val), "sub_type": STICKER_SUB_TYPE}})
    return out


async def send_text(websocket, message_type, user_id, group_id, text):
    """按 OneBot v11 协议发一条消息（私聊 / 群聊）"""
    message = build_message(text)
    if message_type == "private":
        response = {
            "action": "send_private_msg",
            "params": {"user_id": int(user_id), "message": message}
        }
    elif message_type == "group":
        response = {
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": message}
        }
    else:
        return
    await websocket.send(json.dumps(response))
    if isinstance(message, list):
        kinds = "+".join(seg["type"] for seg in message)
        preview = " ".join((seg["data"].get("text") or "[图]") for seg in message)
        print(f"[💬] 已回复({kinds}): {preview[:50]}...")
    else:
        print(f"[💬] 已回复: {message[:50]}...")


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


async def send_qzone(websocket, content, ugc_right=UGC_ALL, target_uins=None, images=None):
    """
    把一条说说**送出去**（不等回执，立即返回）。

    images：**本地绝对路径**的列表（配图）；留空 = 纯文字。
    ⚠ 别传 base64 —— WS 单帧 ~16 MB，本项目最大一张配图 3.5 MB，base64 还有 ~33% 膨胀。

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
                                target_uins=target_uins, images=images, echo=echo)
        fut = asyncio.get_running_loop().create_future()
        _pending_receipts[echo] = fut
        await websocket.send(json.dumps(payload))
        asyncio.create_task(_watch_receipt(echo, fut))
        return True, "已送出"
    except Exception as e:
        return False, "发送异常：%s" % e


async def maybe_handle_sticker_cmd(websocket, message_type, user_id, group_id, raw_message):
    """
    表情包测试指令（`#表情` / `#表情 得意`）。命中并处理了返回 True，否则 False。

    ⭐ 为什么非要有它：表情包链路里**唯一本地验不了的**就是「图到底显不显示」——
      payload 形状、冷却闸、标签命中都能本地 spy 验，只有 NapCat 收到后怎么渲染不行。
      ⇒ 用这条指令走**真实发送路径**甩一张，真机上一眼就能看出对不对。

    ⚠ 跟 `#发说说` 同一个口径：只发图、**不说话**（他本来也常常只甩一张），
      不排期、不消耗语料。标签没命中 ⇒ 随机挑一张（别弹「未找到」那种机器人腔）。
    """
    text = (raw_message or "").strip()
    if not STICKER_CMD_PREFIX or not text.startswith(STICKER_CMD_PREFIX):
        return False

    tags = available_tags()
    if not tags:
        print("[🎭] 表情测试：标签表是空的（card/stickers.md 没读到？）")
        return True

    tag = text[len(STICKER_CMD_PREFIX):].strip()
    if tag and pick_sticker(tag):
        chosen, note = tag, "指定「%s」" % tag
    else:
        chosen = random.choice(tags)
        note = "指定「%s」没命中 ⇒ 随机" % tag if tag else "随机"

    await send_text(websocket, message_type, user_id, group_id, "[表情:%s]" % chosen)
    # ⚠ 这也是他**真的发出来**的东西 ⇒ 进对话记忆，否则她回「这表情好可爱」他接不住
    record_proactive(user_id, "[表情:%s]" % chosen)
    print("[🎭] 表情测试：%s ⇒ %s" % (note, chosen))
    return True


async def maybe_handle_qzone_cmd(websocket, message_type, user_id, group_id, raw_message):
    """
    朋友圈测试指令。命中并处理了返回 True，否则 False（交回给正常对话流程）。

        #发说说            → **从语料池随机挑一条**（只你可见；带图的会带图发）
        #发说说图          → 强制挑**带图**的一条（验「图到底上没上」专用）
        #发说说公开        → 池子随机 + 所有人可见（ugc_right=1）
        #发说说 内容       → 仍发你写的字（老行为，调试可见范围/风控用）
        #发说说私          → 「私」保留为别名，等价于默认

    ⚠ 2026-09-19 把**默认翻了**：原来默认公开、要私有得写「私」。
      她实测踩到坑（A 发指令、B 也看到）⇒ 二期方案本来就是「每人一条私有」，
      默认就不该是公开。想公开发就明写「公开」，不靠记一个字。
    ⚠⭐ 2026-09-19 二改：**默认改成从池子挑**，且**带图篇目真的带图发**。
      之前这条指令调 `send_qzone()` 根本没传 `images` ⇒ 它永远只发纯文字，
      **验不出图能不能上**（我先前说「用 #发说说 验带图」是错的，特此更正）。
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

    ugc = UGC_ALL if public else UGC_PARTIAL
    targets = None if public else [user_id]

    # 🎯 默认改成「从语料池挑一条」，走**和自动发完全同一条**渲染路径（含配图）
    imgs = None
    want_img = rest.startswith("图")
    if want_img:
        rest = rest[1:].strip()

    if rest and not want_img:
        content, note = rest, "手动正文"          # 老行为：发你写的字
    else:
        entry, note = pick_manual(user_id, want_image=want_img,
                                  context=recent_context(user_id))
        if not entry:
            # 池子读不到时不让整条指令废掉 —— 退回固定测试句，至少还能验风控/可见范围
            content, imgs = QZONE_TEST_TEXT, None
            note = "池子挑不出（%s）⇒ 用兜底测试句" % note
        else:
            content = render_text(entry, user_id)
            imgs = image_paths(entry) or None
            note = "%s｜%s" % (note, entry["id"])

    print("[🧪] 朋友圈测试：ugc_right=%s targets=%s %s / 正文 %r"
          % (ugc, targets, ("+%d图" % len(imgs)) if imgs else "无图", content[:40]))
    sent, note2 = await send_qzone(websocket, content, ugc_right=ugc,
                                   target_uins=targets, images=imgs)
    note = "%s → %s" % (note, note2)

    # ⚠ 2026-09-19 三改：回她的这句话**必须是他本人的口气**。
    #   之前回「✅ 说说已送出 —— 回执（如果有）我打在服务端日志里」——
    #   ✅ / 回执 / 服务端日志 全是后台那一面，一眼机器人（她当场指出）。
    #   ⇒ 成功 ⇒ 走提醒语料 `card/qzone_reminds.md`（轮换避重 + 「她的名字」替换成她的称呼）；
    #     失败 ⇒ 只说一句他自己的话，真实原因**只进服务端日志**（下面 [🧪] 那行）。
    if sent:
        tip = reminder_text(user_id)
    else:
        tip = QZONE_CMD_FAIL_TEXT
    await send_text(websocket, message_type, user_id, group_id, tip)

    # ⚠ 这句是他**真的说出口**的话 ⇒ 必须进对话记忆，否则她回一句「发了什么？」
    #   模型根本不知道上一句是他说的（跟主动打招呼 / 自动提醒同一个口径：
    #   任何绕开 get_reply 直接发出去的话都要补写）。
    record_proactive(user_id, tip)

    print("[🧪] 指令回执 sent=%s｜回她的话 %r｜内部原因 %s" % (sent, tip, note))
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

        # 2026-09-19：表情包测试指令（#表情 / #表情 标签）—— 命中就甩一张图，不进对话。
        if await maybe_handle_sticker_cmd(websocket, message_type, user_id,
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


# ============================================
# 主动发朋友圈（S2：他自己按排期挑一条发出来）
# ============================================
# ⚠⚠ 与上面「主动打招呼」是**两条独立排期**：各自记录（{uid}_greet.json / {uid}_qzone.json）、
#    各自日上限、各自后台 task，**绝不共用闸**。代价是**同一天可能既打招呼又发说说**
#    （双份非消息类主动行为），这是刻意接受的 —— 共用闸会让两条互相抢当天名额。
# 选条 / 排期 / 去重都在 ai-Rafayel\Rafayel_qzone_auto.py 里，本函数只管「发送 + 提醒」。

async def _send_one_qzone(ws, uid, entry, bday=None):
    """
    发一条说说 + 私聊提醒一句。返回是否真发出去。

    bday=None  ⇒ 普通排期那条，记进 `{uid}_qzone.json`（`mark_posted`）
    bday=kind  ⇒ 生日那条，记进 `{uid}_bday.json`（`mark_bday_sent`），**不碰**普通排期
    """
    text = render_text(entry, uid)          # 「用户」/`@用户` → 她的称呼（每人不同，不能预烘）
    imgs = image_paths(entry)               # 带图篇目必须带图发；缺图在选条阶段已排除

    sent, msg = await send_qzone(ws, text, ugc_right=UGC_PARTIAL,
                                 target_uins=[uid], images=imgs or None)
    if bday:
        # ⚠ 只有**真发出去**才记「今年发过了」—— 没发出去就让它 15 分钟后再试一次
        if sent:
            mark_bday_sent(uid, bday)
    else:
        # ⚠ 成败都要记一笔：成功了这条对这个人作废；失败了只推进排期、**不进 sent**
        #   —— 既不会每 15 分钟重试同一条，也不会白白耗掉一条语料。
        mark_posted(uid, entry, delivered=sent)

    if not sent:
        print("[📮] 说说没送出去 %s：%s（%s）" % (uid, msg, entry["id"]))
        return False

    tag = "🎂%s生日 " % ("祁煜" if bday == "rafayel" else "她的" if bday else "")
    print("[📮] %s发说说 -> %s：%r%s（%s）"
          % (tag, uid, text[:30], (" +%d图" % len(imgs)) if imgs else "", entry["id"]))

    # —— 私聊提醒：QQ 空间入口太深，不提醒她基本看不到 ——
    await asyncio.sleep(random.uniform(QZONE_AUTO_REMIND_DELAY_MIN,
                                       QZONE_AUTO_REMIND_DELAY_MAX))
    remind = reminder_text(uid)
    await send_text(ws, "private", uid, None, remind)
    # ⚠ 提醒也是「他说过的话」，必须进对话记忆 —— 否则她回「什么说说？」
    #   模型根本不知道上一句是他说的，会出现接不住的回复。
    record_proactive(uid, remind)
    return True


async def auto_qzone_scan():
    """
    扫一遍私聊过的用户：谁排期到了，就替他发一条**只有她可见**的说说，再私聊提醒一句。

    ⚠ NapCat 没连上时**静默什么都不做**（开头就 return，不打日志）⇒ 排查前先确认
      启动时印过 `[✅] NapCat 已连接`。
    ⚠ uid 必须纯数字（QQ 号），免得给 "cli" 这种测试号发说说。
    ⚠🎂 生日**优先于**普通排期：今天有生日就只发生日那条（两者不共用闸，
      但同一天连发两条太吵 ⇒ 发生日那条，普通那条等下次排期）。
    """
    if not QZONE_AUTO or not connected_clients:
        return
    ws = next(iter(connected_clients))

    for path in glob.glob(os.path.join(MEMORY_DIR, "*.json")):
        uid = os.path.splitext(os.path.basename(path))[0]
        # ⚠ 只认「对话记忆」文件本身；带后缀的都是旁支记录（画像 / 打招呼 / 发说说 / 生日）
        if uid.endswith(("_profile", "_greet", "_qzone", "_bday")):
            continue
        if not uid.isdigit():
            continue

        # 🎂 生日专项（独立排期，不占每天名额、不推进 next_at）
        kind, bentry, bnote = bday_due(uid)
        if bentry:
            # 新用户**第一天**就撞上生日：普通记录还没建 ⇒ 先建好，
            # 否则「第一条最早第二天」会失效（next_at 空 ⇒ 下次扫描立刻发普通那条）。
            if not has_record(uid):
                should_post(uid)
            await _send_one_qzone(ws, uid, bentry, bday=kind)
            continue
        if kind:
            print("[🎂] 生日跳过 %s：%s" % (uid, bnote))
            continue

        ok, why = should_post(uid)
        if not ok:
            continue          # 绝大多数是「排期未到」，别刷日志

        # 🎯 带上「她最近在聊什么」⇒ 挑条时优先挑相关的（软相关，命中不了就退回随机）
        entry, note = pick_post(uid, context=recent_context(uid))
        if not entry:
            print("[📮] 朋友圈跳过 %s：%s" % (uid, note))
            continue

        await _send_one_qzone(ws, uid, entry)


async def auto_qzone_loop():
    while True:
        await asyncio.sleep(QZONE_AUTO_SCAN_SECONDS)
        try:
            await auto_qzone_scan()
        except Exception as e:
            print("[⚠️] 发朋友圈扫描出错：%s" % e)


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
        if QZONE_AUTO:
            # ⚠ 与 auto_greet_loop 是**两个独立 task**、各自排期 —— 别把这两个合成一个循环。
            asyncio.create_task(auto_qzone_loop())
            st = pool_stats()
            print("📮 发朋友圈已开启（每人每 %s~%s 天一条；池子 %d 条 = 可发 %d + hold %d + 🎂生日 %d，其中带图 %d）"
                  % (QZONE_AUTO_GAP_DAYS_MIN, QZONE_AUTO_GAP_DAYS_MAX,
                     st["total"], st["after_hold"], st["held"], st["bday"], st["with_image"]))
            if QZONE_BDAY:
                print("🎂 生日专项已开启：祁煜生日 %s 发（%d 篇）；她的生日抓到画像 birthday 才发"
                      % (QZONE_BDAY_RAFAYEL, len(bday_pool("rafayel"))))
        if QZONE_CMD_PREFIX:
            print("🧪 朋友圈测试指令已开启：「%s 内容」=只你可见；「%s公开 内容」=所有人可见"
                  % (QZONE_CMD_PREFIX, QZONE_CMD_PREFIX))
        await asyncio.Future()  # 永久运行

if __name__ == "__main__":
    asyncio.run(main())
