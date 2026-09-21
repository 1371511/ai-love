import asyncio
import glob
import json
import os
import random
import sys
import time

import websockets

# ========== 导入祁煜（Rafayel）的对话引擎 ==========
# 2026-09-17 搬家：引擎代码都在 ai-Rafayel\ 子目录，而入口 bot 仍留在项目根 ——
# 所以先把代码目录挂进 sys.path，下面那句 from Rafayel_chat import … 才找得到。
_CODE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai-Rafayel")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from Rafayel_chat import (comment_opening, comment_reply, get_reply,
                         recent_context, record_proactive, take_opening)
from Rafayel_config import (
    AFFINITY_UNLOCK,
    POKE_COOLDOWN, POKE_ECHO_BACK, POKE_ENABLE, POKE_PROMPT,
    AUTO_GREET, AUTO_GREET_IDLE_HOURS, AUTO_GREET_SCAN_SECONDS, MEMORY_DIR,
    QZONE_AUTO, QZONE_AUTO_GAP_DAYS_MAX, QZONE_AUTO_GAP_DAYS_MIN,
    QZONE_AUTO_REMIND_DELAY_MAX, QZONE_AUTO_REMIND_DELAY_MIN, QZONE_AUTO_SCAN_SECONDS,
    QZONE_BDAY, QZONE_BDAY_RAFAYEL,
    QZONE_CMD_FAIL_TEXT, QZONE_CMD_PREFIX, QZONE_CMD_UIDS, QZONE_RECEIPT_TIMEOUT,
    QZONE_TEST_TEXT, STICKER_CMD_PREFIX, STICKER_IMAGE_AS_BASE64,
    POKE_COOLDOWN_SECONDS, POKE_ENABLE, POKE_PROMPT, POKE_REPLY_BACK,
    POKE_TYPING_MAX, POKE_TYPING_MIN,
    REPLY_TYPING_MAX, REPLY_TYPING_MIN, REPLY_TYPING_PER_CHAR,
    REPLY_WAIT_MAX, REPLY_WAIT_QUIET,
    STICKER_IMAGE_AS_FILE_URI, STICKER_REPLY_TO_STICKER, STICKER_SUB_TYPE,
    QZONE_CMT_DELAY_MAX, QZONE_CMT_DELAY_MIN, QZONE_CMT_ENABLE,
    QZONE_CMT_EVENT_WS, QZONE_CMT_HOUR_END, QZONE_CMT_HOUR_START,
    QZONE_CMT_MAX_PER_DAY, QZONE_CMT_POLL_SECONDS, QZONE_CMT_REPLY_ENABLE,
    QZONE_SELF_UIN,
)
from Rafayel_affinity import (current_level, init_unlocked, load_egg_levels,
                              load_sms_nodes, pending_unlock)
from Rafayel_daily import backfill_unlocked, load_unlocked, save_unlocked
from Rafayel_greet import try_greet
from Rafayel_sticker import (available_tags, has_sticker, parse_incoming,
                             pick_sticker, plain_text, random_reply_tag,
                             split_segments)
from Rafayel_qzone import UGC_ALL, UGC_PARTIAL, build_payload
from Rafayel_qzone_comment import (already_replied, clean_comment,
                                   fetch_feeds, is_known_user, mark_done,
                                   mark_replied, note_posted, refresh_baseline,
                                   remembered_tid_content, remember_tids,
                                   scan_new_comments, send_comment, take_ready)
from Rafayel_qzone_auto import (
    bday_due, bday_pool, has_schedule, image_paths, mark_bday_sent, mark_posted,
    pick_manual, pick_post, pool_stats, reminder_text, render_text, should_post,
)

# ============================================
def your_ai_lover_response(user_message: str, user_id: str, media: bool = False) -> str:
    """调用祁煜的对话引擎（Rafayel_chat）"""
    # 直接调用 get_reply，它会自动管理该用户的对话历史和记忆
    # media = 她这条是不是图 / 表情 ⇒ 只进每日统计（好感度用），不参与对话内容
    return get_reply(user_message, user_id, media=media)


def _img_payload(p):
    """
    聊天表情包的图 ⇒ NapCat 认的形态。

    ⚠ 真机梯子（2026-09-20 凌晨走完）：裸路径 ✗ → file:// ✗（NapCat ENOENT，
      它看不见本机目录，多半在 Docker 里没挂载）⇒ 默认 **base64**，
      把图字节直接塞进消息段，不依赖文件系统。
    """
    p = str(p)
    if "://" in p:
        return p
    if STICKER_IMAGE_AS_BASE64:
        import base64
        with open(p, "rb") as f:
            return "base64://" + base64.b64encode(f.read()).decode("ascii")
    if STICKER_IMAGE_AS_FILE_URI:
        return "file://" + p
    return p


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
            # ⚠ 跟朋友圈配图同一个坑：裸路径/file:// NapCat 都吃不下（ENOENT）⇒ base64
            out.append({"type": "image",
                        "data": {"file": _img_payload(val), "sub_type": STICKER_SUB_TYPE}})
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
# 🎁 牵绊度跨级 ⇒ 补一条**官方原文**（2026-09-21 新）
# ============================================
# 跨过一个等级时，让他发一条官方素材：
#   该等级是 45 个短信节点之一 ⇒ 发那条短信的**开头句**（`card\affinity\牵绊短信\`）；
#   否则取 86 条彩蛋里绑在该等级上的那条（等级映射在 `card\affinity.md` 的 `EGG_AT=`）。
# ⭐ 一级最多一条、**短信优先于彩蛋**；每用户每条只发一次；**不补发历史**。
# ⚠ 素材原文直发、不改一个字（只有 `用户` 这种占位符换成她的称呼），**不进 LLM ⇒ 零 token**。

async def maybe_send_unlock(websocket, message_type, user_id, group_id):
    """
    跨级了就补一条官方素材。决策在 `Rafayel_affinity.pending_unlock`（**只读**），
    本函数只负责「发出去 + 记账」。

    ⭐⭐ **牵绊短信不进 QQ**（2026-09-21 她定：那批已经进网页端了）——
      ⇒ QQ 端**只发彩蛋**；短信节点一到级就**只标「已解锁」、一个字都不发**，
        留给网页端展示。所以这里 `mark_sms` 是记账、`send` 才是要说话的。

    ⚠ 四条铁律：
      ① 发出去的那句话**必须进对话记忆**（`record_proactive`）—— 不然她回话时模型
         不知道上一句是他说的，会出现完全接不住的回复（老规矩，跟打招呼/说说同一口径）。
      ② 记「已发送」放在**发送成功之后** —— 发送抛异常就不留下"他说过"的假记录。
         （短信那列是「已解锁」不是「已发送」，且根本不发，所以一进来就能记。）
      ③ **不等回执**。主流程里等回执必然超时，真根因见 `_watch_receipt` 那段注释。
      ④ 只在**私聊**里发：群里冒出一条官方素材很奇怪。
    """
    if not AFFINITY_UNLOCK:
        return
    try:
        if message_type != "private":
            return

        rec = load_unlocked(user_id)
        if rec is None:
            # 第一次接入：只记「现在几级」，**不补发历史**（老用户可能一上来就 100 级，
            # 否则会一口气把二十多条历史素材全灌给她）。
            save_unlocked(user_id, init_unlocked(current_level(user_id, MEMORY_DIR)))
            return

        item = pending_unlock(user_id, MEMORY_DIR)
        if not item:
            return

        # ① 短信：只记「已解锁」（网页端读它），**绝不发到 QQ**
        got_sms = [int(x) for x in (rec.get("sms") or [])]
        for lv in (item.get("mark_sms") or []):
            if lv not in got_sms:
                got_sms.append(int(lv))
        rec["sms"] = sorted(got_sms)

        # ② 彩蛋才是唯一真的发出去的东西
        send = item.get("send")
        if send:
            await send_text(websocket, message_type, user_id, group_id, send["text"])
            record_proactive(user_id, send["text"])
            got = list(rec.get("eggs") or [])
            if send["key"] not in got:
                got.append(send["key"])
            rec["eggs"] = got
            print("[💗] 牵绊度 %d 级 ⇒ 发彩蛋：%r" % (send["level"], send["text"][:30]))
        else:
            print("[💗] 牵绊度 %d 级 ⇒ 这级没有彩蛋（只解锁，短信不进 QQ）"
                  % item["level_now"])

        rec["level"] = max(int(rec.get("level") or 0), int(item["level_now"] or 0))
        save_unlocked(user_id, rec)
    except Exception as e:
        print("[💗] 跨级素材发送失败（不影响对话）：%s" % e)


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
# 👆 戳一戳（2026-09-22 加；她选 C：回戳 + 说一句）
# ============================================
# ⭐ 为什么值得做：戳一戳是 QQ 里最像「真人小动作」的交互 —— 没有文字、没有话题，
#   纯粹是碰一下。他要有反应（回戳 + 说一句），才会像个活人在手机那头。
#
# ⚠ 三条硬规矩：
#   ① **她戳的是别人就不管**：`target_id != self_id` ⇒ 直接 return（群里很常见）。
#   ② **有冷却**：她连着戳也不会刷屏。
#   ③ **回戳失败无所谓**：`send_poke` 是 NapCat 扩展，发不出去就发不出去，
#      下面那句话照说 —— 她戳了一定有反应，这才是关键。
_poke_last = {}      # uid -> 上次回应的时间戳
_poke_busy = set()   # 正在处理的 uid（防她连点导致两条回复并发）


async def send_poke(websocket, message_type, user_id, group_id):
    """
    戳回去。`send_poke` 是 **NapCat 扩展**（不是 OneBot v11 标准）。

    ⚠ **不等回执**：跟 `send_text` 同一个口径（⚠⚠ 绝不在处理消息的协程里等 NapCat 回执）。
    ⚠ 失败只是日志里一行，不影响后面那句话 —— 她戳了**一定有反应**。
    """
    params = {"user_id": int(user_id)}
    if message_type == "group" and group_id:
        params["group_id"] = group_id
    try:
        await websocket.send(json.dumps({"action": "send_poke", "params": params}))
        print("[👆] 已回戳 %s（%s）" % (user_id, message_type))
    except Exception as e:
        print("[👆] 回戳失败（不影响说话）：%r" % e)


async def handle_poke(data, websocket):
    """她戳了他 ⇒ 回戳一下 + 说一句话。"""
    if not POKE_ENABLE:
        return

    user_id = str(data.get("user_id") or "")
    if not user_id.isdigit():
        return
    target = str(data.get("target_id") or "")
    self_id = str(data.get("self_id") or globals().get("SELF_UIN") or "")

    # ① 她戳的是别人 ⇒ 不关他的事（群里最常见）
    if self_id and target and target != self_id:
        print("[👆] %s 戳了 %s（不是他）⇒ 不管" % (user_id, target))
        return

    # ② 冷却 / 正在处理
    now = time.time()
    if now - _poke_last.get(user_id, 0) < POKE_COOLDOWN_SECONDS:
        print("[👆] %s 又戳了一下（冷却 %.0fs 内）⇒ 装作没看见"
              % (user_id, POKE_COOLDOWN_SECONDS))
        return
    if user_id in _poke_busy:
        return
    _poke_last[user_id] = now
    _poke_busy.add(user_id)

    group_id = data.get("group_id")
    message_type = "group" if group_id else "private"
    print("[👆] %s 戳了他 ⇒ 回戳 + 说一句" % user_id)

    try:
        # ③ 先回戳（NapCat 扩展，失败无所谓）
        if POKE_REPLY_BACK:
            await send_poke(websocket, message_type, user_id, group_id)

        # ④ 再说一句（走 get_reply ⇒ 自动进对话记忆）
        reply = await asyncio.to_thread(your_ai_lover_response, POKE_PROMPT, user_id)
        if not (reply or "").strip():
            tag = random_reply_tag()
            if tag:
                reply = "[表情:%s]" % tag
        if reply:
            d = _typing_delay(reply, POKE_TYPING_MIN, POKE_TYPING_MAX)
            await asyncio.sleep(d)
            await send_text(websocket, message_type, user_id, group_id, reply)
    finally:
        _poke_busy.discard(user_id)


async def process_notice(data, websocket):
    """
    处理 notice 事件（戳一戳 / 其它）。

    ⚠ 戳一戳是 **notice 不是 message** ⇒ 以前 `post_type == "message"` 那一支根本收不到，
      她在 QQ 里戳他，他一点反应都没有。
    """
    if data.get("notice_type") == "notify" and data.get("sub_type") == "poke":
        await handle_poke(data, websocket)
        return
    # 其余 notice（撤回 / 群提示 …）不处理，但留一行探针 ——
    # 真机核字段就靠它（NapCat 版本不同，戳一戳的字段名可能不一样）。
    print("[🔎] notice %s/%s：%s"
          % (data.get("notice_type"), data.get("sub_type"),
             json.dumps(data, ensure_ascii=False)[:160]))


# ============================================
# ⏱ 回复节奏：等她说完 + 假装打字（2026-09-22 她定的）
# ============================================
# ⭐ 为什么要有这一层：她的原话是「发完一句它就立刻回了，根本没机会说四句」。
#   秒回 + 一句一答 = 一眼机器人；真人是「等对方说完 → 想一下 → 慢慢打」。
#
# ⚠ 三条硬规矩：
#   ① **每用户独立**：她等她的，别人发消息不受影响。
#   ② **静默被新消息重置**：她接着说 ⇒ 计时从头来（这正是「等她说完」的意思）。
#   ③ **有强制上限**（REPLY_WAIT_MAX）：她一直在说到不了静默 ⇒ 到点也发车。
#      没有这条，她连发 5 分钟他一个字都不回 —— 比秒回还糟。
#
# ⚠⭐ 顺带解决**并发乱序**：`get_reply` 是同步的（里面有 requests.post），
#    以前它把事件循环堵住、反而串行了；一旦这里改成 await，她的新消息就会并发进来
#    ⇒ 两条回复交错。现在同一用户只在静默结束时回一次，天然串行。
_pending_replies = {}


def _typing_delay(text, lo=None, hi=None):
    """
    发出去之前等几秒（假装打字）。字多就多等一点，但压在 [lo, hi] 里。

    lo / hi 不传就用平时的 `REPLY_TYPING_*`；戳一戳那种短回复传 `POKE_TYPING_*`（快一点）。
    """
    lo = REPLY_TYPING_MIN if lo is None else lo
    hi = REPLY_TYPING_MAX if hi is None else hi
    base = lo + len(text or "") * REPLY_TYPING_PER_CHAR
    top = min(hi, max(lo, base))
    return random.uniform(lo, top)


def _enqueue_reply(websocket, message_type, user_id, group_id, parsed):
    """她来一条 ⇒ 记进这一批；静默够了才真的去回复（见 `_wait_for_quiet`）。"""
    st = _pending_replies.get(user_id)
    if st is None:
        st = {"lines": [], "media": False, "pure": False,
              "t0": time.time(), "last": time.time()}
        _pending_replies[user_id] = st
        asyncio.ensure_future(_wait_for_quiet(user_id))
    st["lines"].append(parsed.get("prompt") or "")
    st["media"] = bool(st["media"] or parsed.get("sticker") or parsed.get("photo"))
    st["pure"] = bool(parsed.get("pure"))        # 最后一条是不是只甩了张图
    st["last"] = time.time()
    st["ws"] = websocket
    st["message_type"] = message_type
    st["group_id"] = group_id
    print("[⏱] %s 第 %d 句 ⇒ 等她说完再回" % (user_id, len(st["lines"])))


async def _wait_for_quiet(user_id):
    """一直等到「静默够了」或「等太久了」，然后发车。"""
    while True:
        await asyncio.sleep(0.25)
        st = _pending_replies.get(user_id)
        if st is None:                       # 已经被别处清掉了
            return
        if time.time() - st["last"] >= REPLY_WAIT_QUIET:
            why = "静默 %.0fs" % REPLY_WAIT_QUIET
            break
        if time.time() - st["t0"] >= REPLY_WAIT_MAX:
            why = "等太久（上限 %.0fs）" % REPLY_WAIT_MAX
            break
    await _flush_reply(user_id, why)


async def _flush_reply(user_id, why=""):
    """真的去回复：把她这一批话并起来 ⇒ 想一下 ⇒ 打字 ⇒ 发出去。"""
    st = _pending_replies.pop(user_id, None)
    if not st:
        return
    ws = st.get("ws")
    if ws is None:
        return
    text = "\n".join(x for x in st["lines"] if (x or "").strip())
    if not text:
        return

    # ⭐ `get_reply` 是同步的（里面有 requests.post）⇒ 直接调用会把整个事件循环堵住，
    #   说说排期 / 打招呼扫描 / 别人的消息全停。丢到线程里跑。
    reply = await asyncio.to_thread(your_ai_lover_response, text, user_id,
                                    media=st["media"])

    # 2026-09-20：她只甩了张表情、一个字没说 ⇒ 他也甩一张（对打）。
    if st["pure"] and STICKER_REPLY_TO_STICKER and not has_sticker(reply):
        tag = random_reply_tag()
        if tag:
            reply = "[表情:%s]" % tag + (reply or "").strip()

    # 空回复兜底：模型一个字都没回 ⇒ 至少甩一张图，别让她等个寂寞。
    if not (reply or "").strip():
        tag = random_reply_tag()
        if tag:
            reply = "[表情:%s]" % tag

    if reply:
        d = _typing_delay(reply)
        print("[⏱] %s：%d 句并一批 ⇒ 打字 %.1fs 再发"
              % (why or "发车", len(st["lines"]), d))
        await asyncio.sleep(d)
        await send_text(ws, st["message_type"], user_id, st["group_id"], reply)

    # 🎁 牵绊度跨级 ⇒ 再补一条官方素材（短信开头句 / 彩蛋）。
    #    ⚠ 放在回复**之后**：升级一定发生在她刚说完话之后，语境最自然。
    await maybe_send_unlock(ws, st["message_type"], user_id, st["group_id"])


# ============================================
# 👉 戳一戳（2026-09-22 她要的）
# ============================================
# 她戳他 ⇒ ① 回戳一下 ② 打字说一句。
#
# ⚠ 三条硬规矩：
#   ① **只认戳他的**（`target_id == self_id`）—— 群聊里她戳别人不该有反应。
#   ② **回戳是锦上添花**：`send_poke` 是 NapCat 扩展、真机没验过 ⇒
#      失败就静默跳过、**话照说**，绝不能变成「戳了完全没反应」。
#   ③ **冷却期内整个忽略**：她连着戳只回应第一次，防刷屏。
_poke_last = {}


async def send_poke(websocket, user_id, group_id=None):
    """
    回戳她一下（QQ 的「戳一戳」）。成功返回 True，失败 False。

    ⚠ `send_poke` 不是 OneBot v11 标准动作，是 NapCat / go-cqhttp 的扩展
      ⇒ 这里**只管发、不等回执**；成败交给调用方降级处理。
    """
    params = {"user_id": int(user_id)}
    if group_id:
        params["group_id"] = group_id
    try:
        await websocket.send(json.dumps({"action": "send_poke", "params": params},
                                        ensure_ascii=False))
        return True
    except Exception as e:
        print("[👉] 回戳发送失败（不影响说话）：%r" % e)
        return False


def _poke_at_me(data):
    """这是不是「她戳了他」这件事本身（她戳别人 ⇒ 不算）。"""
    if data.get("post_type") != "notice":
        return False
    if data.get("notice_type") != "notify" or data.get("sub_type") != "poke":
        return False
    target = str(data.get("target_id") or "")
    me = str(data.get("self_id") or globals().get("SELF_UIN") or "")
    # ⚠ 群聊里她也可能戳别人 ⇒ 两个 id 都对得上才算「戳他」
    return bool(me) and bool(target) and target == me


async def maybe_handle_poke(data, websocket):
    """命中并处理了返回 True（交回给调用方 return，不再往下走）。"""
    if not POKE_ENABLE or not _poke_at_me(data):
        return False
    uid = str(data.get("user_id") or "")
    if not uid.isdigit():
        return False

    now = time.time()
    if now - _poke_last.get(uid, 0) < POKE_COOLDOWN:
        print("[👉] %s 又戳了一下（冷却 %.0fs 内）⇒ 不理" % (uid, POKE_COOLDOWN))
        return True
    _poke_last[uid] = now

    gid = data.get("group_id")
    mt = "group" if gid else "private"
    print("[👉] %s 戳了他 ⇒ 回戳 + 说一句" % uid)

    if POKE_ECHO_BACK:
        await send_poke(websocket, uid, gid)

    # 说话走正常对话引擎（「她戳了戳你」是合成提示，跟她发张图同一个口径）
    reply = await asyncio.to_thread(your_ai_lover_response, POKE_PROMPT, uid,
                                    media=False)
    if not (reply or "").strip():
        tag = random_reply_tag()
        if tag:
            reply = "[表情:%s]" % tag
    if reply:
        await asyncio.sleep(_typing_delay(reply))
        await send_text(websocket, mt, uid, gid, reply)

    # 🎁 牵绊度跨级 ⇒ 补一条官方素材（跟文字消息同一口径）
    await maybe_send_unlock(websocket, mt, uid, gid)
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

    # 👆 2026-09-22：notice 事件（戳一戳）—— ⚠ 它不是 message，
    #    以前这一整类被下面的判断漏掉，她戳他完全没反应。
    if data.get("post_type") == "notice":
        await process_notice(data, websocket)
        return

    # 👉 2026-09-22：戳一戳（notice + sub_type=poke）—— 她戳他 ⇒ 回戳 + 说一句。
    if await maybe_handle_poke(data, websocket):
        return

    # ⚠ 探针：**没认出来的 notice 原样打出来**。NapCat 各版本字段不一样
    #   （戳一戳也可能挂在别的 notice_type 下），真机戳一下看这行就能核字段。
    if data.get("post_type") == "notice":
        print("[🔎] 未处理的 notice：%s"
              % json.dumps(data, ensure_ascii=False)[:300])
        return

    # 只处理消息事件（私聊和群聊）
    if data.get("post_type") == "message":
        message_type = data.get("message_type")  # "private" 或 "group"
        user_id = str(data.get("user_id"))
        raw_message = data.get("raw_message", "")

        # 群聊消息可以加上群号
        group_id = data.get("group_id") if message_type == "group" else None

        print(f"[📩] 收到 {message_type} 消息: {raw_message} (来自: {user_id})")

        # 2026-09-20：记住他自己的 QQ 号（拉自己的空间要用）。
        #   配置里没写死就从 NapCat 事件里的 self_id 取 —— 省得她手填。
        _self = str(data.get("self_id") or "").strip()
        if _self.isdigit() and not globals().get("SELF_UIN"):
            globals()["SELF_UIN"] = _self

        # 2026-09-20：她发来的常常**不是文字**（表情包 / 照片 / 商城表情）。
        #   raw_message 那时只是一串 CQ 码（甚至空串），直接喂给模型 ⇒
        #   它有时猜得出「她发了张图」就回一句，有时觉得无从接话就回空
        #   ⇒ 下面 if reply: 一空就整条不发，她看到的就是「没反应」，而且时好时坏。
        #   现在先翻译成他能看懂的话。
        parsed = parse_incoming(data)
        if parsed["probe"]:
            print(f"[🔎] 消息段: {parsed['probe']} (来自: {user_id})")

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
            # ⚠ 开场白**不走等待**：她第一句话就干等十几秒，会以为 bot 坏了。
            await send_text(websocket, message_type, user_id, group_id, opening)

        # ⏱ 2026-09-22：不再秒回 —— 先等她说完（她连着说的几句并成一批一起回），
        #    再假装打字 2~6 秒才发。细节全在 `_enqueue_reply` 那一节。
        _enqueue_reply(websocket, message_type, user_id, group_id, parsed)

# ============================================
# 主动打招呼（他忍不住先开口）
# ============================================

async def auto_greet_scan():
    """
    扫一遍私聊过的用户：谁冷场够久了，就让他主动说一句。

    最后活跃时间直接取 memory/{uid}.json 里的 saved_at（save_memory 每次都会写），
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
    # 2026-09-20：记下「这条正文是发给谁的」—— 评论数比对要靠它把说说认回人。
    #   ⚠ 不能用 tid（bridge 给的 tid 每次都变），只能用正文指纹。
    if QZONE_CMT_ENABLE:
        try:
            note_posted(uid, text)
        except Exception as e:
            print("[⚠️] 记说说正文失败（不影响发送）：%s" % e)
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
            # 新用户**第一天**就撞上生日：普通排期还没排 ⇒ 先排好，
            # 否则「第一条最早第二天」会失效（next_at 空 ⇒ 下次扫描立刻发普通那条）。
            # ⚠ 用 `has_schedule` 不是 `has_record`：空记录（旁路建的）也得重新排。
            if not has_schedule(uid):
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


async def auto_comment_scan():
    """
    扫一遍：谁的评论数涨了 ⇒ 他知道她在他那条说说底下留了话 ⇒ 过几分钟跑来私聊找她。

    ⚠ 为什么是「私聊」不是「在空间回复」：这台服务器上**评论内容读不到**
      （详情 1502、列表只有 cmtnum、tid 还会漂）⇒ 只知道「她留了话」，不知道写了什么。
      ⇒ 那就让她在私聊里说，他接得住 —— 真人也常这么跑来问一句。
    ⚠ NapCat 没连上就什么都不做（跟打招呼、发说说同一个口径）。
    """
    if not QZONE_CMT_ENABLE or not connected_clients:
        return
    uin = QZONE_SELF_UIN or globals().get("SELF_UIN")
    if not uin:
        return          # 还不知道自己是谁，等下一条消息带 self_id 过来

    ws = next(iter(connected_clients))

    # ① 到点的：真的去找她
    for item in take_ready():
        uid = str(item.get("uid") or "")
        if not uid.isdigit():
            continue
        line = comment_opening(uid, item.get("content") or "")
        if not line:
            continue          # 模型没写出来 ⇒ 这次就算了，别硬发一句不通的
        await send_text(ws, "private", uid, None, line)
        record_proactive(uid, line)     # ⭐ 主动说的话必须进记忆，否则她接不住
        mark_done()
        print("[💬] 她在说说下留话 ⇒ 他去找 %s：%s" % (uid, line[:40]))

    # ② 比对评论数，排新的
    #   ⭐ 顺便把「当次解析」的 tid→正文 记下来：事件流里的 post_tid 靠它认正文
    #      （tid 每次解析都变，只在本批次内有效 —— 见 Rafayel_qzone_comment 文件头）
    posts, ferr = fetch_feeds(uin)
    if not ferr:
        remember_tids([(p["tid"], p["content"])
                       for p in posts if p.get("tid")])
    added, logs = scan_new_comments(uin)
    for line in logs:
        print("[💬] %s" % line)


async def auto_comment_loop():
    while True:
        await asyncio.sleep(QZONE_CMT_POLL_SECONDS)
        try:
            await auto_comment_scan()
        except Exception as e:
            print("[⚠️] 评论扫描出错：%s" % e)


def _bridge_ws_connect(url):
    """兼容各版 websockets：优先 asyncio 客户端，退回老接口。"""
    import websockets as _w
    fn = getattr(_w, "connect", None)
    if fn is None:
        import websockets.asyncio.client as _c
        fn = _c.connect
    return fn(url)


async def comment_event_loop():
    """
    订阅 qzone-bridge 的评论事件 ⇒ **在空间里直接回复她那条评论**（真双向）。

    2026-09-20 深夜真机实测：bridge 的 WS 事件流里**带评论内容**
      {"post_type":"notice", "notice_type":"qzone_comment",
       "user_id":她的QQ, "comment_content":"…什么巧合？", "post_tid":"…"}
    而 REST 那边读不到内容（tid 会漂 + 详情 1502 + 列表只有 cmtnum）
      ⇒ 这条是**主路**；计数轮询（auto_comment_scan）降级为兜底。

    ⚠ 每条事件**另起一个 task**：回复要隔几分钟，不能把事件流堵住。
    ⚠ 断线自动重连；一切异常吞掉 —— 她不能察觉这一路的存在。
    """
    if not (QZONE_CMT_ENABLE and QZONE_CMT_REPLY_ENABLE):
        return
    while True:
        try:
            async with _bridge_ws_connect(QZONE_CMT_EVENT_WS) as ev_ws:
                print("[💬] 评论事件流已连上：%s" % QZONE_CMT_EVENT_WS)
                async for raw in ev_ws:
                    try:
                        ev = json.loads(raw)
                    except Exception:
                        continue
                    asyncio.create_task(handle_qzone_comment_event(ev))
        except Exception as e:
            print("[💬] 评论事件流断开（%s），1 分钟后重连" % e)
            await asyncio.sleep(60)


async def handle_qzone_comment_event(ev):
    """一条评论事件 ⇒ 认人 → 隔几分钟 → 生成回复 → 写回空间（失败降级私聊）。"""
    try:
        if not isinstance(ev, dict):
            return
        if ev.get("post_type") != "notice" or ev.get("notice_type") != "qzone_comment":
            return

        uid = str(ev.get("user_id") or "")
        post_tid = str(ev.get("post_tid") or "")
        her = clean_comment(ev.get("comment_content") or "")
        # ⚠ bridge 不一定给 comment_id ⇒ 用「谁+哪条+写了啥」拼一个指纹兜底去重
        cid = str(ev.get("comment_id") or "") or "%s|%s|%s" % (uid, post_tid[:10], her[:24])

        if not uid.isdigit() or not her:
            return                    # 认不出是谁 / 纯符号评论
        if already_replied(cid):
            return                    # 这条回过了
        if not is_known_user(uid):
            return                    # 陌生人（说说只她可见，正常不会有）

        uin = str(QZONE_SELF_UIN or globals().get("SELF_UIN") or ev.get("post_uin") or "")

        # ⭐ 立刻拉一次列表刷新 tid→正文：事件里的 post_tid 是「bridge 那次解析」的 tid，
        #   只有**同一批次**才对得上 ⇒ 趁热刷，比用几小时前那张表命中率高得多。
        fresh, ferr = await asyncio.to_thread(fetch_feeds, uin) if uin else ([], "no uin")
        if not ferr:
            remember_tids([(p["tid"], p["content"]) for p in fresh if p.get("tid")])
        tids = remembered_tid_content()
        post_text = tids.get(post_tid) or (list(tids.values())[0] if tids else "")

        # 隔几分钟再回（秒回太假）；深夜就等天亮 —— 他睡着了，明早才看见
        wait = random.randint(QZONE_CMT_DELAY_MIN * 60, QZONE_CMT_DELAY_MAX * 60)
        now = time.localtime()
        if not (QZONE_CMT_HOUR_START <= now.tm_hour < QZONE_CMT_HOUR_END):
            wait += _seconds_until_hour(QZONE_CMT_HOUR_START)
        print("[💬] %s 在说说下评论：%s（%d 秒后回）" % (uid, her[:20], wait))
        await asyncio.sleep(wait)

        if already_replied(cid):
            return                    # 等的这几分钟里别处已经回了
        reply = comment_reply(uid, post_text, her)
        if not reply:
            return                    # 模型没写出来 ⇒ 不硬回

        ok, why = await asyncio.to_thread(send_comment, uin, post_tid, reply)
        if ok:
            mark_replied(cid)
            record_proactive(uid, reply)      # ⭐ 绕开 get_reply 发的话都要进记忆
            refresh_baseline(uin)             # ⭐ 刷基线，计数轮询才不会重复找她
            print("[💬] 已在空间回复 %s 的评论：%s" % (uid, reply[:40]))
        else:
            # 写回空间失败 ⇒ 降级：私聊去找她（她留了话，他接得住）
            print("[💬] 空间回复失败（%s）⇒ 降级私聊" % why)
            line = comment_opening(uid, post_text)
            if line and connected_clients:
                ws = next(iter(connected_clients))
                await send_text(ws, "private", uid, None, line)
                record_proactive(uid, line)
                refresh_baseline(uin)     # ⭐ 同样刷基线：这条已经处理过了，轮询别再来一遍
    except Exception as e:
        print("[⚠️] 评论事件处理出错：%s" % e)


def _seconds_until_hour(hour):
    """现在离下一个 hour 点还有几秒（给「等天亮再回」用）。"""
    now = time.localtime()
    target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                          hour, 0, 0, now.tm_wday, now.tm_yday, now.tm_isdst))
    if target <= time.mktime(now):
        target += 86400
    return int(target - time.mktime(now))


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
        if AFFINITY_UNLOCK:
            print("🎁 牵绊度跨级素材已开启（%d 个短信节点 + %d 条彩蛋；一级最多一条，短信优先）"
                  % (len(load_sms_nodes()), len(load_egg_levels())))
            # ⭐⭐ 2026-09-21 她定：启动时给「聊过、但还没有 unlocked」的用户补一次底。
            #    起因：这类用户的网页端「他说过的那句话」**永远空着**（那张卡有内容才渲染）
            #      —— 而 `unlocked` 只在收到**私聊**时才写，功能上线前就聊过的老用户永远等不到。
            #    ⚠ 只补缺、不覆盖、不补发历史（`backfill_unlocked` 里管着，别在这儿重写一遍）。
            try:
                _filled, _skipped, _names = backfill_unlocked(init_unlocked, current_level)
                if _filled:
                    print("🎁 启动补底：%d 个用户补了跨级记录（跳过 %d）—— %s%s"
                          % (_filled, _skipped, ", ".join(_names[:6]),
                             "…" if len(_names) > 6 else ""))
                else:
                    print("🎁 启动补底：没有需要补的（跳过 %d 个）" % _skipped)
            except Exception as e:
                print("⚠️ 启动补底失败（不影响启动）：%s" % e)
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
        if QZONE_CMT_ENABLE:
            if QZONE_CMT_REPLY_ENABLE:
                asyncio.create_task(comment_event_loop())
                print("💬 她评论说说 ⇒ 他隔 %s~%s 分钟在空间回她那条（深夜等天亮；写不进去就私聊找她）"
                      % (QZONE_CMT_DELAY_MIN, QZONE_CMT_DELAY_MAX))
            asyncio.create_task(auto_comment_loop())
            print("💬 兜底：评论数涨了也会去找她（每 %s 秒查一次，隔 %s~%s 分钟，每天最多 %s 次）"
                  % (QZONE_CMT_POLL_SECONDS, QZONE_CMT_DELAY_MIN,
                     QZONE_CMT_DELAY_MAX, QZONE_CMT_MAX_PER_DAY))
        if QZONE_CMD_PREFIX:
            print("🧪 朋友圈测试指令已开启：「%s 内容」=只你可见；「%s公开 内容」=所有人可见"
                  % (QZONE_CMD_PREFIX, QZONE_CMD_PREFIX))
        await asyncio.Future()  # 永久运行

if __name__ == "__main__":
    asyncio.run(main())
