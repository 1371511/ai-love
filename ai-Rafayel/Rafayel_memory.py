# -*- coding: utf-8 -*-
"""
祁煜（Rafayel）的**记忆与对话管理**（2026-09-17 从 `Rafayel_chat.py` 拆出）。

两块：
  ① 落盘 —— `save_memory` / `load_memory`（memory/{user_id}.json）
  ② `ConversationManager` —— 对话状态、历史截断、关键事实、定期摘要与画像补丁

⚠ 依赖方向：本模块 import config 与 profile，**不 import Rafayel_llm**。
  （get_reply 在 llm 层，反过来 import 本模块。若这里再 import llm 就成环了。）
"""

import json
import os
import re
import time

import requests

from Rafayel_config import (
    API_URL, AUTO_GREET_TZ_OFFSET, MAX_FACTS, MAX_HISTORY_TURNS, MEMORY_DIR,
    MODEL, NOW_GAP_HOURS, NOW_PROMPT, REPLY_SHAPE, REPLY_SHAPE_HINT,
    SUMMARY_INTERVAL, SUMMARY_MAX_TOKENS,
)
from Rafayel_daily import record as daily_record
from Rafayel_profile import UserProfile

# ============================================================
#  🕐 当前时间（2026-09-19）
# ============================================================
# ⚠ 这条链上**只该有一个「现在几点」** —— 打招呼 / 发朋友圈 / 对话注入全用
#   `AUTO_GREET_TZ_OFFSET` 换算，别再自己新开一个偏移（换服务器时只改一处）。
_WEEKDAYS_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _now_bj():
    """按 `AUTO_GREET_TZ_OFFSET` 换算后的「现在」（北京时间）。"""
    return time.localtime(time.time() + AUTO_GREET_TZ_OFFSET * 3600)


def now_prompt_text(gap_hours=None):
    """
    拼「## 🕐 现在」那一段；`NOW_PROMPT` 关掉就返回空串。

    ⚠ 两件必须说清的事（都是她实测踩出来的）：
      ① **照实说**：模型没时间感知，不注入就会瞎编钟点 ⇒ 明确写「她问就照实说」。
      ② **隔了多久**：她睡一觉回来，模型看到的历史是**连续的**，
         不点明「隔了两三个小时」它就会接着上次的动作演（睡前洗虾、睡醒还在洗）。
         ⚠ 那个「睡」是**午睡 2~3 小时**（2026-09-20 她纠正），不是过夜 —— 别按更夸张的场景设计。
    ⚠ system_prompt 禁 `**` 与 ASCII 双引号 ⇒ 这段一律用「」或不用引号。
    """
    if not NOW_PROMPT:
        return ""
    n = _now_bj()
    lines = ["## 🕐 现在（北京时间）",
             "%d年%d月%d日 %s %02d:%02d" % (n.tm_year, n.tm_mon, n.tm_mday,
                                            _WEEKDAYS_CN[n.tm_wday], n.tm_hour, n.tm_min)]

    if gap_hours is not None and gap_hours >= NOW_GAP_HOURS:
        # ⚠ 分钟档**必须单独写**：Python 的 `round(0.5)` 是 **0**（银行家舍入），
        #    照旧写成「约 %d 小时前」会印出「约 0 小时前」。
        if gap_hours >= 24:
            when = "约 %d 天前" % int(round(gap_hours / 24.0))
        elif gap_hours >= 1:
            when = "约 %d 小时前" % int(round(gap_hours))
        else:
            when = "约 %d 分钟前" % int(round(gap_hours * 60))

        # ⚠ 这半句是重点：光给数字模型未必会用，得**翻译成演戏的指令**。
        #    两层意思，缺一不可（第二层是她 2026-09-19 追加的）：
        #      ① 上一回合已经结束了，别接着演；
        #      ② **他手上的事也该推进了** —— 隔了两三个小时还说在洗虾，
        #         做饭这种流程早该收尾（她说「正常做饭流程 1h，饭也该做完了」）。
        # ⚠ system_prompt 禁 `**` ⇒ 强调只能靠措辞，别用星号。
        if gap_hours >= 8:
            hint = "—— 隔了这么久，那是上一回事了：手上的事早该做完了，别接着上次的动作继续演。"
        elif gap_hours >= 2:
            hint = "—— 中间隔了这么久，别接成像刚聊到一半；手上的事也该推进到下一步了。"
        else:
            hint = "—— 中间过了这么久，你手上的事（做饭、洗澡、走路这类）也该有进展了，别还停在原地。"
        lines.append("她上一条消息是%s %s" % (when, hint))

    lines.append("（这是真实时间。她问就照实说，别自己编一个钟点。）")
    return "\n".join(lines)


# ============================================================
#  💾 记忆持久化
# ============================================================

def save_memory(user_id: str, cm):
    """把某个用户的记忆保存到 JSON 文件"""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    data = {
        "messages": cm.messages,
        "long_term_summary": cm.long_term_summary,
        "key_facts": cm.key_facts,
        "turn_count": cm.turn_count,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        # 🕐 她最后一条消息的时间戳（用于重启后第一条也算得出「隔了多久」）
        "last_msg_at": cm.last_msg_at,
    }
    path = os.path.join(MEMORY_DIR, f"{user_id}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 先写临时文件再替换，防止写一半崩了丢数据


def load_memory(user_id: str, cm) -> bool:
    """启动时读回记忆，文件不存在返回 False"""
    path = os.path.join(MEMORY_DIR, f"{user_id}.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cm.messages = data.get("messages", cm.messages)
        cm.long_term_summary = data.get("long_term_summary", cm.long_term_summary)
        cm.key_facts = data.get("key_facts", [])
        cm.turn_count = data.get("turn_count", 0)
        # 🕐 读回「她最后一条消息」的时间戳。
        #    老文件没有 `last_msg_at` ⇒ 退回 `saved_at`（那是上次**存盘**的时刻，
        #    比她真正说话晚一轮回复，够用）；都读不到就 None ⇒ 这一轮不显示间隔。
        stamp = data.get("last_msg_at")
        if stamp is None:
            saved = data.get("saved_at")
            try:
                stamp = time.mktime(time.strptime(saved, "%Y-%m-%d %H:%M:%S"))
            except Exception:
                stamp = None
        cm.last_msg_at = stamp
        return True
    except Exception as e:
        print(f"⚠️ 读取记忆失败（{user_id}）：{e}，将从头开始")
        return False


def recent_context(user_id, n=6):
    """
    🎯 取「她最近在聊什么」的一段文本（给**发说说挑条**做相关性用）。

    内容 = 她最近 n 条消息 + 长期摘要尾部 + 最近几条关键事实。

    ⚠ 直接读 `memory\{uid}.json`，**不碰** `ConversationManager` ——
      发说说那条链（后台 task）不该依赖对话引擎的进程内状态，
      而且它跑在**另一个协程**里，去摸 `cm.messages` 既没必要也不安全。
    """
    path = os.path.join(MEMORY_DIR, f"{user_id}.json")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""

    parts = []
    for m in (data.get("messages") or []):
        if isinstance(m, dict) and m.get("role") == "user":
            parts.append(str(m.get("content") or ""))
    parts = parts[-n:]

    summary = (data.get("long_term_summary") or "").strip()
    if summary and summary != "（你们刚开始聊天，还没有值得记录的重要事件。）":
        parts.append(summary[-300:])          # 摘要可能很长，只取尾巴（越近越有用）

    for f in (data.get("key_facts") or [])[-5:]:
        parts.append(str(f))

    return "\n".join(parts)


# ============================================================
#  🗂 对话管理
# ============================================================

class ConversationManager:
    """对话管理器：维护每个用户的对话状态、记忆和用户画像"""

    def __init__(self, base_prompt, user_id="unknown"):
        self.base_prompt = base_prompt
        self.user_id = user_id
        self.messages = [{"role": "system", "content": base_prompt}]
        self.long_term_summary = "（你们刚开始聊天，还没有值得记录的重要事件。）"
        self.key_facts = []
        self.turn_count = 0
        self.pending_summary = []  # 待总结的对话片段

        # 上一轮请求的真实用量与结束原因（2026-09-15 加，查截断/排查用，不参与对话）
        self.last_finish_reason = None
        self.last_usage = None

        # 🕐 她**上一条消息**的时间戳（epoch 秒）—— 用来算「隔了多久」。
        #    ⚠ 只由 `add_user_message` 推进：发说说/打招呼那些**他说的话**不算她开口，
        #      否则他刚提醒过一句，下次就变成「隔了 0 小时」，间隔提示永远不出现。
        self.last_msg_at = None
        # ⚠ `gap_hours` 是**快照**（收她这条消息那一刻算的），不是实时算：
        #    `get_reply` 的顺序是先 add_user_message 再 update_system_message，
        #    等拼 system 时 `last_msg_at` 已经被推进到「现在」了 ⇒ 实时算永远是 0。
        self.gap_hours = None

        # 用户画像：独立文件，从对话里慢慢积累
        self.profile = UserProfile(user_id)

    def get_full_system_prompt(self):
        """构建完整的系统提示词，包含用户画像、记忆摘要和关键事实"""
        # 基础 prompt
        full_prompt = self.base_prompt

        # 追加用户画像（独立文件，从对话里积累；一条都没有就整段不注入）
        profile_text = self.profile.to_prompt_text()
        if profile_text:
            full_prompt += "\n\n" + profile_text

        # 追加长期记忆摘要
        full_prompt += f"\n\n## 📖 长期记忆摘要（请记住这些重要内容）\n{self.long_term_summary}"

        # 追加关键事实
        if self.key_facts:
            facts_text = "\n".join([f"- {f}" for f in self.key_facts[-MAX_FACTS:]])
            full_prompt += f"\n\n## 📌 关键事实（用户让你记住的事）\n{facts_text}"

        # 🕐 「现在几点 + 隔了多久」**不在这里**了 —— 见下面 `now_hint_text()` 的注释。
        #    2026-09-20 挪走：这段每轮都变，留在 system 里会把 DeepSeek 的**前缀缓存**拦腰截断。

        # 💬 回复形状（2026-09-19 她挑的段内不拆行 + 2026-09-22 放宽成一到四段）。
        #    ⚠ 代码层还有一道保底（Rafayel_llm 发出前整形）—— 这里只是让模型少犯错。
        if REPLY_SHAPE and REPLY_SHAPE_HINT:
            full_prompt += "\n\n" + REPLY_SHAPE_HINT

        return full_prompt

    def now_hint_text(self):
        """
        🕐 「现在几点 + 隔了多久」那一段 —— 由调用方**追加在聊天历史之后**送出去。

        ⭐ 2026-09-20 从 `get_full_system_prompt()` 末尾挪到这里，为的是**钱**：
          DeepSeek 的缓存是**前缀匹配** —— 只要前缀里有一处变了，从那一点往后
          **全部**按「未命中」计费。这段每轮都变（几点、隔了多久），它待在 system 里
          ⇒ system 每轮不同 ⇒ 它后面那**整段聊天历史**（12 轮 ≈ 2000+ tokens）
          永远吃不到缓存。挪到最后一条 ⇒ system + 历史整段稳定 ⇒ 命中率大涨。
          （9/19 实测：命中 404K / 未命中 142K，命中率 74% ⇒ 目标 90%+。）

        ⚠ 只进 request_messages，**绝不写回 cm.messages**（跟世界书/表情说明同一个口径）：
          否则会被 save_memory 落盘，每轮累积一份，还会变成常驻人设。
        ⚠ 注意力：原来靠「system 越靠后越受关注」，现在改成「整段 prompt 的最后一条」，
          位置同样是最靠后 ⇒ 真机 A/B 验过再定，不行就往回挪。
        """
        return now_prompt_text(self.gap_hours)

    def _compute_gap_hours(self):
        """
        距她上一条消息过了多少小时；没有基准（第一次聊 / 记录缺失）返回 None。
        """
        if not self.last_msg_at:
            return None
        try:
            return (time.time() - float(self.last_msg_at)) / 3600.0
        except (TypeError, ValueError):
            return None

    def update_system_message(self):
        """更新 messages 中的 system 消息"""
        self.messages[0]["content"] = self.get_full_system_prompt()

    def add_user_message(self, content, media=False):
        """
        添加用户消息。

        `media` = 她这条是不是图 / 表情（记进每日统计，好感度会用到）。
        """
        # 🕐 先算「隔了多久」再推进时间戳 —— 顺序反了就永远算成 0。
        self.gap_hours = self._compute_gap_hours()
        self.last_msg_at = time.time()

        # 📅 每日统计（好感度要用：哪天聊过 / 连续几天 / 谁先开口 / 发没发图）。
        #    ⚠ gap 必须传**刚算出来的快照**：函数里再算一次就已经被上面推进成 0 了。
        #    ⚠ daily_record 自己吞异常，这里不再包一层（写不进去最多少几个数，别拖累对话）。
        daily_record(self.user_id, gap_hours=self.gap_hours, media=media)

        self.messages.append({"role": "user", "content": content})
        self.turn_count += 1
        self.pending_summary.append({"role": "user", "content": content})

        # 检测关键词，提取关键事实
        self._extract_facts(content)

        # 从她的话里留意她的喜好（规则命中即存，命中就落盘）
        if self.profile.extract_from_text(content):
            self.profile.save()

    def add_assistant_message(self, content):
        """添加助手消息"""
        self.messages.append({"role": "assistant", "content": content})
        self.pending_summary.append({"role": "assistant", "content": content})

        # 从祁煜的回复中提取可能的重要信息（比如承诺、约定）
        self._extract_facts_from_reply(content)

    def _extract_facts(self, text):
        """从用户输入中提取关键事实"""
        # 检测用户是否在强调某件事
        patterns = [
            (r"记住[：:]\s*(.+)", "用户让你记住：{}"),
            (r"别忘了[：:]\s*(.+)", "用户提醒你：{}"),
            (r"答应我[：:]\s*(.+)", "你答应了用户：{}"),
            # 2026-09-15 修：原写法 (吗\?|吗|么|不)? 是**第 1 个捕获组**，
            # 用户输入「你还记得xxx」时 group(1)=None → len(None) TypeError 直接崩掉整轮回复。
            # 改成非捕获组 (?:…)，group(1) 才是真正要记的内容。
            #   ⚠ 顺带修掉原正则里的 `.{0,10}`：它是贪婪的，会把用户真正想说的内容吃掉，
            #     最后只捕获到末尾一两个字，再被 len>3 过滤掉 ⇒ 不崩了但也什么都记不到。
            (r"你还记得(?:吗\?|吗|么|不)?[，,。.、]?\s*(.+)", "用户提起过去的回忆：{}"),
            (r"以前[：:]\s*(.+)", "用户提到以前的事：{}"),
            (r"约定[：:]\s*(.+)", "你们之间的约定：{}"),
        ]

        for pattern, template in patterns:
            match = re.search(pattern, text)
            if match:
                # 2026-09-15 修：原写法 match.group(1) if match.groups() 有两个坑——
                #   ① groups() 判的是「元组非空」，group(1) 仍可能是 None（可选组没匹配上）
                #   ② None 传进 len() 直接 TypeError，而本函数由 add_user_message 调用且无 try 保护
                # 改用 lastindex 判断最后一个参与匹配的捕获组，并兜底取整句。
                fact = ""
                if match.lastindex:
                    fact = (match.group(match.lastindex) or "").strip()
                if not fact:
                    fact = text[:50]
                if len(fact) > 3:
                    self._add_fact(template.format(fact[:80]))
                    break

    def _extract_facts_from_reply(self, text):
        """从祁煜的回复中提取承诺、约定等"""
        patterns = [
            (r"我答应你[：:]\s*(.+)", "祁煜答应了：{}"),
            (r"我保证[：:]\s*(.+)", "祁煜保证了：{}"),
            # 2026-09-15 修：[记住|记得] 是**字符类**，匹配「记/住/|/得」任一单字，
            # 永远匹配不到「我会记住：xxx」。改成 (?:记住|记得)。
            (r"我会(?:记住|记得)[：:]\s*(.+)", "祁煜说会记住：{}"),
        ]
        for pattern, template in patterns:
            match = re.search(pattern, text)
            if match:
                fact = ""
                if match.lastindex:
                    fact = (match.group(match.lastindex) or "").strip()
                if not fact:
                    fact = text[:50]
                if len(fact) > 3:
                    self._add_fact(template.format(fact[:80]))
                    break

    def should_summarize(self):
        """是否需要触发摘要更新"""
        return self.turn_count > 0 and self.turn_count % SUMMARY_INTERVAL == 0

    def get_recent_messages(self):
        """获取最近 N 轮对话（不含 system）"""
        all_messages = self.messages[1:]  # 去掉 system
        if len(all_messages) > MAX_HISTORY_TURNS * 2:
            return all_messages[-(MAX_HISTORY_TURNS * 2):]
        return all_messages

    def truncate_history(self):
        """截断历史，保留最近 N 轮 + system"""
        all_messages = self.messages[1:]  # 去掉 system
        if len(all_messages) > MAX_HISTORY_TURNS * 2:
            recent = all_messages[-(MAX_HISTORY_TURNS * 2):]
            self.messages = [self.messages[0]] + recent

    def generate_summary(self, api_key):
        """调用 AI 生成对话摘要"""
        if not self.pending_summary:
            return

        # 取待总结的对话
        to_summarize = self.pending_summary.copy()
        self.pending_summary = []

        # 构造摘要 prompt
        summary_prompt = f"""你正在为祁煜整理对话记忆。

【任务一】用一段话（不超过200字）总结这段对话的核心内容：
1. 她表现出了哪些情绪、需求或想法？
2. 祁煜做出了哪些重要的回应、承诺或行动？
3. 发生了什么可能影响后续对话的重要事件？

【任务二】从对话里留意「关于她」的事实。只写**她自己明确说过**的，不要推测、不要脑补：
- name：她让祁煜怎么叫她（没说过就空字符串）
- likes：她喜欢什么（数组）
- dislikes：她讨厌 / 不吃 / 不喜欢什么（数组）
- traits：其他稳定特征，比如职业、习惯、作息（数组）
- birthday：她**自己**的生日，形如 "03-06"（公历，月-日两位）。
  ⚠ 只填她**明确说过**的（「我生日是3月6号」）；她没说过、或是**祁煜**的生日，一律空字符串。

对话内容：
{json.dumps(to_summarize, ensure_ascii=False, indent=2)}

输出格式（严格照做，不要加别的小标题）：
先写任务一的摘要正文，
然后换行，最后单独一行写：
PROFILE: {{"name": "", "likes": [], "dislikes": [], "traits": [], "birthday": ""}}
"""
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "model": MODEL,
                "messages": [{"role": "user", "content": summary_prompt}],
                "stream": False,
                "max_tokens": SUMMARY_MAX_TOKENS
            }
            response = requests.post(API_URL, headers=headers, json=data, timeout=10)
            result = response.json()

            if "choices" in result:
                raw = result["choices"][0]["message"]["content"].strip()

                # ① 拆出任务二的画像补丁（若模型没按格式输出，就当没有，不影响摘要）
                m_prof = re.search(r"PROFILE:\s*(\{.*?\})\s*$", raw, re.S)
                if m_prof:
                    new_summary = raw[:m_prof.start()].strip()
                    try:
                        if self.profile.merge(json.loads(m_prof.group(1))):
                            self.profile.save()
                    except Exception as pe:
                        print(f"⚠️ 画像补丁解析失败（忽略）：{pe}")
                else:
                    new_summary = raw

                if not new_summary:
                    new_summary = "（本轮无可摘要内容）"

                if self.long_term_summary == "（你们刚开始聊天，还没有值得记录的重要事件。）":
                    self.long_term_summary = new_summary
                else:
                    self.long_term_summary = self.long_term_summary + "\n\n" + new_summary
                if len(self.long_term_summary) > 1500:
                    self.long_term_summary = self.long_term_summary[-1500:]
                    self.long_term_summary = "...(较早记忆已压缩)...\n" + self.long_term_summary
        except Exception as e:
            print(f"⚠️ 摘要生成失败：{e}，将继续正常对话")
            self.pending_summary = to_summarize + self.pending_summary

    def _add_fact(self, fact: str):
        """追加关键事实（去重 + 上限）。2026-09-15 新增：旧版无去重，同一句会被反复记进去。"""
        if fact in self.key_facts:
            return
        self.key_facts.append(fact)
        self.trim_facts()

    def trim_facts(self):
        """限制关键事实数量"""
        if len(self.key_facts) > MAX_FACTS:
            self.key_facts = self.key_facts[-MAX_FACTS:]
