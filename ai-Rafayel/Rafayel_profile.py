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
            items = self.data.get(kind) or []
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
