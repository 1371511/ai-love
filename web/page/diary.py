# -*- coding: utf-8 -*-
"""
📔 日记（`/diary`）—— 他每聊够一阵子写下的那段话，攒成一本。

⭐ 2026-09-30 新建。她定的三条口径：
   ① 「LLM 每 8 轮总结出来的那段话」放到这儿 —— 原先 `long_term_summary` 那份
      **一个字都没在界面上露过**（2026-09-30 做「他记住的」覆盖审计时挖出来的：
      它一直在 prompt 里干活，但哪儿都不显示）。现在它是日记的一条。
   ② 「按天进行多次总结」—— 一天聊得多，那天就好几条；**不合并、不覆盖**，
      一条就是一段。（9.29 聊了 20 轮 ⇒ 那天两条，是这个意思。）
   ③ 「同样支持增删修改」—— 他自己写的她也**能改能删**（她选的「都能改都能删」）。

🟡 **本页要写 `memory/{uid}_diary.json`** —— 这是 ADR-22 的第 6 条开口
   （在 `tools/check_static.py` 的 `WEB_WRITE_EXCEPTION` 里）。
   ⚠ 为什么必须写：她要在网页上增 / 改 / 删，不落盘等于白写。
   ⚠ 为什么**不塞进 `memory/{uid}.json`**：那份文件归 `save_memory()` 管，
      只写固定的 8 个字段，塞进去 bot 一保存就被冲没。
      ⇒ 所以另开一个文件（跟 `{uid}_daily.json` 同一个做法）。
   ⚠ 写盘**一律**走 `Rafayel_memory` 那三个 `*_diary_by_her()`，网页端
      **绝不自己 json.dump** —— 字数上限 / 条数上限 / 脏文件不覆盖，全在引擎那边。
      本页只负责「把她说的话传过去」和「把结果显示出来」。

🔗 读：`load_diary()`（没文件 ⇒ 空，**不创建**）+ `group_diary()`（按天分组）。
🔗 写：`add_diary_by_her()` / `edit_diary_by_her()` / `delete_diary_by_her()`。
⚠⭐ 日期文案（今天 / 昨天 / 9月30日 星期三）**只有 `group_diary()` 一份实现** ——
   本页不许自己拼日期。上次 `/affinity` 那边把「认识第 N 天」抄了三份、三处口径打架
   才下沉到 `base.met_known()`，这里别再犯同样的错。

⚠ 排序无需担心：本页没有 `/diary/{xxx}` 这种路径参数 ⇒ 不存在被抢路由的问题
   （`/home/edit/{kind}` 要排在 `/home/edit/profile` 后面是**那一页**的坑，跟这儿无关）。
"""
from urllib.parse import quote

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from base import app, _page, _backbar, _esc, _current_uid
# ⚠⭐ `Rafayel_memory` 在 `check_static.py` 的 `WRITER_MODULES` 里 ⇒ 必须先在
#   `WEB_WRITE_EXCEPTION` 给 `web/page/diary.py` 开口，否则静态检查第 ④ 关报红。
#   ⚠ 这一处就把「网页端写 memory」的开口从 5 条变成 6 条 ——
#     再要加别的写盘模块，得重新过一遍 ADR-22。
from Rafayel_memory import (
    load_diary, group_diary,
    add_diary_by_her, edit_diary_by_her, delete_diary_by_her,
)
from Rafayel_config import DIARY_MAX_ITEMS, DIARY_MAX_LEN


DIARY_CSS = """/* 📅 一天一张卡（2026-09-30）：日期在上，那天写的几条顺着往下排。
   ⚠ 类名一律加 `d` 前缀 —— 全站那份 CSS 里 `.chip` / `.hint` / `.card` 都占着了，
      起通用名迟早跟别页撞（`.who` 那次是被 Base 顶过的，别再来一回）。 */
.dgrp{margin-bottom:12px}
.dgrp:last-of-type{margin-bottom:0}
.dlbl{font-size:12px;color:var(--c-muted);margin:0 0 6px}
/* 一条 = 一行：时间在左、正文在中、「改」在右，整行可点 */
.drow{display:flex;align-items:baseline;gap:10px;padding:8px 0;
      border-bottom:0.5px solid var(--c-hair);font-size:13px;
      text-decoration:none;color:inherit}
.drow:last-of-type{border-bottom:none}
.drow .dtm{flex:0 0 auto;color:var(--c-hint);font-size:12px;
           font-variant-numeric:tabular-nums;white-space:nowrap}
/* ⭐ `pre-wrap` —— 她手写的一条**可以是好几行**（回车间保留），不能像
      「他记住的事」那样压成一句话：那条 = 一句承诺，这条 = 一段日记。
      ⚠ 但必须配 `overflow-wrap` —— 超长英文/链接会把卡片撑破。 */
.drow .dtx{flex:1;min-width:0;white-space:pre-wrap;overflow-wrap:anywhere}
.drow .dgo{flex:0 0 auto;color:var(--c-hint);font-size:12px;white-space:nowrap}
/* 🖊 「我」这个小章：标她自己写的那几条（他写的占绝大多数，不必逐个标） */
.dmine{flex:0 0 auto;font-size:11px;color:var(--c-brand-deep);line-height:1.5;
       background:var(--c-brand-soft);border-radius:999px;padding:0 7px}
/* 表单：跟主页那套同一个形状（宽度uniform + border-box），照抄的口径见 home.py */
.dform textarea{width:100%;box-sizing:border-box;font:inherit;padding:8px 10px;
                border-radius:var(--r-ctl);border:0.5px solid var(--c-line-2);
                background:var(--c-card);min-height:120px;line-height:1.6;
                resize:vertical;display:block}
.dwhen{font-size:12px;color:var(--c-hint);margin:0 0 10px}"""


def _norm(s):
    """
    表单里按的回车，Windows / Mac / Linux 交上来的换行符不统一（`\\r\\n` vs `\\n`）
    ⇒ 统一成 `\\n`，不然同一段字在不同浏览器里存下的字节不一样，
      将来「改一条」会误判成「跟原来不一样」。（只动行尾，不动内容。）
    """
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def _find(uid, eid):
    """
    在**分组结果**里按 `id` 找一条，顺带把「那天」和「几点」带回来。
    返回 `(entry, day_label, hm)`；找不到 ⇒ `(None, "", "")`。

    ⚠ 为什么绕分组来找，而不是直接 import 引擎里的 `_hm()`：
       `_hm` 是私有helper，而且它那套「按 `AUTO_GREET_TZ_OFFSET` 换算」的逻辑
       必须跟 `_day_key()` 成对使用 —— 单拎出来用必然会跟列表页漂。
       ⇒ `group_diary()` 已经把 (时间, 条目) 配好对了，拿它的结果就是唯一真相源。
    ⚠ **`id` 是唯一主键**：一天可能有好几段话长得差不多，拿文本定位会改错一条。
    """
    for _day, label, items in group_diary(load_diary(uid)["entries"]):
        for hm, e in items:
            if str(e.get("id")) == str(eid):
                return e, label, hm
    return None, "", ""


@app.get("/diary", response_class=HTMLResponse)
async def diary_page(request: Request, saved: str = ""):
    """
    📔 日记本：**最新的那一天在最上面**，天内按时间正序（早的在前）。

    ⚠ `saved=1` 只用来显示一句「存下了」，**不参与任何业务逻辑** ——
      它来自 URL（她自己能改），所以**绝不能**拿它当「上一次写成功了」的判断。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")

    groups = group_diary(load_diary(uid)["entries"])
    ok = '<p class="hint" style="margin:0 0 10px">存下了。</p>' if saved == "1" else ""

    if not groups:
        list_html = """
    <div class="card">
      <p class="muted" style="margin:0;font-size:13px">
        还没有。<br>
        你们再聊一阵子，他就会写第一段。</p>
      <p class="hint" style="margin:8px 0 0">
        他每隔几轮会把这段时间里记住的事写下来。不想等的话，也可以自己先写一条。</p>
    </div>"""
    else:
        cards = []
        for _day, label, items in groups:
            rows = []
            for hm, e in items:
                mine = ('<span class="dmine">我</span>'
                        if e.get("src") == "her" else "")
                rows.append(
                    '<a class="drow" href="/diary/e?id=%s">'
                    '<span class="dtm">%s</span>%s'
                    '<span class="dtx">%s</span>'
                    '<span class="dgo">改 ›</span></a>'
                    % (quote(str(e.get("id"))), _esc(hm), mine, _esc(e.get("text"))))
            cards.append(
                '<div class="card dgrp"><p class="dlbl">%s</p>%s</div>'
                % (_esc(label), "".join(rows)))
        list_html = "".join(cards)

    body = """
    <div class="card">
      <h1>日记</h1>
      <p class="hint" style="margin:2px 0 12px">
        他记下来的那些事，和他当时没说出口的那些。</p>
      %s
      <form method="get" action="/diary/new">
        <button type="submit" class="ghost">＋ 写一条</button>
      </form>
    </div>
    %s
    <p class="hint" style="margin:10px 4px 0">
      一条最多 %d 字，最多留 %d 条，满了丢最早的（你自己写的不会丢）。</p>
    %s
    """ % (ok, list_html, DIARY_MAX_LEN, DIARY_MAX_ITEMS, _backbar())
    return _page(body, title="日记", css=DIARY_CSS)


@app.get("/diary/new", response_class=HTMLResponse)
async def diary_new(request: Request, err: str = ""):
    """➕ **写一条** —— 她自己往日记里添一段。空 / 超长 ⇒ 回来报一句，不静默失败。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    msg = '<p class="err">%s</p>' % _esc(err) if err else ""
    body = """
    <div class="card">
      <h1 style="text-align:center">写一条</h1>
      %s
      <form class="dform" method="post" action="/diary/new">
        <textarea name="text" maxlength="%d"
                  placeholder="今天发生了什么？"></textarea>
        <button type="submit">存下</button>
      </form>
      <p class="hint" style="margin:12px 0 0">
        这一段他会当成自己写的日记。空着留不住。</p>
    </div>
    %s
    """ % (msg, DIARY_MAX_LEN, _backbar())
    return _page(body, title="写一条", css=DIARY_CSS)


@app.post("/diary/new")
async def diary_new_save(request: Request, text: str = Form("")):
    """存新增的一条。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if not add_diary_by_her(uid, _norm(text)):
        return RedirectResponse("/diary/new?err=" + quote("空着存不下，或者写太长了"),
                                status_code=303)
    return RedirectResponse("/diary?saved=1", status_code=303)


@app.get("/diary/e", response_class=HTMLResponse)
async def diary_edit(request: Request, id: str = "", err: str = ""):
    """
    ✏️ **改一条** —— 点列表里那一行进来的。**他写的那几条也能改**（她定的）。

    ⚠ `id` 来自 URL ⇒ 只当**查询条件**用（精确匹配），不承担任何别的语义。
    ⚠ 删除放在**这一页里**（得先点进那一条）⇒ 已经是两步，不容易误删。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    e, label, hm = _find(uid, id)
    if not e:
        return RedirectResponse("/diary", status_code=303)   # 改完了 / 被删了 ⇒ 回列表
    msg = '<p class="err">%s</p>' % _esc(err) if err else ""
    mine = ' <span class="dmine">我</span>' if e.get("src") == "her" else ""
    body = """
    <div class="card">
      <h1 style="text-align:center">改一条</h1>
      <p class="dwhen" style="text-align:center">%s %s%s</p>
      %s
      <form class="dform" method="post" action="/diary/e">
        <input type="hidden" name="id" value="%s">
        <textarea name="text" maxlength="%d">%s</textarea>
        <button type="submit">保存</button>
      </form>
      <div style="margin-top:14px">
        <form method="post" action="/diary/del"
              onsubmit="return confirm('删掉就不要了吗？他会忘掉这一天。')">
          <input type="hidden" name="id" value="%s">
          <button type="submit" class="ghost">删掉这条</button>
        </form>
      </div>
      <p style="margin:10px 0 0;text-align:center">
        <a href="/diary" class="hint">‹ 取消</a></p>
    </div>
    %s
    """ % (_esc(label), _esc(hm), mine, msg,
           _esc(str(e.get("id"))), DIARY_MAX_LEN, _esc(e.get("text")),
           _esc(str(e.get("id"))), _backbar())
    return _page(body, title="改一条", css=DIARY_CSS)


@app.post("/diary/e")
async def diary_edit_save(request: Request, id: str = Form(""), text: str = Form("")):
    """
    存改动。

    ⚠ **只改正文**：时间 / 谁写的（`src`）一个都不动 —— 改几个字不等于改了时间，
      而且要是把她精修过的那条转成「她写的」，列表里那个「我」就是假的。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if not edit_diary_by_her(uid, id, _norm(text)):
        return RedirectResponse("/diary/e?id=%s&err=%s"
                                % (quote(str(id)), quote("没改成 —— 跟原来一样？"),
                                   ), status_code=303)
    return RedirectResponse("/diary?saved=1", status_code=303)


@app.post("/diary/del")
async def diary_del(request: Request, id: str = Form("")):
    """
    🗑 删一条。⭐ **不留抑制名单**（跟「他记住的事」一致、跟画像标签相反）:
      日记没有后台自动轨会把它「写回来」—— 下一次摘要总结的是**新的对话**、
      是新内容，留痕反而会把将来那条新日记误挡在门外。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    delete_diary_by_her(uid, id)
    return RedirectResponse("/diary?saved=1", status_code=303)
