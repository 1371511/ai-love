# -*- coding: utf-8 -*-
"""
🏠 主页（`/home`）—— **她自己的那一页**：个人信息 + 他记住的东西。

⭐ 2026-09-30 新建（她定的 IA）：「我的页」这块地全归主页，装四样东西
     ① 头像卡（我是谁）
     ② 关于你（称呼 / 生日 / 相遇那天 / 认识第几天）
     ③ 他记住的你（喜欢 / 不喜欢 / 特点 三类标签）
     ④ 他记住的事（`key_facts`）
   ⚠ 跟 `/affinity` **不是一回事**：那边是好感度后台（分数 / 等级 / 额度 / 牵绊短信），
     这边是「她这个人」。**别把好感度数字搬过来**。

🟢 **第 1 批整页只读** —— 一个字都不写 `memory/`。
🟡 **第 2 批（2026-09-30 本次）**：称呼 / 生日 **她自己可设** ——
   整页唯一的写入口是 `POST /home/edit/{kind}` ⇒ `UserProfile.set_by_her()`，
   已在 `tools/check_static.py` 的 `WEB_WRITE_EXCEPTION` 里开口（`Rafayel_profile`）。
🟡 2026-09-30 追加：
   · 头部改版（她按截图定的）：**头像在左**，右侧上=用户名、下=各种信息 + 「更改」。
   · 新增**独立的更改页** `/home/edit/profile`（**只改名字 + 头像**）——
     她明确要求**不套用 `/settings`**（「设置页我后面也会改内容的」）。
     ⚠⚠ 它**必须注册在 `/home/edit/{kind}` 前面**，否则 `{kind}` 会把 `profile` 抢走。
   · **上传口藏在头像里**（2026-09-30 她定的）：点头像 → 选图 → 选完**自动上传**。
     ⚠ 她同时澄清了一条口径：**「我没有说全站 0JS」** ⇒ 少量原生 JS 可以用
       （这儿只用了 `label[for]` + `onchange="this.form.submit()"` 一行，
       不引任何框架 / 外部文件）。顺手留了 `<noscript>` 兜底按钮 —— 关掉 JS 也能传。
   · **选完先裁剪**（2026-09-30 她要的「自由裁剪」）：圆形取景框 + 拖动 + 缩放滑块 +
     双指捏合，裁完导出 512×512 交给**同一个** `/home/edit/profile/avatar` ——
     ⇒ **服务端一行没动**：还是 `base.save_avatar()` 那套按文件头认图 / 2 MB 上限的校验，
       没开新路由、没给 ADR-22 加新口子。
     ⇒ 为什么在前端裁：跑 web 的那个 Python **没装 Pillow**，服务端裁要往系统 Python
       里装依赖，不值当；canvas 是浏览器自带的。
     ⇒ 掉链子 / 浏览器太老 / 是动图 ⇒ 一律退回「原图直传」，渐进增强不破。
🟠 还剩两批（第 3 批：他记住的你；第 4 批：他记住的事），见文件末尾的说明。

⚠⭐ 网页端**不许 `import Rafayel_profile` / `Rafayel_memory`** —— 那两个在
   `check_static.py` 的 `WRITER_MODULES` 里，一 import 就报红。
   ⇒ 本页取数只有两条路：
     · `Rafayel_affinity.compute()`（白名单内，安全）→ 称呼 / 三类标签 / 认识天数
     · **直接 `json.load` 读 `memory/`**（读不算写，ADR-22 管的是写）→ 生日 / key_facts
"""
import json
import os
from urllib.parse import quote

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from base import (
    app, _page, _esc, _safe_uid, _avatar_url, _load_users, _save_users, _current_uid,
    _backbar, _two_way_footer, MEMORY_DIR, MENU_PATH, AVATAR_MAX,
    drop_avatar, save_avatar,
    _check_met_day, met_known,
    # 🔐 账号表的读-改-写事务（下面两处改 `users.json` 的地方都要包住）
    _users_txn,
)
# ⚠ `_two_way_footer` 原本**定义**在本文件里（2026-09-30 加的），**当天就下沉到 `base.py`** ——
#   因为她要求「日记功能页的底栏也一样」，日记那两个页也要同款底栏。
#   再留在这儿，`page/diary.py` 就只能反向 import 本页（方向脏）或抄一份（口径迟早漂）。
#   ⇒ 跟 `met_known()` / `_check_met_day()` 一样处理：**唯一实现放共用底座**。
#   本文件里所有调用点**一个字没改**，只是来源从本模块变成了 `base`。
from Rafayel_affinity import compute
# ⭐ 她的**默认称呼**（画像里没记的时候他就这么叫）—— 唯一真相源在 `Rafayel_config`。
#   ⚠ `Rafayel_config` 是网页端的**白名单模块**（见 `tools/check_static.py` 的
#     `WEB_WHITELIST`）⇒ 这一行**不需要**开 ADR-22 的口子。
from Rafayel_config import DEFAULT_USER_NAME
# ⚠ 这两个是本页**仅有的**写盘入口（ADR-22 开口，见 `tools/check_static.py` 的
#   `WEB_WRITE_EXCEPTION`）。别再往下加第三个写盘模块 —— 加一个就得再开口子一次。
# ⭐ `Rafayel_memory` 是第 4 批（2026-09-30「他记住的事」可增删改）才开的第 5 条口子。
from Rafayel_profile import UserProfile
from Rafayel_memory import (
    add_fact_by_her, edit_fact_by_her, delete_fact_by_her,
)


# 🌐 第 3 批：她能自己增删改的那三类标签（**只是数组项**，不含 name / birthday 那两个单值）
#    ⚠ 网页端**不许**自己拼 json —— 一律走 `UserProfile` 的 `add/edit/delete_by_her()`。
_TAG_KINDS = ("likes", "dislikes", "traits")
_TAG_LABEL = {"likes": "喜欢", "dislikes": "不吃 / 不喜欢", "traits": "其他特点"}


# 🌐 主页上「她自己能设」的那几项（第 2 批：称呼 / 生日；第 3 批会再扩到三类标签）
#   值 = (标题, 说明, 输入框 placeholder)
#   ⚠ 这一张表里的项**落在 `memory/{uid}_profile.json`**（走 `UserProfile.set_by_her()`）。
_EDITABLE = {
    "name": ("他怎么称呼你",
             "填一个你希望他叫你的名字。改完他下一句就这么叫你。",
             # ⭐ 空框里的提示 = **他实际会叫的那个**（不是随便一个举例）。
             #   2026-09-30 她截图：这儿原来写的是「小辞」（她的名字），
             #   而她画像里没记称呼 ⇒ 他叫的其实是「保镖小姐」⇒ 提示跟实际不一致。
             DEFAULT_USER_NAME),
    "birthday": ("你的生日",
                 "填月和日就行，比如 03-06、3月6号。他记得住，到那天也会自己提。",
                 "03-06"),
}

# ⭐ 占位提示**跟着当前值走**的那几项（2026-09-30 她提的：把「保镖小姐」从框里删掉之后，
#   空框里要提示「保镖小姐」）。
#   ⚠ 那种「空着时提示什么」由表里第三项决定，而**称呼**那一项现在指向
#     `Rafayel_config.DEFAULT_USER_NAME`（= 画像没记时他真会叫的那个）——
#     不是随便一个举例，见上面 `_EDITABLE` 的说明。
#   ⚠ **只有「称呼」这么办**：它的当前值本身就是一个有意义的示范（他现在就这么叫她），
#     擦掉之后拿它当提示，等于「提醒你原来填的是什么、改回也是一个选择」。
#   ⚠ 而「生日」的占位是**格式样例**（`03-06`）—— 换成当前值就把「怎么写」的提示弄丢了，
#     何况那一格本来就带着当前值，用户看得见，不需要再提示一遍。
#   ⚠ 只在当前值**非空**时顶替；空着（刚「清除」过）就回落到表里那个举例。
_PH_FROM_CUR = {"name"}

# 🌐 落盘位置**不一样**的那一项：相遇日存在 `web/users.json`（跟 /settings 同一格），
#    **不进 memory** —— 所以不能混进上面那张表（那张表统一走 `UserProfile`）。
#   ⚠ 两个入口（这里 与 `/settings`）**共用** `base._check_met_day()` 那套校验。
#   ⭐ 2026-09-30 她提：「相遇日，改成和生日类似的格式，不同的是它带年月日」
#      ⇒ 跟生日一样挂在「关于你」那张卡里、各带一个「改」，
#        但输入换成**原生日期控件**（带年月日选择器，不是纯手打）。
_EDITABLE_USERJSON = {
    "met_day": ("你们相遇",
                "填你们认识的那一天（带年份）。填上之后，「认识」才开始算。",
                "2026-09-22"),
}


# 🏠 本页专用样式（走 `_page(..., css=...)`，**不动全站那份 CSS**）
#    ⚠ 只放「这一页才有的东西」；`.card` / `.chip` / `.hint` 那些继续用全站的。
#    ⚠ 说明写在 Python 注释里，**别写进模板字符串** —— 那里面连 HTML 注释都会原样发到浏览器。
HOME_CSS = """/* 头部：头像左，右侧上=用户名、下=各种信息+更改（2026-09-30 她按截图定的版式） */
.hero{display:flex;align-items:center;gap:14px}
.hero .face{flex:0 0 56px;width:56px;height:56px;border-radius:50%;background:var(--c-brand-soft);
            color:var(--c-brand-deep);display:flex;align-items:center;justify-content:center;
            font-size:15px;overflow:hidden}
.hero .face img{width:100%;height:100%;object-fit:cover;display:block}
/* ⚠⚠ 类名**不能叫 `.who`** —— 那份全站 CSS 里已经有 `.who{display:flex;...}`
       （目录页的「头像 + 名字」块用的），重名会把这里顶成一行。
       2026-09-30 真踩：名字和信息挤在同一行，就是被那条顶的。 */
.hero .txtcol{flex:1;min-width:0}
.hero .txtcol h1{margin:0 0 5px;font-size:17px}
.hero .meta{display:flex;align-items:baseline;justify-content:space-between;gap:10px}
/* ⚠⚠ `overflow-wrap:anywhere` 已删（2026-09-30 她截图：日期被从中间劈开）——
   它允许「放不下就在任意字符断开」，日期里的汉字 / 数字全是断点 ⇒ 必须去掉了。
   改由下面 `.mi` 的 `nowrap` 保证「一项要么整块换行、要么整块留在这行」。 */
.hero .meta .txt{color:var(--c-muted);font-size:12px;min-width:0}
.mi{white-space:nowrap}
/* 「 · 」分隔符由 CSS 生成 ⇒ HTML 里每一项都是干干净净的一块（也能单独拿来测） */
.mi+.mi::before{content:" · "}
.hero .meta a{white-space:nowrap}
/* 底栏两个动作的样式（`.twobar`）**已搬到 `base.py` 的全站 CSS**（2026-09-30）：
   `_two_way_footer()` 是共用部件，样式不能寄存在本页的 `HOME_CSS` 里 ——
   不带 `css=HOME_CSS` 的页面（`/home/edit/{kind}`、`/diary/*`）会拿不到，两个链接挤成一团。
   ⚠ 别在这儿写回一份。详见 `base.py` 里 `.twobar` 那段说明。 */
/* 更改页那张大头像 —— 它同时是**上传按钮**（2026-09-30 她定的：上传口藏在头像里） */
.bigav{width:80px;height:80px;border-radius:50%;background:var(--c-brand-soft);color:var(--c-brand-deep);
       display:flex;align-items:center;justify-content:center;font-size:20px;overflow:hidden;
       cursor:pointer}
.bigav img{width:100%;height:100%;object-fit:cover;display:block;pointer-events:none}
.bigav:hover{box-shadow:0 0 0 3px var(--c-brand-halo)}
/* 真·file input 藏起来但**留在 DOM 里**（不能 `display:none`，有些浏览器就不弹选择框了）。
   ⚠ 点 `.bigav`（= label[for=avpick]）等于点它；选完图 `onchange` 触发裁剪面板。 */
.avpick{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden;
        clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap;border:0;padding:0}
/* 🖼 裁剪面板（2026-09-30 她要的「自由裁剪」）—— 选完图就地展开在头像卡下面。
   ⚠ **不做 `position:fixed` 的弹层**：移动端 fixed 弹层要自己锁滚动、还要躲输入法，
     这里页面本来就短，直接展开 + 滚到它更省心，也不会有「弹层里的滚动穿透」。 */
.cropbox{margin-top:16px;border-top:1px solid var(--c-sunk);padding-top:14px}
.cropstage{position:relative;width:100%;max-width:300px;margin:0 auto;
           aspect-ratio:1/1;overflow:hidden;background:var(--c-sunk-soft);border-radius:10px;
           touch-action:none;-webkit-user-select:none;user-select:none;cursor:grab}
.cropstage img{position:absolute;left:0;top:0;max-width:none;will-change:left,top}
/* 圆形取景框：框内是最终头像，框外用 `box-shadow` 铺一层暗纱
   （那个 9999px 的扩散会被 `.cropstage` 的 `overflow:hidden` 切掉，正好只在框内留亮区）。
   ⚠ `pointer-events:none` —— 别让它把手指事件截走，图就拖不动了。 */
.cropmask{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
          width:calc(100% - 64px);height:calc(100% - 64px);border-radius:50%;
          border:2px solid var(--c-card);box-shadow:0 0 0 9999px var(--c-veil);pointer-events:none}
.zoomrow{display:flex;align-items:center;gap:10px;margin:12px 0}
.zoomrow span{font-size:12px;color:var(--c-muted);white-space:nowrap}
.zoomrow input[type=range]{flex:1;min-width:0;margin:0}
.cropbtns{display:flex;gap:10px;flex-wrap:wrap}
.cropbtns button{flex:1 1 0;white-space:nowrap}
.cropbox[hidden]{display:none}
/* 🏷 第 3 批：「他记住的你」按三类分组，她自己填的跟「他记住的」**分开摆**（她拍板：分开放）
   ⚠ 色值一律走 `--c-*` 变量（2026-09-30 立的规矩，别再写硬编码）。 */
.taggrp{margin-bottom:12px}
.taggrp:last-of-type{margin-bottom:0}
.taglbl{font-size:12px;color:var(--c-muted);margin:0 0 7px}
/* chip 在第 3 批变成链接（点进去改 / 删）⇒ 不能带着下划线和蓝字 */
a.chip{text-decoration:none;color:inherit}
.chip.mine{background:var(--c-brand-soft);color:var(--c-brand-deep)}
.chip .tagby{font-size:11px;opacity:.7;margin-left:5px}
/* ⚠ 全站那份 CSS 只给了 `input` / `button`，**没有 `select`** ⇒ 下拉得自己补一套，
   不然它会掉回浏览器默认样式（跟旁边的输入框不是一个画风）。
   ⚠ 2026-09-30 把 `input` / `textarea` 也拉进来一起管宽度 ——
     之前只有 `select` 是满宽，旁边的输入框是浏览器默认那一小条（跟 select 差一截，
     看着像两个控件打架）。现在 `.tagform` 里三种控件同一个宽度带来的代码就一份。
   ⚠ `width:100%` **必须**配 `box-sizing:border-box`：全站（查过 `base.py`）**没有**
      `* { box-sizing: border-box }` ⇒ 少了这句，左右各 10px 的 padding 会把
      控件撑出卡片外面去。
   ⚠ 只作用于 `.tagform` 内部：同页那个**视觉隐藏的文件框**（`.avpick`）挂在
      `#avform` 上、**不带** `tagform` ⇒ 不会被这条撑成一整行露出来。 */
.tagform select,.tagform input,.tagform textarea{width:100%;box-sizing:border-box;
                font:inherit;padding:8px 10px;border-radius:var(--r-ctl);
                border:0.5px solid var(--c-line-2);background:var(--c-card)}
/* 📝 「他记住的事」要**长文本框**（2026-09-30 她指出：一条最多 80 字，单行框塞不下）
   ⚠ `resize:vertical` —— 只允许上下拉；横向拉会把卡片撑变形（很难看，别开）。
   ⚠ `display:block` —— textarea 默认是 inline-block，底部会留一条行内空隙。 */
.tagform textarea{min-height:104px;line-height:1.6;resize:vertical;display:block}
/* 📌 第 4 批：「他记住的事」每条一行，整行可点（点进去改 / 删）
   ⚠ 类名**别叫 `.go`** —— 全站 CSS 里已经有 `.go` 了（查过，会撞）。 */
.factrow{display:flex;justify-content:space-between;align-items:baseline;gap:10px;
         padding:8px 0;border-bottom:0.5px solid var(--c-hair);
         font-size:13px;text-decoration:none;color:inherit}
.factrow:last-of-type{border-bottom:none}
.factrow .facttxt{min-width:0;overflow-wrap:anywhere}
.factrow .factgo{color:var(--c-hint);white-space:nowrap;font-size:12px}"""


# 🖼 裁剪面板的 JS（2026-09-30 加）。
#    ⚠ 只在这一页用 ⇒ 就放这儿；**等 `/settings` 也要裁剪时再搬到 `base.py`**
#      （跟「头像写盘三件套」同一个道理：别提前为「将来可能复用」做抽象）。
#    ⚠ 零依赖、零框架：原生 FileReader + canvas + fetch，不引任何外部文件。
#    ⚠ 全程走**同一条提交路径**：裁完的图还是 POST 给 `/home/edit/profile/avatar`，
#      服务端那份 `base.save_avatar()` 校验（按文件头认图 / 2 MB 上限）原样生效 ——
#      **不开新路由、不新增 ADR-22 开口**，省得又要破一次例。
#    ⚠ 任何一步掉链子（读不了图 / 浏览器太老 / 导出失败）⇒ `fallback()` 直传原图，
#      也就是 `<noscript>` 那一条路 —— **渐进增强没被绕开**。
#    ⚠ `__SOFT__` 由 Python 替换成数字（服务端 2 MB 上限，这里留一成余量）。
CROP_JS = """
<script>
(function(){
  var box=document.getElementById('cropbox');
  if(!box) return;
  var stage=document.getElementById('cropstage'),
      img=document.getElementById('cropimg'),
      zoom=document.getElementById('cropzoom'),
      form=document.getElementById('avform'),
      input=document.getElementById('avpick'),
      bOk=document.getElementById('cropok'),
      bRaw=document.getElementById('cropraw'),
      bNo=document.getElementById('cropcancel');
  if(!stage||!img||!zoom||!form||!input) return;

  var PAD=32;          /* 取景圆到舞台边的留白（跟 CSS 的 calc(100% - 64px) 对齐） */
  var OUT=512;         /* 导出边长：头像最大才显示 80px，512 够 2 倍屏了 */
  var MAXS=4;          /* 最多放大到初始的 4 倍 */
  var SOFT=__SOFT__;   /* 超过这个体积就改存 jpeg */

  var S={img:null,nat:null,x:0,y:0,s:1};
  var pts={},base=null;

  function W(){ return stage.clientWidth||300; }
  function D(){ return W()-2*PAD; }
  function scale0(){ return Math.max(D()/S.nat.w,D()/S.nat.h); }
  function boxOf(s){ var k=scale0()*s; return {w:S.nat.w*k,h:S.nat.h*k}; }
  /* 图片不许缩到露白，也不许拖出取景圆 —— 夹住偏移量 */
  function clamp(){
    var b=boxOf(S.s),d=D();
    var lx=Math.max(0,(b.w-d)/2), ly=Math.max(0,(b.h-d)/2);
    if(S.x>lx)S.x=lx; if(S.x<-lx)S.x=-lx;
    if(S.y>ly)S.y=ly; if(S.y<-ly)S.y=-ly;
  }
  function draw(){
    var b=boxOf(S.s),w=W();
    img.style.width=b.w+'px'; img.style.height=b.h+'px';
    img.style.left=(w/2+S.x-b.w/2)+'px';
    img.style.top=(w/2+S.y-b.h/2)+'px';
    zoom.value=Math.round(S.s*100);
  }
  function rel(p){
    var r=stage.getBoundingClientRect();
    return {x:p.x-r.left-r.width/2,y:p.y-r.top-r.height/2};
  }
  function mid(){
    var ks=Object.keys(pts),x=0,y=0,i;
    for(i=0;i<ks.length;i++){ x+=pts[ks[i]].x; y+=pts[ks[i]].y; }
    return {x:x/ks.length,y:y/ks.length};
  }
  function span(){
    var ks=Object.keys(pts);
    if(ks.length<2) return 0;
    var a=pts[ks[0]],b=pts[ks[1]];
    return Math.sqrt((a.x-b.x)*(a.x-b.x)+(a.y-b.y)*(a.y-b.y));
  }
  /* ⭐ 「抓点拖动」：按下那刻记下手指落在**图片的哪个位置**（u,v 是 0~1 的比例），
     之后不管平移还是双指缩放，都让那一点跟着手指走。
     好处是平移和缩放能共用同一套公式 —— 单指时 span 不变 ⇒ s 不变 ⇒ 退化成纯平移。 */
  function setBase(){
    var ks=Object.keys(pts);
    if(!ks.length||!S.nat) return;
    var m=rel(mid()),b=boxOf(S.s);
    base={s:S.s,d:Math.max(1,span()),
          u:(m.x-S.x+b.w/2)/b.w,v:(m.y-S.y+b.h/2)/b.h};
  }
  stage.addEventListener('pointerdown',function(e){
    if(box.hidden||!S.nat) return;
    try{ stage.setPointerCapture(e.pointerId); }catch(err){}
    pts[e.pointerId]={x:e.clientX,y:e.clientY};
    setBase();
    e.preventDefault();
  });
  stage.addEventListener('pointermove',function(e){
    if(!pts[e.pointerId]||!base) return;
    pts[e.pointerId]={x:e.clientX,y:e.clientY};
    var s=base.s*(Object.keys(pts).length>1? span()/base.d : 1);
    S.s=Math.max(1,Math.min(MAXS,s));
    var b=boxOf(S.s),m=rel(mid());
    S.x=m.x+b.w*(0.5-base.u);
    S.y=m.y+b.h*(0.5-base.v);
    clamp(); draw();
    e.preventDefault();
  });
  function end(e){
    if(!pts[e.pointerId]) return;
    delete pts[e.pointerId];
    if(Object.keys(pts).length) setBase(); else base=null;
  }
  stage.addEventListener('pointerup',end);
  stage.addEventListener('pointercancel',end);

  zoom.addEventListener('input',function(){
    if(!S.nat) return;
    var old=boxOf(S.s);
    var ns=Math.max(1,Math.min(MAXS,zoom.value/100));
    var k=boxOf(ns).w/old.w;      /* 绕舞台中心缩放 ⇒ 偏移量按同一比例变 */
    S.x*=k; S.y*=k; S.s=ns;
    clamp(); draw();
  });

  /* 直传原图 —— 也就是关掉 JS 时 `<noscript>` 走的那条路。
     ⚠ 动图（gif）只能走这条：canvas 导出来是静图，裁了就不动了。 */
  function fallback(){ form.submit(); }

  function send(cv){
    function push(blob){
      if(!blob){ fallback(); return; }
      var fd=new FormData();
      fd.append('pic',blob,'avatar.png');
      fetch(form.action,{method:'POST',body:fd,credentials:'same-origin'})
        .then(function(){ location.replace('/home/edit/profile?ok=avatar'); })
        .catch(function(){
          location.replace('/home/edit/profile?err='+
            encodeURIComponent('换头像失败了，再试一次'));
        });
    }
    cv.toBlob(function(blob){
      if(blob&&blob.size>SOFT){ cv.toBlob(push,'image/jpeg',0.92); return; }
      push(blob);
    },'image/png');
  }
  /* 导出的是**取景圆的外接正方形** —— 显示的头像正是这个正方形的内切圆
     （CSS 那边是 object-fit:cover + border-radius:50%），所以「裁的时候看到的」=
     「显示出来的」，不会差一块。 */
  function crop(){
    if(!S.nat) return;
    var w=W(),d=D(),b=boxOf(S.s),k=b.w/S.nat.w;
    var ix=w/2+S.x-b.w/2, iy=w/2+S.y-b.h/2, cx=(w-d)/2;
    var cv=document.createElement('canvas');
    cv.width=OUT; cv.height=OUT;
    var ctx=cv.getContext('2d');
    ctx.fillStyle='#fff'; ctx.fillRect(0,0,OUT,OUT);  /* 垫白底：万一转 jpeg 不会出黑块 */
    ctx.drawImage(S.img,(cx-ix)/k,(cx-iy)/k,d/k,d/k,0,0,OUT,OUT);
    send(cv);
  }
  function open(file){
    var fr=new FileReader();
    fr.onload=function(){
      var im=new Image();
      im.onload=function(){
        S.img=im;
        S.nat={w:im.naturalWidth||im.width,h:im.naturalHeight||im.height};
        if(!S.nat.w||!S.nat.h){ fallback(); return; }
        S.s=1; S.x=0; S.y=0;
        img.src=im.src;
        box.hidden=false;
        draw();
        box.scrollIntoView({block:'center'});
      };
      im.onerror=fallback;
      im.src=fr.result;
    };
    fr.onerror=fallback;
    fr.readAsDataURL(file);
  }
  window.RAFcrop=function(inp){
    if(!inp.files||!inp.files[0]) return;
    if(!(window.FileReader&&window.FormData&&document.createElement('canvas').toBlob)){
      inp.form.submit(); return;
    }
    open(inp.files[0]);
  };
  if(bOk) bOk.onclick=crop;
  if(bRaw) bRaw.onclick=fallback;
  if(bNo) bNo.onclick=function(){
    box.hidden=true; input.value='';   /* 清空才能重选同一张 */
  };
})();
</script>"""


def _read_json(path):
    """只读地读一个 json；没有 / 坏了 ⇒ 返回 {}（**不写、不抛**）。"""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print("[🏠] 读文件失败（%s）：%s" % (os.path.basename(path), e))
        return {}


def _one_line(s):
    """
    📝 长文本框里她**可能会按回车** ⇒ 把换行 / 连续空白压成单个空格。

    ⭐ 为什么要压：`key_facts` 里一条就是**一句话**（塞给 LLM 时换行没意义），
       而且主页那一行的 HTML 会把换行渲染成一个空格 ——
       **进库前就压平，才是「存的是什么 = 她看到的是什么」**。
    ⚠ `str.split()` 不带参数自带这个效果，不用正则、不用 `import re`。
    ⚠ 顺手吃掉**冒号后面**那个空格：她若在「用户让你记住：」后面敲了回车，
       压平会变成「用户让你记住： 她怕黑」—— 跟引擎自己生成的句式不是一个写法。
    """
    return " ".join((s or "").split()).replace("： ", "：")


@app.get("/home", response_class=HTMLResponse)
async def home(request: Request):
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    a = compute(uid, MEMORY_DIR)
    rec = (_load_users().get(uid) or {})

    # 显示名：网页自己存的优先，没有就用他记住的称呼（来自 memory，只读）
    shown_name = (rec.get("display_name") or "").strip() or a["name"] or "你"

    # ---------------- ② 关于你 ----------------
    # ⭐ 第 2 批起用 `UserProfile` 读（`compute()` 不往外吐生日），写也走它 ⇒ 读写同一份，不会漂。
    prof = UserProfile(uid)
    birthday = (prof.data.get("birthday") or "").strip()
    # 称呼仍以 `compute()` 那份为准（它读的是同一个文件，这里不再读第二遍）
    if (prof.data.get("name") or "").strip():
        a["name"] = prof.data["name"]

    # 相遇那天 + 认识第 N 天：**全站一个口径**，逻辑在 `base.met_known()`（别再抄一份）。
    # ⭐⭐ 2026-09-30 她定：「认识多少天默认为空，直到补上相遇日再开始计算」
    #    ⇒ **去掉了**原来那条「没填就回落到日志回填的 first_day」的兜底。
    #    （`compute()` 仍然返回 `first_day` / `known_days`，`/affinity` 那边的日志信息照用；
    #      变的只是**界面口径**：她不填，我们就当不知道。）
    met_day, known = met_known(uid, a)

    def _row(label, value, hint_link="", edit_href="", edit_label="改", suffix=""):
        """
        一行「标签 · 值」（值空 ⇒ 显示一句引导，不空着）。`edit_href` ⇒ 右侧挂一个入口。

        ⚠ `edit_label` 是给「相遇」那行用的：**空着的时候写「填一下」、填过就写「改」**
          —— 空表填是「补」，已有值才是「改」，两个词对用户不是一回事。

        ⚠ `suffix` 是给「兜底默认值」用的（2026-09-30 她选 A）：**没填过的值别冒充是他记住的**。
          称呼没填 ⇒ `保镖小姐（默认）`，跟 `/home/edit/name` 空框里的提示同一口径。
          为什么后缀走 `.hint`（小灰字）而不是把整值置灰：
          ① 这一行的**标签本来就是灰的**，值再灰就整行糊掉了；
          ② 同一张卡里别的空值（「还没记住 / 还没填」）是深色的，只灰这一行像渲染 bug；
          ③ `.hint` 是本页既有的「旁注」样式（右边的「改」就是它），借用不造新类名。
        """
        tail = ""
        if edit_href:
            tail += '　<a href="%s" class="hint">%s</a>' % (edit_href, _esc(edit_label))
        if hint_link:
            tail += '　<a href="%s" class="hint">%s</a>' % hint_link
        return ('<div style="display:flex;justify-content:space-between;align-items:baseline;'
                'padding:7px 0;border-bottom:0.5px solid rgba(0,0,0,.06)">'
                '<span class="muted" style="font-size:13px">%s</span>'
                '<span style="font-size:13px">%s%s</span></div>'
                % (_esc(label),
                   _esc(value) + (('<span class="hint">%s</span>' % _esc(suffix)) if suffix else ""),
                   tail))

    rows = []
    # ⭐ 空的不是「还没记住」—— 他其实**正在用** `DEFAULT_USER_NAME` 叫她，
    #    所以写清楚它是兜底默认（跟输入框里的提示一个口径），并置灰表示「她没改过」。
    rows.append(_row("他怎么称呼你",
                     a["name"] or DEFAULT_USER_NAME,
                     edit_href="/home/edit/name",
                     suffix="" if a["name"] else "（默认）"))
    rows.append(_row("你的生日", birthday or "还没记住",
                     edit_href="/home/edit/birthday"))
    # 🌟 「相遇」现在跟「生日」一个待遇：本卡里就地能改（不再跳 `/settings`）。
    #    空着的时候入口写「填一下」；填过就写「改」。
    rows.append(_row("你们相遇", met_day or "还没填",
                     edit_href="/home/edit/met_day",
                     edit_label="改" if met_day else "填一下"))
    # ⚠ 「认识」**不填相遇日就是空的**（她 2026-09-30 定的口径）——
    #    这里的 `—` 不是「算不出来」，是「还没开始算」。
    rows.append(_row("认识", ("第 %d 天" % known) if known else "—"))
    # 最后一行不要那条分隔线
    rows[-1] = rows[-1].replace("border-bottom:0.5px solid rgba(0,0,0,.06)", "border-bottom:none")

    # ---------------- ③ 他记住的你 ----------------
    # ⭐ 第 3 批（2026-09-30）：**可增删改** + 「她填的」跟「他记住的」**分开放**（她拍板：分开放）。
    #    ⇒ 按三类分组；组内**先列他记住的**，再列**她填的**（异色 + 一个小字标记）。
    #      ⚠ 不分两张卡 —— 同一类（比如「喜欢」）被拆到两处反而更难看全。
    #    ⇒ chip 整体是个链接，**点进去**才看到「保存 / 删掉」
    #      （她 2026-09-30 提过「两个位置不要靠太近，容易误按」⇒ 不在 chip 后挂两个小链接）。
    _manual = prof.data.get("manual") or {}

    def _chip(kind, v, mine):
        return ('<a class="chip%s" href="/home/edit/tag/e?kind=%s&v=%s">%s%s</a>'
                % (" mine" if mine else "", kind, quote(v or ""), _esc(v),
                   '<span class="tagby">你填的</span>' if mine else ""))

    _blocks = []
    for _k in _TAG_KINDS:
        _auto = [x for x in (a.get(_k) or []) if isinstance(x, str) and x.strip()]
        _mine = [x for x in (_manual.get(_k) or []) if isinstance(x, str) and x.strip()]
        if not _auto and not _mine:
            continue
        _blocks.append('<div class="taggrp"><p class="taglbl">%s</p>%s%s</div>'
                       % (_esc(_TAG_LABEL[_k]),
                          "".join(_chip(_k, x, False) for x in _auto),
                          "".join(_chip(_k, x, True) for x in _mine)))
    _add_link = '<p style="margin:4px 0 0"><a href="/home/edit/tag" class="hint">+ 加一条</a></p>'
    if _blocks:
        prof_chips = "".join(_blocks) + _add_link
    else:
        prof_chips = ('<span class="hint">他还没记住什么 —— 多聊几句就有了</span>'
                      + _add_link)

    # ---------------- ④ 他记住的事 ----------------
    # ⭐ 第 4 批（2026-09-30）：**可增删改** —— 每条整行可点，点进去改 / 删。
    #    ⚠ 显示的是**存储原文**（含「用户让你记住：」这类模板前缀）——
    #      那些前缀本是写给模型看的，本批先不拆（见档里 D5），等她看完效果再定。
    #    ⚠ 读还是**直接 json.load**（读不算写，ADR-22 管的是写）；写才走引擎那三个函数。
    mem = _read_json(os.path.join(MEMORY_DIR, "%s.json" % _safe_uid(uid)))
    # ⚠⚠ **`key_facts` 必须是列表**（2026-09-30 补）：它是字符串时会**逐字符迭代**，
    #    「用户对海鲜过敏」就变成页面上竖着一排一个字一个字的行。
    #    （同一份文件的读终点还有一处 `load_memory()`，那边也加了同一道闸 ——
    #     两边口径必须一致，否则会出现「页面显示正常、引擎却当成空」。）
    _raw_facts = mem.get("key_facts")
    facts = [x.strip() for x in (_raw_facts if isinstance(_raw_facts, list) else [])
             if isinstance(x, str) and x.strip()]
    if facts:
        facts_html = "".join(
            '<a class="factrow" href="/home/edit/fact/e?v=%s">'
            '<span class="facttxt">· %s</span><span class="factgo">改 ›</span></a>'
            % (quote(x), _esc(x)) for x in facts)
    else:
        facts_html = ('<span class="hint">他还没记住什么具体的事 ——'
                      ' 聊到要紧的地方，他自己会记下来</span>')
    facts_html += ('<p style="margin:10px 0 0">'
                   '<a href="/home/edit/fact" class="hint">+ 加一件</a></p>')

    # ---------------- ① 头部（头像在左，右边两行：用户名 / 各种信息 + 更改）----------------
    # ⭐ 2026-09-30 她按截图定的版式：**头像左侧**，右侧上面是用户名、下面是各种信息，
    #    「更改」挂在信息那一行的尾巴上（不是再单开一行）。
    # ⚠ 头部的「各种信息」是**概览**（认识多久 / 生日 / 哪天相遇），
    #   逐项能改的入口仍然在下面「关于你」那张卡里（各带一个「改」）—— 两张不是重复关系。
    def _mmdd_cn(v):
        """`03-06` ⇒ `3月6日`（存储格式别直接摆给用户看）。"""
        try:
            mm, dd = (v or "").split("-")
            return "%d月%d日" % (int(mm), int(dd))
        except Exception:
            return v or ""

    def _ymd_cn(v):
        """
        `2026-09-22` ⇒ `2026年9月22日`。

        ⭐ 为什么和生日那条分开写：**相遇日是带年份的**，而生日故意不带
          （年年都过，写年份反而年年要改）。两条格式要求不一样，不硬凑一个函数。
        ⚠ 解析不了就原样返回 —— 存储里是脏数据时，宁可显示原样也别显示空白
          （空白会让她以为「没填」）。
        """
        try:
            y, mm, dd = (v or "").split("-")
            return "%d年%d月%d日" % (int(y), int(mm), int(dd))
        except Exception:
            return v or ""

    _parts = []
    if known:
        _parts.append("认识第 %d 天" % known)
    if birthday:
        _parts.append("生日 %s" % _mmdd_cn(birthday))
    if met_day:
        _parts.append("相遇 %s" % _ymd_cn(met_day))
    # ⭐ 2026-09-30 她截图：「相遇 2024年9月13日」被**从日期中间劈开**（「13日」自己掉到
    #    第二行，看着很乱）。根因是 `.txt` 那条 `overflow-wrap:anywhere` ——
    #    它明确允许「一个词放不下就在任意字符间断开」，而日期里的汉字和数字正好都是断点。
    #    ⇒ 改法：**每一项单独包一个 `.mi`**，`.mi` 上是 `white-space:nowrap`（项内绝不拆），
    #      分隔用的「 · 」改由 CSS 的 `::before` 生成 ⇒ 换行只可能发生在**项与项之间**。
    #    ⚠ 别再拼回一个整串（`" · ".join(...)`）—— 那样又回到「随便哪儿都能断」。
    meta_html = "".join('<span class="mi">%s</span>' % _esc(p) for p in _parts)
    if not meta_html:
        # 一句都没有（刚认识、什么都没填）⇒ 仍然是**一个不可断的块**
        meta_html = '<span class="mi">%s</span>' % _esc("还没聊过，先跟他说句话吧")

    _av = _avatar_url(uid)
    if _av:
        av_html = '<img src="%s" alt="">' % _av
    else:
        av_html = _esc(shown_name[:2])

    # 💬 刚保存完回来 ⇒ 顶上挂一句（**不弹窗、不用 JS**，就是一行蓝底小字）
    saved_note = ('<div class="note">改好了。他下次开口就是按这个来。</div>'
                  if request.query_params.get("saved") else "")

    body = """
    %s
    <div class="card">
      <div class="hero">
        <div class="face">%s</div>
        <div class="txtcol">
          <h1>%s</h1>
          <div class="meta">
            <span class="txt">%s</span>
            <a href="/home/edit/profile" class="hint">更改</a>
          </div>
        </div>
      </div>
    </div>
    <div class="card"><h2>关于你</h2>%s</div>
    <div class="card"><h2>他记住的你</h2>%s</div>
    <div class="card"><h2>他记住的事</h2>%s</div>
    %s
    """ % (saved_note,
           av_html,
           _esc(shown_name),
           # ⚠ 这里进的是**已经拼好且已转义**的 `meta_html`（每个 `.mi` 自己 `_esc`），
           #   别再包一层 `_esc()` —— 那会把标签本身也转义成文字。
           meta_html,
           "".join(rows),
           prof_chips,
           facts_html,
           _backbar())
    return _page(body, css=HOME_CSS)


# ⚠⚠ **这一块必须排在 `/home/edit/{kind}` 前面** —— FastAPI 按注册顺序匹配，
#    `/home/edit/{kind}` 的 `{kind}` 会把 `profile` 也吃进去（那种写法一注册就抢走）。
#    `/home/edit/profile/avatar*` 多一层路径，倒是不会被抢。
@app.get("/home/edit/profile", response_class=HTMLResponse)
async def home_edit_profile(request: Request, ok: str = "", err: str = ""):
    """
    🪪 **更改**（`/home/edit/profile`）—— 她 2026-09-30 要的**独立页**：
    **只改名字和头像**，跟 `/settings` 那套（相遇那天 / 改密码）完全分开。

    ⚠ 她原话：「不要套用设置页的东西，设置页我后面也会改内容的」⇒
      这一页**不复用** `/settings` 的任何表单 / 路由，是独立的一张卡。
    ⚠ 头像那三个路由（上传 / 移除）也在这页名下；但**底层写盘逻辑**复用
      `base.save_avatar()` / `base.drop_avatar()` —— 那是安全逻辑，绝不能出现两个副本
      （`/settings/avatar` 老路由照旧可用，设置页没被拆掉）。
    ⚠ 「名字」改的是 `web/users.json` 里的 **display_name**（网页上显示的那个）。
      「他怎么称呼你」是**他记住的**那一份，在主页「关于你」卡里改 —— 两回事，别混。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    name = ((_load_users().get(uid) or {}).get("display_name") or "").strip()

    # 没头像时那个圆片里放什么字：显示名 ⇒ 他记住的称呼 ⇒ 「你」
    #   ⚠ 别直接 `name or "?"` —— 显示名留空时摆一个问号很难看（2026-09-30 截图里就是）。
    _fallback = name or (UserProfile(uid).data.get("name") or "").strip() or "你"

    # ⭐ 2026-09-30 她定的：**上传口就藏在头像里** —— 点一下头像 = 选图，
    #   选完 `onchange` 自动提交（原来是「选择文件」输入框 + 一个「上传头像」大按钮，
    #   她划掉了：那一坨太占地方）。旁边那行灰字就是提示，所以文案改成动词。
    _av = _avatar_url(uid)
    if _av:
        av_html = '<img src="%s" alt="">' % _av
        av_state = "点击头像换一张"
        # ⚠ 别用 `<p>` 包 `<form>` —— `p` 遇块级元素会被浏览器提前闭合，包不住（用 div）。
        av_remove = ('<div style="margin-top:14px">'
                     '<form method="post" action="/home/edit/profile/avatar/remove">'
                     '<button type="submit" class="ghost">移除头像</button></form></div>')
    else:
        av_html = _esc(_fallback[:2])
        av_state = "点击头像进行更换"
        av_remove = ""

    # ⚠ `ok` / `err` 都是 URL 来的 ⇒ 一律**短码 + 转义**，不给反射型 XSS 留口子。
    _OK = {"name": "名字改好了", "avatar": "头像换好了", "avatar_off": "头像已移除"}
    if err:
        msg = '<p class="err">%s</p>' % _esc(err)
    elif ok:
        msg = '<p class="hint" style="color:#2B7A4B">%s</p>' % _esc(_OK.get(ok, ok))
    else:
        msg = ""

    # 🖼 裁剪面板：**默认藏着**，选完图由 JS 展开（`CROP_JS`）。
    #    ⚠ 里面那个 `<img>` **不带 src 属性** —— 空 src 会让浏览器去请求当前页面自己。
    #    ⚠ 「用原图」是给**动图**留的出口：canvas 导出来是静图，gif 一裁就不动了。
    _crop = """
      <div class="cropbox" id="cropbox" hidden>
        <p style="margin:0 0 10px;font-size:13px;text-align:center">拖动调整位置，双指可缩放</p>
        <div class="cropstage" id="cropstage">
          <img id="cropimg" alt="">
          <div class="cropmask"></div>
        </div>
        <div class="zoomrow">
          <span>缩放</span>
          <input type="range" id="cropzoom" min="100" max="400" value="100">
        </div>
        <div class="cropbtns">
          <button type="button" class="ghost" id="cropcancel">取消</button>
          <button type="button" class="ghost" id="cropraw" title="动图裁完就不动了，想保留就用这个">用原图</button>
          <button type="button" id="cropok">用这张</button>
        </div>
      </div>"""

    body = """
    <div class="card">
      <h1 style="text-align:center">更改</h1>
      %s
      <div style="display:flex;align-items:center;gap:16px;margin:16px 0 4px">
        <form id="avform" method="post" action="/home/edit/profile/avatar"
              enctype="multipart/form-data" style="flex:0 0 auto">
          <label class="bigav" for="avpick" title="点击更换头像">%s</label>
          <input class="avpick" id="avpick" type="file" name="pic"
                 accept="image/png,image/jpeg,image/webp,image/gif"
                 onchange="RAFcrop(this)">
          <noscript><button type="submit" class="ghost">上传</button></noscript>
        </form>
        <div style="min-width:0">
          <p class="muted" style="font-size:12px;margin:0 0 8px">%s</p>
          <p class="hint" style="margin:0">png / jpg / webp / gif，不超过 2 MB</p>
        </div>
      </div>
      %s
      %s
    </div>

    <div class="card">
      <h2>名字</h2>
      <p class="muted" style="font-size:12px;margin:0 0 10px">
        这是网页上显示的名字。留空就用他记住的称呼。</p>
      <form method="post" action="/home/edit/profile">
        <input name="display_name" value="%s" placeholder="留空就用他记住的称呼"
               maxlength="20" style="width:100%%;box-sizing:border-box">
        <button type="submit">保存</button>
      </form>
      <p style="margin:12px 0 0" class="hint">
        他怎么称呼你？那是他记住的事，在主页那张卡里改。</p>
    </div>
    %s
    """ % (msg, av_html, _esc(av_state), _crop, av_remove, _esc(name),
           _two_way_footer("/home", "‹ 回主页"))
    return _page(body, title="更改", css=HOME_CSS,
                 # ⚠ 服务端上限 2 MB，这里打九成 —— 留点余量给表单头
                 script=CROP_JS.replace("__SOFT__", str(int(AVATAR_MAX * 0.9))))


@app.post("/home/edit/profile")
async def home_edit_profile_save(request: Request, display_name: str = Form("")):
    """存名字。⭐ 只能改**自己**那条（uid 来自签名 cookie，不信任前端传的）。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    # 🔐 读-改-写**整段**进事务 —— 管理员后台 / CLI 可能正在同一秒改这张表。
    with _users_txn():
        users = _load_users()
        rec = users.get(uid)
        if not rec:
            return RedirectResponse("/logout")
        rec["display_name"] = (display_name or "").strip()
        users[uid] = rec
        _save_users(users)
    return RedirectResponse("/home/edit/profile?ok=name", status_code=303)


@app.post("/home/edit/profile/avatar")
async def home_edit_profile_avatar(request: Request, pic: UploadFile = File(None)):
    """换头像。校验 / 落盘全在 `base.save_avatar()`（跟设置页是同一份，没有第二份拷贝）。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    raw = b""
    if pic is not None:
        try:
            raw = await pic.read(AVATAR_MAX + 1)
        except Exception:
            raw = b""
    err = save_avatar(uid, raw)
    if err:
        return RedirectResponse("/home/edit/profile?err=" + err, status_code=303)
    return RedirectResponse("/home/edit/profile?ok=avatar", status_code=303)


@app.post("/home/edit/profile/avatar/remove")
async def home_edit_profile_avatar_remove(request: Request):
    """移除头像（普通表单 POST，没有一行 JS）。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    drop_avatar(uid)
    return RedirectResponse("/home/edit/profile?ok=avatar_off", status_code=303)


# ============================================================
#  🏷 第 3 批：她自己增删改那三类标签（喜欢 / 不吃 / 特点）
# ============================================================
# ⚠⚠ **必须排在 `/home/edit/{kind}` 前面** —— 跟 `profile` 同一个坑：
#    `{kind}` 会把 `tag` 抢走（`/home/edit/tag/e` 多一层路径倒是抢不到，
#    但 `/home/edit/tag` 只有一段，晚注册一步就被吃掉）。
# ⭐ 写盘**一律**走 `UserProfile` 那三个 `*_by_her()`（第 3 批新加的），
#    网页端**不自己拼 json** —— 去重 / 抑制名单 / 好感度口径全在引擎里，
#    在页面上重写一遍迟早跟引擎规则漂。
@app.get("/home/edit/tag", response_class=HTMLResponse)
async def home_tag_new(request: Request, err: str = ""):
    """➕ **加一条** —— 她自己往「他记住的你」里添一条。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    msg = '<p class="err">%s</p>' % _esc(err) if err else ""
    body = """
    <div class="card">
      <h1 style="text-align:center">加一条</h1>
      %s
      <form class="tagform" method="post" action="/home/edit/tag">
        <p class="muted" style="font-size:12px;margin:0 0 6px">算在哪一类</p>
        <select name="kind">
          <option value="likes">喜欢</option>
          <option value="dislikes">不吃 / 不喜欢</option>
          <option value="traits">其他特点</option>
        </select>
        <p class="muted" style="font-size:12px;margin:12px 0 6px">内容</p>
        <input name="value" maxlength="30" placeholder="比如：抹茶">
        <button type="submit">加上</button>
      </form>
      <p class="hint" style="margin:12px 0 0">
        你填的会标成「你填的」，跟他自己记住的分开摆 —— 也不算进好感度。</p>
    </div>
    %s
    """ % (msg, _two_way_footer("/home", "‹ 回主页"))
    return _page(body, title="加一条", css=HOME_CSS)


@app.post("/home/edit/tag")
async def home_tag_add(request: Request, kind: str = Form(""), value: str = Form("")):
    """存新加的一条。⭐ `kind` 只认 `_TAG_KINDS` 那三类 —— 别的（name / 未知值）一律送回主页。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if kind not in _TAG_KINDS:
        return RedirectResponse("/home", status_code=303)
    if not UserProfile(uid).add_by_her(kind, (value or "").strip()):
        # 已经有一条了 / 空的 / 太长 —— 都回加一条页，让她自己改
        return RedirectResponse("/home/edit/tag?err=" + quote("这条已经有了，或者填得太长"),
                                status_code=303)
    return RedirectResponse("/home?saved=1", status_code=303)


@app.get("/home/edit/tag/e", response_class=HTMLResponse)
async def home_tag_edit(request: Request, kind: str = "", v: str = "", err: str = ""):
    """
    ✏️ **改一条** —— 点那个 chip 进来的。

    ⚠ `kind` / `v` 都来自 **URL**（她能改）⇒ 进 HTML 前一律 `_esc()`，`kind` 还得在白名单里。
    ⚠ 删除放在**这一页里**（得先点进 chip 才看得到）⇒ 已经是两步，不容易误删。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    v = (v or "").strip()
    if kind not in _TAG_KINDS or not v or len(v) > 30:
        return RedirectResponse("/home", status_code=303)
    msg = '<p class="err">%s</p>' % _esc(err) if err else ""
    body = """
    <div class="card">
      <h1 style="text-align:center">改一条</h1>
      %s
      <p class="muted" style="font-size:12px;margin:0 0 6px">%s</p>
      <form class="tagform" method="post" action="/home/edit/tag/e">
        <input type="hidden" name="kind" value="%s">
        <input type="hidden" name="old" value="%s">
        <input name="value" value="%s" maxlength="30">
        <button type="submit">保存</button>
      </form>
      <div style="margin-top:14px">
        <form method="post" action="/home/edit/tag/del"
              onsubmit="return confirm('删掉就不要了吗？他会忘掉这条。')">
          <input type="hidden" name="kind" value="%s">
          <input type="hidden" name="value" value="%s">
          <button type="submit" class="ghost">删掉这条</button>
        </form>
      </div>
    </div>
    %s
    """ % (msg, _esc(_TAG_LABEL[kind]), _esc(kind), _esc(v), _esc(v),
           _esc(kind), _esc(v), _two_way_footer("/home", "‹ 回主页"))
    return _page(body, title="改一条", css=HOME_CSS)


@app.post("/home/edit/tag/e")
async def home_tag_save(request: Request, kind: str = Form(""), old: str = Form(""),
                        value: str = Form("")):
    """存改动。⭐ 改过的那条会**从自动项转挂到 manual** —— 它已经不是他观察出来的了。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if kind not in _TAG_KINDS:
        return RedirectResponse("/home", status_code=303)
    old = (old or "").strip()
    if not UserProfile(uid).edit_by_her(kind, old, (value or "").strip()):
        return RedirectResponse("/home/edit/tag/e?kind=%s&v=%s&err=%s"
                                % (kind, quote(old), quote("没改成 —— 跟原来一样？"),
                                   ), status_code=303)
    return RedirectResponse("/home?saved=1", status_code=303)


@app.post("/home/edit/tag/del")
async def home_tag_del(request: Request, kind: str = Form(""), value: str = Form("")):
    """删一条。⭐ 走 `delete_by_her()` ⇒ 留痕（抑制名单），自动轨不会转头又学回来。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    if kind in _TAG_KINDS:
        UserProfile(uid).delete_by_her(kind, (value or "").strip())
    return RedirectResponse("/home?saved=1", status_code=303)


# ============================================================
#  📌 第 4 批：她自己增删改「他记住的事」（key_facts）
# ============================================================
# ⚠⚠ 跟 `tag` 那组同一个坑：**必须排在 `/home/edit/{kind}` 前面**。
# ⭐ 写盘**一律**走 `Rafayel_memory` 那三个 `*_fact_by_her()` ——
#    内部是 `load_memory()` → 改 → `save_memory()`，跟 bot 同一套落盘逻辑。
#    ⚠ 网页端**绝不自己 json.dump** `memory/{uid}.json`：那份文件的字段清单归
#      `save_memory()` 管（只写固定的那些），自己拼会漏字段或写出多余的键。
@app.get("/home/edit/fact", response_class=HTMLResponse)
async def home_fact_new(request: Request, err: str = ""):
    """➕ **加一件** —— 她自己往「他记住的事」里添一条。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    msg = '<p class="err">%s</p>' % _esc(err) if err else ""
    body = """
    <div class="card">
      <h1 style="text-align:center">加一件事</h1>
      %s
      <form class="tagform" method="post" action="/home/edit/fact">
        <textarea name="value" rows="4" maxlength="80"
                  placeholder="比如：她对海鲜过敏"></textarea>
        <button type="submit">记住</button>
      </form>
      <p class="hint" style="margin:12px 0 0">
        一条最多 80 字，最多存 20 条，满了就丢最早的一条。这些他都会当成自己记得的事。</p>
    </div>
    %s
    """ % (msg, _two_way_footer("/home", "‹ 回主页"))
    return _page(body, title="加一件事", css=HOME_CSS)


@app.post("/home/edit/fact")
async def home_fact_add(request: Request, value: str = Form("")):
    """存新加的一条。⭐ 空 / 超 80 / 已经有 ⇒ 回加一件页报一句，不静默失败。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    # ⭐ `_one_line()`：文本框里按的回车会变成 `\n` ⇒ 存之前压成空格（一条 = 一句话）
    if not add_fact_by_her(uid, _one_line(value)):
        return RedirectResponse("/home/edit/fact?err=" + quote("这条已经有了，或者填得太长"),
                                status_code=303)
    return RedirectResponse("/home?saved=1", status_code=303)


@app.get("/home/edit/fact/e", response_class=HTMLResponse)
async def home_fact_edit(request: Request, v: str = "", err: str = ""):
    """
    ✏️ **改一件** —— 点那一行进来的。

    ⚠ `v` 来自 **URL**（她能改）⇒ 进 HTML 前一律 `_esc()`，长度卡 80。
    ⚠ 删除放在**这一页里**（得先点进那一行）⇒ 已经是两步，不容易误删。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    v = (v or "").strip()
    if not v or len(v) > 80:
        return RedirectResponse("/home", status_code=303)
    msg = '<p class="err">%s</p>' % _esc(err) if err else ""
    body = """
    <div class="card">
      <h1 style="text-align:center">改一件事</h1>
      %s
      <form class="tagform" method="post" action="/home/edit/fact/e">
        <input type="hidden" name="old" value="%s">
        <textarea name="value" rows="4" maxlength="80">%s</textarea>
        <button type="submit">保存</button>
      </form>
      <div style="margin-top:14px">
        <form method="post" action="/home/edit/fact/del"
              onsubmit="return confirm('删掉就不要了吗？他会忘掉这件事。')">
          <input type="hidden" name="value" value="%s">
          <button type="submit" class="ghost">删掉这件</button>
        </form>
      </div>
    </div>
    %s
    """ % (msg, _esc(v), _esc(v), _esc(v), _two_way_footer("/home", "‹ 回主页"))
    return _page(body, title="改一件事", css=HOME_CSS)


@app.post("/home/edit/fact/e")
async def home_fact_save(request: Request, old: str = Form(""), value: str = Form("")):
    """存改动。"""
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    old = (old or "").strip()
    if not edit_fact_by_her(uid, old, _one_line(value)):
        return RedirectResponse("/home/edit/fact/e?v=%s&err=%s"
                                % (quote(old), quote("没改成 —— 跟原来一样？"),
                                   ), status_code=303)
    return RedirectResponse("/home?saved=1", status_code=303)


@app.post("/home/edit/fact/del")
async def home_fact_del(request: Request, value: str = Form("")):
    """
    删一件。

    ⭐ 跟第 3 批删标签**不一样**：这里**不留抑制名单** ——
      key_facts 只有两种来源（她说「记住：xxx」、他说「我保证：xxx」），
      删掉之后要再生成得**重新发生一次那样的发言**，那是新事件，不该被拦。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    delete_fact_by_her(uid, (value or "").strip())
    return RedirectResponse("/home?saved=1", status_code=303)


@app.get("/home/edit/{kind}", response_class=HTMLResponse)
async def home_edit(request: Request, kind: str, err: str = ""):
    """
    🌐 设置某一项（第 2 批：name / birthday；2026-09-30 追加 met_day）。
    **纯表单，离了 JS 也能用。**

    ⚠ `kind` 只认 `_EDITABLE` / `_EDITABLE_USERJSON` 里的键 ——
      别的（likes/dislikes/traits/未知值）一律送回主页，不给他「顺手改别的字段」的口子。

    ⭐ 两张表**落盘位置不同**，所以这里要分开取当前值：
      · `_EDITABLE`        ⇒ `memory/{uid}_profile.json`（`UserProfile` 那份）
      · `_EDITABLE_USERJSON` ⇒ `web/users.json`（跟 `/settings` 同一格）
    取错了就会「显示空白、保存却写进另一份」，两份值从此打架。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    spec = _EDITABLE.get(kind) or _EDITABLE_USERJSON.get(kind)
    if not spec:
        return RedirectResponse("/home")
    title, note, ph = spec
    is_userjson = kind in _EDITABLE_USERJSON

    if is_userjson:
        cur = str((_load_users().get(uid) or {}).get(kind) or "").strip()
        # 📅 相遇日：用**原生日期控件**（带年月日选择器）。她 2026-09-30 的原话是
        #    「和生日类似，不同的是它带年月日」——生日那边是手打 `03-06`（不带年），
        #    所以两条不能共用一个输入框类型。
        #    ⚠ `type=date` 的值格式**就是** `YYYY-MM-DD`，跟存储格式天然一致，不用转换。
        #    ⚠ 值必须 `_esc()` 后再进属性（虽有服务端校验兜底，但历史脏数据也可能存在）。
        control = ('<input type="date" name="value" value="%s" '
                   'style="width:100%%;box-sizing:border-box">' % _esc(cur))
    else:
        cur = (UserProfile(uid).data.get(kind) or "").strip()
        # ⭐ 占位提示换成「他现在怎么叫你」（见 `_PH_FROM_CUR` 的说明）：
        #    框里**有值**时 placeholder 根本看不见 ⇒ 这条只在「她擦掉之后」显形，
        #    正好是她要的那句提示。空着则不动，继续用表里的举例。
        if kind in _PH_FROM_CUR and cur:
            ph = cur
        control = ('<input name="value" value="%s" placeholder="%s" maxlength="20" '
                   'style="width:100%%;box-sizing:border-box">'
                   % (_esc(cur), _esc(ph)))

    msg = '<p class="err">%s</p>' % _esc(err) if err else ""
    # 🗑 「清除」对相遇日是有意义的：删掉之后就回到「还没填 / 认识 —」。
    #    ⚠ 名字 / 生日没有这句 ⇒ **整段不渲染**（原来拼的是一个空 `<p>`，
    #      会卡在卡片最底部留一段 10px 的空白，那个位置现在归底栏了）。
    hint = ('<p class="hint" style="margin:10px 0 0">'
            '清除相遇日之后，「认识」会回到空白，直到你重新填上。</p>'
            if is_userjson else "")
    body = """
    <div class="card">
      <h2>%s</h2>
      <p class="muted" style="font-size:12px;margin:0 0 10px">%s</p>
      %s
      <form method="post">
        %s
        <button type="submit" name="act" value="save">保存</button>
        <button type="submit" name="act" value="clear" class="ghost">清除</button>
      </form>
      %s
    </div>
    %s
    """ % (_esc(title), _esc(note), msg, control, hint,
           # ⭐ 2026-09-30 她按截图定的：卡片里那个「‹ 取消」**去掉**，
           #   统一走底部栏 —— 跟「更改」页（`/home/edit/profile`）同款
           #   `‹ 回主页 · ‹ 返回目录`。
           #   ⚠ 为什么该去：那个「取消」的去向就是 `/home`，跟「回主页」**重复**，
           #     挂在卡片里还多占一行、多一个误按点。
           #   ⚠ 底部栏**不能**用 `_backbar()`：那个只渲染一个动作，而且带 `backonly`
           #     类 —— 桌面版按它把整条藏掉（底座里写死的规矩）。
           _two_way_footer("/home", "‹ 回主页"))
    return _page(body)


@app.post("/home/edit/{kind}")
async def home_edit_save(request: Request, kind: str,
                         value: str = Form(""), act: str = Form("save")):
    """
    🌐 落盘。**两条路，各写各的那份文件**：

    · `_EDITABLE`          ⇒ **只调 `UserProfile.set_by_her()`**（校验 / 归一化 /
      抑制名单全在引擎那边，网页端不自己拼 json，拼了就会跟引擎那套规则漂）。
    · `_EDITABLE_USERJSON` ⇒ 写 `web/users.json`（**不是** bot 的 memory）。
      ⚠ 先过 `base._check_met_day()` —— 跟 `/settings` **同一个**校验函数，
        不然会出现「设置页拒的日子、主页收下了」。
    """
    uid = _current_uid(request)
    if not uid:
        return RedirectResponse("/")
    val = "" if act == "clear" else (value or "").strip()

    if kind in _EDITABLE_USERJSON:
        err = _check_met_day(val) if val else ""
        if err:
            return RedirectResponse("/home/edit/%s?err=%s" % (kind, quote(err)),
                                    status_code=303)
        with _users_txn():
            users = _load_users()
            rec = users.get(uid) or {}
            changed = str(rec.get(kind) or "") != val
            if changed:
                rec[kind] = val
                users[uid] = rec
                _save_users(users)      # 已是「写 .tmp + os.replace」原子落盘
        return RedirectResponse("/home?saved=1" if changed else "/home", status_code=303)

    changed = False
    if kind in _EDITABLE:
        changed = UserProfile(uid).set_by_her(kind, val)
    # ⚠⭐ `status_code=303` **不能省**：`RedirectResponse` 默认是 **307**，
    #   而 307 要求浏览器**原样用 POST 再请求一次目标地址** ⇒ 会 POST 到 `/home` 直接 405。
    #   （303 = 「换成 GET 去那个地址」，这才是表单提交后该用的那一档。同一批的
    #     `/home/edit/profile*` 三个也全是 303。）
    # ⚠ 只有**真改了**才挂那句「改好了」—— 原样保存 / kind 不认识时不该骗她
    return RedirectResponse("/home?saved=1" if changed else "/home", status_code=303)


# 📌 后面两批要动的地方（先记在这儿，免得将来找不到）：
#   · 第 3 批「他记住的你 可增删改」  ⇒ 删走 `UserProfile.suppress()`、手写走 `manual`
#   · 第 4 批「他记住的事 可增删改」  ⇒ 写 `{uid}.json` 的 key_facts，届时还要
#     在 `tools/check_static.py` 的 `WEB_WRITE_EXCEPTION` 里给本页再加 `Rafayel_memory`。
#   ⚠⚠ 跨进程风险（老规矩，见工作区 MEMORY）：bot 与 web 两个进程都写 `memory/`，
#      文件锁跨不了进程 ⇒ 冻结期（只有 web 在跑）没事；bot 一恢复，它在内存里那份
#      会把 web 的改动盖回去 —— 那时得按那条「web 转发给 bot 进程」来改。
