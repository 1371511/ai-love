# -*- coding: utf-8 -*-
"""
📹 视频通话（2026-10-02 · 她要的是「来电的仪式感」，**不是真视频**）

⚠⭐ 现在这份是**第 1 批 + 第 2 批 + 第 3 批 + 第 4 批**：
   第 1 批 = 振铃页那层皮（~/call/end）；第 2 批 = **绿钮真能接起来**（+ 双向输入、
   等待页一分为二、历史通话页）；第 3 批 = **语音自动连播 + 通话计时器 + 技术债收尾**；
   第 4 批 = **通话记录独立落盘**（`memory/{uid}_calls.json`，一通一条、不裁不压）
   + **单通摘要**（挂断后单独给这通写一段长期记忆和一条日记，不用凑 8 轮）。
   她插话回他在第 2 批顺手做了。
   排期见 `docs/网页端.md` 第九节「主线：视频通话」。

路由（计划 9 条，**9 条全落地**；第 4 批只给 `/call/end` 补了一个 POST 形态）：
  GET  `/call`         振铃页 = **他打来**的等待页（居中头像 + 邀请你视频通话 + 绿红两钮）
                       ⚠ 随机事件、由他主动发起，**不进菜单**（第二阶段做）
  GET  `/call/dial`    拨号页 = **你打去**的等待页（头像 + 等待对方接受邀请 + 红钮取消）
                       ⭐ 菜单「+ 视频通话」指这页；meta refresh 1.5s 自动去敲 connect
  GET  `/call/answer`  接通页 = 振铃页绿钮的过渡（接通中…），meta refresh 1s 去 connect
  GET  `/call/connect` 他「接起」⇒ 拿他的第一句话 ⇒ 303 `/call/live`（**会写 memory**）
  POST `/call/pick`    振铃页绿钮 ⇒ **不跑 LLM**，立刻 303 `/call/answer`
  GET  `/call/live`    通话页（右上名字 + 中间头像 + 居中字幕 + 输入栏 + 底部挂断）
  POST `/call/say`     她说一句 ⇒ 他的回答 ⇒ 303 回 `/call/live`（**会写 memory**）
  GET  `/call/history` 🗄 历史通话 = 每通电话的文字记录（菜单「- → 历史通话」的落点）
  GET  `/call/end`     挂断/取消 ⇒ 303 回 `/chat`（振铃页的挂断、拨号页的取消用这条）
  POST `/call/end`     🛑 通话页那颗红钮（第 4 批改的）：挂断 + **后台去写这通的摘要**
                       ⚠ 有副作用了 ⇒ 按规矩该走 POST；上面那条 GET 保持「纯跳转」不变。

⭐⭐ ADR-22：第 1 批一个字节都不写 memory；**第 2 批起开始写了**。
   ⇒ 按四步走过一遍：
      ① 引擎层入口 —— 第 2 批直接用现成的 `Rafayel_chat.get_reply`，没有造新的写盘口子。
         ⚠ **第 4 批破了这个先例**：通话记录由**本页**写（她 2026-10-02 选的方案 B），
           所以要 import 写盘模块 `Rafayel_calls`。她是**知情选的**：
           我把「引擎本来也要改（单通摘要）、方案 A 能保持零新增开口」摆出来之后，
           她仍然选了「记录由通话页自己写」。⚠ 这是**她亲口批的**，别当成我随手加的。
      ② `tools/check_static.py` 的 `WEB_WRITE_EXCEPTION`：
         第 2 批 `{"Rafayel_chat"}` ⇒ +1（9 → 10），同批 `chat.py` 摘掉 `Rafayel_daily` −1
         ⇒ 第 2 批结束时**收在 9**；
         **第 4 批**再加一个 `Rafayel_calls` ⇒ **9 → 10**（净 +1，只有这一处）。
      ③ POST 之后一律 **303**（`/call/pick`、`/call/say`、第 4 批的 `/call/end`）。
      ④ 自测：`check_static.py` 四关 + 真渲染打印。
   ⚠ 摘要那条走的是 `Rafayel_chat.summarize_call`（门面转出）⇒ **不另开口子**：
     它要写 `memory/{uid}.json`，但那个模块（`Rafayel_memory`）网页端一个名字都没 import。

⚠ 「假视频」的含义先在这儿钉死：**不调摄像头**。
   中间那张是他的头像，底下那行字是他的台词 —— 走的是跟 QQ 同一个 `get_reply`。
"""
import asyncio
import datetime
import threading
import time

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from base import (app, _page, _esc, _current_uid, CHAT_MAX_INPUT,
                  # 🖼 祁煜的头像地址（唯一出处，跟 /chat 的 `_him_av()` 同一个图案）。
                  #    ⚠ 通话页**不再**用 `_avatar_url(uid)` —— 那是她自己上传的头像，
                  #      挂在中间那块等于「画面里是她自己」（她 2026-10-02 抓的）。
                  HIM_AVATAR,
                  # 🔒 对话锁 2026-10-02 从 `page/chat.py` 下沉到底座 ——
                  #    本页要写 memory ⇒ **必须用跟 /chat 同一把锁**，否则两边并发
                  #    会把 `memory/{uid}.json` 整份覆盖 ⇒ 丢话。详见 base.py 那一节。
                  #    ⚠ 第 4 批起它多担一件事：**电话摘要**也要在这把锁里跑
                  #      （`summarize_call` 读改写的是同一份 `{uid}.json`）。
                  _chat_lock,
                  # ☎️ 通话界碑判据（跟引擎**共用同一处** `Rafayel_config.CALL_OPEN`）。
                  _is_call_open, _two_way_footer,
                  # 🔊 2026-10-02 第 3 批语音连播：**必须**跟 `/chat/voice` 路由
                  #    算同一份哈希（`_says_of` 提取台词 → `_vhash` 取短哈希）
                  #    ⇒ 只此一处实现，页面里别另抄一版（抄了就会 404）。
                  VOICE_PATH, _says_of, _vhash)
# ☎️🗄 通话记录（2026-10-02 第 4 批）。⭐ 她选的方案 B＝**记录由本页写** ⇒
#   这是本页第二条 ADR-22 开口（见文件头那一节）。⚠ 写盘一律在 `_chat_lock` 里，
#   读（`calls_last` / `calls_load`）随便。
from Rafayel_calls import (append_turn as calls_append,
                           last_call as calls_last,
                           load as calls_load,
                           open_call as calls_open,
                           pending_ids as calls_pending_ids)
# ☎️📔 `summarize_call`：单通摘要（她 2026-10-02 选「存 + 单通摘要进他的记忆」）。
#   ⚠ 走**门面**取，不直接 import `Rafayel_memory` —— 那是写盘模块，网页端引它就是
#     又开一条口子；门面这条路 `call.py` 本来就开着。
from Rafayel_chat import get_reply, summarize_call, tick_unlock
from Rafayel_config import (WEB_CALL_ENABLE, CALL_BG, CALL_HELLO, CALL_SUB_MAX,
                            # 🎲 2026-10-03：每通电话**随机一个开场话题**（洗牌轮转，
                            #    `Rafayel_config.pick_call_hello()`；话题池 `CALL_TOPICS`）。
                            #    ⚠ 之前这个函数写好了却**零调用** ⇒ 每通都发同一句
                            #      `CALL_HELLO`，模型每轮都读着同一句开场、照着上一通续写
                            #      （实测他逐字复读 9 次）。这里补上唯一的调用点。
                            #    ⚠ 话题句也以 `CALL_OPEN` 前缀开头 ⇒ `_is_call_open`
                            #      照样判得出「一通电话的开头」，旧通话不会失联。
                            pick_call_hello,
                            # 🚦 2026-10-02（方案 A）后台请求闸门：摘要是一次真 LLM
                            #    调用，跟她在 /chat 说话的前台请求抢同一个并发槽 ⇒
                            #    起线程后先让路、再排队。
                            #    ⚠ 为什么能直接 import：`Rafayel_config` 在
                            #      `check_static` 的 `WEB_WHITELIST` 里（只读、**不开口子**）
                            #      ⇒ 本页的 ADR-22 开口数**没有变**。
                            bg_slot,
                            # 🚦 报错显示（2026-10-02 下午 · 她选「两个都要」）：
                            #    上游拒了那一刻别把英文原文当成他的话渲出去 ⇒
                            #    换成人设化的降级话 + 一行小字。
                            #    ⚠ 判据与两句文案都住 config —— 四个显示点必须说
                            #      **同一句话**，而网页端不许各写一份（跟 `bg_slot` 同一个理由）。
                            is_llm_error, LLM_BUSY_SAY, LLM_BUSY_HINT)


# ============================================================
#  📞 电话图标（内联 SVG，2026-10-02 她点名「不要 ✕/✔，要电话图标」）
# ------------------------------------------------------------
# ⭐ 为什么不用 emoji（📞）：emoji 在 Windows / 手机上是**彩色字形**，
#    压在纯色圆钮上颜色不可控（红色的钮上放一张黄底电话，直接穿帮）。
#    SVG 填色跟钮走（白），一个 path 两个方向。
# ⭐ path 用的是 Material Design 的 `call` 图标形状（24×24 viewBox，广泛流通）。
#    ⚠⚠ **两颗钮的 SVG 用同一份常量、path 完全一致**，「挂断 = 转 135°」写在 CSS 里
#      （`.call-act.no svg{transform:rotate(135deg)}`），不在 HTML 上加 transform ——
#      第一版我在 path 上加 `transform` 属性，结果把整颗 `<path>` 塞进了模板的 `d` 属性
#      （拼接嵌套错位）⇒ d 值非法、红钮整个不渲染（她 06:06 截图：红钮空白）。
#      HTML 同构 + 视觉差异全归 CSS，这类错位就**结构上不可能**再发生。
#    ⚠ `aria-label` 照旧挂在钮上（图标对读屏是哑的，语义靠 label）。
_CALL_PATH = ('M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 '
              '1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1'
              '-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 '
              '2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z')
ICON_CALL = ('<svg viewBox="0 0 24 24" width="24" height="24" fill="#fff" '
             'aria-hidden="true"><path d="%s"/></svg>' % _CALL_PATH)

# ▶ 语音兜底钮里那颗三角（第 3 批 · 2026-10-02）。
#    ⚠ 为什么不用字符 `▶`（U+25B6）：这个码位在部分系统上会渲染成**彩色 emoji**
#      （跟当初电话图标不用 `📞` 是同一个坑）—— 压在深蓝半透明圆底上颜色不受控。
#      自己画一个纯 `<path>` 的三角，填色跟钮走（`currentColor`），到哪都一样。
ICON_PLAY = ('<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">'
             '<path d="M5.2 3.3l7.2 4.7-7.2 4.7z" fill="currentColor"/></svg>')


# ============================================================
#  📹 通话页专用样式（2026-10-02 第 1 批）
# ============================================================
# ⚠⭐ 类名一律 `.call-` 前缀 —— 全站 CSS 平铺在同一份 `<style>` 里，
#     `.who / .meta / .txt / .ph-top` 都撞过名（见 MEMORY.md「网页端 UI」那一节）。
# ⚠ 这一页**不长在 `.phone` 里** ⇒ `TALK_CSS` 那套「一屏 + 内部滚动」的高度体系
#    一概不适用；`.call` 自己是独立的竖排居中容器。
# ⚠⚠ `.call-act` **必须重新声明 width / margin-top**：
#     `base.py` 那条 `button{width:100%;margin-top:10px}` 会把圆钮拉成通栏一条。
#
# 🖥📱 **全屏沉浸**（她 2026-10-02 05:56 反馈「占满屏就更好」+ 手机模拟下底部差一截）
#     根因：`.call` 原来按「普通卡片」给了 `min-height:420px` —— 手机视口比 420
#     高出多少，下面就白多少。通话页本来就该像真来电那样占满整屏，修法分两层：
#     ① `.call` 高度改成视口高（100vh 兜底 + 100dvh 覆盖 —— dvh 会跟着手机地址栏
#       收放，10-01 晚 TALK_CSS 那个「手机底部空一截」的坑就是这个单位引起的，
#       这里我们要的恰恰是它的「动态」）；
#     ② **外层三层壳一起拆**：body 的 padding、`.wrap` 的 max-width:560（桌面还有
#       grid 两列）、`.main` 的边距 —— 只改 `.call` 一层的话它撑满了、四周照样露白。
#     ⚠ `:has()` 不是新技术冒险：`base.py` 桌面网格那条 `.main:has(> .footnav…)`
#       已经在用（2026-09-30 起），她的浏览器跑得动。
#     ⚠ `.wrap:has(.call){display:block}` 还顺手拆掉桌面网格 —— `/call` 传了
#       `nav=False`（全屏页不要左栏），`.wrap` 在桌面是 grid、只剩 `.main` 一个子元素
#       时会被塞进 172px 的第一列压扁（`base.py` 里 `/menu` 踩过的同一坑），
#       display:block 连根拆掉。
CALL_CSS = """
/* ⚠ 整页**不许拖动**（2026-10-02 她看示例图定的：「固定在屏幕里，不能滑动」）：
   overscroll-behavior 掐掉 iOS 橡皮筋 / 桌面回弹 —— 这是「固定」的另一半，
   光让内容不多还不够，拖拽的惯性也得禁。 */
body:has(.call){padding:0;overscroll-behavior:none}
.wrap:has(.call){display:block;max-width:none;margin:0}
.main:has(.call){margin:0}
/* ⭐⭐ `box-sizing:border-box` 是「一屏锁死」的**第 0 刀**（2026-10-02 她反馈
   「要固定在屏幕里，不能滑动」才抓到的真凶）：全站没有 `*{box-sizing}`（红线），
   默认 content-box 会把 padding **加在 100dvh 之外** ⇒ 通话页上下 26+104=130px
   全在视口外、振铃页 64px ⇒ 盒子永远比屏幕高出一截 ⇒ 整页能拖动、最底下的
   东西被推出去。⚠ 排查教训：上一轮误记成「body 的 padding 没归零」—— 其实
   `body:has(.call){padding:0}` 早就有了（上面第一行），多余高度一直来自这里。 */
.call{min-height:100vh;min-height:100dvh;color:#fff;padding:32px 16px;
      box-sizing:border-box;
      display:flex;flex-direction:column;align-items:center;justify-content:center;
      text-align:center}
/* 💓 「正在呼叫」这件事**没有 JS 也要看得懂** —— 外面套一层做脉冲，
   里面那圈边框是**静止**的：整个头像跟着胀缩会看晕。 */
.call-avwrap{border-radius:50%;animation:callpulse 1.8s ease-out infinite}
.call-av{width:88px;height:88px;border-radius:50%;object-fit:cover;display:block;
         border:2px solid rgba(255,255,255,.30);background:rgba(255,255,255,.10)}
/* ⚠ 2026-10-02 起这条**已经没人用了** —— 四个状态（振铃 / 等待 / 接通中 / 通话中）
   都改用固定的 `base.HIM_AVATAR`（祁煜那张素材图）了。它原来是「她还没传过头像」
   的兜底（半透明圆底 + 「祁」字）。
   **故意留着**：哪天 `web/assets/qiyu.jpg` 被换掉 / 删掉，这就是现成的退路
   （HTML 里写 `<div class="call-av none">祁</div>` 就能顶上）。
   ⚠ 偏偏**不加**背景色 —— CSS 里已有 `background:rgba(255,255,255,.10)`。 */
.call-av.none{display:flex;align-items:center;justify-content:center;
              font-size:32px;font-weight:500;color:rgba(255,255,255,.85)}
.call-name{font-size:22px;font-weight:500;margin:20px 0 4px}
.call-sub{font-size:12px;color:rgba(255,255,255,.62);margin:0 0 36px}
.call-acts{display:flex;gap:44px;align-items:center}
/* ⚠⚠ 两颗钮**必须结构对称**，不然永远追不齐（她 06:03 第二次截图：还差 ~9px）：
   接听是 `<form><button></form>`、挂断是裸 `<a>` —— form 这层 flex item 的高度
   被按钮里的行盒撑出一点余量，align-items:center 对的是 **form 的**中心，
   不是按钮的中心 ⇒ 绿钮永远偏高。修法：**form 自己也 display:flex**，
   让它的高度严格等于里面那颗按钮 ⇒ 两边 item 都是精确 54px，中心必重合。 */
.call-acts form{display:flex;margin:0}
/* ⚠⚠ `padding:0` + `box-sizing:border-box` 是**必须的**：
   `base.py` 那条 `input,button{padding:8px 10px}` 会漏到 `<button>` 上 ——
   54px 高再叠上下各 8px = **70px 的胖椭圆**，而旁边 `<a>` 那颗是标准 54px
   ⇒ 两颗不同大、圆心虽然重合但视觉上一高一低（她 06:00 截图抓到的）。
   `box-sizing` 一起锁死：以后谁往 `.call-act` 里加 padding，也不会再撑破 54px。 */
.call-act{width:54px;height:54px;margin-top:0;padding:0;box-sizing:border-box;
          border-radius:50%;border:none;
          display:flex;align-items:center;justify-content:center;
          font-size:20px;line-height:1;text-decoration:none;color:#fff}
/* 📞 内联 SVG 图标：svg 是 inline 元素，底下会多 3~4px 基线空隙
   （`.stk` 表情包那个同款坑），block 一刀切平。 */
.call-act svg{display:block}
/* ☎️ 挂断 = 同一颗听筒**转 135° 朝下**（微信同款挂断符号）。
   ⚠ 旋转写在 CSS 而不是 SVG 的 transform 属性 —— 两颗钮的 HTML 因此完全同构，
   拼接错位（第一版红钮空白的根因）在结构上不可能再发生；
   CSS transform 是视觉变换，听筒对角 ~34px < 钮内径 54px，不会被裁。 */
.call-act.no svg{transform:rotate(135deg)}
.call-act.ok{background:#4CAF7D}
.call-act.no{background:#C2453F}

/* ============================================================
   📹 通话进行中（第 2 批 · /call/live）
   ------------------------------------------------------------
   ⭐⭐ 竖排四段，跟她给的截图同一骨架：右上角名字+通话状态 / 中间头像 /
       下面字幕 / 最底下挂断钮（+ 输入栏，第 4 批提前落地的）。
   ⚠⚠ **高度必须锁死一屏 + `overflow:hidden`**（她 06:53 截图抓的：字幕一多，
       挂断钮被顶出屏幕外一半；13:47 又补了一刀「要固定在屏幕里，不能滑动」）
       —— `.call` 的 `min-height` 只保证「至少一屏」，内容比一屏高时整页往下
       溢出，最底下的钮就没了。修法分四刀：
       ⓪ `.call` 加 `box-sizing:border-box`（往上看基础规则那条注释）——
          不做这刀，padding 全被加在 100dvh 之外，**盒子天生比屏幕高**，
          整页能拖，后面三刀全部白搭；
       ① `.call.live` 从 `min-height` 升级成 `height`（100vh 兜底 + 100dvh 覆盖）
          + `overflow:hidden` ⇒ 这一页**永远不会比视口高**；
       ② 中间头像段 `min-height:0` —— flex 子元素默认**不许**收缩到内容以下，
          不加这条它会硬撑着把字幕和按钮挤出去（flex 经典坑）；
       ③ 字幕段给**高度上限**（32dvh）+ `overflow:hidden` 顶部裁旧 ——
          台词再多也只吃这么多，且**不给滚**（她的硬要求）。
   ⚠ `.call-sub` 的 `margin:0 0 36px`（振铃页给按钮留的空）在这儿是**负资产**
      ⇒ `.call.live` 重写 padding，别再叠一层 margin。
   ⭐ 字幕**一律居中**（她 2026-10-02 定的）：`.call` 那条 `text-align:center`
      继承下来即可，这里唯一要小心的是别给 `.call-lines` 加 `text-align:left`。
   ⭐ 渐淡用 opacity（.o0/.o1/.o2）而不是缩小字号 —— 字幕一跳另一种字号，
      眼睛会被拽走；而且 `CALL_SUB_MAX` 加减时不用重排这套设计。
   */
.call.live{height:100vh;height:100dvh;overflow:hidden;
           justify-content:flex-start;
           /* ⚠⚠⚠ 底部 104px 是给「钉在窗口底边的挂断钮」让位的
              （54 钮 + 18 底距 + 32 呼吸）：钮改成 `position:fixed` 之后它就
              不再占文档流的高度，流里最后一件东西是输入栏 —— 不留这块 padding，
              输入栏会被钮压住。配套看 `.call.live .call-acts` 那条注释。 */
           padding:26px 16px 104px;
           /* 🎛 两个微调旋钮（2026-10-02 她圈截图定的，嫌多嫌少改这一个数）：
              --call-av-lift  头像往上提多少 —— 写在头像段的底部留白上，
                              头像在「剩余空间的中心」居中，底部一让位就整体上浮；
              --call-sub-gap  字幕和输入栏/发送钮之间隔多远。 */
           --call-av-lift:14dvh;
           --call-sub-gap:40px}
/* 通话中**不再脉冲**：那是「在叫你」的意思，接起来之后再转圈会看成掉线重试。
   ⚠ 只停 `animation`，头像/静边一样不删 ⇒ 信息一个没少。 */
.call.live .call-avwrap{animation:none}
.call-top{align-self:stretch;display:flex;flex-direction:column;
         align-items:flex-end;gap:3px}
.call-top b{font-size:18px;font-weight:500;line-height:1.2}
.call-state{font-size:12px;color:rgba(255,255,255,.62)}
/* ⚠⚠ `min-height:0` 是这页的命根子：没有它，flex 子元素「宁可把兄弟挤下屏
   也不缩自己」，上面三条修法全部白搭。改这页布局先看这行还在不在。
   ⭐ `padding-bottom:var(--call-av-lift)` = 头像上移的机关：头像在 mid 里
   垂直居中，mid 底部让出一块，中心点就被顶上去（她 07:01 圈的就是这个）。 */
.call-mid{flex:1 1 0;min-height:0;display:flex;align-items:center;
          justify-content:center;padding-bottom:var(--call-av-lift)}
/* 通话页的头像比振铃页大一号（120 vs 88）—— 近景焦距的感觉。
   ⚠ 就靠「写在后面」压住上面 `.call-av{width:88px}`：两条都是单类选择器、
      同权重，后来居上。别改成 `.call-av.live-av`（双类），将来加形状会拧。 */
.live-av{width:120px;height:120px}
/* 📝 字幕区：下对齐（新话贴着输入栏）、**不许滚**（她 13:47 看示例图定的：
   里面的东西「固定在屏幕里，不能滑动」—— 游戏里的字幕从来不给滑）。
   ⭐ `margin-bottom:var(--call-sub-gap)` = 字幕上移 + 和输入栏拉开距离
   （她 07:01 定的「要隔一定的距离」）：字幕块底边锚在「输入栏上方 gap 处」，
   这个数给得越大，整块字幕抬得越高、跟输入栏离得越远。
   ⭐ min-height 88px 是三行的储备（15px×1.55 行高 = 23.25 × 3 + 两条 8px 行距
     = 85.75 ⇒ 取 88）：1~3 条时盒子高度不变 ⇒ 头像不会被一句句台词顶着跑。
   ⭐ 超上限时（长台词折行 / 以后改大 CALL_SUB_MAX）：`overflow:hidden` +
     `justify-content:flex-end` 让**最旧的从顶部溢出、被裁掉** —— 新话永远
     可见、盒子高度不变，正好是游戏的行为；以前给过 `overflow-y:auto`
     内部滚，那样字幕区自己就成了一个小滚动条，她明确不要。 */
.call-lines{width:100%;max-width:520px;min-height:88px;max-height:32dvh;
            flex:0 0 auto;overflow:hidden;
            margin-bottom:var(--call-sub-gap);
            display:flex;flex-direction:column;justify-content:flex-end;gap:8px}
.call-line{margin:0;font-size:15px;line-height:1.55;
           /* 长台词：两边留点呼吸，别顶到屏幕边上 */
           padding:0 6px}
.call-line.o0{opacity:1}      /* 最新的一句：全亮（正在说） */
.call-line.o1{opacity:.55}
.call-line.o2{opacity:.30}    /* 更早的：留个影就够了 */
/* 🫥 一句都还没有（还没拨 / 他那句还没回来） */
.call-line.none{opacity:.45}
/* ⌨️ 她的字幕行（/call/say 进来的）：跟他的同款居中，只是暗一档、小半号
   —— 分得清哪句是谁说的，又不至于抢过他的话。 */
.call-line.me{font-size:14px;color:rgba(255,255,255,.72)}
.call-line.me.o0{color:rgba(255,255,255,.85)}
/* 🚦 上游拒了那一行底下的小字（她 2026-10-02 选的「两个都要」的后半）：
   说清这是**技术原因**，别让她以为他真的不吭声了。
   ⚠⭐ 用 `display:block` 塞在**同一个 `<p>` 里面**，不加进 `.call-lines` 的 flex 流：
      加进去就多一个 flex 子元素，`gap:8px` 会把它跟那句话推开、字幕区的高度账
      也要重算一遍（那段 min-height/max-height 是精算过的，见上面那条注释）。
   ⚠ 比 `.call-line.me` 还暗一档、字号也小一号 —— 它是注解，不该抢字幕。 */
.call-note{display:block;margin-top:2px;padding:0 6px;
           font-size:12px;line-height:1.4;color:rgba(255,255,255,.45)}

/* ============================================================
   ⏱🔊 第 3 批两件（2026-10-02）
   ------------------------------------------------------------
   ⏱ 计时器：`<span class="call-timer">` 由服务端渲初值（`data-el` = 已通话秒数），
      JS 接手后每秒重算 ⇒ 两边的字号必须一致，不然接手的瞬间会跳一下。
      ⚠ `tabular-nums` 是**必须的**：不等宽数字下，「11:11」比「00:08」窄一截，
        每秒都在跳宽跳窄，右上角会一直抖。
   🔊 语音钮：**默认不显示**（`.call-vp{display:none}` + HTML 上带 `hidden`），
      只有 JS 发现「浏览器不让自动播」时才加 `.need` 把它亮出来
      ⇒ **没 JS 就没有这颗钮**，不会留一个戳了没声音的死图标
      （跟 `/chat` 里那颗小喇叭 `.spk` 一个口径）。
      ⚠ 平时（能自动播的时候）它一直是藏着的 —— 通话界面上一排小图标会很杂，
        而声音本身就是「正在说」的提示，不需要再来个按钮抢戏。
   ============================================================ */
/* ⚠ `margin-left` 是必须的：HTML 是 `<span class="call-state">通话中<span …>` —
   两个 inline 元素**中间没有空白字符**，不留这个间距就会连成「通话中00:32」。 */
.call-timer{margin-left:5px;font-variant-numeric:tabular-nums;letter-spacing:.02em}
.call-vp{display:none}
/* ⚠⚠ 这条 `display:inline-flex` **会盖掉 HTML 上的 `hidden`**（作者样式 > 浏览器
   默认样式），所以「要不要亮」这件事**只由 JS 加不加 `.need` 决定**，
   `hidden` 属性只是给「JS 还没跑起来」那一瞬兜底。两处别只改一处。 */
.js .call-line.has-v .call-vp.need{display:inline-flex;align-items:center;
  justify-content:center;width:22px;height:22px;margin-left:7px;
  border-radius:50%;background:rgba(255,255,255,.20);color:#fff;
  text-decoration:none;vertical-align:middle}
.js .call-line.has-v .call-vp.need svg{display:block}

/* 🎛 底部一坨：输入栏 + 挂断钮（2026-10-02 她点名「发消息的框和发送键也没有」
   ⇒ 原排第 4 批的输入栏提前到今天）。打包成 `.call-dock` 才好对齐：
   输入条全宽、挂断钮居中，共用同一列宽。 */
.call-dock{width:100%;max-width:520px;flex:0 0 auto;
           display:flex;flex-direction:column;align-items:center;gap:16px}
/* 🛑 通话页的挂断钮：**钉在可视窗口底边**（2026-10-02 她截图「挂电话的按钮被
   吞了一半」）。
   ⚠⚠ 为什么非 `fixed` 不可：`height:100dvh` 量的是「视口高」，但 `.call` 自己
   是从 `.wrap > .main` 里长出来的 —— 祖先只要有一点点上边距/内边距，这一屏的
   盒子就被整体往下推十几~几十 px，排在最末尾的钮正好被推出可视区
   （她截到的是 54px 的钮只剩上半截 ⇒ 祖先偏移约 27px）。
   **跟祖先算账永远算不赢**：手机地址栏伸缩、模拟器裁切、dvh 与 vh 差，
   每一次都能悄悄改掉这个偏移量，改一次赌一次。
   ⇒ `fixed` 直接锚**可视窗口**：窗口在哪，钮就在哪，不再经过任何祖先。
   ⭐ 只钉 `.call.live` 里这一颗（振铃页那两颗仍在流里，那页没这个偏移问题，
      而且接起/挂断要跟着头像走才好看）；`left:0;right:0` + `justify-content`
      把它原来的「水平居中」手工复原。 */
.call.live .call-acts{position:fixed;left:0;right:0;bottom:18px;
                      display:flex;justify-content:center;z-index:20}
.call-inputrow{display:flex;gap:8px;width:100%;margin:0}
/* ⚠⚠ 输入框**必须重声明 padding/height**：`base.py` 的
   `input,button{padding:8px 10px}` 会漏上来把 40px 撑破（`.call-act` 同坑）。
   ⚠ 字号**必须 ≥16px**：iOS Safari 对字号 <16px 的输入框，聚焦时暴力放大整页
   —— 15px 就是那个坑，16px 才安静。 */
.call-input{flex:1;min-width:0;height:40px;margin:0;padding:0 14px;
            box-sizing:border-box;border:none;border-radius:20px;
            background:rgba(255,255,255,.14);color:#fff;font-size:16px}
.call-input::placeholder{color:rgba(255,255,255,.45)}
.call-input:focus{outline:1px solid rgba(255,255,255,.35)}
/* ⚠ `width:auto` 是必须的：`base.py` 那条 `button{width:100%;margin-top:10px}`
   会把发送钮拉成通栏（跟 `.call-act` 当年同一个坑）。 */
.call-send{flex:0 0 auto;width:auto;height:40px;margin:0;padding:0 18px;
           box-sizing:border-box;border:none;border-radius:20px;
           background:rgba(255,255,255,.22);color:#fff;font-size:15px}
/* ============================================================
   📲 拨号页（/call/dial · **你打去**）—— 仿她给的微信等待界面（2026-10-02 定），
   只保留三样：**头像 / 「等待对方接受邀请」/ 红色取消钮**（麦克风、扬声器都不要）。
   ⭐⭐ 这一页顺便把 LLM 的等待**藏进剧情里**：meta refresh 1.5s 后浏览器去敲
      `/call/connect`（那次请求里跑十几秒的 LLM），期间**本页画面一直挂在屏幕上**
      （浏览器在新页面响应回来之前会保留旧页面的渲染）⇒ 「等待对方接受邀请」
      正好把这十几秒演掉 —— 之前「绿钮按下去没反应」的死机感就没了。
   ⭐ 骨架沿用通话页：`call-mid` 段 flex:1 居中头像（底部空间被文字+按钮吃掉，
      头像自然落在偏上 1/3 处，跟微信一个站位）；一屏锁死四件套从 `.call` 继承，
      这里一行都不用重写。
   ⭐ **不脉冲**：脉冲是「在叫你」的语义（振铃页专用）；自己打出去的等待是静的，
      跟微信一样。也没有名字 —— 她只点名三样东西。
   */
.call.dial .call-avwrap{animation:none}
.dial-av{width:120px;height:120px}   /* 跟通话页的 live-av 同尺寸（近景焦距） */
.dial-wait{font-size:13px;color:rgba(255,255,255,.62);margin:0 0 9dvh}
.call.dial .call-acts{margin:0 0 5dvh}

@keyframes callpulse{
  0%{box-shadow:0 0 0 0 rgba(255,255,255,.30)}
  70%{box-shadow:0 0 0 18px rgba(255,255,255,0)}
  100%{box-shadow:0 0 0 0 rgba(255,255,255,0)}
}
/* 🛑 晕动症 / 关掉动效的人：光圈**不跳**，但头像在、名字在、字也在
   ⇒ 信息一个没丢（改 `display:none` 才是真没收）。 */
@media (prefers-reduced-motion:reduce){
  .call-avwrap{animation:none}
}
"""


# ============================================================
#  ⏱🔊 通话页的脚本（第 3 批 · 2026-10-02）
# ------------------------------------------------------------
# ⚠⚠ 渐进增强底线（跟 `TALK_JS` / `SMS_JS` 同一个口径）：**这一整段没有也不影响通话** ——
#    输入栏是普通表单 POST、挂断是普通链接，她照样能说完一句看他回一句；
#    没有它只是少两样「更顺」的东西：①右上角那个计时器不跳 ②他的台词不会自动念。
#    ⇒ 别把任何**功能**往这段里搬（搬进去 = 断网/老浏览器就废了）。
# ⚠ 语音路由用 `__VP__` 占位再替换，不写成字面量 `/chat/voice`：路由一旦改址，
#   这里会跟着变；写成字面量的话，改路由那天这里是**静默 404**（页面正常、就是没声）。
# ⚠ 也不用 `%` 格式化拼它 —— 这段 JS 里有 `s % 60` 这种取模，`%` 会跟格式化符打架。
# ============================================================
CALL_JS = r"""
<script>
(function () {
  // ⭐ 老规矩：先把「有 JS」这件事打在根上（CSS 里 `.js …` 那几条才认）。
  document.documentElement.classList.add('js');
  var VP = '__VP__';

  // ⏱ 通话计时器
  //    —— 服务端已经把初值渲进 `data-el`（打开这一页时已通话几秒），这里只管往上加。
  //    ⚠⭐ 用「本地基准 + 已经过多少秒」而不是「当前时间 - 服务器给的时间戳」：
  //       手机时钟跟服务器差几分钟是常事，两个钟相减会算出个离谱的时长
  //       （甚至负数）。本地基准只依赖「一秒钟大概多长」，跟绝对时间无关。
  var el = document.querySelector('.call-timer');
  if (el) {
    var base = parseInt(el.getAttribute('data-el') || '0', 10) || 0;
    var t0 = Date.now();
    var two = function (n) { return (n < 10 ? '0' : '') + n; };
    var tick = function () {
      var s = base + Math.round((Date.now() - t0) / 1000);
      if (s < 0) { s = 0; }
      var h = Math.floor(s / 3600);
      el.textContent = (h ? h + ':' + two(Math.floor((s % 3600) / 60))
                          : two(Math.floor(s / 60))) + ':' + two(s % 60);
    };
    tick();                       // 先算一次：别等满一秒才把数字摆正
    setInterval(tick, 1000);
  }

  // 🔊 语音连播（她 2026-10-02 定的：**只播他的台词**）
  //    ⭐ 播哪一句：页面上**最后那一条**他的字幕（服务端给它挂了 `data-v`，
  //       值是这个 message 里**每段气泡**的语音短哈希、空格隔开）。
  //       ⇒ 一次通话里他一句话分了好几个气泡时，**从前往后连着放完**，
  //         不用她一句句去点 —— 「连播」说的就是这个。
  //       ⚠ v1 只播**最新**那句（不是把整通电话从头念一遍）。想听更早的，
  //         去「历史通话」那页翻文字；要「从头播一遍」的话说一声，这里改三行。
  var line = document.querySelector('.call-line[data-v]');
  if (!line) { return; }
  var hs = (line.getAttribute('data-v') || '').split(' ');
  var btn = line.querySelector('.call-vp');

  var play = function (i) {
    if (i >= hs.length || !hs[i]) { return; }
    var au = new Audio(VP + '?h=' + hs[i]);
    var go = function () { play(i + 1); };
    // 放完停一下再接下一段，不然几句挤成一坨、听不出是在哪断的
    au.addEventListener('ended', function () { setTimeout(go, 240); });
    // ⚠ 这一段放不出来（404 = 已经滚出历史 / 503 = TTS 没配）⇒ **跳过**它继续下一段，
    //   绝不能让它卡住整条链（卡住的话后面几句永远轮不到）。
    au.addEventListener('error', go);
    var p = au.play();
    if (p && p.catch) {
      p.catch(function () {
        // ⚠ 只该在**浏览器不让自动播**（NotAllowedError，手机上很常见）时亮钮。
        //      其它失败（没配 TTS）别亮 —— 亮了她点一下还是没声音，白骗她。
        if (btn) { btn.hidden = false; btn.classList.add('need'); }
      });
    }
  };

  if (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();          // 别让浏览器真的去下载那个 wav（整页会被顶掉）
      btn.hidden = true;
      btn.classList.remove('need');
      // 她这一下点击 = 浏览器认可的「用户手势」⇒ 这次一定放得出来
      play(0);
    });
  }

  play(0);
})();
</script>
""".replace("__VP__", VOICE_PATH)


def _bg_css():
    """
    🎨 把她挑的背景色拼进 CSS（而不是把色值写死在 `CALL_CSS` 字串里）。

    ⭐ 为什么要多这一跳：`CALL_CSS` 是**纯字符串**，里面没法引用 Python 常量；
       在这里拼一小段追加在后 ⇒ 换背景改 `Rafayel_config.CALL_BG` 就够，
       不会出现「改了配置但页面上没变」那种追半天的 bug。

    ⭐ 背景色要**同时给两个元素**：`.callbg`（页面主块）+ `body:has(.call)`。
       全屏之后看不见 body 才不是理由 —— 手机上「橡皮筋回弹」（iOS 过滚）和
       桌面滚动回弹露出的都是 **body 的底色**，深色页露一圈白边非常刺眼。
       所以 body 也刷成同一个 CALL_BG，回弹露出来的还是同一种深蓝。

    ⚠ `body` 的内边距归零、整页禁止拖动（`padding:0;overscroll-behavior:none`）
       这类**结构性**重置住在上面 `CALL_CSS` 开头第一行，这里只管**颜色** ——
       别在两个地方各写一份 `body:has(.call)`（2026-10-02 我就把 `padding:0`
       重复写过一遍，还顺着错误线索把「按钮被吞」的根因记成了 body padding；
       真凶是 `.call` 缺 `box-sizing:border-box`，pad 全被加到 100dvh 之外）。
    """
    return (".callbg{background:%s}" % CALL_BG +
            "body:has(.call){background:%s}" % CALL_BG)


# ============================================================
#  🚦 报错行的显示口径（2026-10-02 下午 · 她选「两个都要」）
# ------------------------------------------------------------
# ⭐⭐ 背景：上游把请求拒了（Kimi Tier0 的并发 1 / RPM 3）时，`get_reply` 返回的是
#    `（AI 接口出错：Your account org-… max RPM: 3 …）` 这种**引擎替上游转述的报错**，
#    而她 16:4x 的截图抓到的正是它 —— 一行英文顶在通话字幕上。
# ⇒ 分工（她定的）：
#    · **通话记录 `{uid}_calls.json` 照旧存真实报错** —— 沿用原话留档那套口径
#      （记录要忠实，排查时得看得到上游原话）；
#    · **只在渲染这一层换皮**：人设化的降级话（`LLM_BUSY_SAY`）
#      + 一行小字（`LLM_BUSY_HINT`）⇒ 这就是她说的「两个都要」。
# ⚠ 判据与文案**都住 config**：四个显示点（本页字幕、本页历史页、聊天气泡、QQ 消息）
#   必须说同一句话，而网页端不许各写一份 ⇒ config 在白名单里，**零新增 ADR-22 开口**。
# ⚠ 本页两处（字幕 / 历史页）都用下面这两个小函数，**别再各写一遍判据**。
# ============================================================
def _disp(t):
    """一句话**显示时**该长什么样：是引擎报错 ⇒ 换成人设化的降级话。"""
    return LLM_BUSY_SAY if is_llm_error(t) else t


def _note_of(t):
    """那句话底下要不要跟一行小字（只有报错行要；正常台词返回 `""`）。"""
    return LLM_BUSY_HINT if is_llm_error(t) else ""


def _hms(sec):
    """
    秒 ⇒ `MM:SS`（满一小时就 `H:MM:SS`）。第 3 批的计时器初值用它。

    ⚠ 服务端只渲**初值**，JS 接手后每秒自己重算（两边的格式必须一致，
      不然接手的瞬间会跳一下 —— JS 那份写在 `CALL_JS` 里，改这里要一起改）。
    """
    try:
        s = int(sec)
    except Exception:
        s = 0
    if s < 0:
        s = 0
    if s >= 3600:
        return "%d:%02d:%02d" % (s // 3600, (s % 3600) // 60, s % 60)
    return "%02d:%02d" % (s // 60, s % 60)


def _spawn_call_summary(uid, call_ids):
    """
    ☎️📔 把这通电话摘进他的长期记忆 + 日记（**后台线程，不等**）。返回是否起了线程。

    `call_ids` = 要摘的那几通的 id（**早的在前**）—— 由调用方钉死，
    不靠「最早那通」猜（摘要是异步的，跑起来时列表末尾可能已经又开了一通新的）。

    ⭐ 第 4 批之前，「一通电话说了什么」是靠**扫历史里的哨兵**算出来的
      （`_call_window()`：找最后一条以 `CALL_HELLO` 开头的消息，它后面就是这一场）。
      那个做法有两个硬伤，正是她 2026-10-02 要修的东西：
        ① 历史只有 12 轮，聊久了一通电话的开场白先被裁 ⇒ 哨兵找不到
           ⇒ 字幕退成「只看最后一句」，**一整通电话在页面上只剩一句**；
        ② 它只能「看」，不能「留」—— 裁掉就真没了。
      ⇒ 现在记录独立落盘（`memory/{uid}_calls.json`），通话页与历史页都读那一份。

    ⚠⚠ 必须包 `_chat_lock(uid)`：`summarize_call` 读改写 `memory/{uid}.json`，
      跟 `/chat/send`、`/call/say`（`get_reply` 的落盘）是**同一份文件** ⇒
      不互斥就会被整份覆盖（`MEMORY.md` 架构第一节那条红线）。
      ⚠ 锁由**本页**持，不是引擎：那把锁住在 `base.py`（进程级、全站共用一把），
        引擎不认识它，而这个页面本来就拿着它。
    ⚠ 为什么起线程：一次摘要是真的 LLM 调用（5~10 秒）。挂断钮上**不能等它** ——
      她点完挂断必须立刻回 `/chat`，日记随后自己长出来
      （跟情绪模块 `spawn_update` 同一套路：只起线程、不等结果）。
    ⚠ 一个一个摘、**早的在前**：`long_term_summary` 是按顺序追加的，
      乱序会让他的长期记忆时间线倒带（见 `Rafayel_calls.pending_call` 的说明）。
    ⚠ 空列表 ⇒ **不起线程**（挂断是高频动作，没事就别起线程、别刷日志）。
    """
    if not call_ids:
        return False

    def _work():
        for cid in call_ids:
            try:
                # 🚦 让路 + 排队（2026-10-02 方案 A）。
                #    ⚠⚠ 必须在 `_chat_lock` **之前**进闸：让路那一步是个 sleep
                #       （1~2.5 秒），持着对话锁睡会把她下一条消息一起拖住。
                #       闸门只管「打模型这一刻」，不负责锁 memory。
                with bg_slot():
                    with _chat_lock(uid):
                        if not summarize_call(uid, call_id=cid):
                            break        # 摘不动（模型挂了 / 已被摘过）⇒ 停下，下次再补
            except Exception as e:
                print("[📹] 电话摘要异常（不影响对话，下次再补）：%s" % e)
                break

    try:
        threading.Thread(target=_work, name="call-summary-%s" % uid,
                         daemon=True).start()
        return True
    except Exception as e:
        print("[📹] 电话摘要起线程失败（这通不摘，下次再补）：%s" % e)
        return False


@app.get("/call", response_class=HTMLResponse)
async def call_ring(request: Request):
    """
    📞 振铃页：居中头像 + 「祁煜 / 邀请你视频通话…」+ 绿红两个圆钮。
    ⚠ 跟 `/chat` 一样要登录 —— `_current_uid` 拿到空就踢回 `/`。
    ⚠ `nav=False`：**全屏页不要左栏**（桌面左栏是白底卡片，压在深蓝上非常怪）；
      代价是桌面 grid 少一个子元素 —— 已在 `CALL_CSS` 顶部用 `.wrap:has(.call)`
      {display:block} 连根拆掉（`base.py` 里 `/menu` 踩过的那坑）。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if not WEB_CALL_ENABLE:
        return RedirectResponse("/chat")
    # 🖼 中间那块是**他**（这通电话里说话的人）⇒ 挂祁煜的素材图。
    #    ⚠ 2026-10-02 之前这里用的是 `_avatar_url(uid)` = **她自己上传的头像**，
    #      等于来电画面里放着她自己的脸。她指出后统一成 `base.HIM_AVATAR`。
    avh = '<img class="call-av" src="%s" alt="祁煜">' % HIM_AVATAR
    body = (
        '<div class="call callbg">'
        '<div class="call-avwrap">%s</div>'
        '<div class="call-name">祁煜</div>'
        '<div class="call-sub">邀请你视频通话…</div>'
        # ☎️ 按钮顺序 = **左红（挂断）右绿（接听）**（她 2026-10-02 06:00 定的，
        #   跟微信来电一个布局；我第一版粘反成左绿右红，她截图抓的第二个问题）。
        #   图标 = 同一份听筒 SVG（ICON_CALL），挂断的旋转在 CSS（.call-act.no svg）。
        '<div class="call-acts">'
        '<a class="call-act no" href="/call/end" aria-label="挂断">%s</a>'
        '<form method="post" action="/call/pick">'
        '<button class="call-act ok" type="submit" aria-label="接听">%s</button>'
        '</form>'
        '</div></div>' % (avh, ICON_CALL, ICON_CALL))
    return _page(body, title="祁煜来电", css=CALL_CSS + _bg_css(), nav=False)


def _waiting_page(wait_text, title, next_url, delay):
    """
    📲 等待小页的**公共模子**：拨号页（等待对方接受邀请）和接通页（接通中…）
    长得一模一样 —— 她定的三样：**头像 / 一行状态字 / 红色取消钮**，只有文案不同。
    ⭐⭐ 零 JS 自动前进：`<meta http-equiv="refresh">` 过 `delay` 秒后浏览器去敲
       `next_url`；那边要跑十几秒的 LLM，期间**本页画面一直挂在屏幕上**
       （浏览器在新响应回来前保留旧渲染）⇒ 等待被这一行字演掉，没有死机感。
    ⚠ 取消钮指 `/call/end`（纯跳转回 /chat，无副作用）—— LLM 还没跑完就点它
      也一样能走；连接中期取消最多让他白说一句，无害。
    ⚠ 2026-10-02 去掉了 `uid` 参数：中间那张图改成固定的 `HIM_AVATAR` 之后，
      本函数不再需要知道「她是谁」了（原来那个参数只喂给 `_avatar_url`）。
    """
    # 🖼 等待页中间也是**他** —— 等的是他接起来，所以挂祁煜的素材图。
    avh = '<img class="call-av dial-av" src="%s" alt="祁煜">' % HIM_AVATAR
    body = (
        '<div class="call callbg dial">'
        '<div class="call-mid"><div class="call-avwrap">%s</div></div>'
        '<div class="dial-wait">%s</div>'
        '<div class="call-acts">'
        '<a class="call-act no" href="/call/end" aria-label="取消">%s</a>'
        '</div></div>' % (avh, wait_text, ICON_CALL))
    meta = '<meta http-equiv="refresh" content="%s;url=%s">' % (delay, next_url)
    return _page(body, title=title, css=CALL_CSS + _bg_css(), nav=False,
                 head_extra=meta)


@app.get("/call/dial", response_class=HTMLResponse)
async def call_dial(request: Request):
    """
    📲 拨号页：**你打去**的等待页（2026-10-02 她定的第二种等待 —— 仿微信界面）。

    ⭐ 跟 `/call`（他打来）是两回事：这页只有**头像 + 「等待对方接受邀请」+
      红色取消钮**三样，麦克风/扬声器/名字全不要（她原话「只需要保留这三个东西」）。
    ⭐⭐ **他什么时候接？—— meta refresh 1.5s 后自动去敲 `/call/connect`**，
       LLM 的等待全被「等待对方接受邀请」演掉（机制见 `_waiting_page`）。
    ⭐ 菜单「+ 视频通话」指的就是这页（她 14:09 拍板：入口 = 你打去，她很满意）；
      `/call`（他打来）是**随机事件**，由他主动发起，**不进菜单**（第二阶段做）。
    ⚠ `nav=False`：跟振铃页同一个理由（全屏沉浸页不要桌面左栏白卡片）。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if not WEB_CALL_ENABLE:
        return RedirectResponse("/chat")
    return _waiting_page("等待对方接受邀请", "正在拨号", "/call/connect", "1.5")


@app.get("/call/answer", response_class=HTMLResponse)
async def call_answer(request: Request):
    """
    ☎️ 接通中：**他打来**那页按下绿钮后的过渡页（2026-10-02 她拍板「要」）。

    ⭐ 长相跟拨号页同一个模子（`_waiting_page`），只有文案是「接通中…」——
       绿钮 POST `/call/pick` 只做一跳 303 到这儿（**不跑 LLM**，所以绿钮
       按下去**立刻**有反应），真正等 LLM 的是 meta refresh 落点 `/call/connect`，
       而那十几秒里屏幕上挂着的正是这页「接通中…」⇒ 干等死机感没了。
    ⚠ 接通中**仍然可以反悔**：红色取消钮照常能点（→ /chat）。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if not WEB_CALL_ENABLE:
        return RedirectResponse("/chat")
    return _waiting_page("接通中…", "接通中", "/call/connect", "1")

def _call_ctx(uid, is_open, text):
    """☎️ 交给引擎的「这一通电话的上下文」——只给这一通，不给聊天窗（2026-10-02 晚修复读）。

    ⚠⚠ **最后一条永远是「她刚说的这句」（`text`）** —— 这是本函数唯一的不变量。

    为什么必须显式接上：`get_reply(..., wire=False)` 时她这句**不进聊天窗**，
      而引擎在「给了 ctx」之后 **只用 ctx 当历史**（`_hist = list(ctx)`）
      ⇒ 不接在这儿，模型**整份 payload 里都找不到她刚说了什么**，
        只能接着**上一轮**往下答 —— 她看到的就是「怎么又是刚接通那句」。
    ⚠ 为什么不直接读 `calls_last()` 拿这句：界碑那一下走 `calls_open()`
      （**不入 `lines`**），插话那一下的 `calls_append(...)` 又在 `get_reply`
      **之后**才写 ⇒ 读记录**永远差一句**。只能由调用方把 `text` 递进来。
    ⚠ 这个分支**绝不返回 `None`** —— `None` 的语义是「交给引擎走默认（聊天窗）」，
      通话里退回聊天窗 = 又把聊天记录灌进来，正是这次要修的病。
    """
    if is_open:
        # 刚拨通：这一通还没说过话，她这句（哨兵）就是第一句，它自己兼「界碑」。
        return [{"role": "user", "content": text}] if text else []
    try:
        cur = calls_last(uid) or {}
    except Exception as e:
        print("[📹] 读通话记录失败（这一轮退回聊天窗）：%s" % e)
        return None                  # 拿不到 ⇒ 交给引擎走默认
    # 🎲 首句用**这一通真实的那句开场白**（2026-10-03 随机话题池上线后）：
    #   开场白不再固定是 `CALL_HELLO` 了，写死它等于给模型一句假界碑 ——
    #   模型会当成"这一通的开头是『喂？』"，跟你真实说的那句对不上。
    #   ⚠ 老记录没有 `hello` 字段（界碑当年不入档）⇒ 回落 `CALL_HELLO`，
    #     **旧通话照样读得出来**，不会因为缺字段而空掉。
    _hello = cur.get("hello") or CALL_HELLO
    out = [{"role": "user", "content": _hello}]   # 首句是界碑，读起来才像一通电话
    for m in (cur.get("lines") or []):
        c = m.get("content")
        if is_llm_error(c):
            continue                 # 上游报错占位不是他说的话，别喂回去
        out.append({"role": m.get("role"), "content": c})
    if text:
        out.append({"role": "user", "content": text})   # ←⚠⚠ 她刚说的这句（不变量）
    return out or None


def _turn(uid, text):
    """
    💬 跑一轮通话对话（同步、**持锁**）⇒ 调用处丢 `asyncio.to_thread`。

    ⭐ 接听（`/call/pick`）和插话（`/call/say`）走的是**同一个函数** ——
       对引擎来说没有任何区别，都是「她说了一句 → 他回一句」；
       接起那一下只是 `text` 恰好是哨兵 `CALL_HELLO` 而已。

    ⭐⭐ 锁必须是 `base._chat_lock` —— 跟 `/chat` **同一把**。
       电话和聊天页同时开、她两头各说一句 ⇒ 两条 `get_reply` 并发，
       各自持一份 `cm` 内存副本，后写的那份把先写的**整份覆盖** ⇒ 直接丢话。
       （这条坑见 base.py 那一节的注释，本仓库踩过一次。）

    ⭐ 第 3 批补上 `tick_unlock(uid)`（跨级解锁记账）—— **跟 `/chat/send` 完全同序**：
       `get_reply` 之后、**仍在这把锁里**（记账要读刚写完的 `{uid}.json`，
       出了锁就可能读到别的请求改到一半的状态）。
       ⚠ 之前那版注释写的是「已知取舍：这里不记账」，代价是
         「通话里升了级，`/affinity` 的解锁条数暂时不涨」，看着像坏了；
         现在 `tick_unlock` 已经下沉进引擎、由 `Rafayel_chat` 门面转出
         ⇒ 两页共用同一个实现，取舍取消。
       ⚠ 顺序别调换：先 `get_reply`（写盘）+ 再记账（读盘算级数）。

    ⭐ 第 4 批（2026-10-02）在这儿补了**通话记录**：这一轮的「她一句 + 他一句」
       原样进 `memory/{uid}_calls.json`。写盘是**她选的方案 B**（本页自己写），
       见文件头那一节。
    """
    # ☎️ 这一轮是不是「一通电话的开头」—— 判据跟引擎 / 历史通话页**共用一处**
    #    （`Rafayel_config.CALL_OPEN`）。⚠ 在 `get_reply` **之前**算好：
    #    下面写记录时还要用它（那会儿 `text` 还在，但先算出来更好读、也少一个变量捕获）。
    is_open = _is_call_open("user", text)
    ctx = _call_ctx(uid, is_open, text)    # ←⚠ `text` 必须传：她这句不进聊天窗，只能从这儿进 ctx
    with _chat_lock(uid):
        reply = get_reply(text, uid, ctx=ctx, wire=False) or ""
        tick_unlock(uid)
        # ☎️🗄 通话记录（第 4 批）：**留在这把锁里** ——
        #    「她说了什么 / 他回了什么」是**一对**，两个请求交叉（她两条并排发）
        #    就会写成「她 A + 他 B'」这种对不上的记录。锁跟 `get_reply` 同一把，
        #    天然把这一对圈在一起。
        #    ⚠ 自己包 try：记录写挂了**绝不能**把这一通电话的回复也带崩 ——
        #      她那边只是少一条记录，不是「电话打不通」。
        try:
            _now = time.time()
            if is_open:
                # 界碑：新开一通。界碑那句话本身**不入 lines**（免得刷屏），
                #   但要存进 `hello` —— 插话时 `_call_ctx` 要拿它当首句界碑。
                calls_open(uid, ts=_now, hello=text)
            else:
                calls_append(uid, "user", text, ts=_now)
            calls_append(uid, "assistant", reply, ts=_now)
        except Exception as e:
            print("[📹] 通话记录写入失败（不影响这一轮对话）：%s" % e)
    return reply


async def _answer(uid):
    """
    ☎ 他「接起」那一下的**唯一执行体**：拨号页（`/call/dial`）和接通页
    （`/call/answer`）的 meta refresh 最终都落到 `/call/connect`，走这一套 ——
    🎲 `pick_call_hello()` 挑一句开场话题给引擎 → 拿回他的第一句话 → 303 去通话页。

    ⭐ 发出去的是**以 `（按下接听）` 开头**的一句（话题池 12 句轮转，挑不到才回落
       `CALL_HELLO`）而不是空串 —— 它同时充当「给他的语境」和「给页面的哨兵」，
       两个身份见配置里那条注释。
    ⚠ 这一下会**慢**（十几秒很正常）：底下是一次真的 LLM 调用。
       两条等待路径的屏幕上都挂着状态字（「等待对方接受邀请」/「接通中…」），
       浏览器在新响应回来前保留旧页面画面 ⇒ 等待全部被演掉，没有干等。
    ⚠ 异常只吞不抛：引擎炸了最多这场通话没台词，通话页照样开得出来、
       挂断钮照旧在 ⇒ 她不会被困在一个打不出去也挂不掉的页面上。

    ⭐ 第 4 批（2026-10-02）在这儿加了**补摘**：拨号这一跳顺手把「上一通还没摘完的
       电话」结掉。这是单通摘要的**兜底触发**（主触发是挂断那颗红钮的 POST）——
       她上一通要是直接关掉浏览器、没走挂断，那通就不会被摘；等她下次拨号时由这儿补上。
    """
    try:
        # 🎲 **每通随机一个开场话题**（2026-10-03 · `pick_call_hello()` 洗牌轮转，
        #    池子在 `Rafayel_config.CALL_TOPICS`）。之前这里发的是写死的 `CALL_HELLO`
        #    ⇒ 每通都是同一句开场，模型每轮都读着它、照上一通续写
        #    （实测他那句「查岗还是邀功」逐字复读了 9 次）。
        #    ⚠⚠ **先挑一句再进 `_turn`** —— `_turn` 里 `hello=text` 要把它存进记录，
        #    插话时 `_call_ctx` 靠它拼首句界碑；挑在 `_turn` 外面就只会拿到那句。
        #    ⚠ 挑句**必须成功**：挑不到（池子空/全写错前缀）时回落 `CALL_HELLO`，
        #      那仍是合法界碑，接通不会挂。
        #    ⚠ 这是**模块级洗牌袋**（`_TOPIC_BAG`）⇒ 12 通之内不重样；
        #      进程重启会重新洗一次，这是预期行为（重启本来就该换换口味）。
        _hello = pick_call_hello() or CALL_HELLO
        await asyncio.to_thread(_turn, uid, _hello)
    except Exception as e:
        print("[📹] 接通失败（不影响再拨一次）：%s" % e)
    # ☎️📔 补摘（见 docstring）。⚠ 必须在 `_turn` **之后**：得先把新这通开出来，
    #    「末尾那通」才是刚拨通的这一通，前面那些才是没摘完的旧账。
    #    ⚠⚠ `exclude_last=True` 是关键 —— 此刻列表末尾是**还活着的这一通**，
    #      摘它就是把人家说到一半的电话结掉。
    try:
        _old = calls_pending_ids(uid, exclude_last=True)
    except Exception as e:
        _old = []
        print("[📹] 读通话记录失败（这次不补摘）：%s" % e)
    if _old:
        _spawn_call_summary(uid, _old)
    return RedirectResponse("/call/live", status_code=303)


@app.get("/call/connect")
async def call_connect(request: Request):
    """
    📲 拨号页 meta refresh 的落点：他「接起」了。

    ⚠⚠ **GET 带副作用**（会写 memory）—— 按规矩副作用该走 POST，
       但 `<meta refresh>` 只会 GET，而「零 JS 自动前进」正是这一页的命根子
       ⇒ 刻意开的小口子，**只此一条**：别的写动作照走 POST。
       它的行为跟 `/call/pick` 完全一致（共用 `_answer`），只是触发方式不同。
    ⚠ 303 回 `/call/live`：她若在等待途中手滑刷新，connect 可能被敲两次 ——
       两次各跑一轮「喂？」⇒ 他回两句开场白。罕见且无害（聊多了两句而已），
       不为它加去重状态。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if not WEB_CALL_ENABLE:
        return RedirectResponse("/chat")
    return await _answer(uid)


@app.post("/call/pick")
async def call_pick(request: Request):
    """
    ☎ 振铃页（**他打来**，随机事件、不进菜单）按下那颗绿钮 —— 她拍板「我接」。

    ⭐⭐ 这里**不跑 LLM**：立刻 303 到「接通中…」小页（`/call/answer`），
       由它 meta refresh 去敲 `/call/connect`（真正等 LLM 的地方）。
       绿钮按下去 0.1 秒内就有画面变化，干等死机感不存在了。
    ⚠ 拿到 303 ⇒ 浏览器认「这一跳结束了」，再刷新不会弹「表单要重新提交吗」。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if not WEB_CALL_ENABLE:
        return RedirectResponse("/chat")
    return RedirectResponse("/call/answer", status_code=303)


@app.post("/call/say")
async def call_say(request: Request, text: str = Form("")):
    """
    ⌨️ 通话里她说一句 ⇒ 他的回答 ⇒ 303 回 `/call/live`。

    （她 2026-10-02 看第 2 批效果时点名「发消息的框和发送键也没有」⇒
      原排第 4 批的双向输入**提前**到今天做了；语音连播仍在下一批。）

    ⭐ 收参口径**照抄 `/chat/send`**：`Form("")` 显式给默认 → strip → 空串不进引擎
      → 超 `CHAT_MAX_INPUT` 截断（两个页面用同一把尺子，常量已下沉 base）。
    ⚠ 未登录那条**必须 303 不能 307**：307 会带着 POST 方法重发到 `/`，
      而 `/` 只收 GET ⇒ 未登录的人看到 405 而不是登录页（`/chat/send` 踩过的坑）。
    ⚠ 这一下同样要等 LLM 十几秒：输入框**不发禁用态**（那是第 3 批跟 JS 一起的事），
      但表单本身是零 JS 保底路 —— 没有 JS 也发得出去。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/", status_code=303)
    if not WEB_CALL_ENABLE:
        return RedirectResponse("/chat", status_code=303)
    text = (text or "").strip()
    if not text:
        return RedirectResponse("/call/live", status_code=303)
    if len(text) > CHAT_MAX_INPUT:
        text = text[:CHAT_MAX_INPUT]
    try:
        await asyncio.to_thread(_turn, uid, text)
    except Exception as e:
        print("[📹] 通话插话失败（不影响继续通话）：%s" % e)
    return RedirectResponse("/call/live", status_code=303)


@app.get("/call/live", response_class=HTMLResponse)
async def call_live(request: Request):
    """
    📹 通话进行中 —— 右上名字+状态 / 中间头像 / 下面字幕 / 最底下红色挂断钮。

    ⭐ **纯渲染、不写盘**：字是从**通话记录文件**（`Rafayel_calls.last_call`）读出来的，
       这一跳一个字节都不写 ⇒ 她可以放心刷新（连刷十次也只是重读十遍，不会多出一句话、
       也不会多开一通电话）。
       ⚠ 第 4 批（2026-10-02）之前读的是 `memory/{uid}.json` 里的 12 轮历史 +
         扫哨兵划范围（`_call_window()`）—— 那个做法聊久了一通电话会**只剩最后一句**
         （开场白被裁、哨兵找不到）。现在记录独立落盘，这一页跟「历史通话」页
         读的是**同一份**数据 ⇒ 两边永远一致。
    ⚠ `nav=False`：跟振铃页同一个理由 —— 全屏沉浸页不要桌面左栏那张白卡片。
    ⭐⭐ **第 3 批（2026-10-02）补的两样东西都住在这页**，靠一段 `CALL_JS`：
       ① 右上角「通话中 00:12」的计时器 —— 服务端先渲初值（`data-el` 带上已通话秒数），
          JS 接手后每秒往上加；
       ② 语音连播 —— 只给**他最新那句**挂 `data-v`（每段气泡一个语音短哈希），
          JS 从前往后连着放。
       ⚠ 两样都**不是**功能本身：没有 JS 就只有「一个不跳的数字 / 没有声音」，
         通话照样打得完（输入栏是普通表单、挂断是普通链接）。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if not WEB_CALL_ENABLE:
        return RedirectResponse("/chat")

    # ☎️ 这一通从**记录文件**里读（第 4 批）：一通一条、不裁不压 ——
    #    聊多久都不会「只剩最后一句」（那是以前扫 12 轮历史的老毛病）。
    call = None
    try:
        call = calls_last(uid)
    except Exception as e:
        print("[📹] 读通话记录失败（按「没拨号」处理）：%s" % e)
    # 🚫 **一通都没有** ⇒ 这场通话**压根没开始**（典型是她手敲 `/call/live` 直接进来）
    #    ⇒ 踢回**拨号页**让她先拨（2026-10-02 起入口是「你打去」的 /call/dial）。
    #    ⚠ 以前这里要分两支：「从没聊过」和「哨兵被历史裁掉」（后者人正通话打到一半，
    #      踢出去等于被挂电话）。现在记录独立落盘、**不存在被裁** ⇒ 只剩这一支，
    #      整个判空逻辑简单了一半（这正是她这次要的效果）。
    if call is None:
        return RedirectResponse("/call/dial")

    # 📝 字幕：最后一通里他/她说过的（旧的在前、新的在后），只取最后几条显示。
    #    ⚠ `CALL_SUB_MAX` 管的是**屏幕上同时显示几条**（字幕一多会挤出屏幕），
    #      跟「记录里存几条」是两码事 —— 记录一条都不删（她 2026-10-02 定的）。
    start_ts = call["start_ts"]
    lines = [(m["role"], m["content"]) for m in call["lines"]][-CALL_SUB_MAX:]

    # ⏱ 已通话多久（秒）。⚠ 记录里的 `start_ts` **一定有值**（开一顿时必写）
    #    ⇒ 这一页从此永远有计时器；以前哨兵丢了就退成静态「通话中」，现在不会了。
    elapsed = max(0, int(time.time() - start_ts))

    if lines:
        # 💬 渐淡：**最新的一句在最下面、全亮**（o0），往上一条淡一档。
        #    ⚠ age 按「离最新有多远」算，跟角色无关 ⇒ 她说的和他说的一致地渐淡。
        #    ⚠ 她的行加 `.me`（小半号、暗一档），分清谁在说。
        rows = []
        n = len(lines)
        for j, (role, t) in enumerate(lines):
            cls = "call-line me o%d" % (n - 1 - j) if role == "user" \
                else "call-line o%d" % (n - 1 - j)
            # 🚦 这不是他说的话（上游把请求拒了）⇒ 换成人设化的降级话 + 一行小字。
            #    ⚠ 不挂 `data-v`：既不给一个必然失败的语音，更别让他去把英文念出来。
            #    ⚠ 记录里存的**仍是原文**（`_turn` 那边的 `calls_append` 一个字没改）——
            #      换皮只发生在**渲染这一层**，排查时照样能从 json 里看到上游原话。
            if role != "user" and is_llm_error(t):
                rows.append('<p class="%s">%s<span class="call-note">%s</span></p>'
                            % (cls, _esc(_disp(t)), _esc(LLM_BUSY_HINT)))
                continue
            extra, play = "", ""
            # 🔊 只给**最后一条 · 他说的**那句挂语音（她 2026-10-02 定：只播他的）。
            #    ⚠ `_says_of()` 是**按整条消息**判格式、逐段出结果的（见 base.py）——
            #      多段气泡 ⇒ 好几个哈希，空格隔开，JS 挨个放完。
            #    ⚠ 纯动作 / 纯旁白段提取出来是 `""` ⇒ 过滤掉（一个必然 404 的哈希
            #      没必要发给前端）。
            #    ⚠ `data-v` 一旦挂上，JS 就会去放 ⇒ **没哈希就别挂**（这也是
            #      `.has-v` 只在那时加的原因：CSS 那颗兜底钮认它）。
            if role != "user" and j == n - 1:
                hs = [_vhash(s) for s in _says_of(t) if s]
                if hs:
                    extra = ' data-v="%s"' % " ".join(hs)
                    cls += " has-v"
                    # ▶ 兜底钮：`hidden` + CSS 里默认 `display:none`，
                    #   只有 JS 判定「浏览器不让自动播」时加 `.need` 才亮。
                    #   `href` 指向第一段（无 JS 时用不到它，是给读屏/右键留的语义）。
                    play = ('<a class="call-vp" href="%s?h=%s" hidden'
                            ' aria-label="播放他说的这句" title="播放这句">%s</a>'
                            % (VOICE_PATH, hs[0], ICON_PLAY))
            rows.append('<p class="%s"%s>%s%s</p>'
                        % (cls, extra, _esc(t), play))
        sub = "".join(rows)
    else:
        # 🫥 一句都还没有（还没拨 / 他那句还没回来）
        sub = '<p class="call-line none">……</p>'

    # 🖼 通话中：中间那块 = 正在说话的他（同 `HIM_AVATAR`，跟 /chat 一个图案）。
    avh = '<img class="call-av live-av" src="%s" alt="祁煜">' % HIM_AVATAR

    # ⏱ 右上角状态：有起点才多带一个计时器；`data-el` 是给 JS 接手的初值。
    state = '通话中'
    if elapsed is not None:
        state += '<span class="call-timer" data-el="%d">%s</span>' \
                 % (elapsed, _hms(elapsed))

    body = (
        '<div class="call callbg live">'
        # 📇 右上角：名字 + 通话状态（跟她给的截图一致）
        '<div class="call-top"><b>祁煜</b>'
        '<span class="call-state">%s</span></div>'
        '<div class="call-mid"><div class="call-avwrap">%s</div></div>'
        '<div class="call-lines">%s</div>'
        # 🎛 底部：输入栏（她说一句 → /call/say）+ 挂断钮。
        #    ⭐ 零 JS 保底：普通表单 POST，浏览器自己跳 303 回本页 ——
        #      没有脚本照样能「说一句 → 看他回一句」，只是整页重绘而已。
        #    ⚠ `maxlength` 与服务端 `CHAT_MAX_INPUT` 同一个数（同下沉 base 那把尺子）。
        '<div class="call-dock">'
        '<form class="call-inputrow" method="post" action="/call/say">'
        '<input class="call-input" type="text" name="text" maxlength="%d"'
        ' placeholder="说点什么…" autocomplete="off" aria-label="说点什么">'
        '<button class="call-send" type="submit">发送</button>'
        '</form>'
        # 🛑 挂断钮：**第 4 批起是 `<form method="post">`**（原来是 `<a>`）——
        #    因为挂断现在有副作用了（要起后台线程给这通写摘要），按规矩副作用走 POST。
        #    ⚠⚠ 跟 `@app.post("/call/end")` 是一对：这里改回 `<a>` 就是 405。
        #    ⚠ 结构跟振铃页那颗绿钮**完全同构**（`<form>` 包一颗 `.call-act` 圆钮），
        #      `.call-acts form{display:flex;margin:0}` 那条 CSS 早就为它写好了 ——
        #      没有它，form 这层会把按钮的行盒撑高，圆心就偏了（振铃页 06:03 踩过）。
        '<div class="call-acts">'
        '<form method="post" action="/call/end">'
        '<button class="call-act no" type="submit" aria-label="挂断">%s</button>'
        '</form>'
        '</div></div></div>'
        % (state, avh, sub, CHAT_MAX_INPUT, ICON_CALL))
    return _page(body, title="正在通话", css=CALL_CSS + _bg_css(),
                 script=CALL_JS, nav=False)


@app.get("/call/end")
async def call_end(request: Request):
    """
    ✕ 挂断 / 取消 ⇒ 303 回 `/chat`（**纯跳转，一个字节都不写**）。

    ⚠ 这一条现在只服务**没接起来**的那两种取消：振铃页的红钮（他打来、我不接）
       和拨号页的红钮（我打去、等不及了）。那两处都是 `<a>` ⇒ 只能 GET。
    ⭐ 通话页那颗红钮（**真的挂断**）第 4 批（2026-10-02）改走 POST 了 ——
       见下面的 `call_end_live`。因为「挂断」现在有副作用了（要给他写这通的摘要），
       按规矩副作用该走 POST。⚠ 上面那句「将来若要…再改 POST」指的就是这一次。
    """
    return RedirectResponse("/chat", status_code=303)


@app.post("/call/end")
async def call_end_live(request: Request):
    """
    🛑 通话页那颗红钮 = **真的挂断**（第 4 批 · 2026-10-02）。

    ⭐ 跟上面那条 GET 只差一件事：它在 303 之前**起了个后台线程**去写这通的摘要
      （`_spawn_call_summary`）。挂断本身**不等它** —— 一次摘要是真的 LLM 调用
      （5~10 秒），点完挂断干等十秒就是坏体验；起线程即走，日记随后自己长出来。
    ⭐ 为什么必须是 POST：它**有副作用**（会经 `summarize_call` 写
      `memory/{uid}.json` 与 `{uid}_diary.json`）。
      ⚠⚠ 改 POST 之后，通话页那颗钮**必须从 `<a>` 一起换成 `<form>`** ——
        只改一边就是 405（`GET /call/end` 还在，但浏览器对 `<a>` 只会发 GET，
        而 `<form>` 对这条路由只发 POST，两边是一对，改一处必崩）。
    ⚠ `calls_pending_ids` 在**请求线程里**先读出来：那一刻刚挂断、列表末尾那通
      已经结束了 ⇒ **不排除它**，正该摘它（跟 `/call/connect` 的补摘正好相反）。
      要是挪进后台线程再读，她挂完立刻重拨，读到的就变成「新那通」了。
    ⚠ 未登录那条**必须 303 不能 307**：307 会带着 POST 方法重发到 `/`，
      而 `/` 只收 GET ⇒ 未登录的人看到 405 而不是登录页（`/call/say` 踩过的坑）。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/", status_code=303)
    if WEB_CALL_ENABLE:
        try:
            _ids = calls_pending_ids(uid)
        except Exception as e:
            _ids = []
            print("[📹] 读通话记录失败（挂断时就不摘了，下次拨号会补）：%s" % e)
        _spawn_call_summary(uid, _ids)
    return RedirectResponse("/chat", status_code=303)


# ============================================================
#  🗄 历史通话（`/call/history` · 2026-10-02 下午她提的）
# ------------------------------------------------------------
# ⭐⭐ 数据源 = `memory/{uid}_calls.json`（**第 4 批换的**，原来读 `memory/{uid}.json` 的
#    12 轮历史）。它是**一通一条、不裁不压**的完整记录 ⇒ 这一页现在真的是
#    「一通电话一条」，而不是「聊天历史里还剩下的那点通话」。
# ⚠ 两页读**同一份**：这一页和通话页字幕都走 `Rafayel_calls`
#    ⇒ 「通话页看到的」和「历史里记下的」永远一致。
# ⚠ 这一页**纯只读**：不调引擎、不喂模型、一个字节都不写盘。
#    （写盘那两处都在 `_turn` / `call_end_live` 的调用链上，走的是 `calls_open` /
#      `calls_append`，跟这一页无关。）
# ============================================================
CALLH_CSS = """
.hc{background:var(--c-card);border:0.5px solid var(--c-line);
    border-radius:var(--r-card);margin-bottom:10px;padding:0 14px}
/* ⭐ 用**原生 `<details>`** 折叠：零 JS、老浏览器也折叠得了（渐进增强口径）。
   marker 是浏览器自己那颗三角，不另画 —— 少一处样式就少一处能坏的地方。 */
.hc>summary{cursor:pointer;padding:12px 0;
            display:flex;flex-direction:column;gap:3px}
.hc-t{font-size:13px}
.hc-p{font-size:12px;color:var(--c-hint);
      overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hc[open]>summary{padding-bottom:10px;border-bottom:0.5px solid var(--c-line)}
.hc-body{padding:10px 0 14px}
.hc-r{font-size:14px;line-height:1.6;margin:0 0 8px;white-space:pre-wrap}
.hc-r:last-child{margin-bottom:0}
/* 她说的那句：暗一档 —— 跟通话页 `.call-line.me` 一个语义（分清谁在说） */
.hc-r.me{color:var(--c-muted);font-size:13px}
/* 只有开场白、他那句没回来（刷新重触发 / 引擎报错）——**保留**、标出来。
   ⚠ 别顺手把它过滤掉：那是真发生过的一通电话，删了就是篡改记录。 */
.hc-miss{font-size:13px;color:var(--c-hint)}
/* 🚦 上游拒了那一行底下的小字（跟通话页 `.call-note` 同一个语义、同一句话）。
   ⚠ 也是 `display:block` 塞在同一个 `<p>` 里 —— 理由同 `.call-note`。 */
.hc-note{display:block;margin-top:2px;font-size:12px;color:var(--c-hint)}
"""


def _call_sessions(uid):
    """
    读**通话记录文件**，翻成一场一场的通话，返回**倒序**（最近的在最上面）。

    返回 `[{"id":…, "ts": 起点时间戳, "summarized": bool, "rows": [(role, txt), …]}, …]`
    —— `rows` **不含**那句「（按下接听）喂？」（界碑不入档：`open_call()` 就不记它），
       跟通话页字幕一个口径，不然每场开头都挂一条一样的开场白，读起来像在刷屏。

    ⭐⭐ 第 4 批（2026-10-02）**换过数据源**，原来是把 `memory/{uid}.json` 的 12 轮历史
      按哨兵切（`_call_sessions(stamped)`）。换来三件事：
        ① 老通话**不会消失** —— 以前记录一被 12 轮裁剪挤掉，这一页就整场不见了；
        ② 一通电话**永远完整** —— 以前聊太长，开场白先被裁 ⇒ 整场塌成一句；
        ③ 跟通话页字幕读的是**同一份** ⇒ 两边永远一致（以前是两处各自扫历史）。
      ⚠ 代价：`{uid}_calls.json` 是**第 4 批才开始写的** ⇒ 那之前的通话要靠
        `tools/backfill_calls.py` 从历史里回填一次。⚠ 那个脚本**只对她自己的 uid 跑**
        （服务器上别人的 `memory/*.json` 只报不动，见 `MEMORY.md` 那条硬规矩）。
    ⚠ 读坏了当空档（跟记录模块一个口径）：这一页挂了顶多是「看着像没打过电话」，
      绝不能 500。
    """
    try:
        calls = calls_load(uid)["calls"]
    except Exception as e:
        print("[📹] 读通话记录失败（历史页按空处理）：%s" % e)
        return []
    out = []
    for c in calls:
        out.append({"id": c["id"],
                    "ts": c["start_ts"],
                    "summarized": bool(c.get("summarized")),
                    "rows": [(m["role"], m["content"]) for m in c["lines"]]})
    out.reverse()
    return out


def _hc_time(ts):
    """场次标题上的时间（本地时区 `MM-DD HH:MM`）；没有 `ts` 就说不知道。"""
    if not ts:
        return "时间不详"
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
    except Exception:
        return "时间不详"


def _hc_row(role, t):
    """
    历史通话页的**一行**正文。

    ⚠ 跟通话页字幕同一个口径（都走 `_disp` / `_note_of`，判据只此一处）：
      他那边要是引擎报错 ⇒ 换成人设化的降级话 + 一行小字，别甩英文原文。
      （记录文件里存的**仍是原文** —— 换皮只在渲染层。）
    """
    note = _note_of(t)
    return ('<p class="hc-r%s">%s%s</p>'
            % (" me" if role == "user" else "", _esc(_disp(t)),
               ('<span class="hc-note">%s</span>' % _esc(note)) if note else ""))


@app.get("/call/history", response_class=HTMLResponse)
async def call_history(request: Request):
    """
    🗄 历史通话：把每一通电话**以文字形式**列出来（她说「用来保存通话记录内容」）。

    ⭐ 倒序 + 原生 `<details>` 折叠：摘要行 = `10-02 14:18 · 2 句` + 他的第一句预览，
       展开 = 这场通话的逐句文字（她的暗一档）。
    ⚠ 一场都没接过 ⇒ 给一句「还没有通话记录」，别给空白页。
    ⭐ 底栏用 `_two_way_footer()`（她 14:32 要的「底部加个返回」）：**左 = ‹ 回聊天、
       右 = ‹ 返回目录**，`.twobar` 左右各占一半、中间空开 —— 她 09-30 定的
       「两个位置不要靠太近，容易误按」。
       ⚠ **不能再用 `_backbar()`**：那个只渲染一个动作、而且带 `backonly`
       （桌面版会把整条藏掉）；底栏里但凡有第二个动作就不能带它。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if not WEB_CALL_ENABLE:
        return RedirectResponse("/chat")

    sessions = _call_sessions(uid)
    if sessions:
        blocks = []
        for s in sessions:
            rows = s["rows"]
            his = [t for r, t in rows if r == "assistant"]
            if rows:
                head = "%s · %d 句" % (_hc_time(s["ts"]), len(rows))
                # 🚦 预览行也要换皮 —— 他第一句要是引擎报错，摘要行上别挂一串英文。
                prev = _esc(_disp(his[0] if his else rows[0][1]).replace("\n", " "))
                body_rows = "".join(_hc_row(r, t) for r, t in rows)
            else:
                # 只有开场白、他那句没回来 —— 保留 + 标出来（见 CSS 那条注释）
                head = "%s · 未接通" % _hc_time(s["ts"])
                prev = "这次没接通"
                body_rows = '<p class="hc-miss">拨出去了，但那头没接起来。</p>'
            blocks.append(
                '<details class="hc"><summary>'
                '<span class="hc-t">%s</span>'
                '<span class="hc-p">%s</span>'
                '</summary><div class="hc-body">%s</div></details>'
                % (head, prev, body_rows))
        inner = "".join(blocks)
    else:
        inner = ('<div class="card"><p class="muted" style="margin:0">'
                 '还没有通话记录 —— 打一通电话，这里就会记下来。</p></div>')
    body = (
        '<div class="card"><h1>历史通话</h1>'
        '<p class="muted" style="font-size:12px;margin:0">'
        '电话里说过的，都在这儿（文字形式）</p></div>'
        + inner
        # ⚠ 这里原来有一句「只显示还留在记忆里的那些；更早被聊天历史挤掉的原话，
        #   在「聊天」的留档里还能翻到」—— **第 4 批删了**：记录现在是一通一条、
        #   不裁不压，那句话已经不准了（会让她以为记录还在丢）。
        #   ⭐ 删掉之后**不补新文案**：她 2026-10-02 看这页时说「界面没有问题了」，
        #     UI 一个字不动，这一批只换数据源。
        # 🧭 底栏两个动作，左右分开（左回聊天、右回目录）—— 见上面那条注释
        + _two_way_footer("/chat", "‹ 回聊天"))
    return _page(body, title="历史通话", css=CALLH_CSS)
