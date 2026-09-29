# -*- coding: utf-8 -*-
"""
💗 好感度后台（`/affinity`）—— 她登录后看自己这一份数据的那一页。

⭐ 2026-09-29 **从 `page/me.py` 整体搬来**，代码逐字没动，只换了路由名与函数名。
   搬的原因（她定的新 IA）：「我的页」这个名字要让给「主页」，
   而原本这一页装的内容（好感度 / 轮数 / 额度 / 画像标签 / 他说过的那句话 / 牵绊短信入口）
   整块归到「好感度后台」。
   ⚠ 老地址 `/me` 留了一条跳转壳在 `page/me.py` —— **别把内容搬回去**。

一张头像卡 + 「跟他说话」入口卡 + 好感度卡 + 「你们之间」+ 几张按需长出来的卡。
**只读**：等级从 `compute()` 来、短信节点从 `load_sms_nodes()` 来，页面一个字都不写
（唯一例外是「显示名 / 相遇那天」—— 那两样读的是 `web/users.json`，不是 bot 的 memory）。
"""
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from base import (
    app, _page, _esc, _load_users, _avatar_url, _current_uid, _backbar, MEMORY_DIR,
)
from Rafayel_affinity import compute, days_since, cum_at, MAX_LEVEL, load_sms_nodes


@app.get("/affinity", response_class=HTMLResponse)
async def affinity(request: Request):
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    a = compute(uid, MEMORY_DIR)
    # 显示名：网页自己存的优先，没有就用他记住的称呼（画像 name，来自 memory，只读）
    rec = (_load_users().get(uid) or {})
    shown_name = (rec.get("display_name") or "").strip() or a["name"]

    # 距离「下一级」的进度（用官方累计分表，不是拍脑袋的分档）
    # ⚠ 用 `cum_at()` 而非 `CUM[...]`：**等级没有上限**，过了官方表的 246 级
    #   `CUM` 就没下标了（会 IndexError）。
    pct = 0
    if a["next_at"]:
        low = cum_at(a["level"])
        span = max(1, a["next_at"] - low)
        pct = min(100, int((a["score"] - low) / span * 100))

    # 本档位内的进度：心动 1~30 / 倾情 31~50 / 眷恋 51~100 / 情衷 101 起
    # ⭐⭐ 最后一档（情衷）是**开放式**的 ⇒ 没有分母可用，
    #    所以这一条**整条不显示**，只留「本档第 N 级」。
    #    （2026-09-21 她指出：246 后面那个「+」就是没封顶，
    #     **别把 6140 / 246 当上限摆出来** —— 那是「官方表到哪」，不是天花板。）
    tier_n = a["level"] - a["tier_lo"] + 1
    open_tier = a["tier_hi"] >= MAX_LEVEL
    tier_size = max(1, a["tier_hi"] - a["tier_lo"] + 1)
    tier_pct = min(100, int(tier_n / tier_size * 100))
    # ⭐ 2026-09-21 她定的第二处：连这句里的「分」也去掉 —— 页面上**凡是数字都不挂单位**。
    next_hint = ("距 %d 级还差 %d" % (a["level"] + 1, a["to_next"])) \
        if a["next_at"] else ""

    if open_tier:
        tier_block = ('<div style="margin-top:12px">'
                      '<span class="hint">%s · 本档第 %d 级</span>'
                      '</div>') % (a["tier"], tier_n)
    else:
        tier_block = ('<div style="margin-top:12px">'
                      '<div style="display:flex;justify-content:space-between">'
                      '<span class="hint">%s</span>'
                      '<span class="hint">本档第 %d / %d 级</span>'
                      '</div>'
                      '<div class="bar" style="margin-top:4px">'
                      '<div style="width:%d%%"></div></div>'
                      '</div>') % (a["tier"], tier_n, tier_size, tier_pct)

    chips = "".join('<span class="chip">%s</span>' % x
                    for x in (a["likes"] + a["traits"] + ["不喜欢：" + x for x in a["dislikes"]]))
    if not chips:
        chips = '<span class="hint">他还没记住什么 —— 多聊几句就有了</span>'

    # ⭐ **没内容就整张卡不渲染**（她 2026-09-21 定的：这两张先隐藏）。
    #    ⚠ 不是「显示占位文案」—— 空卡片比没有卡片更打击人。
    #    ⇒ 好处是以后 `topics` / `milestones` 真有数据了，卡片**自己长出来**，不用再改一次代码。
    def _card(title, inner):
        if not inner:
            return ""
        return '<div class="card"><h2>%s</h2>%s</div>' % (title, inner)

    topics_card = _card("最近聊过", "".join(
        '<p style="margin:0 0 6px;font-size:12px" class="muted">%s</p>' % t
        for t in a["topics"]))

    ms_card = _card("他说过的那句话", "".join(
        '<p style="margin:0 0 6px;font-size:13px">「%s」</p>' % m
        for m in a["milestones"]))

    # 🎁 2026-09-21 她定的：入口**单独一张卡，紧跟「他说过的那句话」**——
    #    · 不塞进上面那张卡里（那是两回事）
    #    · 也不放底部导航（底部只留「设置 / 退出」）
    #    ⚠ 这一张**不跟着 milestones 空不空**：它自己是入口，不是那张卡的尾巴。
    # ⚠⭐ 2026-09-21 改名（她看到 15 级面板空着时顺手指出的）：
    #    这张卡数的是 **`load_sms_nodes()` = 45 条短信**的解锁数、点进去也是 `/messages`；
    #    而 86 条**彩蛋按她定的口径根本不进网页端这一页**（§19.6/§19.7，原本只走 QQ）——
    #    所以叫「彩蛋」是**名实不符**。⇒ 改成 **「牵绊短信」**。
    #    ⚠ 彩蛋那条线**没被动**：「/affinity」上那张「他说过的那句话」照读彩蛋。
    #      （⚠ 2026-09-24 起彩蛋也**取消了对 QQ 的主动发送** ⇒ 这张卡现在是彩蛋在
    #       网页端**唯一**的落点，别再顺手删掉。它按等级倒序只取 5 条。）
    _nodes = load_sms_nodes()
    _un = sum(1 for n in _nodes if n[0] <= a["level"])
    sms_card = ('<div class="card">'
                '<div style="display:flex;justify-content:space-between;align-items:baseline">'
                '<h2 style="margin:0">牵绊短信</h2>'
                '<span class="hint">已解锁 %d / %d</span></div>'
                '<p class="muted" style="font-size:12px;margin:6px 0 0">'
                '每跨过一个等级，他就多一点想让你听见的。</p>'
                '<p style="margin:10px 0 0"><a href="/messages" class="hint">进去看看 →</a></p>'
                '</div>') % (_un, len(_nodes))

    # ⭐ 2026-09-21 她定的：**互动天数 / 连续天数整格撤掉** —— 这俩只从接入那天开始记，
    #    老用户认识一百多天却显示「3 天」，摆上去是误导（宁可不显示，也不给假数）。
    #    ⚠ 同理，`a["missing"]`（后台口径：memory/xxx.json、落盘、bot 侧）**绝不显示给用户**。
    missing = ""

    # 💬 2026-09-29：对话窗口入口（QQ 号被冻结期间的替代入口）。
    #    ⭐ 摆在**头像卡之下、好感度之上** —— 这是现在最常用的那张卡，
    #      藏到底下她会找不到。⚠ 它**不是**底部导航（底部只留「设置 / 退出」，那条老规矩没动）。
    talk_card = ('<div class="card">'
                 '<div style="display:flex;justify-content:space-between;align-items:baseline">'
                 '<h2 style="margin:0">跟他说话</h2>'
                 '<span class="hint">在<span class="dot" style="margin-left:0"></span></span>'
                 '</div>'
                 '<p class="muted" style="font-size:12px;margin:6px 0 0">'
                 '在这儿说的话，他也记得 —— 跟 QQ 上是同一份记忆。</p>'
                 '<p style="margin:10px 0 0"><a href="/chat" class="hint">进去聊 →</a></p>'
                 '</div>')

    # 💰 token 消耗：她明确说「后台有人机感没关系」，这格就直给。
    #    ⭐ 口径写清楚：这是**累计请求量**，历史每轮都会重复计入，不是「聊了多少字」。
    def _fmt_tokens(n):
        n = int(n or 0)
        if n >= 10000:
            return "%.1f 万" % (n / 10000.0)
        if n >= 1000:
            return "%.1f 千" % (n / 1000.0)
        return str(n)

    tokens_txt = _fmt_tokens(a["tokens"]) if a.get("has_usage") else "—"
    tokens_unit = "token"

    # ⭐⭐ 「认识第 N 天」—— **她说哪天就是哪天**（2026-09-21 她的原话：
    #   「我给的是一个祁煜的载体，用户真正相遇的那天，由她们自己决定」）。
    #   ⇒ 用户在 /settings 自己填 `met_day`；**她填的优先于日志回填的 first_day**
    #     （bot 自己不记第一次是哪天，所以默认值只能来自她）。
    #   ⚠ 只写 `web/users.json`，**绝不写回 memory**（那份只读）。
    met_day = (rec.get("met_day") or "").strip()
    known = 0
    if met_day:
        known = days_since(met_day)
    elif a.get("known_days"):
        known = a["known_days"]

    if known:
        known_txt = "认识第 %d 天 · " % known
    else:
        known_txt = ""

    if a["last_active"]:
        sub = known_txt + "最近一次 %s" % a["last_active"][:10]
    else:
        sub = known_txt.rstrip(" · ") or "还没聊过"

    # 没填「相遇那天」⇒ 给一句引导（不然她根本不知道这个能自己定）
    if not met_day:
        sub += ('　<a href="/settings" class="hint">你们是哪天相遇的？</a>')

    # 🖼 头像：传了图就显示图；没传就退回「名字首字」那个小圆片（老样子）。
    #    ⚠ 尺寸保持 36px 没动 —— 换图的收益是「那是张真脸」，不是顺手把版式改一遍。
    _av = _avatar_url(uid)
    if _av:
        _av_html = '<img src="%s" alt="" class="avatar">' % _av
    else:
        _av_html = ('<div style="width:36px;height:36px;border-radius:50%%;background:#EEF4FB;'
                    'color:#33506E;display:flex;align-items:center;justify-content:center;'
                    'font-size:13px">%s</div>' % _esc((shown_name or "?")[:2]))

    body = """
    <div class="card" style="display:flex;align-items:center;justify-content:space-between">
      <div>
        <h1>%s</h1>
        <p class="muted" style="font-size:12px;margin:0">%s</p>
      </div>
      <div style="width:36px;height:36px">%s</div>
    </div>
    %s
    %s
    <div class="card">
      <div style="display:flex;justify-content:space-between">
        <span class="muted" style="font-size:13px">好感度</span>
        <span class="muted" style="font-size:13px">%s · %d 级</span>
      </div>
      <div class="big">%d</div>
      <div class="bar"><div style="width:%d%%"></div></div>
      <p class="hint" style="margin:8px 0 0">%s</p>
      %s
    </div>
    <div class="grid">
      <div class="card"><p class="muted" style="font-size:12px;margin:0">聊过</p>
        <div class="n">%d</div><p class="hint" style="margin:2px 0 0">轮</p></div>
      <div class="card"><p class="muted" style="font-size:12px;margin:0">已用额度</p>
        <div class="n">%s</div><p class="hint" style="margin:2px 0 0">%s</p></div>
    </div>
    <div class="card"><h2>你们之间</h2>%s</div>
    %s%s%s
    %s
    """ % (shown_name or "你",
           sub,
           _av_html,
           missing,
           talk_card,
           a["tier"], a["level"], a["score"], pct, next_hint,
           tier_block,
           a["turns"], tokens_txt, tokens_unit,
           chips, topics_card, ms_card, sms_card,
           # ⚠ 2026-09-29 改：底部那条从「设置 · 退出」换成统一的「‹ 返回目录」
           #   （她定的口径：功能页底部**只留回目录**，设置 / 退出挪到目录页里）。
           _backbar())
    return _page(body)
