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
    API_URL, MAX_FACTS, MAX_HISTORY_TURNS, MEMORY_DIR, MODEL,
    SUMMARY_INTERVAL, SUMMARY_MAX_TOKENS,
)
from Rafayel_profile import UserProfile


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
        return True
    except Exception as e:
        print(f"⚠️ 读取记忆失败（{user_id}）：{e}，将从头开始")
        return False


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

        return full_prompt

    def update_system_message(self):
        """更新 messages 中的 system 消息"""
        self.messages[0]["content"] = self.get_full_system_prompt()

    def add_user_message(self, content):
        """添加用户消息"""
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
