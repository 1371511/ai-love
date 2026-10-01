# -*- coding: utf-8 -*-
"""
💬 对话窗口（2026-09-29 · QQ 号被冻结期间的替代入口）

路由：
  GET  `/chat`        聊天页（**只**在「他还没开过场」时写那一句开场白）
  POST `/chat/send`   收她一句话 ⇒ 拿他的回复（**会写 memory**）
  GET  `/chat/voice`  某一条气泡的语音（**只读 memory + 调 TTS，一个字节都不写盘**）

⚠⭐ 这一节**主动破了一条老红线**：原规矩是「网页端只读 memory，一个字都不写」。
   对话功能绕不过去 —— 不写盘 = 她说的话不进记忆 = 白聊。
   ⇒ 收窄后的口径：**只有本节这两条路由写 memory**
     （`/chat` 的开场白、`/chat/send` 的每轮对话 + 跨级记账）；
     `/menu`、`/affinity`、`/messages`、`/settings` **照旧一个字不写**。

⭐ 引擎直接复用 QQ 那套 `Rafayel_chat.get_reply` —— 它自己管记忆读取、
   跨天小结、世界书注入、牵绊度语气、token 记账。`Rafayel_bot.py` 里
   跟 QQ 有关的只有「收消息 → 拆气泡 → 发出去」最后一米，这儿只换掉那一米。
   ⇒ 于是两边**共用同一份 `memory/{uid}.json`**：冻结期在这儿说的话，
     解冻后他在 QQ 里照样记得 —— 不是「第二个他」。
   ⇒ token 与牵绊度也照常涨（记账在 `get_reply` 里面，白捡）。

⚠⚠ **两个进程别同时写**：`Rafayel_bot.py` 和本文件都会写 `memory/`，
   而文件锁跨不了进程。冻结期**只跑 web、别跑 bot**（号都冻了，bot 也收不到事件）。
   将来要两边同时开，得改成「web 把消息转发给 bot 那个进程」，别指望文件锁。

⚠ 同一进程内也**必须按 uid 串行**：她连点两次 / 开两个标签页 ⇒ 两条
   `get_reply` 并发跑，各自持一份 `cm` 内存副本，后写的那份把先写的整个覆盖掉，
   直接丢话。锁在 `_chat_lock`，前端再补一道「发送中禁用按钮」。

⚠ 这三条 import 是新增的耦合：`Rafayel_chat` 会连带读人设卡
   （`card/Rafayel.character.json`）⇒ 那个文件坏了 / 缺了，**web 会起不来**。
   以前网页面只读 memory，没这层依赖。
"""
import asyncio
import collections
import hashlib
import json
import os
import re
import threading
from urllib.parse import quote   # 🗄 日期拼进 URL 要编码（跟 `_dayqs` 同一条规矩：不裸塞）

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from base import (
    app, _page, _esc, _rich, _safe_uid, _avatar_url, _current_uid, _load_users,
    MEMORY_DIR, CHAT_CSS, MENU_PATH, _backbar,
    # 🎨 表情标记的正则 —— 跟 `_rich()` 用**同一条**（`_pic_only()` 要判「剥完还剩不剩字」）
    _STICKER_TXT,
)
from Rafayel_affinity import compute, current_level, init_unlocked, pending_unlock
from Rafayel_chat import get_reply, mood_hint, take_opening
from Rafayel_config import AFFINITY_UNLOCK
from Rafayel_daily import load_unlocked, save_unlocked
from Rafayel_voice import strip_actions, synth_wav


# ============================================================
# 💬 对话窗口的专用样式 / 脚本（2026-09-29 · QQ 号被冻结期间的替代入口）
# ------------------------------------------------------------
# ⭐ 气泡那部分（`.phone` / `.chat` / `.row` / `.av` / `.bub` / `.sys`）**直接复用
#    `CHAT_CSS`**（在 base.py 里）—— 她已经在「牵绊短信」详情页见过那套观感，别另做一套。
#    这里只补「底部输入栏」和右上角那个在线点。
# ⚠⭐ `.bar-bottom` 跟 `.footnav` 一个道理：**必须是内容列 `.main` 的直接子元素**，
#    而且用 `position:sticky`（不是 `fixed`）⇒ 内容不可能跑到它下面；
#    三个 margin 是「左右通栏 + 底部抵消 body 的 padding:2rem」，
#    ⚠ 改 body 的 padding 必须同步改这三个 margin。
# ⚠ `.phone.talk` 给个最小高度 —— 不然刚开聊时内容很短，输入栏会浮在屏幕中间。
# ============================================================
TALK_CSS = """
/* ============================================================
.member 📐 2026-10-01 · 「发完消息要手动往下拉」的修复（她提的）
------------------------------------------------------------
⭐ 病因不在「忘了滚」，而在**让整页滚**：
   · `TALK_JS` 里那个 `toBottom()` 用的是 `window.scrollTo(0, body.scrollHeight)` ——
     既没在「发送后 / 收到回复后」这两个时点调用，又跟手机地址栏收缩抢时间，
     滚不到底是常态。
   ⇒ 改成：**手机上一屏装下整个手机壳**（顶栏 + 气泡区 + 输入栏），
     整页不再溢出 ⇒ 「往下拉」这件事从根上没有了；**只有气泡区自己滚**，
     JS 每次新气泡进来把它滚到底就够了（`scrollTop` 是同步量，不用跟地址栏商量）。
   ⚠⚠ 顶栏那两条 `position:sticky` 一起删了：手机壳自己一屏高、里面没有可滚的祖先，
     题面根本不存在（原来配合 `overflow:visible` 那套是为了「整页滚时顶栏不滚走」）。
     手机壳的 16px 圆角现在靠 `.phone` 自己的 `overflow:hidden`（base.py 的 CHAT_CSS）
     重新生效 —— 它本来就在，是上一版为了 sticky 才覆盖成 visible 的。
   ⚠ `.phone.talk` 必须 `display:flex;flex-direction:column`：`.chat` 才能
     `flex:1` 吃掉剩下的高度、内部再滚。
   ⚠ 高度用 `calc(100vh - 232px)`：232 是原来那条 `min-height` 里真机量出来的数，
     一个字节没动，只是从「下限」变成了「定死」。
     算的是：地址栏 ≈ 56 + body 上 padding 32 + 输入栏(10+8+40+... )≈ 116 + body 下 padding 32
     ⚠ 改 body 的 padding 或输入栏高度，这个 232 要重新量。
============================================================ */
.phone.talk{display:flex;flex-direction:column;
            height:calc(100vh - 232px);overflow:hidden}
/* 📱 动态视口：地址栏收缩时 `100vh` 不变、真实可视区变高 ⇒ 输入栏会被顶出屏幕。
   `dvh` 跟着真实可视区走。不支持的浏览器**整块忽略**，落回上面那条 `100vh`。 */
@supports(height:100dvh){
  .phone.talk{height:calc(100dvh - 232px)}
}
/* 气泡区：**唯一允许滚的地方**。
   ⚠ `overscroll-behavior:contain` —— 滚到顶/底时别把滚动接力给整页
     （否则又变回「整页跟着动」，白改）。
   ⚠ `min-height:0` 是 flex 子项的老坑：默认 `min-height:auto` 不肯缩，
     内容一多就把 `.phone` 顶高、`overflow` 形同虚设。 */
.phone.talk>.chat{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain}
/* ⚠⭐ `.dot`（在线点）2026-09-29 **已搬去 base.py 的 `CSS`** —— 它 `/affinity` 也在用，
   留在这儿只有 `/chat` 拿得到 ⇒ 那边那个点不显示。**别再往这儿加回来。** */
.bar-bottom{position:sticky;bottom:0;z-index:10;background:#FAFAF8;
            border-top:0.5px solid rgba(0,0,0,.1);padding:10px 1rem;
            margin:0 -1rem -2rem}
.bar-bottom form{display:flex;gap:8px;align-items:flex-end;max-width:560px;margin:0 auto}
/* 🧭 方案 B（她 2026-09-29 选的）：**「回目录」不另占一条底栏，并进输入栏这一行**。
   比多叠一条 40px 的 `.footnav` 省一截屏幕 —— 这就是没选 A 的原因。
   ⚠ 写成「‹ 目录」而不是光一个「‹」：实测光一个箭头点击区只有 **10×28px**（真机量的），
     手机根本戳不中；补上两个字才有个像样的热区。顶栏那条钉住后一直看得见，
     这儿本来就是「单手够不到顶部」时的下位入口。
   ⚠ `align-self:center` 是必须的：`.bar-bottom form` 是 `align-items:flex-end`
     （为了对齐输入框底边），不单独扶一下的话这个链接会贴着行底、跟「发送」不齐。
   ⚠ `white-space:nowrap` 防它在窄屏被折成两行。
   ⚠ `min-width:0` 给 textarea 让路：flex 子项默认 `min-width:auto`，
     不加这条它在 320px 窄屏上会把「发送」挤出屏幕（而不是自己缩窄）。 */
.bar-bottom .bk{flex:0 0 auto;align-self:center;text-decoration:none;
            padding:6px 8px;white-space:nowrap}
.bar-bottom textarea{flex:1;min-width:0;min-height:38px;max-height:120px;resize:none;font:inherit;
            padding:8px 10px;border-radius:10px;border:0.5px solid rgba(0,0,0,.2);
            background:#fff;box-sizing:border-box;line-height:1.4}
.bar-bottom button{width:auto;flex:0 0 auto;margin:0;padding:9px 16px}
.bar-bottom button:disabled{opacity:.45;cursor:default}
/* 🖥 桌面（2026-09-29 第 3 步）—— 通用壳（左栏 / 网格 / 藏底栏）在 `base.py` 的 `@media` 里，
   这里只补**这一页特有**的两处。 */
@media(min-width:900px){
  /* ⚠ 输入栏那套 `margin:0 -1rem -2rem` 里，**左右那两个 `-1rem` 要归零**
     （那是给手机通栏设计的，桌面上卡片限宽 660，通栏会横着溢到左栏和空白区）；
     ⚠⚠ 但**底下那个 `-2rem` 必须留着** —— 它抵消的是 body 的 `padding-bottom:2rem`。
       一开始我把三个都归零了，真机量出输入栏**离屏幕底还有 32px**（`gap:32`，正好就是那 2rem）。
       ⇒ 桌面上正确写法是 `margin:0 0 -2rem`。 */
  .bar-bottom{margin:0 0 -2rem}
  /* ⚠⭐ `.phone` 的 `margin-bottom:14px` **桌面上必须归零**（2026-09-29 那条 `.phone` 在
     base.py 的 CHAT_CSS 里带的，那是给「手机壳 + 底栏」之间留缝的一次性间距）。
     现在手机壳被定成 `高度 = 一屏 - 104` ⇒ 它 + 这 14px 会**比可用空间多出 14px**，
     整页多滚一点点（真机量到 `docH 811 vs innerH 800`，正好 11px 露在输入栏下面）。
     ⚠ 视觉上**没有损失**：手机壳和输入栏之间本来就还有 `.bar-bottom` 的
       `padding-top:10px`；真正的「手机壳贴在输入栏上」那种观感不会出现。
     ⚠ 桌面独有的问题 —— 手机端整页一屏装得下，那 14px 只是把 `.chat` 压矮一点点，
       反而让「手机壳 + 输入栏」两截更分明，所以**手机那条不动**。 */
  .phone.talk{margin-bottom:0}
  /* ⚠⚠ `.phone.talk` 的高度**必须重算**。手机那个 `- 232px` 是按「手机浏览器地址栏 +
     顶栏 + 输入栏 + body padding」估出来的；桌面没有地址栏那一截 ⇒ 不重算的话手机壳
     填不满视口，而 `.bar-bottom` 是 `position:sticky;bottom:0`，**贴不到底就会浮在半空**
     （这正是这条高度当初要治的病）。
     ⚠ `- 104px` 这个数是**真机量出来的**，不是推的：顶栏≈58 + 输入栏≈58，
       再减掉那条 `-2rem` 负 margin 抵消掉的 32 ⇒ 手机壳正好填满一屏。
       改顶栏/输入栏任何一个高度都要重新量。
     ⚠⚠ `min-height:` 是 2026-10-01 改成 `height:` 的 —— 现在手机壳要**一屏装下、
       内部自己滚**（见 `TALK_CSS` 顶部那段说明），光给下限不够。
     ⚠⚠⚠ `!important` 在这里**不是偷懒**，是必需的：手机壳那套写在 `@supports(height:100dvh)`
       里面 —— media query 与 supports 查询**不影响特异性**，而 `@supports` 那条
       `.phone.talk` 出现在本块**之前** ⇒ 不加 `!important`，桌面上会被它按字面顺序压过去，
       高度变成 `100dvh - 232px`（少 128px）⇒ 输入栏浮在半空。这跟「`@supports` 忽略
       不认识的声明」不是一回事，对支持 `dvh` 的桌面浏览器是**必然踩中**。
       同时 `overflow:hidden` 也要重述一遍：`@supports` 那条是**同一条规则、优先级更高**，
       `overflow` 若只在上面写、不在这条里带一遍，桌面会拿到 `@supports` 的 `overflow:hidden`——
       值一样，但依赖别人替你写是错的；重述一遍，这条规则自己是完整的。 */
  .phone.talk{height:calc(100vh - 104px) !important;
              min-height:0;overflow:hidden}
}

/* 🔊 点句子听他说（2026-09-29 · 她定：小喇叭放气泡**右下角**）
   ⚠⭐ 为什么是「文字 + 图标」两个 flex 子项，而**不是** `position:absolute;right:6px;bottom:6px`：
     absolute 不占位 ⇒ 气泡里最后一行排满时，那个小喇叭正好**压住末尾几个字**，
     而且在 CSS 里完全看不出问题（它就是叠在上面）。改成 flex 之后文字列自己变窄让位，
     ⇒ 每一行都不会被压住，图标永远真的在右下角。代价是每行少 26px。
   ⚠ `align-items:flex-end` 对齐的是**文字块底边** ⇒ 图标贴着最后一行。
     换成居中（默认 stretch / center）短句会显得图标悬在半空。
   ⚠ `.tx` 必须 `min-width:0` —— flex 子项默认 `min-width:auto`，
     不让路的话长句子会把气泡顶出 `max-width:76%`。
   ⚠ 这一整套都挂在 `.phone.talk` 下：短信详情页（`/messages/{sid}`）用的是**同一个 `.bub`**
     （在 base.py 的 CHAT_CSS 里），不限定作用域就会把那边也改成 flex。 */
.phone.talk .bub.him{display:flex;align-items:flex-end;gap:6px}
/* 📐 2026-10-01 改：`.tx` 原来是 `flex:1` ⇒ 文字列**主动吃满** `max-width`，
   于是「爱你。」和「（把布往画架上一搭，慢悠悠地…）」长得一样宽，
   一屏下来全是同宽的方块（她 10-01 截图反馈「固定了宽度实测下来效果不行」）。
   ⇒ 改成 `flex:0 1 auto`：**按内容宽度走**，只有超长才被 `max-width` 截住。
   ⚠ `flex-shrink:1`（中间那个 1）必须留：长句子要能收缩给右侧小喇叭让位，
     写成 `0 0 auto` 长句会顶破 `max-width`、把喇叭挤出气泡。 */
.phone.talk .bub.him .tx{flex:0 1 auto;min-width:0}
/* ⚠ `.spk` 默认 `display:none`，靠下面那条 `.js …` 才显形 ——
   **没 JS 就没有这个按钮**，而不是留一个戳了没反应的死图标。 */
.spk{display:none}
.phone.talk .bub .spk{flex:0 0 auto;width:20px;height:20px;margin-bottom:-2px;
      border-radius:50%;background:rgba(142,53,86,.08);color:#8E3556;
      align-items:center;justify-content:center;text-decoration:none;
      -webkit-tap-highlight-color:transparent}
.js .phone.talk .bub .spk{display:flex}
.phone.talk .bub .spk svg{display:block}
/* 合成中 —— TTS 有 1~2 秒延迟，不给反馈她会以为没戳中 */
.phone.talk .bub .spk.busy{opacity:.4}
/* 正在播（再戳同一句 = 停） */
.phone.talk .bub .spk.on{background:#8E3556;color:#fff}
/* 这句拿不到音频（404 已滚出历史 / 503 TTS 挂了）⇒ 闪一下再恢复，别停成死状态 */
.phone.talk .bub .spk.bad{background:rgba(0,0,0,.05);color:#B4B2A9}
/* ============================================================
⌨️ 打字机（2026-10-01 她提的：「他发的消息在 Web 端做成慢慢出现的效果」）
------------------------------------------------------------
⭐ 口径（她定的）：**只有新发出来的消息**打字，历史消息 / 刷新后一律直出。
   ⇒ 服务端只给 `/chat/send` 的 fetch 片段打 `data-ty`（见 `_bubble()`），
     这里负责把「有 `data-ty` 的那条」先按下去。
⚠⚠ 全部规则挂 `.js` 根类（脚本跑起来才加得上）——
   **没 JS ⇒ 一条都不生效 ⇒ 气泡完整直出**。这是本功能的渐进增强底线。
   ⚠ 所以这里**绝不能**写成「默认藏、`.js` 里放开」以外的任何形态
     （比如服务端加 `hidden` 属性）—— 那样没 JS 就真的一片空白了。
⚠ 用 `visibility:hidden` 而不是 `display:none`：
   ① 逐字放行时气泡会一行行长高，`display:none` 时期它不占位 ⇒ 一揭开就整块跳一下；
      留位则高度先按**完整内容**占好（后面几行是空白），揭开只是把字填进去 ⇒ 不抖。
   ② 后面「点任意处全部跳过」要能立刻全部揭示，留位不涉及重排，跳变更小。
   ⚠ 打字期间那条气泡里的小喇叭也会跟着 `visibility:hidden`，
     它本来就 `hidden` 属性挂着（等 JS 解锁），**不冲突**。
------------------------------------------------------------ */
.js .phone.talk .chat .bub[data-ty]{visibility:hidden}
/* 打完 / 被跳过 ⇒ 立刻恢复。
   ⚠ 这个类由 JS 加，且**打完一定会加**（含异常分支）；万一没加上，气泡就一直是空的 ——
     所以 `finally` 那一步是硬要求，见 `TALK_JS` 的 `finish()`。 */
.js .phone.talk .chat .bub.tydone{visibility:visible}
/* 🖊 打字光标：跟在正在出的字后面闪。
   ⚠ 用 `::after` 生成，不动 DOM ⇒ **不会**被 JS 的「逐字 reveal」当成文字放出来，
     也不会进 `textContent`（复制粘贴拿到的仍是干净原文）。 */
.js .phone.talk .chat .bub.typing::after{content:"▍";opacity:.45;
     animation:tyblink .9s steps(1,end) infinite}
@keyframes tyblink{0%,50%{opacity:.45}50.01%,100%{opacity:0}}
/* 🛑 晕动症 / 关掉动效：**整段直出**，一条都不打（跟 `.fresh` 那条一个口径）。
   ⚠ 特异性算一下：上面那条是 `.js .phone.talk .chat .bub[data-ty]` = (0,4,1)；
     本条写成 `.js .phone.talk .chat .bub[data-ty]` 也是 (0,4,1) ⇒ 平局、靠源顺序赢
     —— 本块在 `TALK_CSS` 里本来就排在前面那条**之后**，所以**不用 `!important`**。
     ⚠⚠ 但别把它搬到前面去：一挪就输，而且**不报错**（这条正是最容易搬错的形态）。 */
@media (prefers-reduced-motion:reduce){
  .js .phone.talk .chat .bub[data-ty]{visibility:visible}
  .js .phone.talk .chat .bub.typing::after{content:none}
}
/* 💗 顶栏心情（2026-10-01）
   ⚠⚠ 为什么非加这三条：`.ph-top` 是 `justify-content:space-between` ⇒ 右边那格
     文字一变长，**中间的「祁煜」会被推着左右挪** —— 那就是最不丝滑的地方。
     左右两格各 `flex:1`、中间不伸缩 ⇒ 标题真居中，右边怎么换它都不动。
   ⚠ 只挂 `.phone.talk`：`/messages/{sid}` 用的是 base.py 里**同一个 `.ph-top`**。 */
.phone.talk .ph-top>a,.phone.talk .ph-top>#mood-slot{flex:1;min-width:0;white-space:nowrap}
.phone.talk .ph-top>b{flex:0 0 auto}
.phone.talk .ph-top>#mood-slot{text-align:right;transition:color .5s ease,opacity .25s ease}
/* 🗓 前几天的小结（2026-10-01 加）
   ⚠⭐ 为什么**不能沿用** `.sys` 原本的居中：`.sys` 是给「已经牵绊到 Lv.N」那种
     一行短提示用的；小结是**一整段**（实测 60~120 字），居中 + 灰字会读得很累。
     ⇒ 左对齐、行高放宽、上下各留一截，做成「翻开旧页」的观感。
   ⚠ 日期用 `<b>` 加粗一点点区分，别上色 —— 这一块整体应该是「退到背景里」的，
     抢眼了就会跟真正的气泡打架。
   ⚠ 限定 `.phone.talk`：`.sys` 在短信详情页也在用（base.py 的 CHAT_CSS），
     那边没有小结，不该被这套样式带跑。 */
.phone.talk .sys.daysum{text-align:left;max-width:82%;margin:10px 0 14px;
     line-height:1.65;font-size:12px;color:#8A8880;
     padding:8px 11px;background:rgba(0,0,0,.03);border-radius:10px}
.phone.talk .sys.daysum b{font-weight:500;color:#6E6C65}
/* 🗄 「看那天的原话 ›」（2026-10-01）
   ⚠ `display:block` + 上边距：小结正文是一整段，链接跟在段落末尾会被当成正文的一部分
     （而且点在字里行间很难戳中）⇒ 另起一行、独占一条热区。
   ⚠ 只在**真有存档**的那天才渲染这个 `<a>`（服务端判的，见 `_archived_days`）
     ⇒ 不存在点进去是空页的死链。 */
.phone.talk .sys.daysum .arlink{display:block;margin-top:7px;
     color:var(--c-brand-ink);text-decoration:none;font-size:12px}

/* 📱 2026-10-01 晚 ·「输入栏下面空出一大块」的修复（两台真机对照定位）
------------------------------------------------------------
⭐ 病因：上面 `calc(100dvh - 232px)` 的 232 是**在一台手机上量出来的**
   （地址栏 56 + body 上下 padding 64 + 输入栏 ~112，见本串顶部那段注释）。
   但 `100dvh` 本身就是「跟着真实可视区走」的单位 —— 地址栏/底部工具栏占掉的高度
   **它已经自己扣掉了**，再减 232 里的地址栏那截 = **chrome 被扣了两次**
   ⇒ 支持 dvh 的手机上，输入栏下面永远空出一大块（手机 1 实测 ≈130px）；
   不支持 dvh 的浏览器走上面的 `100vh` 老分支，恰好接近当初量 232 的那台 ⇒ 看着正常。
   ⇒ 一台坏一台好，同一个 CSS。
⭐ 修法：**不再手算 chrome**。让内容列 `.main` 自己当外壳定高 ——
   只扣 64 = body 上下 padding 2rem×2（**页面内部常量，跨浏览器稳定**），
   手机壳 `flex:1` 吃剩余高度、输入栏 `flex:0 0 auto` 自占其高。
   地址栏收缩、底部工具栏高度、输入栏打字长高，全部自动适应，没有 magic number。
⚠ **不支持 dvh 的浏览器整块忽略**（`@supports` 不认识就不生效）⇒ 保持原样、**零回归**。
⚠ 必须排在本串前面那两条 `.phone.talk` 规则**之后**：特异性相同（0,1,0），靠源顺序取胜，
   所以 `height:auto` 才压得住 `calc(100dvh - 232px)`。
⚠ `margin-bottom:0`：手机那条 `.phone` 的 14px 下边距原本是给「壳 + 底栏」留缝的；
   现在 shell 和输入栏同属一个 flex 列、输入栏自己有 `padding-top:10px` 和 `border-top`，
   这道缝反而会变成一小条空白。
⚠ 限定 `max-width:899px`：桌面 `min-width:900px` 那条 `!important` 高度照旧生效，互不打架。
⚠ `/chat` 的 `.main` 只有两个直接子元素 `.phone.talk` + `.bar-bottom`（`chat.py` 里
   `chat_page` 组装的），flex 列不会有第三个孩子被意外拉进来。 */
@supports(height:100dvh){
  @media(max-width:899px){
    .main{display:flex;flex-direction:column;height:calc(100dvh - 64px)}
    .phone.talk{flex:1 1 auto;min-height:0;height:auto;overflow:hidden;margin-bottom:0}
    .bar-bottom{flex:0 0 auto}
  }
}
/* ⌛ 「他正在输入…」占位气泡（2026-10-02）
------------------------------------------------------------
⭐ 为什么需要它：她的气泡改成**乐观插入**（点发送就上屏，见 `TALK_JS` 里 `herBubble()`）
   之后，屏幕是不空了，但接下来等他回话那 1~2 秒仍然是「什么都没发生」。
   这段空白填上一个会呼吸的三个点 ⇒ 那 1~2 秒变成「他正在回我」，而不是「是不是卡了」。
⚠⭐ 全部挂在 `.js` 根类下：**没 JS 就永远不会出现**。
   理由跟 `.spk` 小喇叭一样 —— 这个气泡是**靠 JS 插进去、也靠 JS 撤掉的**；
   没 JS 那条路是整页 POST + 303 重载，页面一刷新就没了，本来也不需要它。
   挂 `.js` 之后，就算哪天 JS 挂了，也只会「没有这个提示」，不会「留一个永远撤不掉的点」。
⚠ 三个 `<i>` 而不是 `::after` 画三个点：三点要**错开相位**依次亮（更像在打字），
   `::after` 只有一个伪元素、没法各自 `animation-delay`。
⚠ `transform` 只动 `translateY` 不动 `width/height`：后者每帧都要重排，前者走合成层。 */
.js .phone.talk .chat .bub.wait{display:flex;align-items:center;gap:5px;padding:12px 13px}
.js .phone.talk .chat .bub.wait i{width:5px;height:5px;border-radius:50%;
     background:#B9B7AF;display:block;animation:tydot 1.1s ease-in-out infinite}
.js .phone.talk .chat .bub.wait i:nth-child(2){animation-delay:.18s}
.js .phone.talk .chat .bub.wait i:nth-child(3){animation-delay:.36s}
@keyframes tydot{0%,60%,100%{opacity:.3;transform:translateY(0)}
                 30%{opacity:.9;transform:translateY(-3px)}}
/* 🛑 关掉动效的人：三个点**静止地留着**，不闪也不跳（跟上面 `.typing` 那条一个口径）。
   ⚠ 别写成 `display:none` —— 那就等于把「他在回我」这个信息也一起没收了。 */
@media (prefers-reduced-motion:reduce){
  .js .phone.talk .chat .bub.wait i{animation:none;opacity:.45}
}
"""

# ⚠⚠ 渐进增强：脚本没了 / 浏览器太老 ⇒ 表单**照旧整页 POST**，功能一点不丢
#    （跟短信详情页 `SMS_JS` 同一个口径：只在「能不能更顺」上让步，
#     不在「离了 JS 就废」上让步）。
TALK_JS = r"""
<script>
(function () {
  // ⭐ 一进来就打「有 JS」的标记：小喇叭（`.spk`）服务端渲染出来是带 `hidden` 的，
  //    靠 CSS 里那条 `.js … .spk{display:flex}` 才显形。
  //    ⇒ **没 JS 就没有这个按钮**，而不是留一个戳了没反应的死图标。
  //    ⚠ 这行必须在下面那些 `return` 之前 —— 放后面的话，`#chat` 一旦拿不到就整段跳过。
  document.documentElement.classList.add('js');

  var box = document.getElementById('chat');
  var form = document.getElementById('say');
  var ta = document.getElementById('t');
  var btn = form ? form.querySelector('button') : null;
  if (!box || !form || !ta) { return; }

  var sending = false;

  // ============================================================
  // ⬇️ 滚到底（2026-10-01 重写 —— 她提「发消息之后要手动往下拉着看，好麻烦」）
  // ⚠⭐ 为什么不再用 `window.scrollTo(0, body.scrollHeight)`：
  //   ① 页面上滚的**根本不是 window** 了 —— 手机壳现在一屏装下、只有气泡区
  //      （`#chat`）在滚（见 `TALK_CSS` 顶部）。滚 window 等于什么都没做。
  //   ② 就算还是整页滚，`window.scrollTo` 在手机上要跟地址栏收缩抢时间 ——
  //      写进去的数会被浏览器改掉，**滚不到底是常态**（这就是她手动往下拉的原因）。
  //      `元素.scrollTop = 元素.scrollHeight` 是纯同步量，不跟地址栏商量。
  // ⚠ `near()` 兜底算一遍 window：哪天布局又变回整页滚，这段也不会失效。
  // ============================================================
  function near() {
    var gap = box.scrollHeight - box.clientHeight - box.scrollTop;
    var wgap = (document.documentElement.scrollHeight || 0) -
               (window.innerHeight || 0) - (window.pageYOffset || 0);
    return Math.max(gap, wgap) < 80;      // 80px 容差：离底不到一屏的十分之一就算「在底部」
  }
  function pin() {                        // 真正的滚动动作：气泡区到底 + 整页到底（兜底）
    try { box.scrollTop = box.scrollHeight; } catch (e) {}
    try { window.scrollTo(0, document.body.scrollHeight); } catch (e) {}
  }
  function toBottom(force) {
    // ⚠⭐ 她在往上翻旧消息时**不许抢她的滚动** —— 只有本来就贴着底才跟下去。
    //    没有这道闸，长对话里她往回翻到一半、他回一句，页面就自己弹回最底下。
    // ⚠ 首屏 / 刚发完消息必须 `force`（那时她本来就在底部，或者就是她主动要往下看）。
    if (!force && !near()) { return; }
    pin();
    // ⚠⭐ 必须再补一帧：`insertAdjacentHTML` 刚插进去那一刻高度还没重排完，
    //    立刻滚会**差最后一条气泡那么高**（这正是「滚了但没到底」的经典成因）。
    requestAnimationFrame(pin);
  }
  function grow() {
    ta.style.height = 'auto';
    ta.style.height = Math.min(120, ta.scrollHeight) + 'px';
  }
  function unlock() {
    sending = false;
    if (btn) { btn.disabled = false; }
    ta.focus();
  }

  // ============================================================
  // ⚡ 乐观插入（2026-10-02 —— 她提的「我发出去的消息没能及时发出去，
  //    要等 1~2 秒、页面像刷新了一下才弹出我的气泡」）
  // ------------------------------------------------------------
  // ⭐⭐ 病因：原来 `submit` 里**一个 DOM 都没插**，只清空输入框就发请求，
  //    等服务端把「她的气泡 + 他的回复」打包回来才 `insertAdjacentHTML` 一次性落地
  //    ⇒ 她的气泡注定要等一整个 LLM 往返，而且两条一起落地 = 高度突变 + 硬滚到底
  //    = 她看到的「刷一下」。
  // ⭐⭐ 修法：她自己敲的那句话**本来就是真相源**，没必要绕服务端一圈 ——
  //    点发送那一刻就地画出来，服务端那趟只用来取「他的回复」。
  // ⚠⭐ 配套改动（**少了这一半会插出两条一样的**）：`/chat/send` 在 fetch 那条路
  //    不再渲染她的气泡（见 Python 侧 `frag = [] if typing else [...]` 那行）；
  //    没 JS 的保底路（整页 303）照旧由服务端渲染 ⇒ 零影响。
  // ⚠ 用 `createElement` + `textContent`，**不拼 HTML 字符串** ⇒
  //    ① 天然防注入（她输入什么都是纯文本）② 不用在 JS 里复刻服务端那套 `_rich()`
  //    （她只发纯文字，服务端那套是为 `[表情:x]` 准备的，这边用不上）。
  // ⚠ 头像不自己拼：从页面上**已有的**一条同类气泡里拷 `.av` 的 innerHTML
  //    （那是服务端渲好的、带正确 URL 的），取不到就留空 —— 空 `.av` 有底色，不难看。
  // ============================================================
  function bubbleRow(side, bubCls, text) {
    // `side`：'me' = 她那侧（右边），'' = 他那侧（左边）。
    var row = document.createElement('div');
    row.className = side ? ('row ' + side) : 'row';
    var av = document.createElement('div');
    av.className = 'av';
    var ref = box.querySelector(side ? '.row.me .av' : '.row:not(.me) .av');
    // ⚠ 她**第一条**消息时页面上还没有她的气泡 ⇒ 从服务端藏的样板 `#herav` 取
    //   （见 Python 侧 `chat_page` 里那个 `<div id="herav" hidden>`）。都取不到就留空。
    if (!ref && side) { ref = document.getElementById('herav'); }
    if (ref) { av.innerHTML = ref.innerHTML; }
    var bub = document.createElement('div');
    bub.className = bubCls;
    if (text) { bub.textContent = text; }
    row.appendChild(av);
    row.appendChild(bub);
    return row;
  }

  function himWait() {
    // ⌛ 等他回话那 1~2 秒别空着：三个会呼吸的点（样式见 `TALK_CSS` 末尾）。
    //    ⚠ 它**不带 `data-ty`** ⇒ 打字机的 `tyCollect()` 不会把它当成「要打字的气泡」。
    var row = bubbleRow('', 'bub wait', '');
    var bub = row.lastChild;
    for (var i = 0; i < 3; i++) { bub.appendChild(document.createElement('i')); }
    return row;
  }

  // ============================================================
  // ⌨️ 打字机（2026-10-01 她提的：「他发的消息在 Web 端做成慢慢出现的效果」）
  // ------------------------------------------------------------
  // ⭐ 她定的口径：**只有新发出来的消息**打字，历史消息 / 刷新后一律直出
  //    ⇒ 服务端只给 `/chat/send` 的 fetch 片段打 `data-ty`（见 `_bubble()`）。
  // ⭐ 节奏（她定的）：**一条打完再打第二条**（串行）+ 每字 `TY_MS` + 可以跳过。
  //    串行是关键 —— 他那条回复常切 2~3 个气泡，一起打就不像"人在一句句发"了。
  // ⚠⚠ 为什么是「逐字揭开**文本节点**」而不是「每帧重设 innerHTML」：
  //    `_rich()` 已经把 `[表情:x]` 渲染成 `<span class="chip">`，用正则/切片去剁
  //    HTML 字符串必然剁坏两样东西 —— ① 标签本身 ② **HTML 实体**
  //    （`&amp;` 被从中间切开就成了 `&am` + `p;`，DOM 里直接显示成乱码）。
  //    文本节点自成边界、实体在 DOM 里已经是一个字符 ⇒ 只在文本节点上切，两边都安全。
  // ⚠ `splitText()` 会在原地把文本节点切开并返回后半截 ⇒ 只要把**后半截的长度**逐帧
  //    缩到 0 就等于"慢慢长出来"，而且 `textContent` 全程都是完整的（选中/复制不受影响）。
  // ⚠ 表情 chip 那类**元素节点整块放行**（一步到位）：
  //    一个字一个字蹦出来的表情圆片很怪，而且它是原子单位。
  // ⚠⚠⚠ **必须拿「已经插进 DOM 的节点」来打**（见 `tyCollect()`）——
  //    再解析一遍 `frag` 字符串拿到的是游离的另一份，改动全落在垃圾上，
  //    页面一动不动而且**不报错**。这是本功能第一版翻的唯一车，别重犯。
  // ============================================================
  var TY_MS = 18;                    // 每字间隔（她定的速度）
  var tyQueue = null;                // 正在跑的那批（`null` = 没在打字）—— 跳过监听只认它
  var tySkip = false;                // 「点任意处全部跳过」的旗标

  function tyReduced() {
    // ⚠ 只在**支持**这个 API 的地方问；老浏览器一律当"没关动效"（照常打字）。
    try {
      return !!(window.matchMedia &&
                window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (e) { return false; }
  }

  function tyNodes(bub) {
    // 收集要逐字揭开的文本节点（DOM 顺序）。
    // ⚠ 跳过 chip 里的文字：它是元素节点，会被整块放行，再收一遍就重复了。
    var out = [], w = document.createTreeWalker(bub, NodeFilter.SHOW_TEXT, null, false);
    var n;
    while ((n = w.nextNode())) {
      var p = n.parentNode;
      if (p && p.classList && p.classList.contains('chip')) { continue; }
      out.push(n);
    }
    return out;
  }

  function tyOpen(bub) {
    // 把这一条按下去，返回 `{nodes, fulls, flush}`：
    //   · `nodes` / `fulls` —— 逐字揭开要用的「节点数组」与「每个节点的全文」
    //   · `flush()`        —— 把剩余部分**一次性**全放出来（正常打完 / 被跳过 / 出错都走它）
    // ⚠ 先记下每个文本节点的全文，再把它们**立刻**砍到 0 长度 ——
    //    砍到 0 而不是等到第一帧才砍：中间那一帧若被浏览器画出来，会闪一下全文。
    // ⚠⚠⚠ `nodes` / `fulls` **必须从这里带出去**，调用方**不许**自己再 `tyNodes()` 读一遍：
    //    这一步已经把 `nodeValue` 砍成空了，再读只会读到一串空串 ⇒ `total` 算成 0
    //    ⇒ 立刻 `finish()` ⇒ 气泡**瞬间全出**、`typing` 一帧都不出现（不报错，最难查）。
    //    2026-10-01 第一版就是这个错，页内 12ms 采样抓了 166 次只看到一种状态才揪出来。
    var nodes = tyNodes(bub);
    var fulls = [];
    for (var i = 0; i < nodes.length; i++) {
      fulls.push(nodes[i].nodeValue);
      nodes[i].nodeValue = '';
    }
    return {
      nodes: nodes,
      fulls: fulls,
      flush: function () {
        for (var k = 0; k < nodes.length; k++) {
          if (nodes[k].nodeValue !== fulls[k]) { nodes[k].nodeValue = fulls[k]; }
        }
      }
    };
  }

  function tyPlay(bub, done) {
    // 打这一条。`done()` 在**打完或被打断**时都会调（含 `data-ty` 缺失/异常）。
    if (!bub || !bub.getAttribute || !bub.getAttribute('data-ty')) { done(); return; }
    var opened;
    try { opened = tyOpen(bub); } catch (e) { done(); return; }

    // ⚠ 直接用 `tyOpen` 带出来的那一份（见它头顶那段说明），**不要**再 `tyNodes()`。
    var nodes = opened.nodes, fulls = opened.fulls, flush = opened.flush;

    bub.classList.add('typing');

    // 全部放出来（正常打完 / 被跳过 / 出错，全走这儿）
    function finish() {
      bub.classList.remove('typing');
      bub.classList.add('tydone');     // ⚠⚠ 必须加 —— 不加就永远是隐藏的（见 TALK_CSS）
      flush();
      done();
    }

    var total = 0;
    for (var j = 0; j < fulls.length; j++) { total += fulls[j].length; }
    if (!total) { finish(); return; }   // 纯动作气泡（`（他笑）` 剥完是空）⇒ 没有可打的字

    var wi = 0, wn = 0, seen = 0;       // 现在揭到第几个节点 / 第几个字 / 已揭字数

    function step() {
      if (tySkip) { finish(); return; }  // 🛑 她点了 ⇒ 这条剩下的立刻全出
      // ⚠ 一个 tick 可能连揭好几个字：18ms 已经是"看得见在打"的节拍，
      //    但长句（40+ 字）要 0.7 秒 —— 差不多是真人打一句的观感，不用再加速。
      while (wi < fulls.length && wn >= fulls[wi].length) { wi++; wn = 0; }
      if (wi >= fulls.length) { finish(); return; }

      nodes[wi].nodeValue = fulls[wi].slice(0, ++wn);
      seen++;
      // ⚠ 每揭几个字就滚一下：气泡在长高，不跟会打到底部看不见。
      //    用 `near()` 的理由跟 `toBottom()` 一样 —— 她翻着旧消息时不许抢滚动。
      if (seen % 4 === 0) { toBottom(false); }
      window.setTimeout(step, TY_MS);
    }

    // ⚠ 起点要 `toBottom(true)`：这条气泡刚插进来时只有它自己高，
    //    它一边长一边看才叫"他正在打给你"。
    toBottom(true);
    window.setTimeout(step, TY_MS);
  }

  // ============================================================
  // ⌛ 段与段之间的「他又在打下一句」（2026-10-02 她提：「等待的时间只有第一段有，
  //    剩下的段没有缓冲直接出来了」）
  // ------------------------------------------------------------
  // ⭐ 病因：打字机是**串行**的（第 1 段逐字打完立刻接第 2 段），而「正在输入」那三个点
  //    只在「点发送 → 回复回来」那一整段里出现过一次 ⇒ 第 1 段一出来，后面几段像连珠炮。
  // ⭐ 修法：第 2 段起，每段**开始之前**插一次三个点、停一会儿再撤掉 ⇒
  //    像真人连发几条（发一条 → 停一下 → 再发一条），而不是一口气倒出来。
  // ⚠⭐ **第 1 段不加**：它前面已经吃到了请求往返那 1~2 秒的点，再停一次就是拖沓。
  // ⚠⭐ 停顿**必须能被打断**：她点屏幕 = 跳过 ⇒ 切成 100ms 一片检查 `tySkip`，
  //    最多多等 100ms 就放行 —— 否则「跳过」会卡在一个她看不见的计时器上，
  //    她会觉得「点了没反应」。
  // ⚠ 关动效（`tyReduced()`）那条分支**根本不进 `tyRun`** ⇒ 完全没有额外停顿，照旧直出。
  // ⚠ 他只回一段时 `first` 一次就走完 ⇒ 一次停顿都不会有，零副作用。
  // ============================================================
  var TY_GAP_BASE = 500;      // 段间停顿基数（毫秒）—— 她选的方案 A
  var TY_GAP_RAND = 500;      // 再叠 0~500 的随机 ⇒ 实际 500~1000ms（不是整齐的机器间隔）
  var TY_TICK = 100;          // 「她是不是点了跳过」的检查粒度：最多多等这么久

  function tyPause(ms, done) {
    var left = ms;
    (function tick() {
      if (tySkip || left <= 0) { done(); return; }   // 🛑 她点了 ⇒ 立刻放行
      left -= TY_TICK;
      window.setTimeout(tick, TY_TICK);
    })();
  }

  function tyRun(list, after) {
    // 串行跑完 `list` 里所有 `[data-ty]` 的气泡，然后调 `after()`。
    // ⚠⭐ `tyQueue` 是「有没有在打字」的**唯一真相源**（跳过监听只认它）——
    //    进门就赋、`finally` 里清，中间任何异常都不能让它卡在非 null（卡住 = 之后
    //    每次点屏幕都置跳过旗标，下一轮第一个字都打不出来）。
    var q = list.slice();
    tyQueue = q;
    var first = true;
    (function next() {
      if (!q.length) { tyQueue = null; after(); return; }
      var bub = q.shift();
      if (first) { first = false; tyPlay(bub, next); return; }  // 第 1 段：前面已经等过了
      // ⌛ 第 2 段起：插三个点 → 停 → 撤掉 → 再打这一条
      var w = himWait();
      box.appendChild(w);
      toBottom(true);          // 让那三个点进她视线（它可能刚好落在屏幕下沿外面）
      tyPause(TY_GAP_BASE + Math.floor(Math.random() * TY_GAP_RAND), function () {
        // ⚠ 撤不掉不致命，但**不能让它抛出去** —— 会打断整条串行链，后面几段全不出来。
        try { if (w && w.parentNode) { w.parentNode.removeChild(w); } } catch (e) {}
        tyPlay(bub, next);
      });
    })();
  }

  function tyCollect(added) {
    // 从**刚插进 DOM 的那批节点**里挑出要打字的节点（跳过她的气泡 / `.sys` 兜底句）。
    // ⚠⚠⚠ 必须查「已经插进文档」的节点，**不能**再拿 `frag` 字符串套一层临时容器去查 ——
    //    那个临时容器里的元素是**游离的另一份**（跟 `insertAdjacentHTML` 建出来的
    //    根本不是同一个对象）。拿游离节点去截字/加类，改动全落在垃圾上：
    //    页面上气泡**原样完整**、永远不 `typing`、永远不 `tydone`，而控制台**一声不响**
    //    —— 2026-10-01 这一版第一稿就是这么写的，真机 0.03s 一采样才看出来。
    // ⚠ 入参是 `insertAdjacentHTML` 之前 `box` 里的节点数（`mark`），
    //    它之后新增的那几个才是这一轮的气泡。
    var out = [], kids = box.children;
    for (var i = added; i < kids.length; i++) {
      var b = kids[i].querySelector('.bub[data-ty]');
      if (b) { out.push(b); }
    }
    return { nodes: out };
  }

  ta.addEventListener('input', grow);
  // 回车发送、Shift+回车换行（手机上回车就是换行，靠「发送」按钮）
  ta.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
    }
  });

  // 进页面（含刷新、含整页 POST 回来）先钉到底 —— **这是原来唯一滚的一次**，
  // 但它当年排在脚本最末、且只会滚 window，所以刷新后经常停在半截。
  toBottom(true);
  // ⚠ 再补两拍：头像 `<img>` 是异步加载的，加载完高度会变 ⇒ 一拍可能滚早。
  setTimeout(function () { toBottom(true); }, 0);
  window.addEventListener('load', function () { toBottom(true); });

  if (!window.fetch) { return; }

  form.addEventListener('submit', function (e) {
    var t = ta.value.replace(/^\s+|\s+$/g, '');
    if (!t) { e.preventDefault(); return; }
    e.preventDefault();
    if (sending) { return; }
    sending = true;
    if (btn) { btn.disabled = true; }
    // ⭐ 立刻清空 —— 她能在等回复的时候接着打字（但按钮锁着，保证一次只跑一轮，
    //    不然两条 get_reply 并发会把 `memory/*.json` 互相覆盖，直接丢话）。
    ta.value = '';
    grow();
    // ⚡ 乐观插入：她的气泡**立刻**上屏，不等这一整轮往返（详见 `bubbleRow()` 头顶那段）。
    //    ⚠ 两条都留引用：他的片段到了要撤掉那三个点（`waitRow`）；
    //      fetch 失败退回整页提交前要撤掉她的气泡（`herRow`）——
    //      否则会出现「输入框里已经恢复这句话、气泡里也还挂着一句」的重复观感。
    var herRow = bubbleRow('me', 'bub', t);
    box.appendChild(herRow);
    var waitRow = himWait();
    box.appendChild(waitRow);
    // ⚠⭐ 发出去就先滚（`force`）—— 她刚发完，视线必须跟着自己那条气泡走，
    //    不能等回复来了才动。这一步是「原来完全没滚」和「现在不拉也能看」的分界。
    toBottom(true);

    var body = new URLSearchParams();
    body.set('text', t);
    fetch('/chat/send', {
      method: 'POST',
      headers: { 'X-Requested-With': 'fetch' },
      body: body
    }).then(function (r) {
      if (!r.ok) { throw new Error(r.status); }
      return r.text();
    }).then(function (frag) {
      // ⌛ 他的话到手 ⇒ 撤掉「正在输入」那三个点。
      //    ⚠ 必须 try：这儿一旦抛异常就会掉进下面的 `catch`，
      //      把**已经拿到的回复**再整页重发一遍（丢话比多留一条点严重得多）。
      try {
        if (waitRow && waitRow.parentNode) { waitRow.parentNode.removeChild(waitRow); }
      } catch (e) {}
      // ⚠⭐ `mark` 必须在 `insertAdjacentHTML` **之前**取 —— 它是这一轮之前的子节点数，
      //    插完之后从 `mark` 往后数，正好就是刚加进来的那几条 `.row`。
      //    ⚠ 不能用「`frag` 里数出来几个」或「再解析一遍 `frag`」代替：
      //      前者把「服务端给的数据」当成「DOM 里的节点」，后者直接拿到游离的一份。
      var mark = box.children.length;
      box.insertAdjacentHTML('beforeend', frag);
      var n = document.getElementById('new');
      if (n) { n.removeAttribute('id'); }
      unlock();
      // ⚠⭐ 他的回复到手**必须再滚一次**（`force`）—— 这是她抱怨的那一刻：
      //    新气泡冒在输入栏下面看不见，得自己往下拉。放在 `unlock()` 之后
      //    是因为 `unlock()` 会 `ta.focus()`、输入框可能变高一点。
      // ⌨️ 2026-10-01：原来这里是「直接 `toBottom(true)` 收工」，
      //    现在改成**先打字、打完再收底** —— 打字过程中气泡在长高，
      //    `tyPlay()` 自己每几个字滚一次；等全部打完再钉死一次最底。
      // ⚠ `refreshMood()` **不放在打字后面**：情绪是异步判定（1.5s / 4s 两次拉），
      //    跟打字各走各的、互不依赖 —— 串起来只会让顶栏的心情也晚 1~2 秒。
      refreshMood();          // 💗 等他那边判完心情（1.5s / 4s 各拉一次）

      var picked = tyCollect(mark);
      if (!picked.nodes.length || tyReduced()) {
        // 没得打（比如他一个字都没回、只有 `.sys` 那句）或用户关了动效 ⇒ 老样子收底。
        // ⚠⚠ 这条分支**必须调 `toBottom(true)`** —— 上面已经把原来那次收底搬进
        //    `tyPlay()` 了，不在这儿补一发的话，这些情况页面就停着不动了。
        toBottom(true);
        return;
      }
      tySkip = false;
      tyRun(picked.nodes, function () {
        tySkip = false;       // 🔁 打完复位，下一轮不带着上一轮的「跳过」旗标跑
        toBottom(true);       // 全部打完 ⇒ 钉到最底（最后一条完整可见）
      });
    }).catch(function () {
      // 拿不到片段 ⇒ 退回整页提交（她那句话别丢）
      // ⚠ 先把乐观插入的那两条撤掉：文本马上要还回输入框，气泡里再留一份就是两条。
      //    整页提交后页面重载本来也会清掉，但**提交前**这一瞬间不能让她看见重复。
      try {
        if (waitRow && waitRow.parentNode) { waitRow.parentNode.removeChild(waitRow); }
        if (herRow && herRow.parentNode) { herRow.parentNode.removeChild(herRow); }
      } catch (e) {}
      ta.value = t;
      grow();
      unlock();
      form.submit();          // ⚠ 这条是整页跳，新页面由上面那两拍 `toBottom(true)` 接管
    });
  });

  // ============================================================
  // 💗 顶栏心情自动切换（2026-10-01）
  // ⚠⭐ 为什么不能顺手塞进 `/chat/send` 的响应里：判定是**异步线程**
  //   （`Rafayel_mood.spawn_update`），send 返回那一刻它还没算完 ⇒ 拿到的永远是上一轮的值。
  //   ⇒ 只能等一会儿再单独拉一次；拉两次是给慢的那次兜底（判定超时上限 8s）。
  // ⚠ 淡出 → 换字换色 → 淡入：纯换 textContent 是硬切，那就谈不上丝滑。
  // ⚠ 没变就直接 return，否则每次发送都白闪一下。
  // ============================================================
  function refreshMood() {
    function pull() {
      fetch('/chat/mood').then(function (r) { return r.json(); }).then(function (d) {
        var el = document.getElementById('mood-slot');
        if (!el) { return; }
        var sig = d.calm ? 'calm' : d.text;
        if (el.getAttribute('data-sig') === sig) { return; }
        el.style.opacity = 0;
        setTimeout(function () {
          if (d.calm) {
            el.style.color = '';
            el.innerHTML = '在<span class="dot"></span>';
          } else {
            el.style.color = 'var(' + d.color + ')';
            el.textContent = d.text;
          }
          el.setAttribute('data-sig', sig);
          el.style.opacity = 1;
        }, 250);
      }).catch(function () {});
    }
    setTimeout(pull, 1500);
    setTimeout(pull, 4000);
  }

  // ============================================================
  // 🛑 点任意处 = 跳过打字（2026-10-01 她选的：**点输入框/屏幕任意处跳过**）
  // ------------------------------------------------------------
  // ⚠ 用 `document` 上的**捕获**监听，理由两条：
  //   ① 她人在哪都可能点（气泡上、输入框里、屏幕空白处）⇒ 挂一个地方最省；
  //   ② 捕获阶段先于冒泡跑 ⇒ 就算她点的是小喇叭 `.spk`（那条自己的 handler 会
  //      `preventDefault` + 起播），跳过也已经生效了 —— 不会出现"点了但没跳过"。
  // ⚠ 只在**真的在打字时**才置旗标：`tyQueue` 是「有没有在跑」的唯一真相源，
  //    没在打字时点屏幕不该有任何副作用（正常情况下她只是点一下）。
  // ⚠ 不 `preventDefault()`、不 `stopPropagation()`：
  //    这一点只是"加速显示"，不该抢掉点击本身该干的事（点输入框要聚焦、
  //    点喇叭要播音、点链接要跳转）。
  // ⚠ `pointerdown` 而不是 `click`：`click` 要等手指抬起，打字机在跑的时候
  //    那几十毫秒的延迟是感觉得到的。
  // ============================================================
  document.addEventListener('pointerdown', function () {
    if (tyQueue) { tySkip = true; }
  }, true);

  // ============================================================
  // 🔊 点句子听他说（2026-09-29）
  // ⚠⭐ 用**事件委托**挂 `#chat`，不是进页面时逐个绑定：
  //    `/chat/send` 是 fetch 回来 `insertAdjacentHTML` 追加的气泡，
  //    逐个绑的话**新来的那几条点不动**（而且在真机上得先滑到底才发现）。
  // ⚠ `<a href="/chat/voice?h=…">` 必须 `preventDefault()`：
  //    不拦的话浏览器直接导航去那个 wav，聊天页整个被顶掉。
  // ⚠ 同一时刻只留一路音频：戳第二句先把上一句 `pause()` ——
  //    否则两句叠着念（她第一次用就点得出来）。
  // ============================================================
  var cur = null, curAu = null;

  function hush() {
    if (curAu) { try { curAu.pause(); } catch (e) {} curAu = null; }
    if (cur) { cur.classList.remove('busy', 'on', 'bad'); cur = null; }
  }

  box.addEventListener('click', function (e) {
    var a = (e.target && e.target.closest) ? e.target.closest('.spk') : null;
    if (!a) { return; }
    e.preventDefault();
    if (cur === a) { hush(); return; }          // 再戳同一句 = 停
    hush();
    cur = a;
    a.classList.add('busy');
    var au = new Audio(a.getAttribute('href'));
    curAu = au;
    au.addEventListener('playing', function () {
      a.classList.remove('busy'); a.classList.add('on');
    });
    au.addEventListener('ended', function () { if (cur === a) { hush(); } });
    // 404（这段已经滚出历史）/ 503（TTS 那边挂了）都落这儿 —— 闪一下就好，
    // 别把她按在一个「看不出坏没坏」的状态里。
    au.addEventListener('error', function () {
      if (cur !== a) { return; }
      hush();
      a.classList.add('bad');
      setTimeout(function () { a.classList.remove('bad'); }, 1400);
    });
    var p = au.play();
    if (p && p.catch) { p.catch(function () {}); }   // 失败统一交给上面那个 error
  });
})();
</script>"""

CHAT_MAX_INPUT = 800            # 她一条最多多少字（防手滑粘长文烧 token）
_CHAT_LOCKS = {}
_CHAT_LOCKS_GUARD = threading.Lock()


def _chat_lock(uid):
    """拿到某个 uid 的对话锁（没有就现造一个）。"""
    with _CHAT_LOCKS_GUARD:
        lk = _CHAT_LOCKS.get(uid)
        if lk is None:
            lk = threading.Lock()
            _CHAT_LOCKS[uid] = lk
        return lk


def _memory_path(uid):
    return os.path.join(MEMORY_DIR, "%s.json" % _safe_uid(uid))


def _read_talk(uid):
    """读 `memory/{uid}.json` 的对话历史（**只读**），返回 [(role, text), …]。"""
    p = _memory_path(uid)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("[💬] 读记忆失败（%s）：%s" % (uid, e))
        return []
    out = []
    for m in (data.get("messages") or []):
        if not isinstance(m, dict):
            continue
        role, txt = m.get("role"), m.get("content")
        if role in ("user", "assistant") and isinstance(txt, str) and txt.strip():
            out.append((role, txt))
    return out


def _read_days(uid):
    """
    🗓 读 `memory/{uid}.json` 的**日小结**（`day_summaries`），返回 [(date, text), …]。

    ⭐⭐ 为什么要有这个函数（她 2026-10-01 反馈「三天前的记录不见了」）：
      `roll_days()` 一跨天就把**那一整天的原话从 `messages` 里摘走**（物理删，
      不是网页不显示），换成这里的一句小结。而 `/chat` 原来**只渲染 `messages`**
      ⇒ 前一天说过什么，在网页上连个痕迹都没有，看着就像凭空蒸发。
      ⇒ 把小结补回气泡区顶部，她往上翻至少能看到「9月24日：……」。
    ⚠ 只是**补显示**，不恢复原话 —— 原话真的没了，这是 `DAY_ROLL` 的既定设计
      （想留原话得改 `DAY_ROLL`，那是另一个决定，别在这儿顺手改）。
    ⚠ 跟 `_read_talk()` 同一个口径：**直接读 json**，不 import `Rafayel_memory`
      （ADR-22：网页端不许直接引引擎的读写模块）。只读、不写。
    ⚠ 自己吞异常：小结读挂了顶多是不显示这几天，不能让整页打不开。
    """
    p = _memory_path(uid)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("[💬] 读日小结失败（%s）：%s" % (uid, e))
        return []
    raw = data.get("day_summaries")
    if not isinstance(raw, list):      # ⚠ 类型是对象/字符串时 `or []` 会迭代 key/字符
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        d = str(item.get("date") or "").strip()
        t = str(item.get("text") or "").strip()
        if d and t:
            out.append((d, t))
    return out


# ⚠ 小结正文**自带日期**（`_summarize_day` 生成的就是「9月24日星期四，她…」开头），
#   再把 `day` 字段拼一个「9月24日」在前面 ⇒ 屏幕上会出现「9月24日 9月24日星期四…」。
#   ⇒ 用这条把正文开头的日期**剥出来**单独加粗，而不是另加一份。
_HEAD_DATE = re.compile(r"^\s*(\d{1,2}月\d{1,2}日(?:星期[一二三四五六日])?)\s*[,，、]?\s*")


def _arch_path(uid):
    """🗄 原话留档文件路径 `memory/{uid}_archive.json`（只读，跟 `_read_days` 一个套路）。"""
    return os.path.join(MEMORY_DIR, "%s_archive.json" % _safe_uid(uid))


def _archived_days(uid):
    """
    🗄 哪些天**存了原话**，返回 `set(date)`。

    ⭐ 为什么要有它：小结是**每天都有**的，但原话存档只有 `ARCHIVE_KEEP` 打开之后
      才写 ⇒ 老数据（她 09-24 / 09-25 那两条）**只有小结、没有原话**（原话早被删了）。
      ⇒ 只对真有存档的那天挂「看原话」链接，没存档就别挂一个点进去是空页的死链。
    ⚠ 直接 `json.load`，**不 import** `Rafayel_archive`（ADR-22：网页端不引引擎模块）。
    ⚠ 读挂了返回空集合 —— 顶多是「链接不出现」，绝不能让整页打不开。
    """
    p = _arch_path(uid)
    if not os.path.isfile(p):
        return set()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("[💬] 读原话存档失败（%s）：%s" % (uid, e))
        return set()
    if not isinstance(data, dict):
        return set()
    days = data.get("days")
    if not isinstance(days, list):
        return set()
    out = set()
    for d in days:
        if isinstance(d, dict):
            dt = str(d.get("date") or "").strip()
            if dt:
                out.add(dt)
    return out


def _arch_day(uid, date):
    """
    🗄 某一天的原话，返回 [(role, text), …]；没有存档 ⇒ 空列表。

    ⚠ 类型闸（全站红线）：`days`/`messages` 都**先 isinstance 再看内容**。
    ⚠ `date` 来自 URL（她自己能改）⇒ 只做**等值比对**，不拼路径、不 eval
      ⇒ 改不出越权读别人的档（文件名是登录 uid 定的，跟 `date` 无关）。
    """
    p = _arch_path(uid)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("[💬] 读原话存档失败（%s）：%s" % (uid, e))
        return []
    if not isinstance(data, dict):
        return []
    days = data.get("days")
    if not isinstance(days, list):
        return []
    for d in days:
        if not isinstance(d, dict) or str(d.get("date") or "") != date:
            continue
        msgs = d.get("messages")
        if not isinstance(msgs, list):
            return []
        out = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            role, txt = m.get("role"), m.get("content")
            if role in ("user", "assistant") and isinstance(txt, str) and txt.strip():
                out.append((role, txt))
        return out
    return []


def _day_head(d):
    """`2026-09-24` ⇒ `9月24日`。⚠ 只做显示，失败就原样返回（不猜）。"""
    try:
        y, m, dd = d.split("-")
        return "%d月%d日" % (int(m), int(dd))
    except Exception:
        return d


def _split_day_text(d, t):
    """
    把一条小结拆成 (日期, 正文)，**正文里不再重复日期**。

    ⭐ 优先用**正文开头自带的日期**（它连星期都有，比 `date` 字段信息量更大）；
      正文万一没带（老数据 / 模型没照格式写）⇒ 退回 `date` 字段算一份。
    ⚠ 剥不干净就整条当正文、日期用兜底 —— 宁可日期朴素，也不能把正文切坏。
    """
    m = _HEAD_DATE.match(t or "")
    if m:
        return m.group(1), t[m.end():].strip()
    return _day_head(d), (t or "").strip()


def _segs(text):
    """
    一条消息 ⇒ 几条气泡。

    ⭐ 跟 QQ 侧同一个口径（2026-09-22 她拍板）：「分段」就是**一条气泡一段**，
      不是一条气泡里换行（换行她看着还是一大坨）。
    ⚠ QQ 那边还会按字数再细切（`_split_bubble`，在 `Rafayel_bot.py` 里）——
      网页端气泡没有长度上限，不用切。
    """
    return [x.strip() for x in str(text or "").replace("\r\n", "\n").split("\n")
            if x.strip()]


def _pic_only(text):
    """
    这一段剥掉 `[表情:x]` 和**括号动作**之后一个字都不剩吗？（⇒ 纯表情段，走图片气泡）

    ⚠ 用**同一个** `_STICKER_TXT` 正则（`base.py` 里 `_rich` 用的那条）——
      两处各写一个正则的话，将来标签语法一改，一边认一边不认，图会画在错的位置。
    ⚠ 只认 `[表情:x]` 不够：`（他挑眉）[表情:卖萌]` 这种**带动作的纯表情**也要算。
      但也**必须要求确实有表情标记** —— 他真正只发一个动作、不发图时不该被当成图片气泡
      （那种气泡里一个字都没有、图也没有，加 `.pic` 只会把内边距吃掉、看着像空泡）。
    ⚠ 返回 True 的真实例子（`card/stickers.md` 的语料形态）：
      `[表情:得意]` / `（他笑）[表情:卖萌]`
    """
    if not _STICKER_TXT.search(text or ""):
        return False                       # 压根没有表情标记 ⇒ 不是图片气泡
    rest = _STICKER_TXT.sub("", text or "")
    return not strip_actions(rest)[1]      # [1] = 剥掉括号后的台词列表；空 = 没字


def _him_av():
    """他那侧的头像 —— 项目素材，走白名单路由 `/asset/qiyu`。"""
    return '<img src="/asset/qiyu" alt="祁煜">'


def _her_av(uid, name):
    """她那侧：传过头像就用真图，没传就退回「名字首字」小圆片。"""
    u = _avatar_url(uid)
    if u:
        return '<img src="%s" alt="">' % u
    return _esc((name or "你")[0])


VOICE_PATH = "/chat/voice"
_VOICE_TAG = re.compile(r"\[[^\[\]]*\]")   # `[表情:涂鸦叽]` 这类方括号标记
_VOICE_QUOTE = re.compile(r"「([^」]*)」")  # 剧情格式的台词标记
# ⭐ 开关（2026-09-29 加）：整条消息里出现过「」⇒ 判为「剧情格式」，
#   只念引号内的台词，引号外的旁白一个字都不念。
#   关掉它就退回上一版行为（只剥圆括号 ⇒ 剧情格式的旁白会被整段念出去）。
VOICE_QUOTE_MODE = True
_VOICE_PUNCT = "，。！？…、；：,.!?"
_VOICE_SPK = ('<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">'
              '<path d="M3.2 6.1h2.3L8.9 3.3v9.4L5.5 9.9H3.2z" fill="currentColor"/>'
              '<path d="M11.1 6.1a3 3 0 0 1 0 3.8" fill="none" stroke="currentColor" '
              'stroke-width="1.4" stroke-linecap="round"/></svg>')


def _vhash(text):
    """
    一段气泡原文 ⇒ 语音路由用的短哈希（16 位十六进制）。

    ⚠⭐ 为什么用哈希，而不是「第几条」：`Rafayel_memory.truncate_history()`
      是**从头部裁**的（`messages = [system] + recent`）⇒ 「第 N 条」这个下标
      会随着聊下去往前漂 —— 她点一个旧气泡，服务端可能读到**另一句话**，
      而且**不报错，只是念错内容**。哈希是从文本算的、跟位置无关
      ⇒ 只会在「这句已经滚出历史」时失败（404），不会张冠李戴。
    ⚠ 两侧必须算**同一份**文本 —— 2026-09-29 起哈希的是 `_voice_text()` 提取后的**台词**，
      而不是气泡原文：`_bubble` 按 `say` 算、路由里对每段重算同一个 `say` ⇒ 天然一致。
      （`add_assistant_message` 原样落盘 + `_voice_text` 是纯函数 ⇒ 两次必然算出同一份。）
    """
    return hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()[:16]


def _voice_text(text, quote=None):
    """
    气泡原文 ⇒ 该**念出来**的台词。

    ⭐ 项目里并存两种书写格式，靠 `quote` 分流（由 `_says_of` 按**整条消息**判定后传进来）：
      · `quote=True`（剧情格式，整条里出现过「」）⇒ **只取「」里的台词**；
        引号外的旁白（「他站起来往门口走，顺手接过你手里的袋子…」）一个字都不念。
      · `quote=False`（日常格式，system prompt 规定的 `（动作）话跟着写在同一段`）
        ⇒ 剥掉括号动作后把话念出来（跟上一版行为一致）。
      `quote=None` ⇒ 就地按本段自己猜（只有单段调用才不用传，页面与路由都显式传）。

    ⚠⭐ 为什么格式判定必须按**整条消息**、不能按单段：剧情格式里
      「他往厨房走，走了两步又停下来。」这种纯旁白段**自己身上没有「」**，
      逐段判就会把它当成日常格式 ⇒ 又整段念出去（这正是这次要修的 bug）。
    ⚠ 两件清洗都得做，否则读出来是噪音：
      · `（他笑）` / `（挑眉）` —— `strip_actions` 管这个
      · `[表情:涂鸦叽]` —— 页面上 `_rich` 把它画成小圆片，但 TTS 会照字念「表情 涂鸦叽」
    ⚠ 剥完没台词（纯动作 / 纯旁白气泡）⇒ 返回 `""`，调用处**不给它渲染小喇叭** ——
      一个点了必然失败的按钮不如没有。
    """
    t = _VOICE_TAG.sub("", str(text or ""))
    if quote is None:
        quote = VOICE_QUOTE_MODE and ("「" in t)

    if quote:
        parts = []
        for x in _VOICE_QUOTE.findall(t):
            # 引号里若还夹着括号动作（「（他笑）你回来了。」）⇒ 再剥一层
            parts.extend(strip_actions(x)[1])
    else:
        _actions, parts = strip_actions(t)

    out = ""
    for x in parts:
        x = x.strip()
        if not x:
            continue
        # 括号被 `strip_actions` 换成了换行 ⇒ 断开处补个停顿；
        # 但上一句已经带标点就不补（不然会冒出「。」「，」连着两个）。
        if out and out[-1] not in _VOICE_PUNCT:
            out += "，"
        out += x
    return out.strip()


def _says_of(text):
    """
    一条消息 ⇒ **每个气泡**该念的内容（与 `_segs()` 的段一一对应、同序）。

    ⭐ 格式判定在**整条消息**这一层做完，再逐段套用 —— 理由见 `_voice_text()`。
      返回值里 `""` 表示「这一段没台词」⇒ 不出小喇叭。
    """
    segs = _segs(text)
    quote = VOICE_QUOTE_MODE and ("「" in str(text or ""))
    return [_voice_text(seg, quote) for seg in segs]


def _bubble(who, text, her_av, say=None, typing=False):
    """
    一个气泡。`who` 取 `"her"`（右边）或 `"him"`（左边）。

    ⭐ 他那一侧右下角挂个小喇叭（`.spk`，戳一下放这一句的语音）。
      ⚠ 它是 `<a href>` + `hidden`：有 JS ⇒ CSS 把它显形、点击走 `new Audio()`；
        **没 JS ⇒ 它根本不出现**（而不是留个戳了没反应的死图标）。
        用 `<a>` 而不是 `<button>`，是为了让「这一句可以听」这件事在
        键盘 / 读屏 / 右键菜单里也说得通。
    ⚠ `say` 由 `_says_of()` 按**整条消息**算好传进来（剧情格式里旁白段会是 `""`）；
      不传就退化成「只看这一段自己」（`_voice_text` 自行判定）。
    ⚠ 没台词（纯动作 / 纯旁白）**不挂喇叭** —— 挂了也点不出声音。
      ⚠ 哈希算的是 `say`（提取后的台词），路由那边对每段重算同一个 `say` 来对。

    ⌨️ `typing=True`（2026-10-01 她提的「打字机」）：
      ⭐ **只由 `/chat/send` 这条 fetch 路传** —— 历史页（`/chat` 整页渲染）一律不传
        ⇒ 「只有新发出来的消息打字，历史消息不用」，这正是她选的那个口径。
      ⚠ 标记是**加在气泡外层 `.bub` 上的 `data-ty`**，不是外层 `.row` ——
        JS 要逐字揭开的就是 `.bub` 里面的东西；`.row` 里还含头像，不该被算进去。
      ⚠⚠ 这个属性**对没有 JS 的浏览器零影响**：CSS 里那几条打字规则全部挂在
        `.js` 根类下面（见 `TALK_CSS`），而 `.js` 只有脚本跑起来才加得上
        ⇒ 没 JS ⇒ 属性在 HTML 里、但没有任何规则认它 ⇒ 气泡照旧完整直出。
        **这是本功能的渐进增强底线，别把它改成 `hidden` 属性那种服务端就藏起来的东西。**
    """
    if say is None:
        say = _voice_text(text)
    ty = ' data-ty="1"' if typing else ""
    # 🎨 A1（她 2026-10-01 定的）：整段剥掉 `[表情:x]` 后一个字都不剩 ⇒ 这是**纯表情段**，
    #    给它自己的「图片气泡」形态（`.bub.pic`，见 `base.py` 的 `.stk` 那段）。
    #    ⚠ 原作语料里 6 成以上是这个形态 —— 他不说话、只甩一张图，所以这不是边角情况。
    #    ⚠ 纯图段**一定没有台词**（`_voice_text` 把整个 `[表情:x]` 剥掉后是空串）
    #      ⇒ 天然走「不挂喇叭」那条，不用额外判。
    #    ⚠ `.pic` 只加在**他**那一侧：她这边的表情走 QQ，网页端她只发文字，
    #      但万一将来她也能发表情，那边要不要 `.pic` 是另一个决定，别顺手改。
    pic = (who != "her") and _pic_only(text)
    cls = "bub pic" if pic else ("bub him" if say else "bub")
    if who == "her":
        return ('<div class="row me"><div class="av">%s</div>'
                '<div class="bub"%s>%s</div></div>' % (her_av, ty, _rich(text)))
    if not say:
        return ('<div class="row"><div class="av">%s</div>'
                '<div class="%s"%s>%s</div></div>' % (_him_av(), cls, ty, _rich(text)))
    return ('<div class="row"><div class="av">%s</div>'
            '<div class="%s"%s><span class="tx">%s</span>'
            '<a class="spk" href="%s?h=%s" hidden aria-label="播放这一句" '
            'title="播放这一句">%s</a></div></div>'
            % (_him_av(), cls, ty, _rich(text), VOICE_PATH, _vhash(say), _VOICE_SPK))


def _shown_name(uid):
    """网页里存的名字优先，没有就用他记住的称呼（画像 name，只读）。"""
    rec = (_load_users().get(uid) or {})
    return (rec.get("display_name") or "").strip() or (compute(uid, MEMORY_DIR)["name"] or "")


def _open_once(uid):
    """
    他先开口 —— **只在真的第一次聊时**（`take_opening` 自己判断，老朋友返回 ""）。

    ⭐ 贴的是原作设定：游戏里她打开主页，他就会主动说一句（README 里那条）。
    ⚠ 这是本节**唯一「读页面就写盘」**的地方：开场白必须进历史，
      不然他下一轮不记得自己刚说过什么。
    ⚠ 同步函数（里面 `save_memory` 写文件）⇒ 调用处丢 `asyncio.to_thread`。
    ⚠ 先判 `exists` 再拿锁 —— 顺序反了的话，她正聊着（文件已存在）时
      开个新标签页，那个 GET 会卡在锁上等上一轮跑完（十几秒）。
    """
    if os.path.exists(_memory_path(uid)):
        return ""
    with _chat_lock(uid):
        try:
            return take_opening(uid) or ""
        except Exception as e:
            print("[💬] 开场白失败（不影响后面）：%s" % e)
            return ""


def _unlock_tick(uid):
    """
    跨级解锁记账 —— `Rafayel_bot.maybe_send_unlock` **去掉「发送」那一半**。

    ⚠ 为什么 web 侧也得做：`unlocked` 原来**只在收到 QQ 私聊时**才写 ⇒
      冻结期在网页上聊到升级，`/affinity` 的「他说过的那句话」和 `/messages`
      的解锁条数会停在那儿不动，看着像坏了。
    ⚠ 只记账、**一个字都不发**（QQ 侧本来就是 `AFFINITY_UNLOCK_SEND = False`）。
    ⚠ 写的是 `memory/{uid}_daily.json` —— 属于本节开门的那条口子，别再扩散。
    """
    if not AFFINITY_UNLOCK:
        return
    try:
        rec = load_unlocked(uid)
        if rec is None:
            # 第一次接入：只记「现在几级」，**不补发历史**（跟 bot 侧同口径）
            save_unlocked(uid, init_unlocked(current_level(uid, MEMORY_DIR)))
            return
        item = pending_unlock(uid, MEMORY_DIR)
        if not item:
            return
        got_sms = [int(x) for x in (rec.get("sms") or [])]
        for lv in (item.get("mark_sms") or []):
            if lv not in got_sms:
                got_sms.append(int(lv))
        rec["sms"] = sorted(got_sms)
        send = item.get("send")
        if send:
            got = list(rec.get("eggs") or [])
            if send.get("key") not in got:
                got.append(send["key"])
            rec["eggs"] = got
        rec["level"] = max(int(rec.get("level") or 0), int(item.get("level_now") or 0))
        save_unlocked(uid, rec)
    except Exception as e:
        print("[💞] 跨级记账失败（不影响对话）：%s" % e)

def _mood_html(uid):
    """顶栏右侧那一格：有心情 ⇒ 彩色文案；否则 ⇒ 原样「在 ●」。

    ⚠ 自己吞异常 —— 心情读挂了顶多是「看不出他今天怎么样」，不能让整页打不开。
    ⚠ `data-sig` 给前端比对用：没变就不重放淡入淡出。
    """
    try:
        m = mood_hint(uid)
    except Exception as e:
        print("[💗] 心情渲染失败（退回在线点）：%s" % e)
        m = None
    if not m:
        return ('<span class="hint" id="mood-slot" data-sig="calm">'
                '在<span class="dot"></span></span>')
    text, color = m
    return ('<span class="hint" id="mood-slot" data-sig="%s" '
            'style="color:var(%s)">%s</span>' % (text, color, _esc(text)))

def _one_turn(uid, text):
    """跑一轮对话（同步、**持锁**）⇒ 调用处丢 `asyncio.to_thread`。"""
    with _chat_lock(uid):
        reply = get_reply(text, uid) or ""
        _unlock_tick(uid)
    return reply


def _talk_body(uid, msgs, shown, tail=""):
    """
    气泡区 HTML（`<div class="chat">` 里面那一段）；`tail` 追加在最后。

    🗓 顶部那几行「前几天」（2026-10-01 加）：`roll_days()` 把旧原话摘走后换成的
      日小结，原来**根本不渲染** ⇒ 她往上翻只看到「记录凭空消失」。
      ⇒ 现在补在最前面，她至少知道那天聊过什么。
    ⚠ 小结排在**最前**：气泡区是「旧的在上、新的在下」，跟时间顺序一致。
    ⚠ `rows` 为空但**有小结**时不能落进「还没聊过」那一支 —— 那样小结会被吃掉。
    """
    her = _her_av(uid, shown)
    rows = []
    # 🗓 前几天的小结（旧原话被 roll_days 摘走后留下的那句话）
    #    ⭐ 真存了原话的那天，再挂一个「看那天的原话 →」（她 10-01 定「原话不删，供用户查看」）。
    #    ⚠ 只对 `_archived_days()` 里有的那天挂 —— 老数据原话早删了，挂了是点进去空页的死链。
    archived = _archived_days(uid)
    for d, t in _read_days(uid):
        head, body = _split_day_text(d, t)
        link = ('<a class="arlink" href="/chat/history?day=%s">看那天的原话 ›</a>'
                % quote(d)) if d in archived else ""
        rows.append('<div class="sys daysum"><b>%s</b> %s%s</div>'
                    % (_esc(head), _esc(body), link))
    for role, txt in msgs:
        # ⚠ 逐段配 `_says_of()` 的结果（同序同长）—— 格式判定要看**整条** txt
        for seg, say in zip(_segs(txt), _says_of(txt)):
            rows.append(_bubble("her" if role == "user" else "him", seg, her, say))
    if tail:
        rows.append(tail)
    if not rows:
        rows.append('<div class="sys">还没聊过 —— 说点什么吧</div>')
    return "".join(rows)


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    """
    💬 对话窗口 —— QQ 号被冻结期间的替代入口。

    ⚠⚠ 破红线的两条路之一（另一条是 `POST /chat/send`）：
      本页**会写 memory**，但只在「他还没开过场」时写那一句开场白，其余全只读。
    ⚠ 等级**不显示**：README 铁律 2 是「好感度那一面绝不进对话」——
      聊天气泡上头挂个「第 N 级」，她一眼就跳戏了。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")

    try:
        await asyncio.to_thread(_open_once, uid)
    except Exception as e:
        print("[💬] 开场检查失败：%s" % e)

    shown = _shown_name(uid)
    chat = _talk_body(uid, _read_talk(uid), shown)
    head = ('<div class="ph-top"><a href="%s" class="hint">‹ 目录</a>'
            '<b>祁煜</b>%s</div>'
            % (MENU_PATH, _mood_html(uid)))
    her = _her_av(uid, shown)
    body = ('<div class="phone talk">%s<div class="chat" id="chat">%s</div></div>'
            '<div class="bar-bottom">'
            '<form id="say" method="post" action="/chat/send">'
            # ⚡ 她的头像「样板」（2026-10-02）：乐观插入**第一条**消息时，页面上还
            #    没有她的气泡 ⇒ JS 的 `bubbleRow()` 拷不到头像，会留一个空圆圈。
            #    在这儿藏一份给它取（`hidden` ⇒ 不显示、不占位，纯粹是模板）。
            '<div id="herav" hidden>%s</div>'
            # 🧭 方案 B：回目录并进输入栏这一行（顶部那条「‹ 目录」钉住后一直看得见，
            #    这儿是给单手够不到顶部的人一个下位的入口）。
            '<a href="%s" class="hint bk" title="返回目录" aria-label="返回目录">‹ 目录</a>'
            # ⚡ `maxlength`（2026-10-02）：服务端会把超长的话截到 `CHAT_MAX_INPUT`
            #    （见 `/chat/send`），而乐观插入用的是**她输入的原文** ⇒
            #    不加这道闸，超长情况下「本地气泡是全文、刷新后只剩前 800 字」。
            #    让浏览器在输入框里就截住 ⇒ 她看见的、气泡里的、存进记忆的三者一致。
            '<textarea id="t" name="text" rows="1" maxlength="%d" '
            'placeholder="跟他说点什么…" '
            'autocomplete="off" enterkeyhint="send"></textarea>'
            '<button type="submit">发送</button>'
            '</form></div>' % (head, chat, her, MENU_PATH, CHAT_MAX_INPUT))
    return _page(body, title="祁煜", css=CHAT_CSS + TALK_CSS, script=TALK_JS)


@app.get("/chat/history", response_class=HTMLResponse)
async def chat_history(request: Request, day: str = ""):
    """
    🗄 某一天的原话（2026-10-01 她定「原话不删，供用户查看，不调用」）。

    ⭐ **只读**：跟 `/chat/voice` 一个口径，一个字节都不写盘。
    ⚠ `day` 来自 URL（她自己能改）⇒ 只做等值比对、不拼路径
      ⇒ 改不出越权读别人的档（文件由**登录 uid** 决定，跟 `day` 无关）。
    ⚠ 没有这天 / 这天没存档 ⇒ **不报错**，给一句「这天还没有存档」——
      老数据（09-24 / 09-25）的原话在被删掉的年代就真没了，补不回来，
      如实说比给个空页面好。
    ⚠⚠ **不调引擎、不喂模型**：这里只是把留档渲出来给她看，
      归档从头到尾不进 prompt（进了就把 `DAY_ROLL` 白做了）。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    day = (day or "").strip()
    msgs = _arch_day(uid, day) if day else []
    shown = _shown_name(uid)
    if day and msgs:
        chat = _talk_body(uid, msgs, shown)
        title = "%s 那天" % _day_head(day)
    elif day:
        chat = ('<div class="sys">这天还没有存下原话 —— '
                '原话是从开启留档之后才开始存的，更早的那些已经不在了。</div>')
        title = "%s 那天" % _day_head(day)
    else:
        chat = '<div class="sys">没指定哪一天</div>'
        title = "那天"
    # ⭐ 顶栏左边那格是「‹ 聊天」（回 `/chat`）；右格留空但**必须占位** ——
    #    `.ph-top` 是 `space-between`，少一格中间的标题就被推偏（见 `TALK_CSS` 那条规矩）。
    head = ('<div class="ph-top"><a href="/chat" class="hint">‹ 聊天</a>'
            '<b>%s</b><span class="hint"></span></div>' % _esc(title))
    # ⭐ 底栏用 `_backbar()`：它就渲染「‹ 返回目录」**一个**动作（带 `backonly`，
    #    桌面版整条藏掉）。⚠ 别再挂一个「回聊天」—— 顶栏已经有了，
    #    **同一去向绝不挂两处**；要挂第二个动作就得换 `_two_way_footer()` 且绝不带 backonly。
    body = ('<div class="phone talk">%s<div class="chat">%s</div></div>%s'
            % (head, chat, _backbar()))
    return _page(body, title=title, css=CHAT_CSS + TALK_CSS)


@app.post("/chat/send")
async def chat_send(request: Request, text: str = Form("")):
    """
    收她一句话 ⇒ 拿他的回复。

    ⭐ 两种返回：
      · 普通表单 POST ⇒ **303 回 `/chat`**（零 JS 也能用，这是保底那条路）
      · 带 `X-Requested-With: fetch`（脚本在跑）⇒ 只回**这一轮的气泡片段**，
        前端 append 上去，不整页刷
    ⚠ 两条路都**只回这一轮的片段** —— 别把整页渲进去，
      不然 fetch 那条会在聊天区里再套一个手机壳。
    """
    uid = _current_uid(request)
    if not uid:
        # ⚠ 这里必须是 **303**（不是默认的 307）：307 会**保留 POST 方法**重发到 `/`，
        #    而 `/` 只收 GET ⇒ 未登录的人看到的是「405 Method Not Allowed」，
        #    而不是登录页。303 一律转成 GET，才会把人领到登录页去。
        return RedirectResponse("/", status_code=303)

    text = (text or "").strip()
    if not text:
        return RedirectResponse("/chat", status_code=303)
    if len(text) > CHAT_MAX_INPUT:
        text = text[:CHAT_MAX_INPUT]

    reply = ""
    try:
        reply = await asyncio.to_thread(_one_turn, uid, text)
    except Exception as e:
        print("[💬] 这一轮失败（%s）：%s" % (uid, e))

    shown = _shown_name(uid)
    her = _her_av(uid, shown)
    # ⌨️ 打字机只在这条 fetch 路上开（2026-10-01 她定的「只要新发出来的消息有这效果」）：
    #    ⚠⚠ **只有 `fetch` 这条分支才给他那几条打 `data-ty`** —— 整页 POST 那条（零 JS 保底）
    #      渲染出来是给「没有 JS 的浏览器」看的，打上标记毫无意义（没人认它）；
    #      更重要的是**别让标记漏进历史页**：`/chat` 的整页渲染走 `_talk_body()`，
    #      那条路永远不会传 `typing` ⇒ 刷新后历史气泡全是干净的。
    # ⚠ 这个变量名叫 `typing`，其实判的是「是不是脚本发来的 fetch」
    #    （前端只在 `X-Requested-With: fetch` 时才给他的气泡打 `data-ty`）。
    typing = request.headers.get("x-requested-with") == "fetch"
    # ⚡ 乐观插入（2026-10-02）：fetch 这条路**不再渲染她的气泡** ——
    #    前端点发送那一刻已经用 `bubbleRow('me', ...)` 就地画好了，不等这一整轮往返。
    #    ⚠ 不去掉就会插出**两条一模一样的**（她提的「页面刷一下才弹出我的气泡」修的就是这个）。
    #    ⚠ 只有 `typing` 这一支跳过：没 JS 的保底路走下面 303 + 整页渲染，
    #      她的气泡照旧由 `/chat` 渲出来 ⇒ 那条路零影响、功能一点不丢。
    frag = [] if typing else [_bubble("her", text, her)]
    got = _segs(reply)
    if got:
        for seg, say in zip(got, _says_of(reply)):
            frag.append(_bubble("him", seg, her, say, typing=typing))
    else:
        # ⚠ 兜底：模型一个字都没回（或接口报错）⇒ 说清楚，别让她以为界面坏了。
        #    **不进记忆**（本来就没这句话）—— 绝不能留下「他说过」的假记忆。
        #    ⚠ 这条**不打 `data-ty`**：它是个 `.sys` 提示、不是他说的话，打字机不该碰它。
        frag.append('<div class="sys">他没接上话，再说一句试试</div>')

    if typing:
        return HTMLResponse("".join(frag), headers={"Cache-Control": "no-store"})
    return RedirectResponse("/chat", status_code=303)


# ============================================================
# 🔊 点句子听他说（2026-09-29）
# ------------------------------------------------------------
# ⚠⭐ 这条路由**不写盘**：读 memory（只读）+ 调 TTS（返回字节）⇒ 网页端只读红线
#    （ADR-22）那条口子**没有扩大**，本页依旧只有 `/chat` 与 `/chat/send` 写 memory。
# ⚠ `synth_wav` 是**阻塞**调用（requests，1~2 秒）⇒ 必须 `asyncio.to_thread`，
#   否则整个事件循环被卡住，她那边的其它请求（含正在等的这一页）全排队。
# ⚠ 缓存**只在内存**（跟 bot 侧同口径：TTS 产物不落盘）。上限 60 条，超了丢最老的
#    —— 她反复戳同一句不该重复计费，但也不能让这个字典无限长。
# ⚠ 同一句**并发**戳两次会各合成一次（没做 in-flight 合并）。代价是多花一句话的合成费，
#   换来的是不必再养一张锁表；前端有「合成中」态、这时按钮也点不炸，实测撞上概率很低。
#   真觉得浪费，再补 per-key 锁 —— 别提前加。
# ============================================================
VOICE_CACHE_MAX = 60
_VOICE_CACHE = collections.OrderedDict()
_VOICE_GUARD = threading.Lock()


@app.get("/chat/mood")
async def chat_mood(request: Request):
    """顶栏心情的**只读**接口（自动刷新用）。

    ⚠ 一个字节都不写盘 —— 跟 `GET /chat/voice` 同待遇，ADR-22 那条只读口子没扩大。
    ⚠ 平静 / 没数据 / 没登录 ⇒ 一律 `{"calm": true}`，前端据此显示「在 ●」。
    """
    uid = _current_uid(request)
    m = None
    if uid:
        try:
            m = mood_hint(uid)
        except Exception as e:
            print("[💗] 心情接口失败：%s" % e)
    if not m:
        return JSONResponse({"calm": True})
    return JSONResponse({"text": m[0], "color": m[1]})


@app.get(VOICE_PATH)
async def chat_voice(request: Request, h: str = ""):
    """
    某一条气泡的语音（`audio/wav`）。

    ⚠ 未登录回 **401**，不是重定向 —— 这是 `<audio>` 在拉的资源，
      303 会被静默跟随、拿回一坨 HTML，前端只看到「播放失败」，反而更难查。
    ⚠ 四种「没有音频」分得很细，是为了日志里能区分（前端现在统一闪一下）：
      400 = 哈希格式不对；404 = 这段已经滚出历史；
      204 = 这句本来就没台词（纯动作气泡）；503 = TTS 那边挂了。
    """
    uid = _current_uid(request)
    if not uid:
        return Response(status_code=401)

    h = (h or "").strip().lower()
    if len(h) != 16 or any(c not in "0123456789abcdef" for c in h):
        return Response(status_code=400)

    raw, found = "", False
    for role, txt in _read_talk(uid):
        if role != "assistant":
            continue                       # 她自己的气泡没有播放按钮，不认她的哈希
        for say in _says_of(txt):
            # ⚠ 哈希算的就是 `say` 本身（页面上也是这么算的）；
            #   `say` 为空 = 那一段没台词（纯动作 / 纯旁白）⇒ 不参与匹配。
            if say and _vhash(say) == h:
                raw, found = say, True
                break
        if found:
            break
    if not found:
        return Response(status_code=404)   # 这句已经被 truncate_history 裁掉了
    text = raw            # 已经是提取好的台词，不能再过一遍 `_voice_text`（会二次剥引号）
    if not text:
        return Response(status_code=204)   # 理论上到不了（空 say 匹配不上），留作防御

    key = (uid, h)
    with _VOICE_GUARD:
        wav = _VOICE_CACHE.get(key)
        if wav is not None:
            _VOICE_CACHE.move_to_end(key)  # LRU：常用的往后挪，被淘汰的是最久没戳的
    if wav is None:
        try:
            wav = await asyncio.to_thread(synth_wav, text)
        except Exception as e:
            print("[🔊] 网页合成失败（%s）：%s" % (uid, e))
            return Response(status_code=503)
        with _VOICE_GUARD:
            _VOICE_CACHE[key] = wav
            while len(_VOICE_CACHE) > VOICE_CACHE_MAX:
                _VOICE_CACHE.popitem(last=False)

    # ⚠ `private` 是关键：同一串 URL 对不同 uid 的内容**不一样**（都是她自己的记忆），
    #   只有 `no-store` 或 `private` 才不会被中间缓存拿去串给别人。
    #   跟页面那条 `no-store` 不同 —— 音频**要**让她本机缓存（一天内再戳同一句直接出）。
    return Response(content=wav, media_type="audio/wav",
                    headers={"Cache-Control": "private, max-age=86400",
                             "Vary": "Cookie"})
