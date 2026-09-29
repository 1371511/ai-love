# -*- coding: utf-8 -*-
"""
🎁 牵绊提升 —— 跨级解锁的 45 条**短信**（列表 / 详情 / 局部刷新片段）。

路由：
  GET `/messages`               列表（已解锁 + 🔒 未解锁占位）
  GET `/messages/{level}`       一条短信的详细对话页（模拟手机聊天，A/B/C 分支树）
  GET `/messages/{level}/frag`  只回气泡区那一段 HTML（`SMS_JS` 点选项时局部换）

⚠⚠ **没解锁的内容一个字都不许进 HTML**（不是用 CSS 藏起来）——
   否则右键「查看源代码」就能把 200 级以后的剧情全读了。这条唯一的关口是 `_chat_html()`，
   详情页和 frag 接口**共用它**，别在这条路上另写一套判断。
⚠ 只读 memory：等级从 `compute()` 来，页面一个字都不写。
⚠ 2026-09-21 她定的口径：**彩蛋不上这一页**（那 86 句是 QQ 端聊天时的语料）⇒ 这里只剩短信。
"""
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from base import (
    app, _page, _esc, _rich, _lock_row, _backbar, _current_uid, _load_users,
    MEMORY_DIR, CHAT_CSS, MENU_PATH,
)
from Rafayel_affinity import compute, load_sms_nodes, sms_full


# ⭐ 2026-09-22 她要的「点选项别像翻页」⇒ **只换聊天区**（`/messages/{等级}/frag`）。
#    ⚠⚠ 这是**渐进增强**：脚本没了 / 浏览器不支持 fetch ⇒ 链接照旧整页跳，
#    功能一点不丢（全站零 JS 那条老规矩，只在「能不能更顺」上让步，不在「离了 JS 就废」上让步）。
SMS_JS = r"""
<script>
(function () {
  var chat = document.getElementById('chat');
  if (!chat || !window.fetch) { return; }
  function fragUrl(href) { return href.replace(/(\/messages\/\d+)\?/, '$1/frag?'); }
  function go(href) {
    chat.style.opacity = '.55';
    fetch(fragUrl(href), { headers: { 'X-Requested-With': 'fetch' } }).then(function (r) {
      if (!r.ok) { throw new Error(r.status); }
      return r.text();
    }).then(function (html) {
      chat.innerHTML = html;
      chat.style.opacity = '';
      try { history.pushState(null, '', href); } catch (e) {}
      var n = document.getElementById('new');
      if (n) { n.scrollIntoView({ block: 'start', behavior: 'smooth' }); }
    }).catch(function () {
      location.href = href;                // 取不到就整页跳，功能不丢
    });
  }
  document.addEventListener('click', function (e) {
    var t = e.target;
    var a = t && t.closest ? t.closest('a.pick') : null;
    if (!a) { return; }
    e.preventDefault();
    go(a.getAttribute('href'));
  });
  // 整页进来（分享的链接 / 老浏览器）：也滚到最新那几行，别让她从头往下拖
  if (!location.hash && document.getElementById('new')) {
    document.getElementById('new').scrollIntoView({ block: 'start' });
  }
  window.addEventListener('popstate', function () { location.reload(); });
})();
</script>"""


@app.get("/messages", response_class=HTMLResponse)
async def messages_page(request: Request):
    """
    「牵绊提升」—— 跨级解锁的官方素材（45 条短信）。

    ⚠⚠ 2026-09-21 她定的口径：**彩蛋不上网页端这一页**。
       那 86 句是「**QQ 端聊天时可用的语料**」—— 等级到了按等级解锁；
       而不是摆成一页给人从头翻到尾。所以这一页**只剩短信**，一个字彩蛋都不渲染。
       （⚠ 2026-09-24 起它连 QQ 也不主动发了 ⇒ 彩蛋在网页端只剩 `/affinity` 那张卡这一个落点。）
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")

    a = compute(uid, MEMORY_DIR)
    lv = int(a["level"] or 1)

    nodes = load_sms_nodes()
    sms_un = [n for n in nodes if n[0] <= lv]
    sms_lk = [n for n in nodes if n[0] > lv]

    parts = ['<div class="card"><h1>牵绊提升</h1>'
             '<p class="muted" style="font-size:12px;margin:0">'
             '每跨过一个等级，他就多一点想让你听见的。</p>'
             '<p class="hint" style="margin:8px 0 0">已经解锁 %d / %d 条短信</p>'
             '</div>' % (len(sms_un), len(nodes))]

    # ---------------- 短信：一条一个框，点进去才是详细对话 ----------------
    # ⭐ 2026-09-21 她定的版式（就是她截的那张图）：
    #    标题 + 右上「第 N 级 · 档位」+ 开头句一句 + 一行灰字。
    # ⭐ 灰字**从「就地展开」改成「跳转」** ⇒ 列表页轻了，完整对话单开一页（还能一句句聊）。
    parts.append('<h2 style="margin:18px 0 10px">短信</h2>')
    if not sms_un:
        parts.append('<p class="hint" style="margin:0 0 10px">还一条都没解锁 —— 到第 %d 级就有第一条了。</p>'
                     % (sms_lk[0][0] if sms_lk else 1))
    for lvl, tier, title, fn in reversed(sms_un):
        opening = sms_full(fn)["opening"]
        intro = ('<p style="margin:8px 0 0;font-size:13px">「%s」</p>' % _rich(opening)) if opening else ""
        # ⭐ 2026-09-21 她定的：**别再拿 `<a>` 把整张卡包起来** ——
        #    卡里除了那行「展开查看 →」，其余全当普通的字（标题、档位、开头句都不该是链接色）。
        parts.append('<div class="card">'
                     '<div style="display:flex;justify-content:space-between;align-items:baseline">'
                     '<h2 style="margin:0">%s</h2>'
                     '<span class="hint">%d 级 · %s</span></div>'
                     '%s'
                     '<p style="margin:8px 0 0">'
                     '<a href="/messages/%d" class="cta">展开查看 →</a></p>'
                     '</div>'
                     % (_esc(title), lvl, _esc(tier), intro, lvl))
    if sms_lk:
        parts.append('<p class="hint" style="margin:16px 0 8px">—— 还没解锁 ——</p>')
        parts.extend(_lock_row(lvl, title) for lvl, _t, title, _fn in sms_lk)

    # ⚠⚠ 2026-09-21 她拆掉的「彩蛋」段落（86 句、一行一句）**不要加回来** ——
    #     她定：彩蛋是**QQ 端聊天时用的语料**（等级到了才解锁），
    #     不是摆成一页给人从头翻的展示内容。彩蛋不走这一页，网页端这儿只有短信。
    #     ⭐ 2026-09-24 起它连 QQ 也不主动发了 ⇒ 彩蛋在网页端只剩 `/affinity` 那张卡。

    # ⚠ 2026-09-29 改：底部那条从「回去」（指向旧 `/me`）换成统一的「‹ 返回目录」（→ `/menu`）。
    parts.append(_backbar())
    return _page("".join(parts), title="牵绊提升")


def _chat_html(uid, level, p):
    """
    🧱 短信对话的**气泡区**（`<div class="chat">` 里面那一段）+ 标题栏。

    ⭐ 详情页 `/messages/{level}` 和片段接口 `/messages/{level}/frag`
      **共用这一份渲染**（2026-09-22 她要「点选项局部刷新」时才抽出来的）——
      两处各渲染一遍迟早跑偏（一边新气泡、一边没新 ⇒ 修不完的 bug）。

    返回 `(head_html, rows_html, title)`；**等级不够 / 没有这一级 ⇒ 返回 None**
      （⚠⚠ 未解锁的正文一个字都不许出去 —— 这一层是唯一的关口，frag 也走它）。

    ⭐ `class="fresh"` + `id="new"`：**最后选的那一段**（刚冒出来的那几行气泡）包一层 ——
      JS 局部刷新后要**滚到这儿**，新气泡的淡入动画也挂在它身上。
      ⚠ 用外层包一层（而不是给气泡加 class）⇒ 老页面里气泡的 HTML **一个字没变**，
      那些数气泡的断言不会跟着碎。
    """
    a = compute(uid, MEMORY_DIR)
    lv = int(a["level"] or 1)

    node = None
    for n in load_sms_nodes():
        if n[0] == level:
            node = n
            break
    if node is None or level > lv:
        return None

    _lv, tier, title, fn = node
    full = sms_full(fn)
    rec = (_load_users().get(uid) or {})
    shown = (rec.get("display_name") or "").strip() or a["name"] or "你"

    # 选了哪几个（URL 里的脏值一律当「没选」，宁可让她重聊，也别把页面搞成 500）
    # ⭐ 2026-09-21 收紧：**只认纯数字**，出现任意一个脏值就整段作废 ⇒ 从头开始。
    #    （原来是塞个 -1 蒙混过关，于是 `?p=x,0` 这种半截状态真能渲染出半页对话 —— 看着就像没重置。）
    picked = []
    for x in (p or "").split(","):
        x = x.strip()
        if not x:
            continue
        if not x.isdigit():
            picked = []
            break
        picked.append(int(x))

    # 🖼 他的头像 = 那张蓝海油画（2026-09-21 她给的图），走白名单路由、别把路径写进 HTML。
    #    她的还是「名字首字」那个小圆片。
    him_av = '<img src="/asset/qiyu" alt="祁煜">'
    her_av = (shown or "你")[0]
    rows = ['<div class="sys">%s</div>' % _esc(title)]

    def _bub(who, text):
        # ⚠ 他的头像是 **HTML**（`<img>`）⇒ 这条**不能再 `_esc`**；她的仍是纯文本，照旧转义。
        av = _esc(her_av) if who == "她" else him_av
        rows.append('<div class="%s"><div class="av">%s</div><div class="bub">%s</div></div>'
                    % ("row me" if who == "她" else "row", av, _rich(text)))

    if full["opening"]:
        _bub("他", full["opening"])

    mark_bi = len(picked) - 1        # 最后一段（刚选的）⇒ 它的气泡包进 fresh
    bi = 0
    done = True
    for b in full["blocks"]:
        if b["kind"] != "branch":
            _bub(b["who"], b["text"])
            continue
        idx = picked[bi] if bi < len(picked) else None
        if idx is None or not 0 <= idx < len(b["options"]):
            # 这一段还没选 ⇒ 摆出选项，**后面的内容一个字都不渲染**（break 掉了）
            done = False
            rows.append('<div class="sys">（她回复之后，对话才会继续）</div>')
            rows.append('<div class="row me"><div class="av">%s</div>'
                        '<div class="bub tip">（在下面 %d 个选项里选一个回复）</div></div>'
                        % (_esc(her_av), len(b["options"])))
            for i, op in enumerate(b["options"]):
                nxt = ",".join(str(x) for x in picked[:bi] + [i])
                rows.append('<a class="pick" href="/messages/%d?p=%s">'
                            '<b>%s</b>%s<span class="go">›</span></a>'
                            % (level, nxt, _esc(op["key"]), _esc(op["title"])))
            break
        op = b["options"][idx]
        if bi == mark_bi:
            rows.append('<div class="fresh" id="new">')
        for x in op["her"]:
            _bub("她", x)
        for x in op["him"]:
            _bub("他", x)
        if bi == mark_bi:
            rows.append("</div>")
        bi += 1

    if done:
        rows.append('<div class="end">—— 说到这儿就停了 ——</div>')

    # ⚠ 2026-09-21 她定：标题栏**不加**头像（只有气泡里那个换真图）。别再往这儿塞 `<img>`。
    head = ('<div class="ph-top"><a href="/messages" class="hint">‹ 返回</a>'
            '<b>祁煜</b><span class="hint">第 %d 级</span></div>' % level)
    return head, "".join(rows), title


@app.get("/messages/{level}", response_class=HTMLResponse)
async def message_detail(request: Request, level: int, p: str = ""):
    """
    📱 一条牵绊短信的**详细对话页** —— 模拟手机互发消息，一句一句往下走。

    ⭐ 2026-09-21 她定的交互（原文：「系统：用户回复后再进行接下来的对话」）：
       进来先只看见**他开口的第一句** + 那一段的选项；**她选了之后**，
       她的话和他的回应才出现，再摆下一段的选项 …… 直到对话结束。
       ⚠ 素材本来就是 A/B/C 分支树（42 条 3 段、还有 4 段 / 2 段 / 0 段的），
         不是线性剧本 ⇒ **必须「选一个才往下走」**，一次性铺开就等于剧透自己。

    ⚠ 进度走 URL（`?p=0,1` = 第 1 段选 A、第 2 段选 B），两个好处：
       ① **一个字都不写盘**（web 端对 memory 只读这条铁律不破）
       ② 她能把这个链接存下来 / 发给别人看，进度跟着走
    ⭐ 2026-09-22 她嫌「像放 PPT」⇒ 加了 `SMS_JS`：**点选项只换聊天区**（不整页刷）。
       ⚠⚠ 是**渐进增强**：脚本没了 / 浏览器太老 ⇒ 链接照样整页跳，**功能一点不丢**。
    ⚠⚠ 等级没到 ⇒ 直接弹回列表：**详情页也不许泄漏没解锁的内容**。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")

    got = _chat_html(uid, level, p)
    if got is None:
        return RedirectResponse("/messages")
    head, chat, title = got

    # ⭐ 2026-09-21 加的 `id="top"` + 重置链接上的 `#top`：
    #    有的短信（比如 16 级那条）**一开局就有 8 条气泡**，页面本身还是很长 ——
    #    浏览器若接着上次的滚动位置显示，她抬眼看到的就是中间那一段，很像「没重置」。
    #    ⇒ 挂个锚点，浏览器一定会**滚回对话开头**。
    # ⚠ 2026-09-29 17:37 她定：底部**只留两条**（「↺ 从头再聊一遍」/「‹ 返回目录」）。
    #   ⇒ 「回列表」**撤掉了**。她原话「功能重复了」，成立：这页**顶栏**本来就有个
    #     「‹ 返回」指向 `/messages`（见上面 `head` 里那个链接，一直钉在顶上），
    #     底部再来一个「回列表」是同一件事做两遍。
    #   ⚠ 仍**不换成 `_backbar()`**：那个只渲染「‹ 返回目录」一个，会把「↺ 从头再聊一遍」顶掉
    #     （这一页要的是「重来一遍」+「回目录」两个动作，跟别处的单条底栏不是一回事）。
    body = ('<div class="phone" id="top">%s<div class="chat" id="chat">%s</div></div>'
            '<p class="footnav">'
            '<a href="/messages/%d#top" class="hint">↺ 从头再聊一遍</a> · '
            '<a href="%s" class="hint">‹ 返回目录</a></p>'
            % (head, chat, level, MENU_PATH))
    return _page(body, title="祁煜 · %s" % title, css=CHAT_CSS, script=SMS_JS)


@app.get("/messages/{level}/frag", response_class=HTMLResponse)
async def message_frag(request: Request, level: int, p: str = ""):
    """
    🧩 只回**气泡区那一段 HTML** —— 她点选项时，JS 用它换掉聊天区（不整页刷）。

    ⚠ 等级 / 「有没有这一级」的校验跟详情页**同一份**（`_chat_html`）
      ⇒ 未解锁的短信一个字也漏不出来（别在这条路上另写一套判断）。
    ⚠ 拿不到 ⇒ **403 + 空串**；前端脚本会**退回整页跳转**，功能不丢。
    """
    uid = _current_uid(request)
    got = _chat_html(uid, level, p) if uid else None
    if got is None:
        return HTMLResponse("", status_code=403, headers={"Cache-Control": "no-store"})
    return HTMLResponse(got[1], headers={"Cache-Control": "no-store"})
