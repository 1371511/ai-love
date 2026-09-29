# -*- coding: utf-8 -*-
"""
祁煜（Rafayel）的**用户画像**（2026-09-17 从 `Rafayel_chat.py` 拆出）。

画像 = 他「自己留意到」的关于她的事，与 `key_facts`（她明确让他记住的事）分工不同。
本模块只管画像本身：规则提取 / 落盘 / 渲染 / 合并 —— 不碰网络、不碰对话管理。

⚠ 本文件里全是踩过 P0 的正则，改动前先读注释，别凭直觉简化。
   · 三道闸（语气助词 / 就近否定 / 代词）任意一道拆掉，都会让「他给祁煜起名」
     被记成「她让祁煜这样叫她」，人设当场被带偏。
   · 归一化 `_norm` **只用于比较**，落盘永远存她的原话。
"""

import json
import os
import re
import time

from Rafayel_config import MEMORY_DIR, MAX_PROFILE_ITEMS


# ============================================================
#  👤 用户画像：从多轮对话里慢慢积累（独立存放）
# ============================================================
# 2026-09-15 改（小辞裁定）：
#   ① 删掉开局的五问表单（名字/爱好/食物/技能/补充）——
#      称呼本就该由用户在对话里引导（卡的称呼三层：用户引导 > 保镖小姐 > 猎人小姐），
#      开局填表既打断沉浸，也和「引导层开放集合」的设定重复。
#   ② 改成两条腿：规则抓显式表述（实时、零成本）+ 每 8 轮 LLM 总结补隐含信息。
#   ③ 独立存到 memory/{user_id}_profile.json，不再混进对话记忆文件。

PROFILE_RULES = [
    ("name",     r"(?:以后叫我|你可以叫我|叫我|我叫|我的名字是)\s*([^\s，,。.！!？?、]{1,8})"),
    ("likes",    r"我(?:最喜欢|最爱|超喜欢|特别喜欢|很喜欢|喜欢|爱)\s*([^\s，,。.！!？?、]{1,16})"),
    ("dislikes", r"我(?:最讨厌|讨厌|不喜欢|不爱|不吃|受不了)\s*([^\s，,。.！!？?、]{1,16})"),
]

PROFILE_TEMPLATE = """## 关于她（你在相处中慢慢留意到的）
{lines}
（这些是你从对话里记下的。没把握的不要用，她没提过的事不要替她编。）"""

# 称呼末尾的语气助词。
# 2026-09-15 实测：「叫我小辞吧」会被记成「小辞吧」——中文助词不在标点排除集里，
# 正则会把它一起吞进去。只剥**语气助词**，不剥「的/了」这类可能是名字一部分的字。
# 2026-09-17 补：允许助词前带一个「了/的」再一起剥——
#   「保镖小姐了好吗」里「了」不在助词表，从「了」断开就只剥掉「吗」，剩「保镖小姐了好」。
NAME_PARTICLE_RE = re.compile(
    r"(?:了|的)?(?:吧|啊|呢|呀|哦|啦|嘛|咯|呗|鸭|呐|喽|吗|哈|嘿)+$")

# ⭐ 就近闸门（2026-09-17 实测新增）：看触发词「叫我」**前面紧邻的 4 个字**。
# 前两道闸只看「捕获出来的那截」，看不到整句在说什么，于是这些句子全被误收：
#   别叫我保镖小姐了好吗   → 记成「保镖小姐了好」（她其实在拒绝）
#   以后别叫我保镖小姐     → 记成「保镖小姐」
#   谁说我叫保镖小姐的     → 记成「保镖小姐的」
#   他叫我经理人           → 记成「经理人」（那是唐知理的称呼）
# ⚠ 必须「就近」判，不能整句判否定：
#   「我不叫保镖小姐，叫我小辞」整句含否定，但后半句是真引导，整句丢会误伤。
NAME_NEG_NEAR_RE = re.compile(
    r"(别|不要|不许|不用|甭|不再|不叫|没有叫|没叫|谁说|谁讲|他|她|它|别人|人家)")
NAME_CTX_LEN = 4

# 称呼里不该出现代词。
# 2026-09-15 实测（更危险的那种）：用户说「我叫你小鱼好不好」是在给**祁煜**起名字，
# 旧正则会把「你小鱼好不好」整段当成「她让你这样叫她」写进画像，人设直接被带偏。
# 中文称呼不含代词，命中就整条丢弃（宁可漏记一条，也不能记错成「她叫这个」）。
NAME_PRONOUN_RE = re.compile(r"[你我他她它咱您]")

# ---- 🎂 生日（2026-09-19 新增）----
# 为什么要单独一个字段：朋友圈里有 3 篇「她的生日」语料，只能在**她生日当天**发。
#   放进普通随机池 ⇒ 会在 7 月某天冒出一句「生日快乐」，她当场就知道是错的。
#   ⇒ 生日必须是**画像里的一个字段**（双轨抓），由发圈模块按日期触发。
# 正则只认「**她的**生日」：主语必须是「我」，不含「我」的一律不收
#   （「祁煜生日是 3 月 6 号」是她告诉我**他**的生日，记反了就给他自己发错日子）。
BDAY_PATTERNS = [
    # ①「我生日（是）3月6号」—— 允许带年份（「2026年3月6号」）
    r"我(?:的)?生日(?:是|在|为)?\s*[:：]?\s*(?:\d{4}\s*年)?\s*(\d{1,2})\s*[月.\-/]\s*(\d{1,2})\s*[日号]?",
    # ②「我3月6号生日」/「我是3月6号的生日」—— `是|在|过` **必须可选**，
    #    写死的话「我3月6号生日」这种最常见说法会整条漏掉。
    r"我(?:的)?(?:是|在|过)?\s*(\d{1,2})\s*[月.\-/]\s*(\d{1,2})\s*[日号]?\s*(?:的)?生日",
    # ③「3月6号是我生日」
    r"(\d{1,2})\s*[月.\-/]\s*(\d{1,2})\s*[日号]?\s*(?:是|为)?\s*我(?:的)?生日",
]
# ⚠ **没有**「就近否定」闸（跟 name 那套不同，别照抄）：
#    这里靠**正则结构**挡否定 —— `(?:是|在|为)?` 紧贴「生日」后面，
#    「我生日不是3月6号」的「不」既不在可选组里、又挡着后面的数字 ⇒ 直接匹配不上。
#    曾经照抄 name 的做法加了就近否定闸，结果「其他的先不说，我生日是3月6号」
#    被前 4 字的「先不说」误杀 —— 那句是**肯定**句。宁可不收，也不能收反，
#    但这次是收反的风险为零、误杀的风险很大 ⇒ 不设这道闸。
BDAY_OTHER_RE = re.compile(r"(祁煜|他|她|它|别人|人家)")


def _bday_canon(value: str) -> str:
    """
    🎂 「3月6号」/「3/6」/「03-06」 ⇒ 一律收成 `MM-DD`；认不出来返回 ""。

    ⭐ 2026-09-30 加（主页第 2 批「清除生日」逼出来的坑）：
      抑制名单里存的是**存储格式**（`03-06`），而两条自动轨喂进来的永远是**原话**
      （「我生日是3月6号」里抓出来的是 `3月6号`）—— 拿 `_norm()` 比字面，这两样
      八辈子对不上 ⇒ 她刚点掉的生日，下一句就自己长回来了，等于「清除」白点。
      ⇒ 生日这一档**必须先规范到 MM-DD 再比**。
    """
    m = _BDAY_VALUE_RE.search(value or "")
    if not m:
        return ""
    return _parse_birthday(m.group(1), m.group(2))


def _parse_birthday(month, day):
    """
    (月, 日) ⇒ 规范成 `MM-DD`；不合法返回 ""。

    ⚠ 只认**公历**：农历要转公历得查表，跨年还会变，宁可不收也别记错。
    ⚠ 月日都要校验范围（`2 月 30 号` 这种输入不收）。
    """
    try:
        m, d = int(month), int(day)
    except (TypeError, ValueError):
        return ""
    if not (1 <= m <= 12) or not (1 <= d <= 31):
        return ""
    return "%02d-%02d" % (m, d)


# 落盘前最后一道：把「3月6号」/「3/6」/「03-06」统统收成 `MM-DD`。
# ⚠ 不含「年」—— 「2026年3月6号」这种输入交给上面的正则去抓，这里只认月日。
_BDAY_VALUE_RE = re.compile(r"(\d{1,2})\s*[月.\-/]\s*(\d{1,2})\s*[日号]?")


# 归一化：只用于**比较**，绝不改存储值（存进去的永远是她的原话）。
#   规则轨（实时、每句）→ 用原文严格比：保守，宁可多记一条也不误并；
#   LLM 轨（每 8 轮总结）→ 用归一化比：「吃甜的」与「甜食」归一后同源，不再重复记。
# 之所以分两档：语义级去重（甜食 / 吃甜的）不是字面关系，规则层硬做会把
# 「喜欢猫」「喜欢熊猫」误并成一条 —— 那种静默丢信息比多记一条更糟。
_PROF_HEAD_RE = re.compile(r"^(?:吃|喝|看|听|玩|买|做|逛|养|学|抽|穿)+")
_PROF_TAIL_RE = re.compile(r"(?:的|东西|什么|啥|一类|之类的|这种|那种)+$")


def _norm(s: str) -> str:
    """归一化（仅比较用）。头尾都剥空时退回原文，避免整条被剥成空串。"""
    s = (s or "").strip()
    head = _PROF_HEAD_RE.sub("", s)
    if head:
        s = head
    tail = _PROF_TAIL_RE.sub("", s)
    if tail:
        s = tail
    return s


class UserProfile:
    """
    用户画像。只存「关于她」的事，独立落盘。

    与 key_facts 的区别：
      key_facts = 她**明确让你记住**的事（「记住：…」）
      本类     = 你**自己观察留意**到的她的喜好与习惯
    """

    # ⚠ `birthday` 是**单值**（字符串），不是 list —— 落盘/读取必须走另一条路，
    #   跟 `KINDS` 里其余三类（数组）分开。混着按 `list(...)` 处理会把 "03-06" 拆成字符。
    KINDS = ("name", "likes", "dislikes", "traits", "birthday")
    SCALAR_KINDS = ("name", "birthday")

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.path = os.path.join(MEMORY_DIR, f"{user_id}_profile.json")
        self.data = {
            "user_id": user_id,
            "name": None,
            "likes": [],
            "dislikes": [],
            "traits": [],
            "birthday": None,
            "updated_at": None,
            # 👇 2026-09-30 主页「可改可删」配套；三个都是新增字段 ⇒ 现有数据零迁移
            "suppressed": {},   # {kind: [原话...]} 她删掉的 —— 两条轨都不许再写回来
            "manual": {},       # {kind: 值 或 [原话...]} 她手写的 —— 只进 prompt，不计好感度
            "prof_seen": 0,     # 自动项条数的历史峰值（只涨不跌）⇒ 删一条不掉好感度
        }
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for k in self.KINDS:
                if k in self.SCALAR_KINDS:
                    self.data[k] = saved.get(k)
                else:
                    self.data[k] = list(saved.get(k) or [])
            self.data["updated_at"] = saved.get("updated_at")
            self.data["suppressed"] = dict(saved.get("suppressed") or {})
            self.data["manual"] = dict(saved.get("manual") or {})
            # 老文件没有 prof_seen ⇒ 用「现在的条数」补齐（一次性迁移）
            self.data["prof_seen"] = int(saved.get("prof_seen") or 0) or self._auto_n()
        except Exception as e:
            print(f"⚠️ 读取用户画像失败（{self.user_id}）：{e}，将从空画像开始")

    def save(self):
        try:
            os.makedirs(MEMORY_DIR, exist_ok=True)
            self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            print(f"⚠️ 保存用户画像失败（{self.user_id}）：{e}")

    def _add_one(self, kind: str, value: str, loose: bool = False) -> bool:
        """
        追加一条（去重 + 上限）。返回是否真新增。

        loose=False（规则轨，默认）：原文严格比子串。
        loose=True （LLM 轨）       ：先归一化再比，能收「吃甜的」/「甜食」这类语义重复。
        """
        value = (value or "").strip()
        # 她删过的东西不许再长回来（比归一化后的值 —— 双轨写进来的都是原话）
        if self._is_suppressed(kind, value):
            return False
        if kind == "birthday":
            return self._set_birthday(value, loose=loose)
        if kind == "name":
            value = NAME_PARTICLE_RE.sub("", value).strip()
            if NAME_PRONOUN_RE.search(value):
                return False
        if not value or len(value) > 30:
            return False
        if kind == "name":
            if self.data["name"] == value:
                return False
            self.data["name"] = value          # 称呼只留最新一次引导
            return True
        bucket = self.data.setdefault(kind, [])
        cand = _norm(value) if loose else value
        for old in bucket:
            ref = _norm(old) if loose else old
            if not ref:
                continue
            # 去重：完全相同、或互为子串（严格轨：「甜的」与「吃甜的」视为一条）
            if ref == cand or ref in cand or cand in ref:
                return False
        bucket.append(value)                   # 存原话，不存归一化结果
        if len(bucket) > MAX_PROFILE_ITEMS:
            self.data[kind] = bucket[-MAX_PROFILE_ITEMS:]
        self._bump_seen()                      # 峰值只涨不跌 ⇒ 她删一条好感度不掉
        return True

    def _is_suppressed(self, kind: str, value: str) -> bool:
        """她删过的东西 ⇒ 两条轨（规则实时 / LLM 每 8 轮）都不许再写回来。"""
        bucket = (self.data.get("suppressed") or {}).get(kind) or []
        # 🎂 生日比**规范后的 MM-DD**（原话 vs 存储格式对不上，见 `_bday_canon`）
        if kind == "birthday":
            cand = _bday_canon(value)
            return bool(cand) and any(_bday_canon(old) == cand for old in bucket)
        cand = _norm(value)
        return any(_norm(old) == cand for old in bucket if _norm(old))

    def _auto_n(self) -> int:
        """自动抽取到的条数（**不含** manual） —— 好感度计的就是这个。"""
        return sum(len(self.data.get(k) or []) for k in ("likes", "dislikes", "traits"))

    def _bump_seen(self) -> None:
        """历史峰值只涨不跌 ⇒ 她删一条，好感度不掉（2026-09-30 她定）。"""
        n = self._auto_n()
        if n > int(self.data.get("prof_seen") or 0):
            self.data["prof_seen"] = n

    def suppress(self, kind: str, value: str) -> bool:
        """
        删一条（她在主页点「删」）。返回是否有变化。

        ⭐ 不只是从列表里拿掉 —— 必须**留痕**，否则规则轨 / LLM 轨下一轮就写回来
        （LLM 轨还是宽松去重，模型换个说法直接算新的一条）。
        ⚠ 好感度**不掉**（`prof_seen` 只涨不跌；计分方见 `Rafayel_affinity.compute`）。
        """
        if kind not in self.KINDS:
            return False
        changed = False
        if kind in self.SCALAR_KINDS:                     # name / birthday 是单值
            if self.data.get(kind):
                self.data[kind] = None
                changed = True
        else:
            bucket = self.data.get(kind) or []
            rest = [v for v in bucket if _norm(v) != _norm(value)]
            if len(rest) != len(bucket):
                self.data[kind] = rest
                changed = True
        sup = self.data.setdefault("suppressed", {})
        lst = sup.setdefault(kind, [])
        nv = _norm(value)
        if nv and not any(_norm(x) == nv for x in lst):
            lst.append(value)                             # 存原话，比对时再归一化
            changed = True
        return changed

    def unsuppress(self, kind: str, value: str) -> bool:
        """恢复 —— 把一条从抑制名单里放出来，允许自动轨重新学到。"""
        sup = self.data.get("suppressed") or {}
        lst = sup.get(kind) or []
        # 🎂 同一条规矩：生日按 MM-DD 比（否则她用「3月6号」设回来时，名单里那条清不掉）
        if kind == "birthday":
            cand = _bday_canon(value)
            rest = [x for x in lst if not (cand and _bday_canon(x) == cand)]
        else:
            nv = _norm(value)
            rest = [x for x in lst if _norm(x) != nv]
        if len(rest) == len(lst):
            return False
        sup[kind] = rest
        return True


    def _set_birthday(self, value: str, loose: bool = False) -> bool:
        """
        写入生日（规范成 `MM-DD`）。返回是否真变化。

        ⚠ 覆盖策略（两轨不同）：
          · 规则轨（她自己明说，loose=False）⇒ **允许改** —— 她说「我生日是 5 月 1 号」就该生效。
          · LLM 轨（每 8 轮总结，loose=True）⇒ **只在原来没有时写**，绝不覆盖。
            模型每次总结都可能把「她 3 月 6 号生日」换个说法再输出一次，
            让它能覆盖 ⇒ 哪次抽风写错就把真生日冲掉了，而且是静默的。
        """
        m = _BDAY_VALUE_RE.search(value or "")
        if not m:
            return False
        mmdd = _parse_birthday(m.group(1), m.group(2))
        if not mmdd:
            return False
        old = (self.data.get("birthday") or "").strip()
        if old == mmdd:
            return False
        if old and loose:
            return False
        self.data["birthday"] = mmdd
        return True

    def set_by_her(self, kind: str, value: str) -> bool:
        """
        🌐 她**自己在网页上设的**（2026-09-30 主页第 2 批）。返回是否有变化。

        ⭐ 跟两条自动轨的区别：自动轨怕记错，所以堆了一堆闸门（就近否定 / 别人主语 /
          宽松去重 / LLM 轨不覆盖生日）；**她自己填的就是标准答案**，所以这里
            · **一律允许覆盖** —— 她说改就改（生日走 `_set_birthday(loose=False)`，本来就允许改）
            · **先 `unsuppress` 再写** —— 否则她删过一次、又想设回来时会被 `_is_suppressed`
              挡住；那道闸是防自动轨的，**不该防她本人**。
        ⚠ `value` 为空 = 她想**清掉** ⇒ 走 `suppress()`（留痕），自动轨才不会转头又写回来。
        ⚠ 只收 `name` / `birthday` 这两个**单值**项（`SCALAR_KINDS`）；
          三类数组项（likes/dislikes/traits）走下面那三个 `*_by_her`（要区分「自动 / 手写」）。
        """
        if kind not in self.KINDS:
            return False
        value = (value or "").strip()
        if not value:
            # ⚠ `suppress()` 只改内存、**不落盘**（它的调用方各自决定何时写）
            #   ⇒ 这里必须自己 save，否则「清除」在页面上看着生效了，一刷新又回来了。
            changed = self.suppress(kind, self.data.get(kind) or "")
            if changed:
                self.save()
            return changed
        if kind not in self.SCALAR_KINDS:
            return False
        self.unsuppress(kind, value)
        changed = False
        if kind == "birthday":
            changed = self._set_birthday(value)     # loose=False ⇒ 允许改（她自己设的）
        else:                                       # name
            v = NAME_PARTICLE_RE.sub("", value).strip()
            # ⚠ 代词/语气助词那两道闸**照样拦**（她填「你」「我」这种，填了也白填）
            if v and not NAME_PRONOUN_RE.search(v) and len(v) <= 30 \
                    and self.data.get("name") != v:
                self.data["name"] = v
                changed = True
        if changed:
            self.save()
        return changed

    def add_by_her(self, kind: str, value: str) -> bool:
        """
        🌐 她**自己在网页上加一条**（2026-09-30 主页第 3 批）。返回是否有变化。

        ⭐ 写进 `manual[kind]`，**不写**自动项（`likes` / `dislikes` / `traits`）：
          那三个是「他**自己观察**出来的」，好感度照它们算（`_auto_n()`）；
          她手写的混进去 ⇒ 好感度能自己刷上去，系统就假了。
        ⭐ 先 `unsuppress`：她删过一次又想加回来时，不该被那道闸挡住 ——
          那道闸是防**自动轨**的，**不该防她本人**（跟 `set_by_her` 同一个道理）。
        ⚠ 自动项里已经有了 ⇒ 不重复写（界面上已经摆着一条了，再写一条是同义重复）。
        """
        if kind not in self.KINDS or kind in self.SCALAR_KINDS:
            return False
        value = (value or "").strip()
        if not value or len(value) > 30:
            return False
        if any(_norm(x) == _norm(value) for x in (self.data.get(kind) or [])):
            return False                       # 他已经记过了，界面上有
        self.unsuppress(kind, value)
        man = self.data.setdefault("manual", {}).setdefault(kind, [])
        if any(_norm(x) == _norm(value) for x in man):
            return False
        man.append(value)
        if len(man) > MAX_PROFILE_ITEMS:
            self.data["manual"][kind] = man[-MAX_PROFILE_ITEMS:]
        self.save()
        return True

    def edit_by_her(self, kind: str, old: str, new: str) -> bool:
        """
        🌐 她**自己在网页上改一条**（2026-09-30 主页第 3 批）。返回是否有变化。

        ⭐ 改过的那条**已经不是他观察出来的了**，是她定的 ⇒
          · 旧值在**自动项** ⇒ `suppress()` 撤下（留痕，防自动轨写回）+ 新值写进 `manual`
          · 旧值本来就在 **`manual`** ⇒ 就地替换
        ⚠ 一律 `save()`（`suppress()` 只改内存，第 2 批踩过的 P0）。
        """
        if kind not in self.KINDS or kind in self.SCALAR_KINDS:
            return False
        old = (old or "").strip()
        new = (new or "").strip()
        if not old or not new or len(new) > 30 or _norm(old) == _norm(new):
            return False
        man = self.data.setdefault("manual", {}).setdefault(kind, [])
        in_man = any(_norm(x) == _norm(old) for x in man)
        in_auto = any(_norm(x) == _norm(old) for x in (self.data.get(kind) or []))
        if not in_man and not in_auto:
            return False                       # 这条根本不存在，改不了
        changed = False
        if in_man:
            self.data["manual"][kind] = [new if _norm(x) == _norm(old) else x
                                         for x in man]
            changed = True
        else:
            if self.suppress(kind, old):        # 从自动项撤下 + 留痕
                changed = True
            self.unsuppress(kind, new)          # 新值不许被旧值的抑制名单误伤
            man = self.data.setdefault("manual", {}).setdefault(kind, [])
            if not any(_norm(x) == _norm(new) for x in man):
                man.append(new)
                changed = True
        if changed:
            self.save()
        return changed

    def delete_by_her(self, kind: str, value: str) -> bool:
        """
        🌐 她**自己在网页上删一条**（2026-09-30 主页第 3 批）。返回是否有变化。

        ⭐ 跟直接调 `suppress()` 的区别：`suppress()` 只管**自动项**，
          可她要删的也可能是**她自己填的**（在 `manual` 里）—— 那条同样要留痕，
          否则她删了自己填的「甜的」，转头规则轨从对话里又学回一个「甜的」，白删。
        ⚠ **必须 `save()`** —— `suppress()` 只改内存（第 2 批踩过的 P0）。
        ⚠ 好感度**不掉**（`prof_seen` 只涨不跌）。
        """
        if kind not in self.KINDS or kind in self.SCALAR_KINDS:
            return False
        value = (value or "").strip()
        if not value:
            return False
        changed = self.suppress(kind, value)     # 自动项（有就撤）+ 留痕
        man = self.data.setdefault("manual", {}).setdefault(kind, [])
        rest = [v for v in man if _norm(v) != _norm(value)]
        if len(rest) != len(man):
            self.data["manual"][kind] = rest
            changed = True
        if changed:
            self.save()
        return changed

    def merge(self, patch: dict) -> bool:
        """合并一组提取结果（规则命中 或 LLM 总结）。返回是否有变化。"""
        changed = False
        if not isinstance(patch, dict):
            return False
        name = patch.get("name")
        if isinstance(name, str) and name.strip():
            if self._add_one("name", name):
                changed = True
        # 🎂 生日：LLM 轨（loose=True）⇒ 只在原来没有时写，不覆盖（见 _set_birthday）
        bday = patch.get("birthday")
        if isinstance(bday, str) and bday.strip():
            if self._add_one("birthday", bday, loose=True):
                changed = True
        for kind in ("likes", "dislikes", "traits"):
            items = patch.get(kind) or []
            if isinstance(items, str):
                items = [items]
            for it in items:
                # LLM 轨走宽松去重：模型每次总结都会把「她喜欢甜的」换个说法再输出一次，
                # 严格比会一条条堆起来。这里允许归一化后判重。
                if isinstance(it, str) and self._add_one(kind, it, loose=True):
                    changed = True
        return changed

    def extract_from_text(self, text: str) -> bool:
        """用规则从一条**用户**消息里抓显式表述。返回是否有变化。"""
        changed = False
        for kind, pat in PROFILE_RULES:
            for m in re.finditer(pat, text or ""):
                # 称呼：就近看触发词前 4 字，命中否定/反问/第三人称主语就丢**这条匹配**
                if kind == "name":
                    ctx = (text or "")[max(0, m.start() - NAME_CTX_LEN):m.start()]
                    if NAME_NEG_NEAR_RE.search(ctx):
                        continue
                if self._add_one(kind, m.group(1)):
                    changed = True
        if self._extract_birthday(text):
            changed = True
        return changed

    def _extract_birthday(self, text: str) -> bool:
        """
        🎂 规则轨抓生日（实时，她一说明天就能用）。

        ⚠ 两道闸，跟称呼那套同理，拆任何一道都会记错：
          ① **就近否定**：「我生日不是 3 月 6 号」—— 命中 `不是/不/没` 就丢这条。
          ② **别人主语**：「祁煜生日是 3 月 6 号」—— 那是**他**的生日，记成她的就全反了。
        """
        s = text or ""
        for pat in BDAY_PATTERNS:
            for m in re.finditer(pat, s):
                ctx = s[max(0, m.start() - 4):m.start()]
                # ⚠ 只查**就近 4 字**，不查整句：整句查会把「其他的先不说，我生日是…」
                #   里的「其**他**」当成第三人称主语，白白漏记一条真生日。
                if BDAY_OTHER_RE.search(ctx):
                    continue
                if self._add_one("birthday", m.group(0)):
                    return True
        return False

    def to_prompt_text(self) -> str:
        """渲染成注入 system_prompt 的文本；一条都没有就返回空串。"""
        lines = []
        if self.data.get("name"):
            lines.append(f"- 她让你这样叫她：{self.data['name']}")
        for kind, label in (("likes", "喜欢"), ("dislikes", "不吃/不喜欢"), ("traits", "其他")):
            # ⭐ 2026-09-30 第 3 批：**她自己填的（`manual`）也要进 prompt** ——
            #   不然她在网页上添了一条，他压根不知道，等于「填了没用」。
            #   ⚠ 自动项在前、她填的在后 ⇒ 跟界面上的顺序一致，好排查。
            items = list(self.data.get(kind) or [])
            for v in ((self.data.get("manual") or {}).get(kind) or []):
                if not any(_norm(x) == _norm(v) for x in items):
                    items.append(v)
            if items:
                lines.append(f"- {label}：{'、'.join(items)}")
        # 🎂 生日照常注入：他得知道，不然她生日当天他只会发那条朋友圈、聊天里却不会说一句。
        #   形态写成「3月6日」而不是「03-06」—— 后者是内部存储格式，模型照抄会很出戏。
        bday = (self.data.get("birthday") or "").strip()
        if bday:
            try:
                mm, dd = bday.split("-")
                lines.append(f"- 她的生日：{int(mm)}月{int(dd)}日")
            except Exception:
                pass
        if not lines:
            return ""
        return PROFILE_TEMPLATE.format(lines="\n".join(lines))


# ============================================================
#  🛠 画像的外部工具函数
# ============================================================

def get_user_profile(user_id: str) -> dict:
    """
    读取指定用户的画像（从独立文件读，不经过对话管理器）

    返回：
        dict: {"name":…, "likes":[…], "dislikes":[…], "traits":[…]}
              用户不存在或还没积累到任何信息时返回 None
    """
    p = UserProfile(user_id)
    if not p.to_prompt_text():
        return None
    return p.data


def get_user_birthday(user_id: str) -> str:
    """
    🎂 取她的生日（`MM-DD`）；没抓到返回 ""。

    ⚠ 为什么不复用 `get_user_profile`：那个函数在**空画像**时返回 None
      （本意是「没得注入就别注入」），但发圈模块要的是「有没有生日」，
      空画像（只记了生日、没别的）也得照读 ⇒ 这里直接读文件。
    """
    try:
        p = UserProfile(user_id)
        return (p.data.get("birthday") or "").strip()
    except Exception:
        return ""


def set_user_profile(user_id: str, **kwargs) -> bool:
    """
    手动写入画像（调试 / 迁移用）。日常请不要调用——
    画像应当从对话里自然积累，手动塞会让他「知道本来不知道的事」。

    用法：set_user_profile("10001", name="小辞", likes=["甜的"])
    """
    p = UserProfile(user_id)
    if p.merge(kwargs):
        p.save()
        return True
    return False


def clear_user_profile(user_id: str):
    """清空某个用户的画像（测试期清数据用）"""
    p = UserProfile(user_id)
    if os.path.exists(p.path):
        os.remove(p.path)
