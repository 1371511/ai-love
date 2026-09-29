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

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from base import (
    app, _page, _esc, _rich, _safe_uid, _avatar_url, _current_uid, _load_users,
    MEMORY_DIR, CHAT_CSS, MENU_PATH,
)
from Rafayel_affinity import compute, current_level, init_unlocked, pending_unlock
from Rafayel_chat import get_reply, take_opening
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
.phone.talk{min-height:calc(100vh - 232px)}
/* ⭐ 2026-09-29 她定的：**顶栏固定在顶部**（滚气泡时「‹ 目录 / 祁煜 / 在●」不许滚走）。
   ⚠⚠ `overflow:visible` 这行**删不得** —— `.phone`（在 base.py 的 CHAT_CSS 里）写着
      `overflow:hidden`（为了 16px 圆角）。**父级一旦不是 visible，子元素的
      `position:sticky` 会整个失效**（sticky 只贴「最近的滚动祖先」，而 overflow:hidden
      的祖先把滚动吃掉了）⇒ 不覆盖这行，下面那句 sticky 就是废的，而且**不报错**。
   ⚠ 覆盖成 visible 后圆角靠谁？靠 `.ph-top` 自己补 `border-radius:16px 16px 0 0`
     （它是最上面那个子块，背景 #F7F7F7 会盖住圆角），下面 `.chat` 是透明底、不越界
     ⇒ 手机壳那圈圆角照样在，不用把 border-radius 从 `.phone` 上摘掉。 */
.phone.talk{overflow:visible}
/* z-index 要压过气泡（气泡是普通流、z-index:auto）⇒ 钉住时气泡从下面滚过去。 */
.phone.talk .ph-top{position:sticky;top:0;z-index:20;border-radius:16px 16px 0 0}
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
  /* ⚠⚠ `.phone.talk` 的最小高度**必须重算**。手机那个 `- 232px` 是按「手机浏览器地址栏 +
     顶栏 + 输入栏 + body padding」估出来的；桌面没有地址栏那一截 ⇒ 不重算的话页面填不满
     视口，而 `.bar-bottom` 是 `position:sticky;bottom:0`，**贴不到底就会浮在半空**
     （这正是这条 min-height 当初要治的病）。
     ⚠ `- 104px` 这个数是**真机量出来的**，不是推的：顶栏≈58（钉住）+ 输入栏≈58，
       再减掉那条 `-2rem` 负 margin 抵消掉的 32 ⇒ 页面正好填满一屏。
       改顶栏/输入栏任何一个高度都要重新量。 */
  .phone.talk{min-height:calc(100vh - 104px)}
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
.phone.talk .bub.him .tx{flex:1;min-width:0}
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

  function toBottom() {
    try { window.scrollTo(0, document.body.scrollHeight); } catch (e) {}
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

  ta.addEventListener('input', grow);
  // 回车发送、Shift+回车换行（手机上回车就是换行，靠「发送」按钮）
  ta.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
    }
  });

  if (!window.fetch) { toBottom(); return; }

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
      box.insertAdjacentHTML('beforeend', frag);
      var n = document.getElementById('new');
      if (n) { n.removeAttribute('id'); }
      unlock();
      toBottom();
    }).catch(function () {
      // 拿不到片段 ⇒ 退回整页提交（她那句话别丢）
      ta.value = t;
      grow();
      unlock();
      form.submit();
    });
  });

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

  toBottom();
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


def _bubble(who, text, her_av, say=None):
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
    """
    if say is None:
        say = _voice_text(text)
    if who == "her":
        return ('<div class="row me"><div class="av">%s</div>'
                '<div class="bub">%s</div></div>' % (her_av, _rich(text)))
    if not say:
        return ('<div class="row"><div class="av">%s</div>'
                '<div class="bub">%s</div></div>' % (_him_av(), _rich(text)))
    return ('<div class="row"><div class="av">%s</div>'
            '<div class="bub him"><span class="tx">%s</span>'
            '<a class="spk" href="%s?h=%s" hidden aria-label="播放这一句" '
            'title="播放这一句">%s</a></div></div>'
            % (_him_av(), _rich(text), VOICE_PATH, _vhash(say), _VOICE_SPK))


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


def _one_turn(uid, text):
    """跑一轮对话（同步、**持锁**）⇒ 调用处丢 `asyncio.to_thread`。"""
    with _chat_lock(uid):
        reply = get_reply(text, uid) or ""
        _unlock_tick(uid)
    return reply


def _talk_body(uid, msgs, shown, tail=""):
    """气泡区 HTML（`<div class="chat">` 里面那一段）；`tail` 追加在最后。"""
    her = _her_av(uid, shown)
    rows = []
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
            '<b>祁煜</b><span class="hint">在<span class="dot"></span></span></div>'
            % MENU_PATH)
    body = ('<div class="phone talk">%s<div class="chat" id="chat">%s</div></div>'
            '<div class="bar-bottom">'
            '<form id="say" method="post" action="/chat/send">'
            # 🧭 方案 B：回目录并进输入栏这一行（顶部那条「‹ 目录」钉住后一直看得见，
            #    这儿是给单手够不到顶部的人一个下位的入口）。
            '<a href="%s" class="hint bk" title="返回目录" aria-label="返回目录">‹ 目录</a>'
            '<textarea id="t" name="text" rows="1" placeholder="跟他说点什么…" '
            'autocomplete="off" enterkeyhint="send"></textarea>'
            '<button type="submit">发送</button>'
            '</form></div>' % (head, chat, MENU_PATH))
    return _page(body, title="祁煜", css=CHAT_CSS + TALK_CSS, script=TALK_JS)


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
    frag = [_bubble("her", text, her)]
    got = _segs(reply)
    if got:
        for seg, say in zip(got, _says_of(reply)):
            frag.append(_bubble("him", seg, her, say))
    else:
        # ⚠ 兜底：模型一个字都没回（或接口报错）⇒ 说清楚，别让她以为界面坏了。
        #    **不进记忆**（本来就没这句话）—— 绝不能留下「他说过」的假记忆。
        frag.append('<div class="sys">他没接上话，再说一句试试</div>')

    if request.headers.get("x-requested-with") == "fetch":
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
