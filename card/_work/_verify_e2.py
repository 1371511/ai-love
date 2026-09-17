# -*- coding: utf-8 -*-
"""批次 E 端到端验证：桩掉网络与落盘，检查 get_reply 真实构造出来的 messages。"""
import sys, io, os

ROOT = r"E:\ai-love"                        # 项目根（card\ memory\ .env 都在这）
CODE = os.path.join(ROOT, "ai-Rafayel")     # 祁煜代码层（2026-09-17 从项目根搬入）
sys.path.insert(0, CODE)
import Rafayel_chat as R
# ⚠ 2026-09-17 拆分后：get_reply / save_memory 的**实际所在层**是 Rafayel_llm。
#   桩必须打在「真正使用该名字的那个模块」上 —— 打 Rafayel_chat 是打不到的
#   （Rafayel_chat 只是重新导出，改了它不影响 Rafayel_llm 里的全局查找）。
#   `requests` 例外：它是模块对象，各层共用同一个，打任一处都生效。
import Rafayel_llm as L

# —— 桩：不联网、不写用户的记忆文件 ——
CAP = {}
WRITTEN = []

class FakeResp(object):
    def __init__(self, payload):
        self._p = payload
    def json(self):
        return self._p

def fake_post(url, headers=None, json=None, timeout=None, **kw):
    CAP["messages"] = list(json["messages"])
    CAP["payload"] = dict(json)          # 2026-09-15：顺带捕获生成参数（max_tokens）
    return FakeResp({"choices": [{"message": {"content": "（stub 回复）"}}]})

def fake_snapshot(payload):
    return payload

L.requests.post = fake_post
L.save_memory = lambda user_id, cm: WRITTEN.append(user_id)

out = []
def p(*a):
    out.append(" ".join(str(x) for x in a))

def run(text, uid="verify_e"):
    reply = R.get_reply(text, uid)
    msgs = CAP["messages"]
    roles = [m["role"] for m in msgs]
    return reply, msgs, roles

def has(txt, needle):
    return needle in txt

p("=== 轮 1：聊「白沙湾画室」 ===")
r1, m1, roles1 = run("今天去白沙湾的画室找你")
p("  角色序列：%s" % roles1)
sys0 = m1[0]["content"]
p("  system 长度：%d" % len(sys0))
p("  ├ 含 description 职业段（2047）：%s" % has(sys0, "2047"))
p("  ├ 含 system_prompt 规则（两种情绪切换）：%s" % has(sys0, "两种情绪切换"))
p("  ├ 含世界书 before 段标题：%s" % has(sys0, "## 相关设定"))
p("  ├ 命中条目「白沙湾 · Mo Art Studio」：%s" % has(sys0, "白沙湾 · Mo Art Studio"))
p("  └ 末尾是否含 post_history：%s" % has(m1[-1]["content"], "不要重复开场白"))
p("")

# ⚠ 预期澄清：SillyTavern 的世界书**会扫描最近几条历史**（本项目 depth=4，约 2 轮），
#   所以上一轮聊过的话题在这一两轮里还会继续命中 —— 这是刻意的上下文连贯行为，
#   不是 bug。真正要验证的是：话题滚出扫描窗口后应当消失。
p("=== 轮 2：改聊「海神」（历史仍含白沙湾，会连带命中，属正常）===")
r2, m2, roles2 = run("你当上海神的时候是什么感觉")
sys2 = m2[0]["content"]
p("  system 长度：%d" % len(sys2))
p("  ├ 含海神/利莫里亚条目：%s" % ("海神之位" in sys2 or "利莫里亚" in sys2))
p("  ├ 仍含上一轮的「白沙湾」条目（历史残留，符合 depth=4 设计）：%s"
  % ("白沙湾 · Mo Art Studio" in sys2))
p("  └ post_history 仍只有 1 份（未累积）：%s" % (m2[-1]["content"].count("不要重复开场白") == 1))
p("")

p("=== 轮 3：连续聊 4 条与之前完全无关的日常 → 旧话题应滚出扫描窗口 ===")
for t in ["今天中午吃什么好呢", "我有点困", "窗外在下雨", "你帮我看看这个颜色好看吗"]:
    r3, m3, _ = run(t)
sys3 = m3[0]["content"]
p("  最后一条输入：你帮我看看这个颜色好看吗")
p("  ├ 命中「色彩执念」（当轮话题）：%s" % ("色彩执念" in sys3))
p("  ├ 已不含「白沙湾」条目（滚出窗口）：%s" % ("白沙湾 · Mo Art Studio" not in sys3))
p("  └ 仍含人设与规则：%s" % ("两种情绪切换" in sys3 and "2047" in sys3))
p("")

p("=== 轮 4：after_char（称呼）是否追加到历史之后 ===")
r4, m4, roles4 = run("以后我叫你小鱼好不好")
p("  角色序列：%s" % roles4[1:])
# 只统计 index>0 的 system（messages[0] 是 system_prompt + before 段，不算"历史之后"）
tail_sys = [m["content"] for i, m in enumerate(m4) if i > 0 and m["role"] == "system"]
p("  历史之后的 system 段数：%d" % len(tail_sys))
for a in tail_sys:
    p("    · %s" % a.replace("\n", " / ")[:70])
last = m4[-1]["content"]
p("  最后一条是 post_history：%s" % ("不要重复开场白" in last))
p("")

p("=== 检查：沉淀进 messages（会被 save_memory 写盘的部分）里有没有夹带世界书 ===")
cm = R._user_managers["verify_e"]
p("  cm.messages[0] 含世界书标题：%s（应为 False）" % ("## 相关设定" in cm.messages[0]["content"]))
p("  cm.messages[0] 含 post_history：%s（应为 False）" % ("不要重复开场白" in cm.messages[0]["content"]))
p("  cm.messages 条数：%d" % len(cm.messages))

p("")
p("=== 检查：生成参数（2026-09-15 起不再写死 300）===")
p("  MAX_TOKENS = %d / SUMMARY_MAX_TOKENS = %d（旧值均为 300）"
  % (R.MAX_TOKENS, R.SUMMARY_MAX_TOKENS))
p("  ├ 常量已调大：%s" % (R.MAX_TOKENS > 300 and R.SUMMARY_MAX_TOKENS > 300))
p("  └ 回复请求用的是 MAX_TOKENS：%s（实发 %s）"
  % (CAP.get("payload", {}).get("max_tokens") == R.MAX_TOKENS,
     CAP.get("payload", {}).get("max_tokens")))

cm.pending_summary = [{"role": "user", "content": "（用于检查摘要请求的参数）"}]
cm.generate_summary("fake_key")
p("  摘要请求用的是 SUMMARY_MAX_TOKENS：%s（实发 %s）"
  % (CAP.get("payload", {}).get("max_tokens") == R.SUMMARY_MAX_TOKENS,
     CAP.get("payload", {}).get("max_tokens")))
# 2026-09-17 拆分后要扫**全部 4 个模块**：API 调用已移到 Rafayel_llm.py，
# 只扫 Rafayel_chat.py 会永远为 True（假绿）。
_MODULES = ["Rafayel_config.py", "Rafayel_profile.py",
            "Rafayel_memory.py", "Rafayel_llm.py", "Rafayel_chat.py"]
_hard = [m for m in _MODULES
         if "\"max_tokens\": 300" in io.open(os.path.join(CODE, m), encoding="utf-8").read()]
p("  ※ 源码里已无写死的 max_tokens：%s（扫描 %d 个模块；命中：%s）"
  % (not _hard, len(_MODULES), _hard or "无"))

io.open(r"F:\workB\JOB\lysk\_e3.txt", "w", encoding="utf-8").write("\n".join(out))
print("done")
