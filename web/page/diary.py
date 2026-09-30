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
   ⚠ `edit_diary_by_her()` 的**可选第 4 参 `ts`** 是 2026-09-30 加的：
   传了就能**连时间一起改**（她提的「圈起来的时间要换成可以更改的」）。
   不传 ⇒ 旧行为，一个字都不动。
⚠⭐ 日期文案（今天 / 昨天 / 9月30日 星期三）**只有 `group_diary()` 一份实现** ——
   本页不许自己拼日期。上次 `/affinity` 那边把「认识第 N 天」抄了三份、三处口径打架
   才下沉到 `base.met_known()`，这里别再犯同样的错。

⚠ 排序无需担心：本页没有 `/diary/{xxx}` 这种路径参数 ⇒ 不存在被抢路由的问题
   （`/home/edit/{kind}` 要排在 `/home/edit/profile` 后面是**那一页**的坑，跟这儿无关）。
"""
from urllib.parse import quote

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from base import app, _page, _backbar, _two_way_footer, _esc, _current_uid
# ⚠⭐ `Rafayel_memory` 在 `check_static.py` 的 `WRITER_MODULES` 里 ⇒ 必须先在
#   `WEB_WRITE_EXCEPTION` 给 `web/page/diary.py` 开口，否则静态检查第 ④ 关报红。
#   ⚠ 这一处就把「网页端写 memory」的开口从 5 条变成 6 条 ——
#     再要加别的写盘模块，得重新过一遍 ADR-22。
from Rafayel_memory import (
    load_diary, group_diary, diary_ts_from, day_label,
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
/* 🖊 「我」这个小章：标她自己写的那几条（他写的占绝大多数，不必逐个标）
   ⚠ `vertical-align:middle` 是给「修改内容」页那个标题旁边的用法加的 ——
     在列表页它是 flex 子项，这条对它不起作用（无害）。 */
.dmine{flex:0 0 auto;font-size:11px;color:var(--c-brand-deep);line-height:1.5;
       background:var(--c-brand-soft);border-radius:999px;padding:0 7px;
       vertical-align:middle}
/* 表单：跟主页那套同一个形状（宽度uniform + border-box），照抄的口径见 home.py */
/* ⭐ `min-height` 120 → 200（2026-09-30 她提「日记的修改文本框还要放得再多一点」）。
   它担两个角色：① 有 JS 时**空框的最低高度**（JS 只管跟着字数长，不设下限）；
   ② **没有 JS 时的兜底高度**（那条路只有 `rows="10"` + 这一条撑着）。
   ⚠ 别把下限搬进 JS —— 空 textarea 的 `scrollHeight` 只有两行，单靠 JS 会缩成一条缝。 */
.dform textarea{width:100%;box-sizing:border-box;font:inherit;padding:8px 10px;
                border-radius:var(--r-ctl);border:0.5px solid var(--c-line-2);
                background:var(--c-card);min-height:200px;line-height:1.6;
                resize:vertical;display:block}
/* ⚠⚠ 上面那条原来**只写了 `textarea`** ⇒ 直接往 `.dform` 里塞 `<input>` 会是**裸的**
   （无宽度 / 无 padding / 无圆角），而且全站没有 `box-sizing:border-box`
   ⇒ 加 `width:100%` 又会被 padding 撑出卡片（`docs/网页端.md` 红线第 11 的同一条坑）。
   别以为这条是装饰 —— 没有它，2026-09-30 新加的那两个时间控件在手机上就是歪的。 */
.dform input{width:100%;box-sizing:border-box;font:inherit;padding:8px 10px;
             border-radius:var(--r-ctl);border:0.5px solid var(--c-line-2);
             background:var(--c-card)}
.dwhen{font-size:12px;color:var(--c-hint);margin:0 0 10px}
/* 🕒 「日期 + 时间」两栏（2026-09-30 她选的 B 形态：两个原生控件并排）。
   ⚠ 横向尺寸走 flex / 相对单位，不写死 px —— 手机端适配口径（她定的）。
   ⚠⚠ `min-width:0` **不能省**：flex 子项默认 `min-width:auto`，而原生日期 / 时间控件
     都带内在最小宽度 ⇒ 不加这一条，两栏在窄屏上会把卡片顶破（横向溢出）。
   ⚠ 放在 `.dform input` **后面**：两条选择器同权重（都是 1 类 + 1 标签），
     靠后写的才生效（其实是 `flex-basis:0` 覆盖掉 `width:100%`，谁在前谁白写）。 */
.dtime{display:flex;gap:8px;margin:0 0 10px}
.dtime input{flex:1 1 0;min-width:0}
/* 🔎 日期筛选（2026-09-30 她提的「再加个日期筛选，放在第一张卡里」）。
   ⚠ 单独一个 `.dfilter` 作用域，**别去改 `.dform` 那套** ——
      那是「写一条 / 改一条」的输入框，跟「挑一天看」语义不同，混一起迟早互相牵制。
   ⚠ `select` 在全站 CSS 里**从来没被样式化过**（那条只写了 `input,button`）
      ⇒ 这一条不是「微调」，是它**唯一的**样式来源；漏了就是个系统默认灰框。
   ⚠ 兜底按钮必须 `width:auto` + `margin-top:0`：全站 `button` 默认是 `width:100%`
      还带 `margin-top:10px` ⇒ 不改的话它会把 select 挤成一条缝。
      有 JS 时这个按钮会被藏掉（选完自动提交），**没有 JS 时它就是唯一的提交方式**。
   ⚠ 宽度走 flex + `min-width:0`（手机端口径），`box-sizing` 全站没有通配那条，自己补。 */
.dfilter{display:flex;gap:8px;align-items:center;margin:14px 0 0}
.dfilter select{flex:1 1 auto;min-width:0;box-sizing:border-box;font:inherit;
                padding:8px 10px;border-radius:var(--r-ctl);
                border:0.5px solid var(--c-line-2);background:var(--c-card);color:inherit}
.dfilter button{flex:0 0 auto;width:auto;margin-top:0}"""


# ⚠⚠ 渐进增强：脚本没了 / 浏览器太老 ⇒ 靠 `<textarea rows="10">` + 上面那条 `min-height`
#    兜底，照样写得下、存得进（跟 `TALK_JS` / `SMS_JS` 同一个口径：
#    只在「能不能更顺」上让步，不在「离了 JS 就废」上让步）。
DIARY_JS = r"""
<script>
(function () {
  // ⭐ 文本框**跟着字数长**（2026-09-30 她提：「还要放得再多一点」）。
  //   ⚠ 刻意**不设上限**：日记一条最多 500 字，手机上框里再套一层滚动条最难用
  //     （框内滚 + 页面滚，手指会打架）⇒ 让框整段撑开，交给**页面**去滚。
  //     （`chat.py` 那个输入框的 `grow()` 是封顶 120px 的 —— 那是草稿框，语义不同，别照抄。）
  //   ⚠ `height='auto'` 那一步不能省：不先归零，框只会越撑越高，删字也缩不回去。
  var tas = document.querySelectorAll('.dform textarea');
  if (!tas.length) { return; }

  function grow(ta) {
    ta.style.height = 'auto';
    ta.style.height = (ta.scrollHeight + 2) + 'px';   // +2 留边框余量，防最后一行被切
  }

  for (var i = 0; i < tas.length; i++) {
    (function (ta) {
      grow(ta);                                        // 进来就撑开：改一条时整段直接看得见
      ta.addEventListener('input', function () { grow(ta); });
    })(tas[i]);
  }

  // 转屏 / 手机软键盘弹出都会改变换行数 ⇒ 重算一次
  window.addEventListener('resize', function () {
    for (var j = 0; j < tas.length; j++) { grow(tas[j]); }
  });
})();
</script>
"""


def _norm(s):
    """
    表单里按的回车，Windows / Mac / Linux 交上来的换行符不统一（`\\r\\n` vs `\\n`）
    ⇒ 统一成 `\\n`，不然同一段字在不同浏览器里存下的字节不一样，
      将来「改一条」会误判成「跟原来不一样」。（只动行尾，不动内容。）
    """
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def _dayqs(day, first=False):
    """
    🔎 把「当前筛的那天」拼成查询串片段：`first=True` ⇒ `?day=...`，否则 `&day=...`。

    ⚠ 为什么必须**只有这一个出口**：列表里每一行的 href、「改」完 / 「删」完的 303、
      底栏的「回日记」、出错退回的链接 —— **四处**都要带上它，
      各拼一遍迟早漏一处（上次删「取消」漏掉 `<input id>` 那个 500 就是这么来的）。
    ⚠ `day` 来自 URL（她自己能改）⇒ 一定要 `quote`，别裸塞进 href。
    """
    key = (day or "").strip()
    if not key:
        return ""
    return ("?day=" if first else "&day=") + quote(key)


def _day_filter(groups, sel, total):
    """
    🔎 下拉：**「全部」+ 按月份分组的每一天**，每一项后面缀条数
       （她要的形状：`9月28日 星期一 · 2 条`）。

    ⚠⭐ **选项里用的是 `day_label(day)`（绝对日期），不是 `group_diary()` 那个
      「前天（9月28日 星期一）」** —— 2026-09-30 她截图：安卓那个原生下拉被折成两行
      （可用宽度只有 200 出头：内边距 + 右边那个单选圈 + optgroup 缩进全在吃宽度）。
      ⇒ 两个文案**各自取**，但**都来自引擎**：
        · 下拉选项 = `day_label(day)`（短，`Rafayel_memory` 里的公共函数）
        · 卡片标题 = `group_diary()` 给的相对说法（地方宽，「前天」更有温度）
      ⚠ 页面**一个字都不重算日期** —— 这条是红线 6 的下延，别改成在这儿拼字符串。
      ⚠ 挑日子时绝对日期本来就比「9 天前」好认（相对说法每过一天就变）。
    ⚠ `optgroup` 的年份：只有**不是**最新那组的年份才补「2025年」前缀，其余只写「9月」。
      基准取 `groups[0]`（最新那天）的年份 —— 日记里没写过东西的年份不会出现在下拉里，
      无所谓它是不是「今年」。
    ⚠ `day_key` 同时进 `value` 属性和 `label` 属性 ⇒ 两个都要 `_esc()`。
    ⚠ `<optgroup>` 必须**成对**闭合：开了新月份才关上一个，最后补收尾那一次。
    """
    opts = ['<option value=""%s>全部 · %d 条</option>'
            % (" selected" if not sel else "", total)]
    cur_year = str(groups[0][0])[:4]
    cur_m = ""
    # ⚠ 元组第二项（相对说法）在这儿**故意不用** ⇒ 变量名leading `_` 标出来，
    #   免得下次看的人以为漏用了。
    for d, _rel_label, items in groups:
        d = str(d)
        if d[:7] != cur_m:                      # 换月了 ⇒ 收口上一个 optgroup，开新的
            if cur_m:
                opts.append("</optgroup>")
            try:
                mon = int(d[5:7])
                lab = "%d月" % mon if d[:4] == cur_year else "%s年%d月" % (d[:4], mon)
            except Exception:
                # 脏 day_key（比如 `2026-9-5` 这种没补零的）⇒ **整条当分组名**。
                # ⚠ 别图省事写 `d[:7]` —— 那是机械截断，会截出「2026-9-」这种半截东西，
                #   比原样显示更难懂。（`_day_key()` 和导入脚本给的都是补零的，
                #   正常数据不会走到这儿；这一支纯粹是"万一"。）
                lab = d
            opts.append('<optgroup label="%s">' % _esc(lab))
            cur_m = d[:7]
        opts.append('<option value="%s"%s>%s · %d 条</option>'
                    % (_esc(d), " selected" if d == sel else "",
                       _esc(day_label(d)), len(items)))
    opts.append("</optgroup>")
    # ⭐ `onchange` 提交只是**更顺**（渐进增强）：没有 JS 时旁边那个「筛」按钮照样能用。
    return """
      <form class="dfilter" method="get" action="/diary">
        <select name="day" aria-label="按天筛选" onchange="this.form.submit()">%s</select>
        <button type="submit">筛</button>
      </form>""" % "".join(opts)


def _find(uid, eid):
    """
    在**分组结果**里按 `id` 找一条，顺带把「那天」「几号」和「几点」带回来。
    返回 `(entry, day_key, day_label, hm)`；找不到 ⇒ `(None, "", "", "")`。

    ⚠ 为什么绕分组来找，而不是直接 import 引擎里的 `_hm()`：
       `_hm` 是私有helper，而且它那套「按 `AUTO_GREET_TZ_OFFSET` 换算」的逻辑
       必须跟 `_day_key()` 成对使用 —— 单拎出来用必然会跟列表页漂。
       ⇒ `group_diary()` 已经把 (时间, 条目) 配好对了，拿它的结果就是唯一真相源。
    ⚠⭐ **`day_key` 也一起拿回来**（2026-09-30 加）：`<input type="date">` 的 `value`
       要的是 `YYYY-MM-DD`，而分组键**正好就是它**。别在页面里拿 `ts` 自己算日期 ——
       那等于把 `_day_key()` 抄了第二份（而且老数据那批的 `day` 是显式存的，
       跟 `ts` 推出来的**不一定一样**，抄一份必然对不上）。
    ⚠ **`id` 是唯一主键**：一天可能有好几段话长得差不多，拿文本定位会改错一条。
    """
    for day, label, items in group_diary(load_diary(uid)["entries"]):
        for hm, e in items:
            if str(e.get("id")) == str(eid):
                return e, day, label, hm
    return None, "", "", ""


@app.get("/diary", response_class=HTMLResponse)
async def diary_page(request: Request, saved: str = "", day: str = ""):
    """
    📔 日记本：**最新的那一天在最上面**，天内按时间正序（早的在前）。

    ⚠ `saved=1` 只用来显示一句「存下了」，**不参与任何业务逻辑** ——
      它来自 URL（她自己能改），所以**绝不能**拿它当「上一次写成功了」的判断。

    🔎 `day`（2026-09-30 她提「再加个日期筛选」）：`YYYY-MM-DD`，下拉选出来的那一天。
      ⚠ **不信任 URL**：值不在 `group_diary()` 的结果里 ⇒ 当没选（回到「全部」）。
        这条很重要 —— 改完时间之后，原来筛的那天可能就没了（条目跑到别的日子去了），
        这时候**自然地退回全部**，别给她一个「筛不出来」的空页。
      ⚠ 纯粹是**读侧**的筛选：不碰 `load_diary()` 的写入，也不新增 ADR-22 开口。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")

    groups = group_diary(load_diary(uid)["entries"])
    ok = '<p class="hint" style="margin:0 0 10px">存下了。</p>' if saved == "1" else ""

    # 🔎 先把「筛哪天」定下来：`sel` 要么是一天、要么是空（= 全部）
    sel = ""
    total = 0
    if groups:
        total = sum(len(items) for _d, _l, items in groups)
        key = (day or "").strip()
        if key in set(str(d) for d, _l, _i in groups):
            sel = key

    filt_html = _day_filter(groups, sel, total) if groups else ""   # 一本都没写 ⇒ 不显示筛选
    q = _dayqs(sel)                                                 # 行内的 href 用它拼
    shown = [g for g in groups if (not sel or str(g[0]) == sel)]

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
        for _day, label, items in shown:
            rows = []
            for hm, e in items:
                mine = ('<span class="dmine">我</span>'
                        if e.get("src") == "her" else "")
                # ⚠ `%s` 第二个占位是 `q`（筛选串）—— 改完回到**原来筛的那天**，
                #   不是跳回全部（2026-09-30 她选的「保持筛选」）。
                rows.append(
                    '<a class="drow" href="/diary/e?id=%s%s">'
                    '<span class="dtm">%s</span>%s'
                    '<span class="dtx">%s</span>'
                    '<span class="dgo">改 ›</span></a>'
                    % (quote(str(e.get("id"))), q, _esc(hm), mine, _esc(e.get("text"))))
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
      %s
      <form method="get" action="/diary/new">
        <button type="submit" class="ghost">＋ 写一条</button>
      </form>
    </div>
    %s
    <p class="hint" style="margin:10px 4px 0">
      一条最多 %d 字，最多留 %d 条，满了丢最早的（你自己写的不会丢）。</p>
    %s
    """ % (ok, filt_html, list_html, DIARY_MAX_LEN, DIARY_MAX_ITEMS, _backbar())
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
        <textarea name="text" rows="10" maxlength="%d"
                  placeholder="今天发生了什么？"></textarea>
        <button type="submit">存下</button>
      </form>
      <p class="hint" style="margin:12px 0 0">
        这一段他会当成自己写的日记。空着留不住。</p>
    </div>
    %s
    """ % (msg, DIARY_MAX_LEN, _two_way_footer("/diary", "‹ 回日记"))
    return _page(body, title="写一条", css=DIARY_CSS, script=DIARY_JS)


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
async def diary_edit(request: Request, id: str = "", err: str = "", day: str = ""):
    """
    ✏️ **修改内容** —— 点列表里那一行进来的。**他写的那几条也能改**（她定的）。

    ⭐ 2026-09-30 她按截图定的两件事：
      ① 标题「改一条」→「**修改内容**」（`<h1>` 和 `_page(title=)` 两处，
         后者是浏览器底部 / 标签栏显示的那个）。
      ② 原来那行只读的「前天（9月28日 星期一） 20:50」**换成两个能改的控件**
         （日期 + 时间，B 形态）。
         ⚠ 那行**不是**导航也不是装饰 —— 它是这条日记的**发生时刻**，改成可编辑之后，
           列表里的「哪一天 / 天内第几条」会跟着它走（见 `edit_diary_by_her` 里 `day.pop()`）。

    ⚠ `id` 来自 URL ⇒ 只当**查询条件**用（精确匹配），不承担任何别的语义。
    ⚠ 删除放在**这一页里**（得先点进那一条）⇒ 已经是两步，不容易误删。
    🔎 `day`（2026-09-30）：她是从**筛了某一天**的列表点进来的 ⇒ 原样收着再带回去；
       改完 / 删完回到列表时还是那一天（她选的「保持筛选」）。
       ⚠ 它只跟着 URL 走，**不参与任何判断** —— 写错 / 过期了最多是筛选落空，不会有别的后果。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    e, dkey, _label, hm = _find(uid, id)
    if not e:
        return RedirectResponse("/diary", status_code=303)   # 改完了 / 被删了 ⇒ 回列表
    msg = '<p class="err">%s</p>' % _esc(err) if err else ""
    # 🖊「我」那个小章原来是挂在时间行尾巴上的，那行现在是控件了 ⇒ 挪到标题旁边。
    mine = ' <span class="dmine">我</span>' if e.get("src") == "her" else ""
    body = """
    <div class="card">
      <h1 style="text-align:center">修改内容%s</h1>
      <p class="dwhen" style="text-align:center">这条的时间</p>
      %s
      <form class="dform" method="post" action="/diary/e">
        <input type="hidden" name="id" value="%s">
        <input type="hidden" name="day" value="%s">
        <div class="dtime">
          <input type="date" name="d" value="%s">
          <input type="time" name="t" value="%s">
        </div>
        <textarea name="text" rows="10" maxlength="%d">%s</textarea>
        <button type="submit">保存</button>
      </form>
      <div style="margin-top:14px">
        <form method="post" action="/diary/del"
              onsubmit="return confirm('删掉就不要了吗？他会忘掉这一天。')">
          <input type="hidden" name="id" value="%s">
          <input type="hidden" name="day" value="%s">
          <button type="submit" class="ghost">删掉这条</button>
        </form>
      </div>
    </div>
    %s
    """ % (mine, msg,
           # ⭐ 第一个 id 是**「保存」表单**的（正文 + 时间一起交上去）
           _esc(str(e.get("id"))), _esc(str(day)),
           # ⚠ 两个原生控件的初值：`type=date` 吃的就是 `YYYY-MM-DD`（分组键原样），
           #   `type=time` 吃 `HH:MM`。两个都来自 `group_diary()` 的结果，页面不算。
           #   ⚠ 值必须 `_esc()` 后再进属性（有服务端解析兜底，但历史脏数据也可能在）。
           _esc(dkey), _esc(hm),
           DIARY_MAX_LEN, _esc(e.get("text")),
           # ⚠ 这第二个 id 是**「删掉这条」那个表单**里的 —— 删「取消」那行时很容易
           #    连带把它一起删掉（那个 `<p>` 里没有占位符，看着像是它配的那个参数）。
           #    少一个参数 ⇒ `TypeError: not enough arguments` ⇒ **整页 500**。
           #    `py_compile` 查不出来（字符串里的事它不管）⇒ 只能靠渲染测试抓。
           #    ⚠ 现在这一组参数是 「id, day」两个 ⇒ 删我用这块的数量也要同步，
           #      别手滑删掉一个又留下另一个 `%s`（那是同一个 500 的另一张脸）。
           _esc(str(e.get("id"))), _esc(str(day)),
           # ⭐ 2026-09-30 她按截图定的：卡片里那个「‹ 取消」**去掉**，
           #   底栏换成 `‹ 回日记 · ‹ 返回目录`（跟「更改」页 / 主页那三个编辑页同款）。
           #   ⚠ 为什么该去：那个「取消」的去向就是 `/diary`，跟底栏的「回日记」**重复**。
           #   ⚠ **「删掉这条」不删** —— 那是动作，不是导航（跟主页那两个「保存/清除」同理）。
           _two_way_footer("/diary%s" % _dayqs(day, first=True), "‹ 回日记"))
    return _page(body, title="修改内容", css=DIARY_CSS, script=DIARY_JS)


@app.post("/diary/e")
async def diary_edit_save(request: Request, id: str = Form(""), text: str = Form(""),
                          d: str = Form(""), t: str = Form(""), day: str = Form("")):
    """
    存改动（**正文 + 时间一起**）。

    ⚠ `src` / `id` 一个都不动 —— 改几个字不等于换个人写，改时间也一样。
      只有 `ts` 是她**真改了时间**才会被重写（见 `edit_diary_by_her`）。
    ⚠ 时间**服务端解析**（`diary_ts_from`）：`<input>` 里的值随手就能伪造，
      前端校验只是顺手，不是保险。解析不了 ⇒ **报错退回，绝不猜一个时间存进去**。
    ⚠ 两栏**都空** ⇒ 当成「没动时间」（她可能只想改字）——
      但**只填了一栏**就是没填完，报错，别静默按另一栏的一半处理。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    back = "/diary/e?id=%s%s" % (quote(str(id)), _dayqs(day))
    ts = None
    if d or t:
        ts = diary_ts_from(d, t)
        if ts is None:
            return RedirectResponse(back + "&err=" + quote("时间没改成 —— 日期和时间要一起填"),
                                    status_code=303)
    if not edit_diary_by_her(uid, id, _norm(text), ts=ts):
        return RedirectResponse(back + "&err=" + quote("没改成 —— 跟原来一样？"),
                                status_code=303)
    # ⚠ 回到列表时**带着筛选**（她选的「保持筛选」）。万一这天已经没条目了
    #   （比如她把时间改到了别的日子），列表那边会自动退回「全部」—— 不会给她一个空页。
    return RedirectResponse("/diary?saved=1%s" % _dayqs(day), status_code=303)


@app.post("/diary/del")
async def diary_del(request: Request, id: str = Form(""), day: str = Form("")):
    """
    🗑 删一条。⭐ **不留抑制名单**（跟「他记住的事」一致、跟画像标签相反）:
      日记没有后台自动轨会把它「写回来」—— 下一次摘要总结的是**新的对话**、
      是新内容，留痕反而会把将来那条新日记误挡在门外。

    🔎 `day`：删完回到列表时**还是她原先筛的那天**（她选的「保持筛选」）。
       ⚠ 例外：删掉的是那天最后一条 ⇒ 那天在下拉里没了 ⇒ 列表自动退回「全部」。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    delete_diary_by_her(uid, id)
    return RedirectResponse("/diary?saved=1%s" % _dayqs(day), status_code=303)
